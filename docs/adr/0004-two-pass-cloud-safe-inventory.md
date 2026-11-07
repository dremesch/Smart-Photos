# ADR-0004: Two-pass cloud-safe inventory

- **Status:** Accepted
- **Date:** 2026-11-07
- **Spec reference:** v1.1 §5.2–5.4

## Context

With OneDrive Files On-Demand, opening an online-only placeholder can trigger a download. Reading EXIF or hashing during enumeration would silently download a ~500 GB library.

## Decision

- **Pass 1** enumerates with `os.scandir()` and classifies each entry from `st_file_attributes` (recall-on-data-access, recall-on-open, offline, pinned, unpinned) without opening any file.
- **Pass 2** reads content only for files that are fully local, re-checking attributes immediately before each read.
- Unknown or ambiguous cloud state is treated as unavailable.
- The user makes folders local through OneDrive ("Always keep on this device"). The app never does this itself.

## Consequences

- No unintended hydration, verified by an instrumented test on a real OneDrive machine.
- Files can be dehydrated by Storage Sense mid-job. They become `unavailable` and are retried later.
- Fully testing this needs real Windows plus OneDrive. Hosted CI tests the logic against a mocked attribute layer only.

## Alternatives considered

| Option | Why not |
|---|---|
| Read everything and let OneDrive hydrate | Violates the spec; disk and bandwidth impact |
| Microsoft Graph API | Network dependency (ADR-0001) |
| Cloud Files API (`CfGetPlaceholderInfo`) | Possible refinement; directory attributes are sufficient and cheaper to start with |
