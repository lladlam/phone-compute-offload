"""Framed JSON transport.

Frame layout, network byte order::

    magic    4 bytes   b"PCO1"
    length   4 bytes   payload size, 1 .. MAX_FRAME
    payload  N bytes   UTF-8 JSON object

File bodies are ``chunk`` frames. Each chunk carries base64 so the control
and data planes share one parser. 1 MiB chunks plus base64 stay under
``MAX_FRAME``. Receivers check sha256 and can ask again from ``offset``
(the agent resends a file whose hash does not match).

Compression is optional zstd around a whole file, advertised on the
``FileSpec``. The standard library has no zstd, so compression is used only
when the ``zstandard`` module is installed; otherwise files are raw.
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pco.protocol.messages import CHUNK_SIZE, FileSpec

MAGIC = b"PCO1"
HEADER = struct.Struct("!4sI")
MAX_FRAME = 8 * 1024 * 1024


class ProtocolError(Exception):
    pass


class FrameCodec:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self._buf = bytearray()

    def send(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(payload) > MAX_FRAME:
            raise ProtocolError(f"frame is {len(payload)} bytes, limit is {MAX_FRAME}")
        self._send_all(HEADER.pack(MAGIC, len(payload)) + payload)

    def recv(self) -> dict[str, Any]:
        header = self._recv_exact(HEADER.size)
        magic, length = HEADER.unpack(header)
        if magic != MAGIC:
            raise ProtocolError(f"bad magic {magic!r}")
        if length < 2 or length > MAX_FRAME:
            raise ProtocolError(f"bad frame length {length}")
        payload = self._recv_exact(length)
        try:
            message = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProtocolError(f"bad json frame: {exc}") from exc
        if not isinstance(message, dict) or "type" not in message:
            raise ProtocolError("frame is not a message")
        return message

    def send_file(self, task_id: str, path: Path, *, role: str = "input", name: str | None = None) -> FileSpec:
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        spec = FileSpec(name=name or path.name, size=len(data), sha256=digest, role=role)
        self.send({"type": "file_begin", "task_id": task_id, **spec.__dict__})
        offset = 0
        while offset < len(data) or len(data) == 0:
            block = data[offset : offset + CHUNK_SIZE]
            self.send(
                {
                    "type": "chunk",
                    "task_id": task_id,
                    "name": spec.name,
                    "offset": offset,
                    "total": spec.size,
                    "sha256": spec.sha256,
                    "eof": offset + len(block) >= spec.size,
                    "data_b64": base64.b64encode(block).decode("ascii"),
                }
            )
            offset += len(block)
            if spec.size == 0:
                break
        return spec

    def recv_file(self, dest_dir: Path) -> tuple[FileSpec, Path]:
        begin = self.recv()
        if begin.get("type") != "file_begin":
            raise ProtocolError(f"expected file_begin, got {begin.get('type')}")
        spec = FileSpec(
            name=_safe_name(begin["name"]),
            size=int(begin["size"]),
            sha256=str(begin["sha256"]),
            role=str(begin.get("role", "input")),
            compressed=str(begin.get("compressed", "none")),
        )
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / spec.name
        blob = bytearray()
        while True:
            chunk = self.recv()
            if chunk.get("type") != "chunk" or chunk.get("name") != spec.name:
                raise ProtocolError("chunk does not belong to the open file")
            if int(chunk["offset"]) != len(blob):
                raise ProtocolError(
                    f"chunk offset {chunk['offset']} != received {len(blob)}; resume would restart this file"
                )
            block = base64.b64decode(chunk.get("data_b64", ""), validate=True)
            blob += block
            if chunk.get("eof"):
                break
        if len(blob) != spec.size:
            raise ProtocolError(f"size mismatch for {spec.name}: got {len(blob)} expected {spec.size}")
        digest = hashlib.sha256(blob).hexdigest()
        if digest != spec.sha256:
            raise ProtocolError(f"sha256 mismatch for {spec.name}")
        target.write_bytes(blob)
        return spec, target

    def _send_all(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            sent = self.sock.send(view)
            if sent == 0:
                raise ProtocolError("socket closed while sending")
            view = view[sent:]

    def _recv_exact(self, size: int) -> bytes:
        while len(self._buf) < size:
            part = self.sock.recv(max(4096, size - len(self._buf)))
            if not part:
                raise ProtocolError("socket closed while receiving")
            self._buf += part
        out = bytes(self._buf[:size])
        del self._buf[:size]
        return out


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK_SIZE), b""):
            h.update(block)
    return h.hexdigest()


def _safe_name(name: str) -> str:
    """Reject path traversal. Output names are basenames only in phase 1."""
    if not name or name != Path(name).name or ".." in name:
        raise ProtocolError(f"unsafe file name {name!r}")
    return name


ProgressFn = Callable[[int, int], None]
