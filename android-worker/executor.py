# Executor for Phone Compute Offload

import json
import subprocess
import time
import hashlib
from pathlib import Path
from typing import Dict, List

def execute_task(task: Dict) -> Dict:
    # Create work directory
    work_dir = Path("/data/local/tmp/pco-work")
    work_dir.mkdir(parents=True, exist_ok=True)
    
    # Write input files
    for spec in task.get("inputs", []):
        (work_dir / spec["name"]).write_bytes(spec["data"])
    
    # Execute command
    argv = task["argv"]
    timeout = task.get("timeout", 3600)
    
    try:
        result = subprocess.run(
            argv,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        
        exit_code = result.returncode
        stdout = result.stdout
        stderr = result.stderr
        
        # Collect output files
        outputs = []
        for glob in task.get("output_globs", []):
            for path in work_dir.glob(glob):
                if path.is_file():
                    outputs.append({
                        "name": path.name,
                        "data": path.read_bytes(),
                    })
        
        return {
            "type": "result",
            "task_id": task["task_id"],
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "outputs": outputs,
            "elapsed_s": time.time() - task.get("start_time", time.time()),
        }
    except subprocess.TimeoutExpired:
        return {
            "type": "result",
            "task_id": task["task_id"],
            "exit_code": 124,
            "error": "timeout",
        }
    except FileNotFoundError:
        return {
            "type": "result",
            "task_id": task["task_id"],
            "exit_code": 127,
            "error": "executable not found",
        }
    except Exception as e:
        return {
            "type": "result",
            "task_id": task["task_id"],
            "exit_code": 1,
            "error": str(e),
        }

def verify_file(path: Path, expected_sha256: str) -> bool:
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(4096), b""):
            sha256.update(block)
    return sha256.hexdigest() == expected_sha256

def hash_file(path: Path) -> str:
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(4096), b""):
            sha256.update(block)
    return sha256.hexdigest()

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
