"""Worker discovery.

Workers announce themselves with a UDP broadcast. The agent can also be
pointed at a host with ``--worker host:port``, which is the reliable path
on Wi-Fi networks that drop broadcasts.

The datagram is one JSON object, small enough for a single UDP packet::

    {"type": "announce", "port": 47821, "name": "...", "worker_id": "...",
     "executors": ["ffmpeg", "clang"]}

TCP carries the full ``WorkerHello``. The announce is only a pointer.
"""

from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass

DISCOVERY_PORT = 47820
WORKER_PORT = 47821
MAGIC = "pco-announce"


@dataclass(frozen=True)
class Announcement:
    host: str
    port: int
    name: str
    worker_id: str
    executors: tuple[str, ...]


def listen(timeout_s: float = 2.0) -> list[Announcement]:
    """Collect announcements until ``timeout_s`` of silence after the first, or the full timeout."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    found: dict[tuple[str, int], Announcement] = {}
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", DISCOVERY_PORT))
        sock.settimeout(0.2)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
            except TimeoutError:
                continue
            try:
                payload = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if payload.get("type") != "announce" or payload.get("magic") != MAGIC:
                continue
            port = int(payload.get("port", WORKER_PORT))
            item = Announcement(
                host=addr[0],
                port=port,
                name=str(payload.get("name", "")),
                worker_id=str(payload.get("worker_id", "")),
                executors=tuple(payload.get("executors") or ()),
            )
            found[(item.host, item.port)] = item
    finally:
        sock.close()
    return list(found.values())


def announce_once(name: str, worker_id: str, executors: list[str], port: int = WORKER_PORT) -> None:
    """One broadcast. The worker calls this on a timer."""
    payload = json.dumps(
        {
            "type": "announce",
            "magic": MAGIC,
            "port": port,
            "name": name,
            "worker_id": worker_id,
            "executors": executors,
        }
    ).encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(payload, ("255.255.255.255", DISCOVERY_PORT))
        # Also the subnet-directed broadcast is not known here; loopback
        # lets a worker and an agent on the same machine find each other
        # when the host does not route 255.255.255.255 to itself.
        try:
            sock.sendto(payload, ("127.0.0.1", DISCOVERY_PORT))
        except OSError:
            pass
    finally:
        sock.close()
