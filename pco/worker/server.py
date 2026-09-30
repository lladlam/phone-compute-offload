"""TCP worker.

One connection runs one task, then the process keeps listening. A second
connection is accepted only after the current task finishes (``max_jobs``
is advertised, but phase 1 runs a single task at a time so a phone does not
melt). Discovery broadcasts are sent from a side thread.

Cancellation: if the agent closes the socket, ``subprocess`` is killed on
the next timeout check. Phase 1 does not multiplex cancel onto a live
connection because the connection is busy carrying logs; closing it is the
cancel signal. The agent treats a dropped connection as a failed attempt.
"""

from __future__ import annotations

import socket
import tempfile
import threading
import uuid
from pathlib import Path

from pco.agent.discovery import WORKER_PORT, announce_once
from pco.protocol.codec import FrameCodec, ProtocolError
from pco.protocol.messages import PROTOCOL_VERSION, Result, Task, dump
from pco.worker.executor import execute
from pco.worker.probe import collect


class WorkerServer:
    def __init__(self, host: str = "0.0.0.0", port: int = WORKER_PORT, name: str | None = None, token: str = ""):
        self.host = host
        self.port = port
        self.name = name or socket.gethostname()
        self.token = token
        self.worker_id = uuid.uuid4().hex[:12]
        self.hello = collect(self.worker_id, self.name)
        self._stop = threading.Event()

    def serve_forever(self) -> None:
        announcer = threading.Thread(target=self._announce_loop, daemon=True)
        announcer.start()
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen(4)
        server.settimeout(1.0)
        print(f"pco-worker {self.worker_id} listening on {self.host}:{self.port}", flush=True)
        print(f"executors: {sorted(self.hello.executor_names())}", flush=True)
        try:
            while not self._stop.is_set():
                try:
                    conn, addr = server.accept()
                except TimeoutError:
                    continue
                try:
                    self._handle(conn, addr)
                except Exception as exc:  # noqa: BLE001 — keep the worker alive
                    print(f"connection {addr} failed: {exc}", flush=True)
                finally:
                    conn.close()
        finally:
            server.close()

    def stop(self) -> None:
        self._stop.set()

    def _announce_loop(self) -> None:
        names = sorted(self.hello.executor_names())
        while not self._stop.is_set():
            try:
                announce_once(self.name, self.worker_id, names, self.port)
            except OSError as exc:
                print(f"announce failed: {exc}", flush=True)
            self._stop.wait(2.0)

    def _handle(self, conn: socket.socket, addr) -> None:
        conn.settimeout(30)
        codec = FrameCodec(conn)
        hello = codec.recv()
        if hello.get("type") != "hello" or int(hello.get("version", 0)) != PROTOCOL_VERSION:
            codec.send({"type": "error", "message": "unsupported hello"})
            return
        if self.token and hello.get("token") != self.token:
            codec.send({"type": "error", "message": "bad token"})
            return
        # Refresh volatile fields so the agent sees current temperature and load.
        fresh = collect(self.worker_id, self.name)
        codec.send(dump(fresh))
        message = codec.recv()
        if message.get("type") == "probe":
            return
        if message.get("type") != "task":
            codec.send({"type": "error", "message": "expected task"})
            return
        task = _task_from(message)
        reason = _refuse_reason(fresh, task)
        if reason:
            codec.send({"type": "task_accept", "task_id": task.task_id, "accepted": False, "reason": reason})
            return
        codec.send({"type": "task_accept", "task_id": task.task_id, "accepted": True, "reason": ""})
        with tempfile.TemporaryDirectory(prefix="pco-") as tmp:
            work = Path(tmp)
            while True:
                nxt = codec.recv()
                if nxt.get("type") == "inputs_done":
                    break
                if nxt.get("type") == "file_begin":
                    # recv_file expects to read file_begin itself. Push it back
                    # by parsing inline: we already consumed file_begin, so
                    # receive chunks here.
                    _receive_open_file(codec, work, nxt)
                    continue
                if nxt.get("type") == "cancel":
                    codec.send(dump(Result(task_id=task.task_id, exit_code=130, error="cancelled")))
                    return
                codec.send({"type": "error", "message": f"unexpected {nxt.get('type')}"})
                return
            conn.settimeout(max(task.timeout_s + 5, 30))
            codec.send({"type": "progress", "task_id": task.task_id, "fraction": 0.1, "message": "running"})
            result = execute(
                task.argv,
                cwd=work,
                env=task.env,
                timeout_s=task.timeout_s,
                output_globs=task.output_globs,
            )
            # Do not ship the inputs back. Outputs are files matching the globs
            # that were not in the input set.
            input_names = {spec["name"] for spec in task.cwd_files}
            outputs = [path for path in result.outputs if path.name not in input_names]
            codec.send(
                dump(
                    Result(
                        task_id=task.task_id,
                        exit_code=result.exit_code,
                        stdout=result.stdout[-100_000:],
                        stderr=result.stderr[-100_000:],
                        outputs=[{"name": path.name, "size": path.stat().st_size} for path in outputs],
                        elapsed_s=result.elapsed_s,
                        error="",
                        worker_id=self.worker_id,
                    )
                )
            )
            for path in outputs:
                codec.send_file(task.task_id, path, role="output", name=path.name)
        print(f"task {task.task_id} from {addr[0]} exit {result.exit_code} in {result.elapsed_s:.3f}s", flush=True)


def _task_from(message: dict) -> Task:
    fields = {key: message[key] for key in Task.__dataclass_fields__ if key in message}
    return Task(**fields)


def _refuse_reason(hello: WorkerHello, task: Task) -> str:
    from pco.agent.capability import worker_supports

    return worker_supports(hello, task) or ""


def _receive_open_file(codec: FrameCodec, dest: Path, begin: dict) -> None:
    """``begin`` is the already-read file_begin. Then chunks, same checks as FrameCodec.recv_file."""
    import base64
    import hashlib

    from pco.protocol.codec import ProtocolError, _safe_name

    name = _safe_name(begin["name"])
    size = int(begin["size"])
    digest = str(begin["sha256"])
    blob = bytearray()
    while True:
        chunk = codec.recv()
        if chunk.get("type") != "chunk" or chunk.get("name") != begin["name"]:
            raise ProtocolError("chunk does not belong to the open file")
        if int(chunk["offset"]) != len(blob):
            raise ProtocolError("unexpected chunk offset")
        blob += base64.b64decode(chunk.get("data_b64", ""), validate=True)
        if chunk.get("eof"):
            break
    if len(blob) != size or hashlib.sha256(blob).hexdigest() != digest:
        raise ProtocolError(f"checksum failed for {name}")
    (dest / name).write_bytes(blob)
