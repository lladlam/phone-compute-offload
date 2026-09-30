# Architecture

Phone Compute Offload (PCO) runs a heavy command on an Android phone when that
is faster end to end, and on the PC when it is not. Phase 1 is explicit:
the user names the command. Nothing is hooked.

```
pco run
  → adapter            builds a Task (argv, inputs, outputs, executor)
  → discovery          UDP announce or --worker host:port
  → capability match   executor, cpu/gpu/npu, api
  → scheduler          local_cost vs remote_cost
  → local executor     subprocess on the PC
    or remote executor TCP to the worker
  → result             exit code, stdout, stderr, files, sha256
```

## Processes

| Process | Where | Role |
|---|---|---|
| `pco` | PC | CLI, scheduler, cache, local fallback |
| `pco-worker` | Android (Termux or a Linux userspace) or a Linux box | capability probe, one task at a time, real argv |

There is no separate scheduler daemon. The PC agent is the scheduler. That is
enough for one user and a handful of phones. A shared farm can split it out
later without changing the task messages.

## Worker capability

The worker reads `/proc`, `/sys`, and `PATH`. It reports architecture, CPU
model and count, GPU and NPU only when a sysfs or device node exists, RAM,
free storage, temperature, battery and charging state, load, OS, executors
(`clang`, `ffmpeg`, `python3`, `blender`, …), codecs, and APIs (`vulkan`,
`opencl`, `nnapi` only if present). It never claims CUDA.

## Cost model

```
local  = compute_pc
remote = compute_phone + input_bytes/uplink + output_bytes/downlink + rtt
```

`compute_pc` is `Task.estimated_compute_s` or a measurement from earlier local
runs of the same adapter. `compute_phone` is a measurement of that worker on
that adapter once three samples exist. Before that, the phone is assumed to
be about 0.35× the PC and the estimate is penalized (a single lucky sample
must not capture every later job).

Remote is chosen only when `remote < local`. These vetoes ignore the time
math and keep the job on the PC:

- temperature ≥ 75 °C
- battery < 15% and not charging
- load per CPU ≥ 2.5
- three failures in a row (blacklist)
- missing executor, or a required GPU/NPU/API the worker did not advertise

`--local` and `--remote` override the comparison. `--remote` does not override
the vetoes or the capability check. If the forced worker is gone, the command
runs locally.

## Protocol

TCP, one connection per task. Each frame is `b"PCO1"`, a 4-byte length, and a
JSON object.

```
agent  → hello
worker → worker_hello          (full capability)
agent  → task
worker → task_accept | refuse
agent  → file_begin + chunk*   (one input at a time, sha256, offset)
agent  → inputs_done
worker → progress | log*
worker → result                (exit, stdout, stderr, output list, elapsed)
worker → file_begin + chunk*   (outputs)
```

Chunks are 1 MiB, base64 inside JSON, with `offset` and `eof`. A hash or size
mismatch fails the transfer; the agent falls back to local execution. Closing
the connection cancels the attempt. Resume of a half-sent file is expressed by
`offset` (the receiver rejects a gap) and is retried as a whole file in phase
1; the fields are there so a later sender can continue instead of inventing a
second protocol.

File names are basenames. `../` is a protocol error.

## Adapters

| Adapter | What it offloads | What it refuses |
|---|---|---|
| `generic` | the argv you passed | nothing structural |
| `compiler` | one `clang`/`gcc`/`rustc`/`javac`/`kotlinc` invocation | `gradle`, `cmake`, `ninja`, `make`; a compile with no `--target` unless `--allow-host-cc` |
| `ffmpeg` | one ffmpeg command; `-i` files are shipped; the last positional is the output | a command that is not ffmpeg |
| `blender` | one blender command; `plan(start, end, n)` splits frames | fan-out across phones (the split is implemented, the gather is not) |

Build systems stay local because they spawn a graph. Offload a single compile,
the way a distributed compiler does, not `gradle assemble`.

GPU and NPU are a capability, not a transport trick. A task with
`required_api=cuda` does not run on a phone. Vulkan / OpenCL / NNAPI can be
added as executors later, each with its own data types and operations.

## Cache

`sha256(adapter, executor, argv, input bytes)` → exit code and output files
under `--state` (default `~/.cache/pco`). A hit skips both machines.

## Failure

| Event | Behavior |
|---|---|
| Worker not discovered | local |
| Worker dies before accept | local fallback |
| Hash mismatch or short read | local fallback, failure counted |
| Worker exit ≠ 0 | that result is returned; it is not retried as local, because the command itself failed |
| Timeout | worker kills the process (exit 124) and returns |
| Phone locks the screen | the worker is a userspace process; if the OS freezes it, the agent times out and falls back |

## Out of scope for phase 1

Transparent interception (LD_PRELOAD, ptrace, eBPF, syscall hooks, Vulkan
layers, DLL injection). Multi-phone gather for a frame range. A CUDA-to-Vulkan
translator. Icecream wire compatibility.
