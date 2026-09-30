# Contributing

## Scope of a change

Phase 1 is explicit offload: `pco run`. Do not add LD_PRELOAD, ptrace, eBPF,
DLL injection, or a Vulkan layer in a drive-by patch. Hooks are a later,
optional component and must not be on by default.

Do not import source from RAPID, Libdroid, or Icecream. Icecream is GPL-2.0-only;
copying it would relicense this MIT project. RAPID and Libdroid have no
repository license. LambDa is MIT; a substantial copy must keep its copyright
notice in `NOTICE`. Prefer reimplementing a small idea over pasting a file.

## What a worker may claim

`WorkerHello` is a statement the scheduler trusts. Do not advertise CUDA,
OpenCL, Vulkan, or NNAPI unless this process can actually run that API.
A CPU fallback belongs in the executor list as `kind: cpu`, not as a fake GPU.

## Tests

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -v
```

A change to the scheduler needs a case where remote is correctly rejected
(tiny task, hot phone, low battery, missing executor) and a case where it is
accepted. A change to the protocol needs a hash-mismatch test. A new adapter
needs a build() test that does not require the phone.

## Commits

One concern per commit. The tree should import (`python3 -c "import pco"`)
after every commit.
