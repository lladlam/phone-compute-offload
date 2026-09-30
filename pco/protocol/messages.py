"""Task, worker, and result records.

Messages are JSON objects with a ``type`` field. Binary payloads are not
inlined: a file is announced, then sent as sequential ``chunk`` frames whose
sha256 is checked by the receiver. That is the opposite of "send the whole
file and hope".
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

PROTOCOL_VERSION = 1

# Chunk size keeps a single frame comfortably under typical socket buffers
# while still being large enough that a multi-gigabyte encode is not a
# million syscalls. 1 MiB.
CHUNK_SIZE = 1024 * 1024


def new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class Hello:
    """First frame from the PC agent after the TCP connection is up."""

    type: str = "hello"
    version: int = PROTOCOL_VERSION
    agent: str = "pco-agent"
    token: str = ""


@dataclass
class Capability:
    """One thing a worker can execute, with an optional version string."""

    name: str
    version: str = ""
    kind: str = "cpu"  # cpu | gpu | npu


@dataclass
class WorkerHello:
    """Registration. The scheduler refuses workers it does not understand."""

    type: str = "worker_hello"
    version: int = PROTOCOL_VERSION
    worker_id: str = ""
    name: str = ""
    architecture: str = ""
    os: str = ""
    cpu: str = ""
    cpu_count: int = 1
    gpu: str = ""
    npu: str = ""
    ram_bytes: int = 0
    storage_free_bytes: int = 0
    temperature_c: float | None = None
    battery_percent: float | None = None
    battery_charging: bool | None = None
    load_1m: float = 0.0
    max_jobs: int = 1
    network_latency_ms: float | None = None
    executors: list[dict[str, Any]] = field(default_factory=list)
    codecs: list[str] = field(default_factory=list)
    apis: list[str] = field(default_factory=list)
    no_remote: bool = False

    def executor_names(self) -> set[str]:
        return {str(item.get("name", "")) for item in self.executors}


@dataclass
class FileSpec:
    """A file that will follow as chunks, or a file the worker must return."""

    name: str
    size: int
    sha256: str
    role: str = "input"  # input | output
    compressed: str = "none"  # none | zstd


@dataclass
class Task:
    """One explicit unit of work. Adapters build this; the worker runs it."""

    type: str = "task"
    task_id: str = field(default_factory=new_id)
    adapter: str = "generic"
    argv: list[str] = field(default_factory=list)
    cwd_files: list[dict[str, Any]] = field(default_factory=list)
    output_globs: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    timeout_s: float = 3600.0
    required_executor: str = ""
    required_kind: str = "cpu"  # cpu | gpu | npu
    required_api: str = ""
    estimated_compute_s: float | None = None
    input_bytes: int = 0
    estimated_output_bytes: int = 0
    cache_key: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskAccept:
    type: str = "task_accept"
    task_id: str = ""
    accepted: bool = False
    reason: str = ""


@dataclass
class Chunk:
    """A slice of a named file. ``offset`` makes resume possible."""

    type: str = "chunk"
    task_id: str = ""
    name: str = ""
    offset: int = 0
    total: int = 0
    sha256: str = ""
    eof: bool = False
    # ``data_b64`` is set by the codec helper; raw bytes never go through JSON
    # without encoding. Callers use FrameCodec.send_file / recv_file.
    data_b64: str = ""


@dataclass
class Progress:
    type: str = "progress"
    task_id: str = ""
    fraction: float = 0.0
    message: str = ""


@dataclass
class LogLine:
    type: str = "log"
    task_id: str = ""
    stream: str = "stdout"  # stdout | stderr
    text: str = ""


@dataclass
class Cancel:
    type: str = "cancel"
    task_id: str = ""
    reason: str = ""


@dataclass
class Result:
    type: str = "result"
    task_id: str = ""
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    outputs: list[dict[str, Any]] = field(default_factory=list)
    elapsed_s: float = 0.0
    error: str = ""
    worker_id: str = ""


def dump(message: Any) -> dict[str, Any]:
    if hasattr(message, "__dataclass_fields__"):
        return asdict(message)
    if isinstance(message, dict):
        return message
    raise TypeError(f"not a message: {type(message)!r}")
