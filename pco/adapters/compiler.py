"""One compiler invocation.

Supported drivers: clang, gcc, rustc, javac, kotlinc, and the ``cc`` name.
Gradle / CMake / Ninja are recognized and refused in phase 1: a build system
holds the graph, and offloading the whole driver would hide which commands
are safe. The user runs ``pco run --adapter compiler -- clang -c file.c -o file.o``.

Cross architecture: a phone clang defaults to arm64 (or whatever the phone
is). That object does not link on an x86_64 PC. The adapter requires
``--target`` (clang/gcc) or ``--target`` / ``--print`` is not our job —
rustc needs ``--target``. Without an explicit target, the task is marked
local-only via ``required_executor`` left set but ``allow_cross`` false,
and the engine refuses remote execution. Pass ``--allow-host-cc`` only when
the phone's native objects are what you want (building *for* the phone).
"""

from __future__ import annotations

from pathlib import Path

from pco.protocol.messages import Task

DRIVERS = {
    "clang": "clang",
    "clang++": "clang",
    "gcc": "gcc",
    "g++": "gcc",
    "cc": "clang",
    "c++": "clang",
    "rustc": "rustc",
    "javac": "javac",
    "kotlinc": "kotlinc",
}

# These invoke a graph of processes. Shipping the graph is a later adapter.
BUILD_SYSTEMS = {"gradle", "gradlew", "cmake", "ninja", "make"}


class CompilerAdapter:
    name = "compiler"

    def build(
        self,
        argv: list[str],
        *,
        inputs: list[Path],
        outputs: list[str],
        timeout_s: float,
        allow_host_cc: bool = False,
    ) -> Task:
        if not argv:
            raise SystemExit("missing compiler command")
        driver = Path(argv[0]).name
        if driver in BUILD_SYSTEMS:
            raise SystemExit(
                f"{driver} is a build driver, not a single compile. "
                "Offload one compiler invocation, or wait for the build-system adapter."
            )
        if driver not in DRIVERS:
            raise SystemExit(f"unsupported compiler driver {driver}")
        if not allow_host_cc and not _has_target(argv):
            raise SystemExit(
                "refusing to offload a compiler command without an explicit target "
                "(clang/gcc --target=..., rustc --target ...). "
                "The phone compiler's default triple will not match this PC. "
                "Pass --allow-host-cc if you intend to build for the phone."
            )
        # Source files mentioned in argv travel as inputs when they exist
        # locally and were not already listed.
        extra = list(inputs)
        known = {path.name for path in extra}
        for arg in argv[1:]:
            if arg.startswith("-"):
                continue
            path = Path(arg)
            if path.is_file() and path.name not in known:
                extra.append(path)
                known.add(path.name)
        # Rewrite argv so the worker finds sources by basename in its cwd.
        rewritten = [driver]
        for arg in argv[1:]:
            path = Path(arg)
            if path.is_file() and not arg.startswith("-"):
                rewritten.append(path.name)
            else:
                rewritten.append(arg)
        output_globs = list(outputs) or _infer_outputs(rewritten)
        return Task(
            adapter=self.name,
            argv=rewritten,
            cwd_files=[{"name": path.name, "size": path.stat().st_size} for path in extra],
            output_globs=output_globs,
            timeout_s=timeout_s,
            required_executor=DRIVERS[driver],
            required_kind="cpu",
            input_bytes=sum(path.stat().st_size for path in extra),
            estimated_output_bytes=max(4096, sum(path.stat().st_size for path in extra)),
            estimated_compute_s=max(0.2, 0.05 * max(1, len(extra))),
        )


def _has_target(argv: list[str]) -> bool:
    for arg in argv:
        if arg.startswith("--target"):
            return True
        if arg.startswith("-target"):
            return True
    return False


def _infer_outputs(argv: list[str]) -> list[str]:
    if "-o" in argv:
        index = argv.index("-o")
        if index + 1 < len(argv):
            return [Path(argv[index + 1]).name]
    return []
