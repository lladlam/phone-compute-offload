"""Run an argv in a private directory and collect outputs.

The worker does not shell out through ``sh -c``. The argument vector is
executed as-is. Relative output paths stay inside the task directory; the
protocol layer already rejected ``..`` in file names.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    elapsed_s: float
    outputs: list[Path] = field(default_factory=list)


def execute(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None,
    timeout_s: float,
    output_globs: list[str],
) -> ExecResult:
    if not argv:
        return ExecResult(127, "", "empty argv\n", 0.0, [])
    import os

    merged = dict(os.environ)
    if env:
        # Do not let a task replace PATH with an empty string unnoticed, but
        # do allow the adapter to set CC, TMPDIR, and similar.
        merged.update(env)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=merged,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
        code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        code = 124
        stdout = exc.stdout or b""
        stderr = (exc.stderr or b"") + b"\nworker timeout\n"
    except FileNotFoundError:
        code = 127
        stdout = b""
        stderr = f"executable not found: {argv[0]}\n".encode()
    elapsed = time.monotonic() - started
    outputs = _collect(cwd, output_globs)
    return ExecResult(
        code,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
        elapsed,
        outputs,
    )


def _collect(cwd: Path, globs: list[str]) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    patterns = globs or []
    for pattern in patterns:
        if pattern.startswith("/") or ".." in Path(pattern).parts:
            continue
        for path in cwd.glob(pattern):
            if path.is_file() and path.resolve() not in seen:
                # Skip the inputs the agent placed at the top level when the
                # glob is broad. Callers pass explicit names (`out.mp4`).
                seen.add(path.resolve())
                found.append(path)
    return found
