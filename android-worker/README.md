# Android Worker for Phone Compute Offload

This is the official Android Worker for the Phone Compute Offload project.

## Installation

1. Install Termux on Android:
   ```bash
   pkg install python
   pip install termux-api
   ```

2. Clone this repo:
   ```bash
   git clone https://github.com/lladlam/phone-compute-offload.git
   cd phone-compute-offload
   ```

3. Start the worker:
   ```bash
   python3 -m android_worker.termux_worker
   ```

The worker will:
- Announce itself via UDP
- Report full capability (CPU, GPU, NPU, temperature, battery, executors)
- Execute tasks from the PC
- Return results with SHA256 verification
