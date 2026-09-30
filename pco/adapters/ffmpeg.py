"""FFmpeg transcode / encode.

The adapter does not parse every ffmpeg flag. It:

* requires the executable name to be ffmpeg
* ships every local file referenced by ``-i``
* treats the last non-flag argument as the output file name
* asks the worker for the ``ffmpeg`` executor

Video encode is the case the cost model is built for: seconds of compute
per megabyte, so a slower phone can still lose once upload is counted,
and a long encode can still win.
"""

from __future__ import annotations

from pathlib import Path

from pco.protocol.messages import Task


class FFmpegAdapter:
    name = "ffmpeg"

    def build(
        self,
        argv: list[str],
        *,
        inputs: list[Path],
        outputs: list[str],
        timeout_s: float,
    ) -> Task:
        if not argv or Path(argv[0]).name != "ffmpeg":
            raise SystemExit("ffmpeg adapter expects an ffmpeg command")
        files = list(inputs)
        known = {path.resolve() for path in files if path.exists()}
        args = argv[1:]
        index = 0
        while index < len(args):
            if args[index] == "-i" and index + 1 < len(args):
                path = Path(args[index + 1])
                if path.is_file() and path.resolve() not in known:
                    files.append(path)
                    known.add(path.resolve())
                index += 2
                continue
            index += 1
        output_name = outputs[0] if outputs else _last_output(args)
        if not output_name:
            raise SystemExit("could not tell which argument is the ffmpeg output; pass --output name")
        rewritten = ["ffmpeg"]
        index = 0
        while index < len(args):
            if args[index] == "-i" and index + 1 < len(args):
                rewritten.extend(["-i", Path(args[index + 1]).name])
                index += 2
                continue
            rewritten.append(args[index])
            index += 1
        if rewritten[-1] != output_name:
            # The last positional is the output; make it a basename.
            rewritten[-1] = Path(rewritten[-1]).name
        input_bytes = sum(path.stat().st_size for path in files)
        return Task(
            adapter=self.name,
            argv=rewritten,
            cwd_files=[{"name": path.name, "size": path.stat().st_size} for path in files],
            output_globs=[Path(output_name).name],
            timeout_s=timeout_s,
            required_executor="ffmpeg",
            required_kind="cpu",
            input_bytes=input_bytes,
            # Encodes often shrink. Guess half unless the user overrides later.
            estimated_output_bytes=max(1, input_bytes // 2),
            estimated_compute_s=max(1.0, input_bytes / 2_000_000),
        )


def _last_output(args: list[str]) -> str:
    skip_next = False
    positional: list[str] = []
    value_flags = {"-i", "-b:v", "-b:a", "-s", "-r", "-c:v", "-c:a", "-vf", "-af", "-t", "-ss", "-q:v"}
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in value_flags:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        positional.append(arg)
    return Path(positional[-1]).name if positional else ""
