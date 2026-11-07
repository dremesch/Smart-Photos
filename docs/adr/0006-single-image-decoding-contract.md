# ADR-0006: Single image decoding contract

- **Status:** Accepted
- **Date:** 2026-11-07
- **Spec reference:** v1.1 §6

## Context

JPEG orientation comes from the EXIF Orientation tag. In HEIF, orientation comes from container transforms (`irot`/`imir`), which the decoder applies; applying EXIF Orientation again double-rotates. If stages decode independently, previews, hashes, detections, crops, and exports can disagree about which way is up.

## Decision

All pixels are obtained through one contract, `load_oriented(photo, max_long_edge) -> OrientedImage`. It applies orientation exactly once using a versioned, format-specific rule, and returns the oriented full-resolution size and the source color description. No other code may decode source files.

## Consequences

- Coordinates (faces, crops) are always in one frame of reference: the oriented source.
- Changing an orientation rule changes the cache key and invalidates dependent results, including crops.
- Test fixtures must cover JPEG orientations 1–8 and rotated and mirrored HEIC.

## Alternatives considered

| Option | Why not |
|---|---|
| Each stage decodes as needed | Inconsistent orientation and color; duplicated native risk |
| Normalize originals once to rotated copies | Writes large copies; wastes disk |
