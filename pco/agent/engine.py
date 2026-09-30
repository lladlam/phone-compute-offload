"""Glue: adapt, schedule, run local or remote, record history, cache."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pco.agent.cache import ResultCache, cache_key
from pco.agent.discovery import listen
from pco.agent.localexec import run_local
from pco.agent.remote import RemoteError, fetch_hello, run_remote
from pco.agent.scheduler import Decision, History, LinkEstimate, Scheduler
from pco.protocol.messages import Task, WorkerHello


@dataclass
class RunReport:
    decision: Decision
    exit_code: int
    stdout: str
    stderr: str
    elapsed_s: float
    output_dir: Path
    files: list[Path] = field(default_factory=list)
    where: str = "local"
    worker_id: str | None = None
    cached: bool = False


class Engine:
    def __init__(self, state_dir: Path, link: LinkEstimate | None = None):
        self.state_dir = state_dir
        self.history = History.load(state_dir / "history.json")
        self.scheduler = Scheduler(self.history, link)
        self.cache = ResultCache(state_dir / "cache")

    def discover(self, endpoints: list[tuple[str, int]], timeout_s: float, token: str = "") -> list[tuple[str, int, WorkerHello]]:
        found = [(item.host, item.port) for item in listen(timeout_s)]
        for endpoint in endpoints:
            if endpoint not in found:
                found.append(endpoint)
        workers = []
        for host, port in found:
            try:
                hello = fetch_hello(host, port, timeout_s=3.0, token=token)
            except (RemoteError, OSError):
                continue
            workers.append((host, port, hello))
        return workers

    def run(
        self,
        task: Task,
        inputs: list[Path],
        *,
        workers: list[tuple[str, int, WorkerHello]],
        output_dir: Path,
        force: str | None = None,
        use_cache: bool = True,
    ) -> RunReport:
        blobs = [(path.name, path.read_bytes()) for path in inputs]
        key = cache_key(task, blobs)
        task.cache_key = key
        if use_cache:
            hit = self.cache.lookup(key)
            if hit is not None:
                output_dir.mkdir(parents=True, exist_ok=True)
                files = []
                for path in hit.files:
                    dest = output_dir / path.name
                    dest.write_bytes(path.read_bytes())
                    files.append(dest)
                decision = Decision("local", None, 0.0, None, "cache hit")
                return RunReport(decision, hit.exit_code, hit.stdout, hit.stderr, 0.0, output_dir, files, "cache", None, True)

        hellos = [hello for _host, _port, hello in workers]
        decision = self.scheduler.decide(task, hellos, force=force)
        if decision.where == "remote" and decision.worker_id:
            endpoint = next((item for item in workers if item[2].worker_id == decision.worker_id), None)
            if endpoint is None:
                decision = Decision("local", None, decision.local_cost_s, decision.remote_cost_s, "worker disappeared")
            else:
                host, port, hello = endpoint
                try:
                    remote = run_remote(host, port, task, inputs, output_dir)
                except RemoteError as exc:
                    self.scheduler.note_failure(hello.worker_id)
                    self._persist()
                    local = self._local(task, inputs, output_dir)
                    return RunReport(
                        Decision("local", None, decision.local_cost_s, decision.remote_cost_s, f"remote failed ({exc}); local fallback"),
                        local.exit_code,
                        local.stdout,
                        local.stderr,
                        local.elapsed_s,
                        output_dir,
                        _files(output_dir, task.output_globs),
                        "local",
                        None,
                        False,
                    )
                self.scheduler.note_success(hello.worker_id)
                self.history.add_remote(task.adapter, hello.worker_id, remote.result.elapsed_s, max(task.input_bytes, 1))
                self._persist()
                if use_cache and remote.result.exit_code == 0:
                    self._store(key, remote.result.exit_code, remote.result.stdout, remote.result.stderr, remote.files)
                return RunReport(
                    decision,
                    remote.result.exit_code,
                    remote.result.stdout,
                    remote.result.stderr,
                    remote.elapsed_s,
                    output_dir,
                    remote.files,
                    "remote",
                    hello.worker_id,
                    False,
                )

        local = self._local(task, inputs, output_dir)
        self.history.add_local(task.adapter, local.elapsed_s, max(task.input_bytes, 1))
        self._persist()
        files = _files(output_dir, task.output_globs)
        if use_cache and local.exit_code == 0:
            self._store(key, local.exit_code, local.stdout, local.stderr, files)
        return RunReport(decision, local.exit_code, local.stdout, local.stderr, local.elapsed_s, output_dir, files, "local", None, False)

    def _local(self, task: Task, inputs: list[Path], output_dir: Path):
        output_dir.mkdir(parents=True, exist_ok=True)
        for path in inputs:
            dest = output_dir / path.name
            if path.resolve() != dest.resolve():
                dest.write_bytes(path.read_bytes())
        return run_local(task.argv, cwd=output_dir, env=task.env, timeout_s=task.timeout_s, output_dir=output_dir)

    def _store(self, key: str, code: int, stdout: str, stderr: str, files: list[Path]) -> None:
        self.cache.store(
            key,
            exit_code=code,
            stdout=stdout,
            stderr=stderr,
            files=[(path.name, path.read_bytes()) for path in files],
        )

    def _persist(self) -> None:
        self.history.save(self.state_dir / "history.json")


def _files(output_dir: Path, globs: list[str]) -> list[Path]:
    found = []
    for pattern in globs:
        found.extend(path for path in output_dir.glob(pattern) if path.is_file())
    return found
