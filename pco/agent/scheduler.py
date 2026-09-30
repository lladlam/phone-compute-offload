"""Local-versus-remote cost scheduler.

The decision is not "CPU > 80%". A remote run is chosen only when its
predicted end-to-end time is strictly lower than the local prediction,
and the worker is healthy enough to accept the job.

::

    local_cost  = estimated_compute / local_speed
    remote_cost = estimated_compute / worker_speed
                + input_bytes / uplink
                + output_bytes / downlink
                + rtt

``local_speed`` and ``worker_speed`` are relative throughput learned from
completed tasks of the same adapter (bytes of useful work per second).
Until a side has a sample, the scheduler uses the caller-supplied estimate
and a conservative phone/PC ratio. With fewer than ``MIN_SAMPLES`` remote
samples the remote estimate is penalized, the same pessimism Icecream
applies to a node's first jobs: one fast sample must not pin every later
task on that phone.

Temperature, battery, and load can veto a worker even when the time math
says remote. They do not by themselves force offload.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pco.agent.capability import filter_workers
from pco.protocol.messages import Task, WorkerHello

MIN_SAMPLES = 3
# Phone is assumed slower until measured. 0.35 means a phone core finishes
# the same compute in about 1/0.35 ≈ 2.9× the PC time. Replaced by history.
DEFAULT_PHONE_SPEED_RATIO = 0.35
HOT_CELSIUS = 75.0
LOW_BATTERY = 15.0
LOAD_PER_CPU_VETO = 2.5


@dataclass
class Sample:
    compute_s: float
    work_units: float


@dataclass
class History:
    """Persisted per-adapter samples. ``work_units`` is input bytes, or 1."""

    local: dict[str, list[Sample]] = field(default_factory=dict)
    remote: dict[str, dict[str, list[Sample]]] = field(default_factory=dict)

    def add_local(self, adapter: str, compute_s: float, work_units: float) -> None:
        self.local.setdefault(adapter, []).append(Sample(compute_s, work_units))
        self.local[adapter] = self.local[adapter][-50:]

    def add_remote(self, adapter: str, worker_id: str, compute_s: float, work_units: float) -> None:
        per_worker = self.remote.setdefault(adapter, {})
        per_worker.setdefault(worker_id, []).append(Sample(compute_s, work_units))
        per_worker[worker_id] = per_worker[worker_id][-50:]

    def speed(self, samples: list[Sample]) -> float | None:
        useful = [sample for sample in samples if sample.compute_s > 0 and sample.work_units > 0]
        if not useful:
            return None
        return sum(sample.work_units for sample in useful) / sum(sample.compute_s for sample in useful)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "local": {
                key: [sample.__dict__ for sample in samples] for key, samples in self.local.items()
            },
            "remote": {
                adapter: {
                    worker: [sample.__dict__ for sample in samples]
                    for worker, samples in workers.items()
                }
                for adapter, workers in self.remote.items()
            },
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> History:
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        history = cls()
        for key, samples in raw.get("local", {}).items():
            history.local[key] = [Sample(**item) for item in samples]
        for adapter, workers in raw.get("remote", {}).items():
            history.remote[adapter] = {
                worker: [Sample(**item) for item in samples] for worker, samples in workers.items()
            }
        return history


@dataclass(frozen=True)
class Decision:
    where: str  # "local" | "remote"
    worker_id: str | None
    local_cost_s: float
    remote_cost_s: float | None
    reason: str

    @property
    def offload(self) -> bool:
        return self.where == "remote"


@dataclass(frozen=True)
class LinkEstimate:
    """Bytes per second. Measured by a probe, or supplied by the caller."""

    uplink_bps: float = 5_000_000.0
    downlink_bps: float = 5_000_000.0
    rtt_s: float = 0.05


class Scheduler:
    def __init__(self, history: History | None = None, link: LinkEstimate | None = None):
        self.history = history or History()
        self.link = link or LinkEstimate()
        self.blacklist: dict[str, int] = {}

    def note_failure(self, worker_id: str) -> None:
        self.blacklist[worker_id] = self.blacklist.get(worker_id, 0) + 1

    def note_success(self, worker_id: str) -> None:
        self.blacklist.pop(worker_id, None)

    def decide(
        self,
        task: Task,
        workers: list[WorkerHello],
        *,
        force: str | None = None,
        local_compute_s: float | None = None,
    ) -> Decision:
        """Pick local or one worker.

        ``force`` is ``"local"`` or ``"remote"`` from the CLI. Forced remote
        still requires a capable worker; it does not bypass the capability
        check. Forced local ignores phones entirely.
        """
        local_cost = self._local_cost(task, local_compute_s)
        if force == "local":
            return Decision("local", None, local_cost, None, "forced local")

        candidates = [
            worker
            for worker in filter_workers(workers, task)
            if self._veto(worker) is None and self.blacklist.get(worker.worker_id, 0) < 3
        ]
        if force == "remote" and not candidates:
            reasons = self._why_not(task, workers)
            return Decision("local", None, local_cost, None, "forced remote but no capable worker: " + reasons)

        best: tuple[float, WorkerHello] | None = None
        for worker in candidates:
            cost = self._remote_cost(task, worker, local_cost)
            if best is None or cost < best[0]:
                best = (cost, worker)

        if best is None:
            return Decision("local", None, local_cost, None, self._why_not(task, workers) or "no workers")

        remote_cost, worker = best
        if force == "remote":
            return Decision("remote", worker.worker_id, local_cost, remote_cost, "forced remote")
        if remote_cost + 1e-9 < local_cost:
            return Decision(
                "remote",
                worker.worker_id,
                local_cost,
                remote_cost,
                f"remote {remote_cost:.3f}s < local {local_cost:.3f}s",
            )
        return Decision(
            "local",
            None,
            local_cost,
            remote_cost,
            f"local {local_cost:.3f}s <= remote {remote_cost:.3f}s",
        )

    def _local_cost(self, task: Task, override: float | None) -> float:
        if override is not None:
            return max(0.0, override)
        if task.estimated_compute_s is not None:
            base = task.estimated_compute_s
        else:
            base = _guess_compute(task)
        measured = self.history.speed(self.history.local.get(task.adapter, []))
        if measured and task.input_bytes > 0:
            return task.input_bytes / measured
        return base

    def _remote_cost(self, task: Task, worker: WorkerHello, local_cost: float) -> float:
        samples = self.history.remote.get(task.adapter, {}).get(worker.worker_id, [])
        speed = self.history.speed(samples)
        if speed and task.input_bytes > 0:
            compute = task.input_bytes / speed
        else:
            ratio = DEFAULT_PHONE_SPEED_RATIO
            # More idle CPUs help, but not linearly (Icecream's SMT penalty).
            idle = max(0.25, 1.0 - min(worker.load_1m / max(worker.cpu_count, 1), 1.0))
            compute = (local_cost / ratio) * (1.0 - 0.5 * (1.0 - idle))
        if len(samples) < MIN_SAMPLES:
            # Pessimism until the phone has been measured a few times.
            compute *= 1.0 + 0.5 * (MIN_SAMPLES - len(samples))
        uplink = max(self.link.uplink_bps, 1.0)
        downlink = max(self.link.downlink_bps, 1.0)
        transfer = task.input_bytes / uplink + task.estimated_output_bytes / downlink
        rtt = self.link.rtt_s
        if worker.network_latency_ms is not None:
            rtt = max(rtt, worker.network_latency_ms / 1000.0)
        return compute + transfer + rtt

    def _veto(self, worker: WorkerHello) -> str | None:
        if worker.temperature_c is not None and worker.temperature_c >= HOT_CELSIUS:
            return f"temperature {worker.temperature_c}C"
        if (
            worker.battery_percent is not None
            and worker.battery_percent < LOW_BATTERY
            and worker.battery_charging is False
        ):
            return f"battery {worker.battery_percent}%"
        if worker.cpu_count > 0 and worker.load_1m / worker.cpu_count >= LOAD_PER_CPU_VETO:
            return f"load {worker.load_1m}"
        return None

    def _why_not(self, task: Task, workers: list[WorkerHello]) -> str:
        if not workers:
            return "no workers"
        parts = []
        for worker in workers:
            from pco.agent.capability import worker_supports

            reason = worker_supports(worker, task) or self._veto(worker)
            if self.blacklist.get(worker.worker_id, 0) >= 3:
                reason = "blacklisted"
            parts.append(f"{worker.name or worker.worker_id}: {reason or 'eligible'}")
        return "; ".join(parts)


def _guess_compute(task: Task) -> float:
    """Very rough stand-in when the caller gave no estimate.

    Generic commands without a measurement stay local unless forced: the
    guess is 1 second, and the unmeasured phone penalty pushes remote above it
    once transfer is added. Adapters that know better set
    ``estimated_compute_s``.
    """
    if task.input_bytes <= 0:
        return 1.0
    # Assume the PC chews through ~20 MB/s of "generic" work.
    return max(0.05, task.input_bytes / 20_000_000)
