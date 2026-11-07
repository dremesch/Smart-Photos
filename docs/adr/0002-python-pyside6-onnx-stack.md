# ADR-0002: Python, PySide6, ONNX Runtime, SQLite stack

- **Status:** Accepted
- **Date:** 2026-11-07
- **Spec reference:** v1.1 §4.2

## Context

The product needs mature HEIC decoding, image processing, local ML inference, and a responsive desktop UI on Windows. It is built by a small team and must iterate quickly during the pilot.

## Decision

- Python 3.12 for the engine and UI.
- PySide6 (Qt Widgets) for the UI, without QtWebEngine.
- ONNX Runtime for all inference, with the CPU execution provider as the baseline.
- SQLite in WAL mode for application state.
- Accelerators (Windows ML first, then DirectML) are evaluated only after the CPU baseline.

## Consequences

- One inference runtime for every model, so models can be swapped through manifests.
- The packaged app is large (Qt, OpenCV, ONNX Runtime) and needs careful PyInstaller configuration.
- The GIL is mitigated because native libraries release it. Decoding runs in subprocesses (ADR-0003).
- A future native client stays possible, because core logic is pure and accessed through services.

## Alternatives considered

| Option | Why not (now) |
|---|---|
| C#/.NET + WinUI + Windows ML | Strong Windows integration, but a weaker imaging and HEIC ecosystem and slower experimentation |
| Electron + Node | Embedded browser surface; heavier; weaker imaging libraries |
| PyTorch at runtime | Large footprint; ONNX export gives a smaller, more portable runtime |
