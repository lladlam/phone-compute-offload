"""Run an argv unchanged on whichever side the scheduler picks.

Inputs are the files named by ``--input``. Outputs are the names given by
``--output``; the remote process is expected to create those paths relative
to its working directory, which is also where the inputs were placed.
"""

from __future__ import annotations

from pathlib import Path

from pco.protocol.messages import Task


class GenericAdapter:
    name = "generic"

    def build(
        self,
        argv: list[str],
        *,
        inputs: list[Path],
        outputs: list[str],
        timeout_s: float,
    ) -> Task:
        if not argv:
            raise SystemExit("missing command after --")
        input_bytes = sum(path.stat().st_size for path in inputs)
        return Task(
            adapter=self.name,
            argv=list(argv),
            cwd_files=[{"name": path.name, "size": path.stat().st_size} for path in inputs],
            output_globs=list(outputs),
            timeout_s=timeout_s,
            required_executor="",
            input_bytes=input_bytes,
            estimated_output_bytes=input_bytes,
        )
