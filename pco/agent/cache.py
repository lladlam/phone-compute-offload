"""Content-addressed result cache.

The key is sha256(adapter, argv, required executor, and the bytes of every
input). A hit returns the previous stdout, stderr, exit code, and output
files without running anything. The cache is a directory of JSON sidecars
plus blobs, not a database.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from pco.protocol.messages import Task


@dataclass
class CachedResult:
    exit_code: int
    stdout: str
    stderr: str
    files: list[Path]


class ResultCache:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def lookup(self, key: str) -> CachedResult | None:
        side = self.root / key / "result.json"
        if not side.exists():
            return None
        meta = json.loads(side.read_text(encoding="utf-8"))
        files = [self.root / key / name for name in meta["files"]]
        if not all(path.exists() for path in files):
            return None
        return CachedResult(meta["exit_code"], meta["stdout"], meta["stderr"], files)

    def store(
        self,
        key: str,
        *,
        exit_code: int,
        stdout: str,
        stderr: str,
        files: list[tuple[str, bytes]],
    ) -> None:
        dest = self.root / key
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        names = []
        for name, data in files:
            (dest / name).write_bytes(data)
            names.append(name)
        (dest / "result.json").write_text(
            json.dumps({"exit_code": exit_code, "stdout": stdout, "stderr": stderr, "files": names}),
            encoding="utf-8",
        )


def cache_key(task: Task, inputs: list[tuple[str, bytes]]) -> str:
    h = hashlib.sha256()
    h.update(task.adapter.encode())
    h.update(b"\0")
    h.update(task.required_executor.encode())
    h.update(b"\0")
    for arg in task.argv:
        h.update(arg.encode())
        h.update(b"\0")
    for name, data in sorted(inputs):
        h.update(name.encode())
        h.update(b"\0")
        h.update(hashlib.sha256(data).digest())
    return h.hexdigest()
