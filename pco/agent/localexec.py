"""Run a task on this machine. Used as the fallback and the correctness oracle."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class LocalResult:
    exit_code: int
    stdout: str
    stderr: str
    elapsed_s: float
    output_dir: Path


def run_local(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout_s: float = 3600.0,
    output_dir: Path | None = None,
) -> LocalResult:
    if not argv:
        raise ValueError("empty argv")
    cwd.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=None if env is None else {**dict(**_base_env()), **env},
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
        code = completed.returncode
        stdout = completed.stdout.decode("utf-8", errors="replace")
        stderr = completed.stderr.decode("utf-8", errors="replace")
    except subprocess.TimeoutExpired as exc:
        code = 124
        stdout = (exc.stdout or b"").decode("utf-8", errors="replace")
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace") + "\nlocal timeout\n"
    elapsed = time.monotonic() - started
    return LocalResult(code, stdout, stderr, elapsed, output_dir or cwd)


def _base_env() -> dict[str, str]:
    import os

    return dict(os.environ)
