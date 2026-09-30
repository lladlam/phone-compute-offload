"""Send one task to a worker and collect the result.

Control flow:

1. TCP connect, ``hello`` / ``worker_hello``.
2. ``task`` description.
3. ``file_begin`` + ``chunk`` frames for each input.
4. Read ``progress``, ``log``, and finally ``result``.
5. Read output files the result announced.
6. On a hash or socket failure, the caller may retry or fall back to local.
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

from pco.protocol.codec import FrameCodec, ProtocolError
from pco.protocol.messages import Hello, Result, Task, WorkerHello, dump


@dataclass
class RemoteOutput:
    result: Result
    files: list[Path]
    worker: WorkerHello
    elapsed_s: float
    logs: list[str] = field(default_factory=list)


class RemoteError(Exception):
    pass


def fetch_hello(host: str, port: int, timeout_s: float = 5.0, token: str = "") -> WorkerHello:
    sock = socket.create_connection((host, port), timeout=timeout_s)
    try:
        sock.settimeout(timeout_s)
        codec = FrameCodec(sock)
        codec.send(dump(Hello(token=token)))
        raw = codec.recv()
        if raw.get("type") != "worker_hello":
            raise RemoteError(f"expected worker_hello, got {raw.get('type')}")
        raw.pop("type", None)
        return WorkerHello(**{key: raw[key] for key in WorkerHello.__dataclass_fields__ if key in raw})
    finally:
        sock.close()


def run_remote(
    host: str,
    port: int,
    task: Task,
    inputs: list[Path],
    output_dir: Path,
    *,
    timeout_s: float | None = None,
    token: str = "",
) -> RemoteOutput:
    timeout = timeout_s if timeout_s is not None else task.timeout_s + 30
    started = time.monotonic()
    sock = socket.create_connection((host, port), timeout=min(10.0, timeout))
    try:
        sock.settimeout(timeout)
        codec = FrameCodec(sock)
        codec.send(dump(Hello(token=token)))
        raw = codec.recv()
        if raw.get("type") != "worker_hello":
            raise RemoteError(f"expected worker_hello, got {raw.get('type')}")
        raw.pop("type", None)
        worker = WorkerHello(**{key: raw[key] for key in WorkerHello.__dataclass_fields__ if key in raw})
        codec.send(task.to_dict())
        accept = codec.recv()
        if accept.get("type") != "task_accept" or not accept.get("accepted"):
            raise RemoteError(accept.get("reason", "task refused"))
        for path in inputs:
            codec.send_file(task.task_id, path, role="input")
        codec.send({"type": "inputs_done", "task_id": task.task_id})
        logs: list[str] = []
        result_raw: dict | None = None
        while result_raw is None:
            message = codec.recv()
            kind = message.get("type")
            if kind == "log":
                logs.append(f"{message.get('stream', 'stdout')}: {message.get('text', '')}")
            elif kind == "progress":
                logs.append(f"progress: {message.get('message', '')}")
            elif kind == "result":
                result_raw = message
            elif kind == "error":
                raise RemoteError(message.get("message", "worker error"))
            else:
                raise RemoteError(f"unexpected message {kind}")
        result_raw.pop("type", None)
        result = Result(**{key: result_raw[key] for key in Result.__dataclass_fields__ if key in result_raw})
        output_dir.mkdir(parents=True, exist_ok=True)
        files: list[Path] = []
        for _ in result.outputs:
            _spec, path = codec.recv_file(output_dir)
            files.append(path)
        return RemoteOutput(result, files, worker, time.monotonic() - started, logs)
    except (ProtocolError, OSError, TimeoutError, KeyError) as exc:
        raise RemoteError(str(exc)) from exc
    finally:
        sock.close()
