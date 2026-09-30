"""Match a task to a worker.

Icecream refuses a compile server whose toolchain environment does not match.
PCO does the same with executor name, device kind (cpu/gpu/npu), and API.
A CUDA task is not sent to a worker that only advertises Vulkan or nothing.
"""

from __future__ import annotations

from pco.protocol.messages import Task, WorkerHello


class MatchError(Exception):
    pass


def worker_supports(worker: WorkerHello, task: Task) -> str | None:
    """Return None when the worker can run the task, otherwise a reason."""
    if worker.no_remote:
        return "worker refuses remote jobs"
    if worker.version != 1:
        return f"protocol {worker.version} is not supported"
    if task.required_executor and task.required_executor not in worker.executor_names():
        return f"missing executor {task.required_executor}"
    if task.required_kind and task.required_kind != "cpu":
        kinds = {str(item.get("kind", "cpu")) for item in worker.executors}
        advertised = set(worker.apis)
        if task.required_kind not in kinds and task.required_kind not in advertised:
            # A cpu-kind executor may still be the fallback for a gpu request
            # only when the task explicitly allows it. Phase 1 does not.
            return f"worker has no {task.required_kind} capability"
    if task.required_api and task.required_api not in worker.apis:
        return f"missing api {task.required_api}"
    return None


def filter_workers(workers: list[WorkerHello], task: Task) -> list[WorkerHello]:
    return [worker for worker in workers if worker_supports(worker, task) is None]
