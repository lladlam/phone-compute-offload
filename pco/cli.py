"""``pco`` command line.

Examples::

    pco workers
    pco workers --worker 192.168.1.20:47821
    pco run --worker 127.0.0.1:47821 --remote --output out.txt -- python3 -c 'open("out.txt","w").write("ok")'
    pco run --adapter ffmpeg --worker 192.168.1.20:47821 -- ffmpeg -i in.mp4 -c:v libx264 out.mp4
    pco run --adapter compiler --allow-host-cc -- clang -c hello.c -o hello.o
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pco import __version__
from pco.adapters import ADAPTERS
from pco.agent.discovery import WORKER_PORT
from pco.agent.engine import Engine


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pco", description="Offload a command to an Android worker, or run it locally.")
    parser.add_argument("--version", action="version", version=f"pco {__version__}")
    parser.add_argument("--state", default=str(Path.home() / ".cache" / "pco"), help="history and cache directory")
    sub = parser.add_subparsers(dest="cmd", required=True)

    workers = sub.add_parser("workers", help="discover workers and print their capabilities")
    _add_worker_args(workers)

    run = sub.add_parser("run", help="run a command, locally or on a worker")
    _add_worker_args(run)
    run.add_argument("--adapter", default="generic", choices=sorted(ADAPTERS))
    run.add_argument("--local", action="store_true", help="force local execution")
    run.add_argument("--remote", action="store_true", help="force remote when a capable worker exists")
    run.add_argument("--output", action="append", default=[], help="output file name the worker must produce (repeatable)")
    run.add_argument("--input", action="append", default=[], help="extra input file to ship (repeatable)")
    run.add_argument("--timeout", type=float, default=3600.0)
    run.add_argument("--out-dir", default="")
    run.add_argument("--allow-host-cc", action="store_true", help="compiler adapter: allow the phone's native target")
    run.add_argument("--no-cache", action="store_true")
    run.add_argument("argv", nargs=argparse.REMAINDER, help="command after --")

    args = parser.parse_args(argv)
    engine = Engine(Path(args.state))
    endpoints = _endpoints(args.worker)
    if args.cmd == "workers":
        found = engine.discover(endpoints, args.discover_timeout, token=args.token)
        if not found:
            print("no workers")
            sys.exit(1)
        for host, port, hello in found:
            names = ", ".join(sorted(hello.executor_names()))
            print(
                f"{hello.worker_id} {hello.name} {host}:{port} arch={hello.architecture} "
                f"cpu={hello.cpu_count} load={hello.load_1m:.2f} temp={hello.temperature_c} "
                f"battery={hello.battery_percent} executors=[{names}] apis={hello.apis}"
            )
        return

    if args.local and args.remote:
        raise SystemExit("pass only one of --local and --remote")
    command = list(args.argv)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SystemExit("usage: pco run [options] -- <command>")
    inputs = [Path(item) for item in args.input]
    for path in inputs:
        if not path.is_file():
            raise SystemExit(f"input not found: {path}")
    adapter = ADAPTERS[args.adapter]
    kwargs = dict(inputs=inputs, outputs=args.output, timeout_s=args.timeout)
    if args.adapter == "compiler":
        task = adapter.build(command, allow_host_cc=args.allow_host_cc, **kwargs)
    else:
        task = adapter.build(command, **kwargs)
    # generic adapter does not set required_executor; compiler/ffmpeg/blender do.
    out_dir = Path(args.out_dir) if args.out_dir else Path.cwd() / "pco-out"
    force = "local" if args.local else "remote" if args.remote else None
    found = [] if force == "local" else engine.discover(endpoints, args.discover_timeout, token=args.token)
    # Inputs the adapter discovered from argv (sources, -i files, blends).
    shipped = []
    seen = set()
    for spec in task.cwd_files:
        # Prefer a path the user passed; else a file by that name in cwd or next to an input.
        path = _resolve_input(spec["name"], inputs, command)
        if path is None:
            raise SystemExit(f"cannot find input {spec['name']}")
        if path.resolve() not in seen:
            shipped.append(path)
            seen.add(path.resolve())
    report = engine.run(task, shipped, workers=found, output_dir=out_dir, force=force, use_cache=not args.no_cache)
    print(
        f"where={report.where} worker={report.worker_id or '-'} exit={report.exit_code} "
        f"elapsed={report.elapsed_s:.3f}s cached={report.cached}"
    )
    print(f"decision: {report.decision.reason}")
    if report.stdout:
        sys.stdout.write(report.stdout if report.stdout.endswith("\n") else report.stdout + "\n")
    if report.stderr:
        sys.stderr.write(report.stderr if report.stderr.endswith("\n") else report.stderr + "\n")
    if report.files:
        print("outputs:")
        for path in report.files:
            print(f"  {path}")
    sys.exit(report.exit_code)


def _add_worker_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--worker", action="append", default=[], help="host:port of a worker (repeatable)")
    parser.add_argument("--discover-timeout", type=float, default=1.5)
    parser.add_argument("--token", default="")


def _endpoints(values: list[str]) -> list[tuple[str, int]]:
    out = []
    for value in values:
        if ":" not in value:
            out.append((value, WORKER_PORT))
            continue
        host, _, port = value.rpartition(":")
        out.append((host, int(port)))
    return out


def _resolve_input(name: str, inputs: list[Path], command: list[str]) -> Path | None:
    for path in inputs:
        if path.name == name:
            return path
    for arg in command:
        path = Path(arg)
        if path.name == name and path.is_file():
            return path
    direct = Path(name)
    if direct.is_file():
        return direct
    return None


if __name__ == "__main__":
    main()
