# 03 — Key Interaction Sequences

The core runtime interactions, at architectural level. Message names describe intent, not final function signatures.

| # | Sequence | Demonstrates |
|---|---|---|
| 1 | [Create event and safe inventory](#1-create-event-and-safe-inventory) | No hydration; partial import |
| 2 | [Analysis job with decoder isolation](#2-analysis-job-with-decoder-isolation) | Crash containment; per-item ledger; single writer |
| 3 | [Resume after interruption](#3-resume-after-interruption) | Resumability; cache reuse |
| 4 | [Review decision and undo](#4-review-decision-and-undo) | Append-only decisions; durability |
| 5 | [Crop proposal and manual override](#5-crop-proposal-and-manual-override) | Geometry invariant; protected regions |
| 6 | [Export and verification](#6-export-and-verification) | Atomic writes; verification; metadata stripping |
| 7 | [Re-analysis after a model upgrade](#7-re-analysis-after-a-model-upgrade) | Versioned cache; decisions preserved |

---

## 1. Create event and safe inventory

```mermaid
sequenceDiagram
    autonumber
    actor U as Curator
    participant UI as UI
    participant S as EventService
    participant J as JobManager
    participant CF as CloudFiles adapter
    participant G as PathGuard
    participant W as DB writer

    U->>UI: Create "Japan 2024", choose folders
    UI->>S: create_event(name, roots)
    S->>G: register source roots (write-protected)
    S->>W: insert Event
    S->>J: start InventoryJob(event)
    J-->>UI: progress events

    Note over J,CF: Pass 1: directory attributes only. No file is opened.
    loop each directory entry
        J->>CF: classify(DirEntry attributes)
        CF-->>J: local / online_only / unpinned / excluded_format
    end
    J->>W: upsert Photo rows with cloud state

    J-->>UI: summary: local, online-only, unpinned, excluded
    alt some photos are online-only or unpinned
        UI->>U: "N photos are not on this device.<br/>Choose 'Always keep on this device', then rescan."
        U-->>UI: Continue with local photos / Rescan later
    end

    Note over J: Pass 2: content, local files only
    J->>J: enqueue ContentJob(local photos)
```

Pass 2 is itself a job. Its per-file steps follow sequence 2: it re-checks attributes, then hashes the file and reads metadata through the decoder pool and ExifTool.

---

## 2. Analysis job with decoder isolation

```mermaid
sequenceDiagram
    autonumber
    participant J as JobManager
    participant L as ItemLedger
    participant CF as CloudFiles adapter
    participant P as DecoderPool
    participant D as Decoder subprocess
    participant C as Core features
    participant I as Inference adapter
    participant K as Cache
    participant W as DB writer

    J->>L: create ProcessingItems(job, photos) status=pending
    loop bounded batches, within memory budget
        J->>L: next pending items
        J->>CF: re-check attributes(photo)
        alt became online-only
            J->>W: item=unavailable
        else local
            J->>K: lookup derivative(cache key)
            alt cache hit
                K-->>J: derivative
            else cache miss
                J->>P: decode(photo, max_long_edge)
                P->>D: decode request
                alt decoded
                    D-->>P: OrientedImage
                    P-->>J: OrientedImage
                    J->>K: store derivative + thumbnail
                else worker crashed or timed out
                    D--xP: exit / timeout
                    P->>P: restart worker
                    P-->>J: DecodeCrash(photo)
                    J->>W: item=crashed, Photo=import_error
                end
            end
            J->>C: compute hashes, quality(derivative)
            J->>I: embeddings, faces, eyes(derivative)
            I-->>J: features
            J->>K: store embedding
            J->>W: PhotoAnalysis rows + item=done
        end
    end
    J->>W: job=completed with counts
```

**Guarantees shown**

- A crash affects one photo, never the batch.
- Each item is marked `done` in the same write that stores its results, so a result can never exist without its ledger entry, and vice versa.
- All writes go through the single writer.

---

## 3. Resume after interruption

```mermaid
sequenceDiagram
    autonumber
    actor U as Curator
    participant UI as UI
    participant S as EventService
    participant J as JobManager
    participant L as ItemLedger
    participant K as Cache

    Note over U,K: App was closed, crashed, or the PC restarted mid-job
    U->>UI: Open "Japan 2024"
    UI->>S: open_event(event)
    S->>J: find interrupted jobs
    J->>L: items where status in (pending, in_progress)
    L-->>J: remaining items
    J-->>UI: "Analysis 62% complete. Resume?"
    U->>UI: Resume
    UI->>S: resume(job)
    S->>J: resume(job)
    J->>L: reset in_progress to pending
    loop remaining items only
        J->>K: lookup by cache key
        Note right of K: Results written before the interruption<br/>are reused, not recomputed
    end
```

---

## 4. Review decision and undo

```mermaid
sequenceDiagram
    autonumber
    actor U as Curator
    participant UI as Review grid
    participant R as ReviewService
    participant W as DB writer
    participant DB as SQLite

    U->>UI: press A on photo P
    UI->>UI: optimistic update (approved)
    UI->>R: record_decision(P, approve)
    R->>W: append DecisionEvent(P, approve, previous=unreviewed)
    W->>DB: INSERT + COMMIT (WAL)
    W-->>R: decision_id
    R-->>UI: confirmed

    U->>UI: press Ctrl+Z
    UI->>R: undo_last(event)
    R->>DB: last DecisionEvent not yet undone
    R->>W: append DecisionEvent(P, revert, undoes=decision_id)
    W->>DB: INSERT + COMMIT
    R-->>UI: P is unreviewed

    Note over DB: Current state is derived from the log.<br/>Nothing is updated in place or deleted.
```

If a write fails, the UI rolls back its optimistic update and shows an error. A decision is shown as saved only after the commit succeeds.

---

## 5. Crop proposal and manual override

```mermaid
sequenceDiagram
    autonumber
    actor U as Curator
    participant UI as Crop review
    participant CS as CropService
    participant P as DecoderPool
    participant I as Inference adapter
    participant CC as Core cropping
    participant W as DB writer

    U->>UI: Prepare approved photos for 4x6
    UI->>CS: propose_crops(event)
    loop each approved photo
        CS->>P: decode(photo, high resolution)
        P-->>CS: OrientedImage
        CS->>I: high-res or tiled face/person/pet detection
        I-->>CS: detections
        CS->>CC: propose(oriented_size, protected_regions, target)
        CC->>CC: maximal 3:2 or 2:3 rectangle
        CC->>CC: exhaustive 1-D position search with hard constraints
        CC->>CC: classify risk
        CC-->>CS: CropProposal + explanation
        CS->>W: ProtectedRegions + CropDecision(pending)
    end
    CS-->>UI: proposals grouped by risk

    U->>UI: drag crop on "needs review" photo
    UI->>CS: save_crop_override(photo, rect)
    CS->>CC: validate pixel-aspect invariant and bounds
    alt valid
        CS->>W: CropDecision(manual_override=true)
        CS-->>UI: saved
    else invalid
        CS-->>UI: rejected, snapped to nearest valid rectangle
    end

    opt no borderless crop keeps all subjects
        U->>UI: choose bordered fit
        UI->>CS: set_mode(photo, bordered)
        CS->>W: CropDecision(mode=bordered)
    end
```

---

## 6. Export and verification

```mermaid
sequenceDiagram
    autonumber
    actor U as Curator
    participant UI as Export view
    participant E as ExportService
    participant G as PathGuard
    participant P as DecoderPool
    participant IM as Imaging adapter
    participant FS as Export folder
    participant W as DB writer

    U->>UI: Export to folder X
    UI->>E: export_prints(event, settings, X)
    E->>G: check X
    alt X is under a source root
        G-->>UI: refused
    else X is under a OneDrive sync root
        G-->>UI: warn, require confirmation
    end
    E->>E: resolution check, warn on low-PPI crops
    E->>W: ExportJob + ExportItems(pending)

    loop each approved crop
        E->>P: decode(photo, full resolution)
        P-->>E: OrientedImage + color description
        E->>IM: crop, resize, P3 to sRGB, 8-bit dither
        E->>IM: encode JPEG, embed sRGB ICC, strip GPS and device IDs
        IM->>FS: write temp file, fsync
        IM->>FS: atomic rename to final name (never overwrite)
        E->>P: re-decode output
        E->>E: verify ratio, size, profile, stripped fields
        E->>W: ExportItem(done or failed, output hash)
    end
    E->>FS: write manifests and summary
    E-->>UI: N exported, M failed, reasons
```

A retry skips items whose output already exists and matches the stored output hash.

---

## 7. Re-analysis after a model upgrade

```mermaid
sequenceDiagram
    autonumber
    actor U as Curator
    participant S as AnalysisService
    participant J as JobManager
    participant K as Cache
    participant DB as SQLite

    U->>S: install approved model v2 (offline package)
    S->>S: verify manifest, hash, license approval
    U->>S: re-analyze "Japan 2024"
    S->>J: AnalysisJob(model=v2)
    loop each photo
        J->>K: lookup key with model_version=v2
        K-->>J: miss, so compute and store under new key
    end
    J->>DB: new PhotoAnalysis rows (v2), groups and ranks regenerated
    Note over DB: DecisionEvents untouched.<br/>Approved photos stay approved even if v2 ranks them lower.
    J->>DB: flag manual crops whose ProtectedRegions changed
    S-->>U: "Ranking updated. 3 manual crops need a quick re-check."
```
