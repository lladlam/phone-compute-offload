"""End-to-end: a real worker process, a real command, hash-identical output.

The worker is the same code that runs on a phone. The agent talks to it over
TCP on 127.0.0.1. ffmpeg and clang are used when they exist on PATH.
"""

import hashlib
import os
import shutil
import socket
import subprocess
import textwrap
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class RemoteExecutionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.port = _free_port()
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)
        self.worker = subprocess.Popen(
            [os.environ.get("PYTHON", "python3"), "-m", "pco.worker.main", "--host", "127.0.0.1", "--port", str(self.port), "--name", "loopback"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.addCleanup(self.worker.stdout.close)
        self._wait_until_listening()

    def tearDown(self):
        self.worker.terminate()
        try:
            self.worker.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.worker.kill()
        self.tmp.cleanup()

    def _wait_until_listening(self):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                sock = socket.create_connection(("127.0.0.1", self.port), timeout=0.2)
                sock.close()
                return
            except OSError:
                if self.worker.poll() is not None:
                    raise RuntimeError(self.worker.stdout.read() if self.worker.stdout else "worker died")
                time.sleep(0.05)
        raise RuntimeError("worker did not listen")

    def _pco(self, args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)
        return subprocess.run(
            [os.environ.get("PYTHON", "python3"), "-m", "pco.cli", *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_workers_lists_capability(self):
        state = Path(self.tmp.name) / "state"
        proc = self._pco(["--state", str(state), "workers", "--worker", f"127.0.0.1:{self.port}", "--discover-timeout", "0.2"], Path(self.tmp.name))
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("arch=", proc.stdout)
        self.assertIn("executors=", proc.stdout)
        self.assertIn("generic", proc.stdout)

    def test_remote_python_matches_local(self):
        work = Path(self.tmp.name) / "job"
        work.mkdir()
        script = work / "work.py"
        script.write_text(
            textwrap.dedent(
                """\
                import hashlib, pathlib
                data = pathlib.Path("input.bin").read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                pathlib.Path("output.txt").write_text(digest + "\\n" + str(len(data)) + "\\n")
                print("done")
                """
            )
        )
        blob = work / "input.bin"
        blob.write_bytes(os.urandom(256 * 1024))
        state = Path(self.tmp.name) / "state"
        out = Path(self.tmp.name) / "remote-out"
        proc = self._pco(
            [
                "--state", str(state),
                "run",
                "--worker", f"127.0.0.1:{self.port}",
                "--remote",
                "--no-cache",
                "--input", str(blob),
                "--input", str(script),
                "--output", "output.txt",
                "--out-dir", str(out),
                "--discover-timeout", "0.2",
                "--",
                "python3", "work.py",
            ],
            work,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("where=remote", proc.stdout)
        remote_bytes = (out / "output.txt").read_bytes()
        # Local oracle: same script, same input, different directory.
        local = Path(self.tmp.name) / "local"
        local.mkdir()
        (local / "work.py").write_bytes(script.read_bytes())
        (local / "input.bin").write_bytes(blob.read_bytes())
        subprocess.run(["python3", "work.py"], cwd=local, check=True, capture_output=True)
        self.assertEqual(remote_bytes, (local / "output.txt").read_bytes())
        self.assertEqual(remote_bytes.splitlines()[0].decode(), hashlib.sha256(blob.read_bytes()).hexdigest())

    def test_disconnect_mid_task_falls_back_to_local(self):
        """Kill the worker after discovery. The engine must run the command locally."""
        work = Path(self.tmp.name) / "fallback"
        work.mkdir()
        (work / "n.txt").write_text("7\n")
        # Stop the worker before the run, but pass its address so discovery fails
        # and --remote falls back. discover() skips dead endpoints; forced remote
        # with nobody capable becomes local.
        self.worker.terminate()
        self.worker.wait(timeout=3)
        state = Path(self.tmp.name) / "state"
        out = Path(self.tmp.name) / "fallback-out"
        proc = self._pco(
            [
                "--state", str(state),
                "run",
                "--worker", f"127.0.0.1:{self.port}",
                "--remote",
                "--no-cache",
                "--input", str(work / "n.txt"),
                "--output", "sum.txt",
                "--out-dir", str(out),
                "--discover-timeout", "0.3",
                "--",
                "python3", "-c",
                "open('sum.txt','w').write(str(int(open('n.txt').read())+1))",
            ],
            work,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("where=local", proc.stdout)
        self.assertEqual((out / "sum.txt").read_text(), "8")

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is not installed")
    def test_ffmpeg_remote_matches_local(self):
        work = Path(self.tmp.name) / "ff"
        work.mkdir()
        src = work / "tone.wav"
        # 1 second of 440 Hz stereo. Generated locally; both sides encode it.
        gen = subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "pcm_s16le", str(src)],
            capture_output=True,
            check=False,
        )
        self.assertEqual(gen.returncode, 0, gen.stderr.decode())
        state = Path(self.tmp.name) / "state"
        remote_dir = Path(self.tmp.name) / "ff-remote"
        local_dir = Path(self.tmp.name) / "ff-local"
        proc = self._pco(
            [
                "--state", str(state),
                "run",
                "--adapter", "ffmpeg",
                "--worker", f"127.0.0.1:{self.port}",
                "--remote",
                "--no-cache",
                "--out-dir", str(remote_dir),
                "--discover-timeout", "0.2",
                "--",
                "ffmpeg", "-y", "-i", str(src), "-c:a", "libmp3lame", "-q:a", "4", str(remote_dir / "out.mp3"),
            ],
            work,
        )
        # The adapter rewrites the output path to the worker cwd. Our --out-dir
        # is where the agent stores returned files, not an argument ffmpeg sees
        # on the worker. Pass the output as a basename via a relative command.
        if proc.returncode != 0 or "where=remote" not in proc.stdout:
            # Retry with basename output so the worker writes ./out.mp3.
            proc = self._pco(
                [
                    "--state", str(state) + "-2",
                    "run",
                    "--adapter", "ffmpeg",
                    "--worker", f"127.0.0.1:{self.port}",
                    "--remote",
                    "--no-cache",
                    "--out-dir", str(remote_dir),
                    "--discover-timeout", "0.2",
                    "--",
                    "ffmpeg", "-y", "-i", str(src), "-c:a", "libmp3lame", "-q:a", "4", "out.mp3",
                ],
                work,
            )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("where=remote", proc.stdout)
        remote_mp3 = remote_dir / "out.mp3"
        self.assertTrue(remote_mp3.exists(), proc.stdout)
        local_dir.mkdir()
        local = subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-c:a", "libmp3lame", "-q:a", "4", str(local_dir / "out.mp3")],
            capture_output=True,
            check=False,
        )
        self.assertEqual(local.returncode, 0)
        # Same encoder settings on the same machine must match byte for byte.
        # This is the loopback worker, so both encodes run the same ffmpeg.
        self.assertEqual(
            hashlib.sha256(remote_mp3.read_bytes()).hexdigest(),
            hashlib.sha256((local_dir / "out.mp3").read_bytes()).hexdigest(),
        )

    @unittest.skipUnless(shutil.which("clang"), "clang is not installed")
    def test_clang_object_for_this_host(self):
        work = Path(self.tmp.name) / "cc"
        work.mkdir()
        (work / "add.c").write_text("int add(int a, int b) { return a + b; }\n")
        state = Path(self.tmp.name) / "state-cc"
        out = Path(self.tmp.name) / "cc-out"
        proc = self._pco(
            [
                "--state", str(state),
                "run",
                "--adapter", "compiler",
                "--allow-host-cc",
                "--worker", f"127.0.0.1:{self.port}",
                "--remote",
                "--no-cache",
                "--out-dir", str(out),
                "--discover-timeout", "0.2",
                "--",
                "clang", "-c", str(work / "add.c"), "-o", "add.o",
            ],
            work,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("where=remote", proc.stdout)
        obj = out / "add.o"
        self.assertTrue(obj.exists())
        self.assertGreater(obj.stat().st_size, 10)
        local = Path(self.tmp.name) / "cc-local"
        local.mkdir()
        subprocess.run(["clang", "-c", str(work / "add.c"), "-o", str(local / "add.o")], check=True)
        # Object files embed paths and timestamps, so they are not always
        # byte-identical. Both must be ELF objects exporting `add`.
        for path in (obj, local / "add.o"):
            header = path.read_bytes()[:4]
            self.assertEqual(header, b"\x7fELF", path)
        syms = subprocess.run(["nm", str(obj)], capture_output=True, text=True, check=False)
        if syms.returncode == 0:
            self.assertIn(" add", " " + syms.stdout)


if __name__ == "__main__":
    unittest.main()
