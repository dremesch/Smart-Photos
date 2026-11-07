# ADR-0003: Process model and decoder isolation

- **Status:** Accepted. Revisit after the M2 benchmark.
- **Date:** 2026-11-07
- **Spec reference:** v1.1 §6.4, §15.4

## Context

Native image decoders (libheif, libde265, libjpeg, OpenCV I/O) parse untrusted bytes. A segfault cannot be caught in Python and would end the whole batch. Splitting everything into many processes adds IPC cost and packaging complexity.

## Decision

- One application process hosts the UI, services, job manager, inference, and the single database writer.
- Native decoding runs in a bounded pool of restartable worker subprocesses with timeouts, pixel caps, and memory limits.
- ExifTool runs as one long-lived `-stay_open` subprocess.
- A file that crashes a worker is recorded and skipped on retry until the decoder version changes.

## Consequences

- The highest-risk code (untrusted parsing) is isolated. The rest stays simple and in-process.
- Decoded pixels cross a process boundary. The IPC mechanism (shared memory or serialization) is a low-level design topic for M0/M1.
- `multiprocessing.freeze_support()` is required in the packaged build.
- If ONNX Runtime instability or memory pressure appears, inference can move into its own process without changing core logic.

## Alternatives considered

| Option | Why not |
|---|---|
| Everything in-process | A single malformed file can terminate the app |
| Separate engine process for the whole pipeline | More IPC and lifecycle complexity, without clear benefit until measured |
| Windows WIC/HEIF extension for decoding | Depends on a Store extension; less control over orientation and color |
