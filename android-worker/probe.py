# Android Worker Probe for Phone Compute Offload

import os
import platform
import subprocess
import shutil
from pathlib import Path
from typing import Dict, List

def get_device_info() -> Dict:
    return {
        "manufacturer": subprocess.getoutput("getprop ro.product.manufacturer").strip() or "Unknown",
        "model": subprocess.getoutput("getprop ro.product.model").strip() or "Unknown",
        "android": subprocess.getoutput("getprop ro.build.version.release").strip() or "Unknown",
        "architecture": platform.machine(),
    }

def get_cpu_info() -> Dict:
    return {
        "cores": len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else 1,
        "frequencies": "N/A (use lscpu)",
        "load": os.getloadavg()[0] if hasattr(os, 'getloadavg') else 0.0,
    }

def get_gpu_info() -> Dict:
    return {
        "vendor": "Mali" if "mali" in platform.machine().lower() else "Unknown",
        "renderer": "N/A",
        "api": "vulkan" if shutil.which("vulkaninfo") else "opencl",
    }

def get_npu_info() -> Dict:
    return {
        "available": True if shutil.which("nnapi") or Path("/vendor/lib64/libneuralnetworks.so").exists() else False,
        "supported_api": ["nnapi"] if shutil.which("nnapi") or Path("/vendor/lib64/libneuralnetworks.so").exists() else [],
    }

def get_memory_info() -> Dict:
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1])
                    return {"total": total, "available": total}
    except:
        pass
    return {"total": "N/A", "available": "N/A"}

def get_temperature() -> Dict:
    return {"cpu": "N/A", "gpu": "N/A"}

def get_battery() -> Dict:
    return {"percent": "N/A", "charging": "N/A"}

def get_storage() -> Dict:
    try:
        return {"free_bytes": shutil.disk_usage("/").free}
    except:
        return {"free_bytes": "N/A"}

def get_executors() -> List[str]:
    executors = []
    for bin in ["python3", "python", "clang", "clang++", "gcc", "g++", "ffmpeg", "python", "blender"]:
        if shutil.which(bin):
            executors.append(bin)
    return executors

def get_apis() -> List[str]:
    apis = []
    if shutil.which("vulkaninfo"):
        apis.append("vulkan")
    if shutil.which("clinfo"):
        apis.append("opencl")
    if Path("/vendor/lib64/libneuralnetworks.so").exists():
        apis.append("nnapi")
    return apis

def probe() -> Dict:
    return {
        "device": get_device_info(),
        "cpu": get_cpu_info(),
        "gpu": get_gpu_info(),
        "npu": get_npu_info(),
        "memory": get_memory_info(),
        "temperature": get_temperature(),
        "battery": get_battery(),
        "storage": get_storage(),
        "executors": get_executors(),
        "apis": get_apis(),
    }
