"""Blender frame-range plan.

LambDa splits ``[frame_start, frame_end]`` across every connected worker and
runs ``blender -b file -s N -e M -a``. This adapter does the split in the
plan, but phase 1 submits **one** range to the scheduler (the whole range,
or the range the user passed). Fanning one job out to many phones needs a
multi-worker gather step that is not in the phase-1 engine; ``plan()`` is
here so that step has a tested split function instead of a guess later.

The command that actually runs is still an explicit blender argv, executed
by the generic remote executor. The worker must advertise ``blender``.
"""

from __future__ import annotations

from pathlib import Path

from pco.protocol.messages import Task


class BlenderAdapter:
    name = "blender"

    def build(
        self,
        argv: list[str],
        *,
        inputs: list[Path],
        outputs: list[str],
        timeout_s: float,
    ) -> Task:
        if not argv or Path(argv[0]).name != "blender":
            raise SystemExit("blender adapter expects a blender command")
        files = list(inputs)
        known = {path.name for path in files}
        for arg in argv[1:]:
            path = Path(arg)
            if path.suffix == ".blend" and path.is_file() and path.name not in known:
                files.append(path)
                known.add(path.name)
        rewritten = []
        for arg in argv:
            path = Path(arg)
            if path.suffix == ".blend" and path.is_file():
                rewritten.append(path.name)
            else:
                rewritten.append(Path(arg).name if arg == argv[0] else arg)
        return Task(
            adapter=self.name,
            argv=rewritten,
            cwd_files=[{"name": path.name, "size": path.stat().st_size} for path in files],
            output_globs=list(outputs) or ["*.png", "*.jpg", "*.exr", "*.mp4"],
            timeout_s=timeout_s,
            required_executor="blender",
            required_kind="cpu",
            input_bytes=sum(path.stat().st_size for path in files),
            estimated_output_bytes=sum(path.stat().st_size for path in files),
            estimated_compute_s=30.0,
        )

    @staticmethod
    def plan(frame_start: int, frame_end: int, workers: int) -> list[tuple[int, int]]:
        """Split an inclusive frame range into ``workers`` contiguous spans.

        The last span absorbs the remainder, matching LambDa. Unlike LambDa,
        extra workers (more phones than frames) get no empty span: callers
        receive fewer spans than workers.
        """
        if workers < 1:
            raise ValueError("workers must be >= 1")
        if frame_end < frame_start:
            raise ValueError("empty frame range")
        count = frame_end - frame_start + 1
        workers = min(workers, count)
        base, extra = divmod(count, workers)
        spans = []
        cursor = frame_start
        for index in range(workers):
            length = base + (1 if index < extra else 0)
            spans.append((cursor, cursor + length - 1))
            cursor += length
        return spans
