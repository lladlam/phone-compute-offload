"""``pco-worker`` entry point."""

from __future__ import annotations

import argparse

from pco.agent.discovery import WORKER_PORT
from pco.worker.server import WorkerServer


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Phone Compute Offload worker")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=WORKER_PORT)
    parser.add_argument("--name", default=None)
    parser.add_argument("--token", default="", help="shared secret the agent must send")
    args = parser.parse_args(argv)
    WorkerServer(host=args.host, port=args.port, name=args.name, token=args.token).serve_forever()


if __name__ == "__main__":
    main()
