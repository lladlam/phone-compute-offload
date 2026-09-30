"""Read this machine's capability. Works on Android/Termux and on Linux.

Every field the scheduler is allowed to see is collected here. Missing
files become null or empty; the worker still registers. GPU and NPU are
reported only when a userspace node actually exists. Advertising CUDA on
an Android phone would be a lie, so it is never invented.
"""

from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path

from pco.protocol.messages import Capability, WorkerHello


def collect(worker_id: str, name: str) -> WorkerHello:
    mem_total = _meminfo("MemTotal")
    mem_available = _meminfo("MemAvailable")
    hello = WorkerHello(
        worker_id=worker_id,
        name=name,
        architecture=platform.machine() or "",
        os=f"{platform.system()} {platform.release()}".strip(),
        cpu=_cpu_model(),
        cpu_count=os.cpu_count() or 1,
        gpu=_first_existing(
            [
                "/sys/class/kgsl/kgsl-3d0/gpu_model",
                "/sys/class/misc/mali0/device/gpuinfo",
            ]
        ),
        npu=_npu(),
        ram_bytes=(mem_total or 0) * 1024,
        storage_free_bytes=_storage_free(),
        temperature_c=_temperature(),
        battery_percent=_battery_percent(),
        battery_charging=_battery_charging(),
        load_1m=_load(),
        max_jobs=max(1, (os.cpu_count() or 1) // 2),
        executors=[cap.__dict__ for cap in _executors()],
        codecs=_codecs(),
        apis=_apis(),
    )
    if mem_available is not None:
        # stash nothing extra; ram_bytes stays the total. Load is the pressure signal.
        pass
    return hello


def _executors() -> list[Capability]:
    found: list[Capability] = []
    for name in ("clang", "gcc", "rustc", "javac", "kotlinc", "ffmpeg", "python3", "python", "blender", "cmake", "ninja"):
        path = shutil.which(name)
        if not path:
            continue
        found.append(Capability(name=name if name != "python" else "python3", version=_version(path), kind="cpu"))
    # One generic marker so `pco run --remote` can target a shell tool the
    # table above does not know. The matcher treats "generic" as "this worker
    # will try". Adapters that name a real executor still require it.
    found.append(Capability(name="generic", version="1", kind="cpu"))
    return found


def _version(path: str) -> str:
    import subprocess

    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    line = (out.stdout or out.stderr or "").splitlines()
    return line[0][:120] if line else ""


def _codecs() -> list[str]:
    if not shutil.which("ffmpeg"):
        return []
    import subprocess

    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    names = []
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0][:1] in {"V", "A"} and parts[0][1:2] in {".", "F", "S"}:
            # lines look like " V.S. libx264"
            if parts[1].startswith("lib") or parts[1] in {"h264", "aac", "png", "mjpeg"}:
                names.append(parts[1])
    return sorted(set(names))[:32]


def _apis() -> list[str]:
    apis = []
    if Path("/dev/dri").exists() or shutil.which("vulkaninfo"):
        apis.append("vulkan")
    if shutil.which("clinfo") or Path("/dev/mali0").exists():
        apis.append("opencl")
    # NNAPI is an Android Java/NDK API. A Python worker cannot call it
    # directly; report the device node only so the scheduler can see it.
    if Path("/dev/nnapi").exists() or Path("/vendor/lib64/libneuralnetworks.so").exists():
        apis.append("nnapi")
    return apis


def _cpu_model() -> str:
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text(errors="replace").splitlines():
            if "model name" in line or line.lower().startswith("hardware"):
                return line.split(":", 1)[-1].strip()[:160]
    return platform.processor() or ""


def _meminfo(key: str) -> int | None:
    path = Path("/proc/meminfo")
    if not path.exists():
        return None
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith(key + ":"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
    return None


def _temperature() -> float | None:
    zones = sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp")) if Path("/sys/class/thermal").exists() else []
    values = []
    for zone in zones:
        try:
            raw = int(zone.read_text().strip())
        except (OSError, ValueError):
            continue
        # millidegrees, but some nodes are already degrees
        values.append(raw / 1000.0 if raw > 200 else float(raw))
    if not values:
        return None
    return round(max(values), 1)


def _battery_percent() -> float | None:
    for path in (
        Path("/sys/class/power_supply/battery/capacity"),
        Path("/sys/class/power_supply/BAT0/capacity"),
        Path("/sys/class/power_supply/BAT1/capacity"),
    ):
        if path.exists():
            try:
                return float(path.read_text().strip())
            except ValueError:
                return None
    return None


def _battery_charging() -> bool | None:
    for path in (
        Path("/sys/class/power_supply/battery/status"),
        Path("/sys/class/power_supply/BAT0/status"),
    ):
        if path.exists():
            text = path.read_text(errors="replace").strip().lower()
            if "charg" in text or text == "full":
                return True
            if "discharg" in text:
                return False
    return None


def _load() -> float:
    try:
        return os.getloadavg()[0]
    except OSError:
        return 0.0


def _storage_free() -> int:
    try:
        return shutil.disk_usage("/").free
    except OSError:
        return 0


def _npu() -> str:
    for path in (Path("/dev/nvmap"), Path("/dev/neuron"), Path("/dev/npu")):
        if path.exists():
            return path.name
    return ""


def _first_existing(paths: list[str]) -> str:
    for item in paths:
        path = Path(item)
        if path.exists():
            try:
                return path.read_text(errors="replace").strip()[:160]
            except OSError:
                return path.name
    return ""
