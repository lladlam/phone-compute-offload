# Phone Compute Offload

Explicit computation offloading from a PC to Android phones.

A PC agent decides, per command, whether the phone will finish sooner than the
PC once upload, download, phone load, temperature, and battery are counted.
If it will, the phone runs the command and returns stdout, stderr, the exit
code, and the output files. If it will not, the command runs locally.

```
PC command
  → adapter
  → scheduler  (local cost vs remote cost)
  → Android worker
  → result
```

This is not a bundle of [RAPID](https://github.com/RapidProjectH2020/rapid-android-DemoApp),
[LambDa](https://github.com/luckiday/LambDa),
[Libdroid](https://github.com/CGCL-codes/Libdroid), or
[Icecream](https://github.com/icecc/icecream). Those projects were studied for
their scheduling and offload ideas. Their source is not in this tree. See
`NOTICE`.

Phase 1 does not hook other programs. You name the command.

## Requirements

- PC: Python 3.11+, Linux (developed on Arch). No third-party packages.
- Phone: Python 3.11+ in Termux, or a Linux userspace that can run the worker.
  The tool you offload (`ffmpeg`, `clang`, `python3`, …) must be installed
  **on the phone**. PCO does not ship a compiler or an encoder.

## Install

```bash
git clone https://github.com/lladlam/phone-compute-offload.git
cd phone-compute-offload
export PYTHONPATH="$PWD"
```

## Run a worker

On the phone (Termux) or on a second machine:

```bash
python3 -m pco.worker.main --name pixel
```

The worker listens on TCP `47821` and broadcasts UDP announcements on `47820`.
It prints the executables it found on `PATH`.

On a network that drops broadcast, skip discovery and pass the address.

## Discover

```bash
python3 -m pco.cli workers --worker 192.168.1.20:47821
```

You should see architecture, CPU count, load, temperature, battery, and the
executor list.

## Offload a command

```bash
python3 -m pco.cli run \
  --worker 192.168.1.20:47821 \
  --remote \
  --input examples/hello/work.py \
  --input input.bin \
  --output output.txt \
  -- python3 work.py
```

`--remote` forces the phone when it advertises a matching executor.
Without `--remote` or `--local`, the scheduler offloads only when the
predicted remote time is strictly smaller.

### ffmpeg

```bash
python3 -m pco.cli run --adapter ffmpeg \
  --worker 192.168.1.20:47821 \
  -- ffmpeg -y -i clip.mp4 -c:v libx264 out.mp4
```

### one compiler invocation

A phone compiler does not target your PC unless you say so.

```bash
python3 -m pco.cli run --adapter compiler \
  --worker 192.168.1.20:47821 \
  -- clang --target=aarch64-linux-android -c examples/compile/add.c -o add.o
```

`--allow-host-cc` means "the phone's native object is what I want".
`gradle`, `cmake`, `ninja`, and `make` are refused: offload a single
compile, not the build driver.

### blender

```bash
python3 -m pco.cli run --adapter blender \
  --worker 192.168.1.20:47821 \
  -- blender -b scene.blend -o //frame_ -s 1 -e 1 -a
```

Frame ranges can be split with `BlenderAdapter.plan`. Sending each span to a
different phone and gathering the frames is not wired up yet.

## Tests

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -v
```

The remote tests start a loopback worker, run a Python task, and when
`ffmpeg` and `clang` exist, a real encode and a real compile. Output is
compared with a local run.

## Layout

```
pco/protocol     frames, task, worker hello, result
pco/agent        discovery, scheduler, cache, local and remote execution
pco/worker       capability probe, executor, TCP server
pco/adapters     generic, compiler, ffmpeg, blender
```

Design details, the cost formula, and the failure table are in
[ARCHITECTURE.md](ARCHITECTURE.md).

## What this does not do yet

- Transparent offload of unmodified programs (no LD_PRELOAD, ptrace, eBPF, or DLL injection).
- CUDA programs on an Android GPU. The scheduler rejects a task that requires an API the phone did not report.
- Multi-phone Blender gather, distributed `gradle`/`cmake`/`ninja`, and chunk-level resume after a dropped connection (the frame fields exist; the retry resends the file).
- A graphical Android app. The worker is a Python process you start in Termux.

## License

MIT. See [LICENSE](LICENSE).
