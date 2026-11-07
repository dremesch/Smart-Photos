# Family Photo Curator — Technical Design & Implementation Specification

| | |
|---|---|
| **Version** | 1.1 — Engineering specification (implementation authority) |
| **Supersedes** | v1.0 (historical baseline, draft for engineering review) |
| **Change basis** | [v1.0 engineering review and disposition](../reviews/v1.0-review-disposition.md) |
| **Platform** | Windows 11, x64 initially |
| **Architecture** | Local-first desktop application |
| **Status** | Approved for Milestone 0. Not yet validated on target hardware. |
| **Pilot collection** | Japan 2024 |

**Primary objective:** Minimize the effort required to select, intelligently crop, and prepare meaningful family photographs for printing, without compromising privacy.

---

## 0. What changed from v1.0

| # | Area | v1.0 | v1.1 |
|---|---|---|---|
| 1 | OneDrive inventory | Read metadata during enumeration | Two-pass inventory; cloud-file state is read from directory attributes before any file is opened (§5) |
| 2 | Orientation | "Apply EXIF orientation consistently" | One format-aware decoding contract; HEIF transforms applied once, HEIF EXIF orientation never re-applied (§6) |
| 3 | Shortlisting | Deterministic filter decides which photos get AI | Lightweight local models run on **all** eligible photos; only expensive analysis is selective (§7–9) |
| 4 | Crop geometry | Normalized rectangle only; example was not 3:2 | Pixel-aspect validation invariant; oriented source dimensions stored; 1-D position search (§12) |
| 5 | Decoder failures | In-process error handling | Native decoding isolated in restartable subprocesses (§6.4) |
| 6 | Data model | State tables only | Content-based identity, append-only `DecisionEvent`, per-item ledgers, new entities (§14) |
| 7 | Acceptance criteria | Percentages without denominators | Defined measurements and protocols, labels created before AI exposure (§18–19) |
| 8 | Milestones | UI after AI; cropping late | Usable review grid in M1; cropping as a parallel workstream (§20) |

### 0.1 Binding architecture decisions

1. **No hard elimination based on image quality.** Low-quality but unique memories stay eligible, ranked and reviewable.
2. **Full-collection inexpensive analysis.** All accessible images receive lightweight local features; expensive AI remains selective.
3. **One shared image-decoding contract.** Every downstream component consumes the same oriented, validated image representation.
4. **User decisions are authoritative.** Recommendations may change between runs; recorded human choices do not.
5. **Privacy and cost are enforceable boundaries.** Local-only processing is a tested system property, not an application setting.
6. **Useful before sophisticated.** M1 must already let the user browse and select an event faster than Windows Photos.

---

## 1. Executive Summary

Family Photo Curator is a Windows desktop application that helps families select the most meaningful photographs from large personal collections. It combines safe ingestion, inexpensive local analysis across the whole event, selective deeper local AI, human review, and non-destructive smart cropping.

The application must never transmit images, thumbnails, embeddings, facial information, or personal metadata to any third-party service.

### 1.1 End-to-end pipeline

```
SOURCE PHOTO COLLECTION  (OneDrive-synchronized or local folders; existing local files only)
        |
[1] Safe inventory            filesystem attributes only; no hydration of cloud-only files
        |
[2] Full-collection           decode once -> analysis derivative -> pHash, embeddings,
    lightweight analysis      face detection, quality metrics   (all eligible local photos)
        |
[3] Group and rank            similar shots, bursts, best expression, original quality,
                              scene diversity
        |
[4] Selective advanced AI     only ambiguous or high-value groups
        |
[5] Human curation            review shortlist, compare alternatives, rescue missed moments
        |
[6] Smart cropping            face protection, 1-D crop search, risk review
        |
[7] Verified 4x6 export       color-managed local JPEGs; originals untouched
```

Every stage is independently resumable at per-photo granularity. No stage may write to, rename, or delete anything under a source root.

---

## 2. Product Scope

### 2.1 In scope — MVP

1. Import locally available photographs from OneDrive-synchronized or other local folders.
2. Inventory JPEG and HEIC photographs without hydrating online-only files.
3. Identify exact duplicates and visually similar groups, including bursts.
4. Compute lightweight local features for every eligible photo.
5. Rank alternatives within groups, preferring good expressions and original-quality files.
6. Run selective advanced local analysis where it changes a decision.
7. Generate balanced, explainable recommendations across an event.
8. Provide a fast, keyboard-driven human review interface.
9. Propose face-safe 4×6 crops; classify risky crops; support bordered fit.
10. Export color-managed, verified print-ready JPEGs and a manifest.
11. Persist progress, analysis results, decisions, and crop settings.
12. Operate fully offline and preserve original-file integrity.

### 2.2 Recognized but excluded inputs

These are inventoried and shown as excluded, never reported as errors: MOV/MP4 (including Live Photo companions), PNG (typically screenshots), DNG/ProRAW, GIF, and other non-JPEG/HEIC files.

### 2.3 Deferred

Print-vendor integration and ordering · cloud inference · mobile app · multi-user collaboration · cloud sync of app state · video curation · album/photobook layout · generative edits or AI upscaling · facial identity recognition, face clustering, or family-member naming · print ratios other than 4×6.

---

## 3. User and Data Assumptions

The primary user is a parent who prioritizes meaningful moments over technical perfection, low effort, protection of family photographs, zero recurring cost, and reversible operations.

| Property | Expected characteristics |
|---|---|
| Existing collection | ~500 GB of photos and videos |
| Organization | OneDrive folders; structure to be verified in M0 |
| Typical source | iPhone photos, possibly from more than one family device |
| Formats | HEIC and JPEG (HEIC may be absent if transferred as "Most Compatible") |
| Target print | 4×6 inches |
| Pilot | 300–500 photos (M1); full Japan 2024 event (M4) |
| Typical event | Thousands of photos |

Hardware, available disk, photo counts, and folder layout are established in M0.

---

## 4. System Architecture

### 4.1 Style

Single-user desktop application. UI process, a headless processing engine, a pool of isolated decoder subprocesses, and a local SQLite database. No remote backend, accounts, or hosted services.

The processing engine must not import UI modules, so it can run headless for tests and benchmarks.

### 4.2 Technology stack

| Layer | Technology |
|---|---|
| Desktop UI | Python / PySide6 (Qt Widgets; **no QtWebEngine**) |
| Processing engine | Python 3.12 (or validated compatible release) |
| Database | SQLite (WAL mode) |
| Decoding | Pillow, pillow-heif (decode-only build if licensing requires) — inside decoder subprocesses |
| Metadata | Pillow/pillow-heif EXIF; ExifTool in `-stay_open` mode for Apple MakerNotes (BurstUUID, ContentIdentifier) on local files only |
| Deterministic analysis | NumPy, OpenCV, imagehash |
| Inference | ONNX Runtime, CPU execution provider as baseline |
| Accelerators | Evaluated after CPU baseline: Windows ML (Microsoft's recommended path for new deployments), DirectML (maintenance mode), others as available |
| Color management | Pillow ImageCms (LittleCMS) |
| Packaging | PyInstaller **one-directory** build, code-signed |
| Tests | pytest |

All libraries, native binaries, and model files are pinned and hashed in a reproducible environment.

### 4.3 Model selection policy

Models are chosen in M0 by benchmark on the user's hardware **and** license review. A model may not be integrated until its weights' license permits local use and the intended distribution.

| Need | Excluded unless license changes | Candidates for M0 evaluation |
|---|---|---|
| Face detection | InsightFace/SCRFD pretrained weights (non-commercial) | YuNet (OpenCV Zoo), MediaPipe face detector |
| Eyes open / landmarks | — | MediaPipe face landmarks + eye-aspect ratio |
| Person/pet detection | Ultralytics YOLO (AGPL-3.0) | YOLOX, RT-DETR (Apache-2.0) |
| Image embeddings | Weights with research-only terms | SigLIP, DINOv2, permissively licensed OpenCLIP weights |
| Saliency | — | U²-Net |
| Expression/emotion | Not in MVP (data provenance and bias, especially on children) | — |

All licenses must be re-verified at integration time; this table is a starting point, not an approval. pillow-heif's bundled codecs (libheif, libde265, possibly x265) and HEVC patent implications must be reviewed before any distribution beyond the family.

### 4.4 Logical modules

```
family-photo-curator/
  app/
    ui/            event_library, review_grid, comparison, timeline, crop_review, export
    core/
      inventory/   cloud_state, enumeration, sync_roots
      decoding/    contract, worker_pool, color, limits
      metadata/    exif, apple_makernotes, time, devices, live_photos
      features/    hashing, perceptual, quality, embeddings, faces
      grouping/    exact, near_duplicate, bursts
      ranking/     within_group, album, preferences
      advanced/    selective_analysis
      cropping/    geometry, search, risk
      export/      render, color, metadata_strip, verify
    persistence/   database, repositories, migrations
    workers/       job_manager, item_ledger, writer_queue
    security/      path_guard, network_guard
    diagnostics/   logging, benchmarks
  models/manifests/
  tests/           unit, integration, regression, privacy, performance, fixtures
```

---

## 5. Safe Inventory

### 5.1 Folder selection

The user selects one or more folders per event (e.g. *Japan 2024*). Subfolders are scanned recursively within the selected roots only.

### 5.2 Two-pass inventory

Opening a OneDrive placeholder for read can trigger hydration. Therefore:

**Pass 1 — attributes only.** Enumerate with `os.scandir()` and read `DirEntry.stat().st_file_attributes`, which come from directory enumeration and do not open file contents. Record for every entry: path, size, filesystem timestamps, extension, and cloud state:

| Attribute | Value | Meaning |
|---|---|---|
| `FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS` | 0x00400000 | Online-only; reading data will hydrate |
| `FILE_ATTRIBUTE_RECALL_ON_OPEN` | 0x00040000 | Opening will hydrate |
| `FILE_ATTRIBUTE_OFFLINE` | 0x00001000 | Data not immediately available |
| `FILE_ATTRIBUTE_PINNED` | 0x00080000 | "Always keep on this device" |
| `FILE_ATTRIBUTE_UNPINNED` | 0x00100000 | Eligible for dehydration |

No file with a recall attribute may be opened by any component.

**Pass 2 — content.** Only for files that are fully local: hash, read metadata, decode.

The UI reports counts of local, online-only, and unpinned files, and asks the user to make the event folder available ("Always keep on this device") before processing. Partial imports proceed with clear status.

### 5.3 Dehydration during processing

Unpinned local files can be dehydrated by Storage Sense mid-job. Before each content read, cloud state is re-checked from attributes. A file that became online-only is marked `unavailable`, not failed, and is retried when available.

### 5.4 Application data location

- Database, caches, logs: `%LOCALAPPDATA%\FamilyPhotoCurator\`.
- OneDrive sync roots are discovered from `HKCU\Software\Microsoft\OneDrive\Accounts\*\UserFolder` (plus any Business accounts). Known Folder Move may redirect Documents/Pictures/Desktop into OneDrive.
- The export destination is resolved to a real path; if it lies under a sync root the user is warned and must confirm. If it lies under a **source root**, it is refused.

### 5.5 Metadata collected (Pass 2)

Stable photo identity (§14.2) · paths (absolute and source-relative) · size · filesystem timestamps · capture timestamp with `OffsetTimeOriginal` when present · timestamp provenance · oriented width/height · format · camera make/model · device identifier hash · Apple `BurstUUID` · Apple `ContentIdentifier` · GPS (local use only) · provenance signals (EXIF completeness, resolution relative to device native).

### 5.6 Time handling

- Time zones are preserved when present and never invented when absent.
- **Event day** is the local calendar date at capture (using the recorded offset), not UTC.
- Missing capture time falls back to a documented order (e.g. EXIF DateTimeDigitized → filesystem time), flagged with provenance and user-correctable.
- Per-device clock offsets (`DeviceClockOffset`) correct drift and devices set to the wrong zone.

### 5.7 Live Photos

Stills and MOV companions are paired by `ContentIdentifier` (fallback: same basename and capture time). The still is ingested; the video is recorded and excluded.

### 5.8 Failure behavior

Corrupt, unreadable, crashing, or unsupported files are logged, listed as import exceptions, never modified, and never terminate the batch.

---

## 6. Image Decoding Contract

### 6.1 Single entry point

All stages obtain pixels through one contract:

```
load_oriented(photo_id, max_long_edge) -> OrientedImage
  pixels           8- or 16-bit RGB, display orientation applied exactly once
  oriented_size    (W, H) of the full-resolution oriented image
  source_color     ICC profile or nclx description
  orientation_rule identifier of the rule applied (versioned)
```

No component may decode source files or apply orientation independently.

### 6.2 Orientation rules

| Format | Rule |
|---|---|
| JPEG | Apply EXIF Orientation (1–8) once |
| HEIC/HEIF | Rely on the container transforms (`irot`/`imir`) applied by libheif/pillow-heif; **do not** apply EXIF Orientation in addition |

The orientation rule is versioned. Changing it invalidates every derived artifact for that format, including crop decisions.

### 6.3 Analysis derivatives

Each photo is decoded at full resolution at most once per pipeline run to produce a cached analysis derivative (long edge ~1024 px, configurable) from which pHash, embeddings, face detection, and quality metrics are computed. Embedded HEIF thumbnails may be used for previews and pHash where validated. Full-resolution decodes are reserved for high-resolution face detection during cropping and for export.

### 6.4 Crash isolation

Native decoding (libheif, libde265, libjpeg, OpenCV I/O) runs in a pool of worker subprocesses.

- A worker crash fails only the current file; the pool restarts the worker.
- A file that crashes a worker is recorded (`decode_crash`) and skipped on retry unless the decoder version changes.
- Per-decode timeouts and memory limits apply.
- `Image.MAX_IMAGE_PIXELS` and an explicit pixel cap guard against decompression bombs.
- `multiprocessing.freeze_support()` is required for the packaged build.

### 6.5 Color at decode

The source color description (ICC or HEIF `nclx`, typically Display P3 on iPhone) is captured and carried with the pixels. Analysis may operate in the source space; export converts as specified in §13.

---

## 7. Full-Collection Lightweight Analysis

### 7.1 Objective

Give every eligible local photo enough features to be grouped, ranked, and protected, so that no photo is excluded before a model has seen it.

### 7.2 Features computed for every eligible photo

| Feature | Purpose |
|---|---|
| SHA-256 (size-bucketed first) | Exact duplicates |
| Perceptual hash | Recompressed/resized copies |
| Image embedding | Near-duplicates, scene clusters, diversity, category tags |
| Face detection (boxes, scores) | People presence, expression-sensitive ranking, crop protection |
| Eyes-open estimate per face | Blink-aware ranking within groups |
| Global quality: exposure, clipping, contrast, noise, resolution | Ranking signals |
| Subject-region sharpness (faces, else salient region) | Focus quality robust to bokeh |
| Zero-shot category tags from embeddings (e.g. screenshot, document, food, landscape) | Filtering non-photos, coverage balance, relevance proxies |

Per-photo cost estimates are **hypotheses until M0/M2 benchmarks**. Decode time, not inference, is expected to dominate and must be measured separately.

### 7.3 Determinism

Results are repeatable for the same inputs, configuration, model versions, and execution provider. The execution provider (CPU, Windows ML, DirectML, ...) is part of the cache key. Scores are rounded to a documented precision before threshold comparisons.

---

## 8. Grouping and Ranking

### 8.1 Exact duplicates

Group by size, then SHA-256; confirm before assigning. Exact duplicates are grouped under one representative and never deleted.

### 8.2 Near-duplicates

1. Generate candidate pairs within a capture-time window, plus pHash matches regardless of time (to catch copies with stripped or wrong timestamps).
2. Score pairs using embedding similarity and pHash distance; dimensions and orientation as secondary signals.
3. Form groups with complete-linkage (or equivalently bounded) clustering inside each window, so A~B and B~C never forces A into C's group without A~C.
4. Thresholds are configurable and calibrated in M0/M2 against labelled pairs.

### 8.3 Bursts

- `BurstUUID` defines a burst when present.
- Otherwise: same device, capture times within a configurable window (default ~10 s after clock correction), and strong visual similarity.
- Photos from different devices are never placed in the same burst, though they may share a near-duplicate group.

### 8.4 Within-group ranking

Representatives are ranked using, in order of weight to be calibrated:

1. Faces: all detected faces have eyes open; subject-region sharpness on faces.
2. Provenance: original resolution and complete EXIF preferred over messaging-app or re-exported copies.
3. Global technical quality.
4. Composition signals.

Each ranking stores a reason payload. Close calls mark the group `ambiguous` for selective advanced analysis (§9) or human comparison.

### 8.5 Status labels

| Label | Meaning |
|---|---|
| `candidate` | Eligible for recommendation |
| `grouped_alternative` | Member of a group but not its current top-ranked photo |
| `excluded_category` | Detected as screenshot/document/etc.; reviewable and restorable |
| `unavailable` | Online-only or dehydrated |
| `import_error` | Could not be decoded or read |

There is no label that removes a photo from ranking because of low quality. Low technical quality lowers a score; it never ends eligibility.

---

## 9. Selective Advanced Analysis

Expensive local analysis runs only where it can change an outcome:

- Ambiguous groups (top two alternatives within a configurable score margin).
- Groups containing faces where eyes-open or sharpness signals conflict.
- Photos near the recommendation cut-off.
- Photos approved for printing (high-resolution detection for cropping, §12).

Candidate advanced analyses include higher-resolution or tiled face detection, landmark-based expression geometry (eyes, mouth openness), person/pet detection, and saliency. Generative captioning or multimodal chat models are not required by the MVP and may be explored only with a measured benefit.

The system never claims to infer emotional importance.

---

## 10. Album-Level Recommendation

### 10.1 Inputs

Candidate scores · embedding clusters · event days · groups · face/pet presence · category tags · user target count · previous decisions · event preferences.

### 10.2 Objective

Recommendations are produced by greedy selection over an explicitly stated objective. The baseline formulation is:

```
F(S) = Σ_i∈S  u(i)  +  λ · Σ_j∈V  max_i∈S  sim(i, j)
```

where `u(i) ≥ 0` is per-photo utility, the second term is facility-location coverage of the event `V` in embedding space, and `λ ≥ 0`. Group constraints (at most k photos per group) and per-day balance are expressed as soft preferences or partition constraints.

**No approximation guarantee is claimed** unless the final objective and constraints are documented and shown to satisfy the guarantee's conditions. The standard (1 − 1/e) bound holds for monotone submodular objectives under a cardinality constraint; additional redundancy penalties can make the objective non-monotone, and partition or other constraints change the applicable bound. The objective, its parameters, and its version are recorded with every recommendation.

### 10.3 Per-photo utility

`u(i)` combines visual quality, subject visibility, scene uniqueness, and a family-relevance proxy derived from observable signals (people/pet presence, category tags) and the user's own decisions. Initial weights are hypotheses.

### 10.4 Preference learning

Weights may be fitted locally from the user's approve/reject decisions (e.g. regularized logistic regression on the same features). Fitting uses a training subset only; evaluation uses a disjoint test subset (§18).

### 10.5 Coverage and targets

Coverage summaries by event day: available, eligible, recommended, approved, and sparse periods. Balanced coverage is a preference, not a rule. Targets: none, suggested (default), or explicit. No photo is rejected to satisfy a quota.

---

## 11. Human Review Interface

### 11.1 Modes

- **Review grid** (M1): fast thumbnail grid with approve/reject.
- **Recommended** (M2): ranked recommendations with explanations.
- **Comparison** (M2): group members side by side; number keys pick the winner.
- **Timeline**: photos by event day (may follow M2).
- **All photos**: every inventoried photo, including alternatives, excluded categories, and unavailable files.
- **Approved**: the final selection.

### 11.2 Actions and shortcuts

| Action | Shortcut |
|---|---|
| Next / previous | → / ← |
| Approve | A |
| Reject | X |
| Full screen | F |
| Compare alternatives | C |
| Pick alternative in comparison | 1–9 |
| Undo | Ctrl+Z |

Also: remove approval, restore rejected, view explanation, filter by day, export.

### 11.3 State model

Review decisions are independent of recommendations and always take precedence. Every decision is written immediately as a `DecisionEvent` (§14). Current state (`unreviewed`, `approved`, `rejected`) is derived from the event log, which also provides undo across restarts.

---

## 12. Smart 4×6 Crop Engine

### 12.1 Geometry

Target ratio is 3:2 (landscape) or 2:3 (portrait), matching the oriented source. For a 4:3 source, the maximal 3:2 crop keeps the full width and 88.9% of the height; for 3:4 portrait, full height and 88.9% of the width. The maximal crop can therefore move along one axis only.

### 12.2 Coordinate representation

```json
{
  "photo_id": "photo-001",
  "source_fingerprint": "sha256:…",
  "orientation_rule": "heif-container-v1",
  "oriented_size": [4032, 3024],
  "target_ratio": "3:2",
  "mode": "borderless",
  "crop": { "x": 0.0, "y": 0.0556, "width": 1.0, "height": 0.8889 },
  "risk_status": "likely_safe",
  "algorithm_version": "crop-v2"
}
```

(4032 × 1.0 = 4032 px; 3024 × 0.8889 ≈ 2688 px; 4032 / 2688 = 1.5.)

### 12.3 Validation invariant

For every stored or edited crop, after rounding to integer source pixels:

```
| (round(w·W) / round(h·H)) − target_ratio | ≤ tolerance equivalent to one source pixel
and the rectangle lies entirely within [0, W] × [0, H]
```

Crops violating the invariant are rejected at the repository layer. Property-based tests cover random source sizes and orientations.

### 12.4 Crop search

1. Load oriented size; compute the maximal valid crop.
2. Load protected regions: faces (from high-resolution or tiled detection on the approved photo), detected people/pets, salient regions, and user-defined regions.
3. Evaluate crop positions exhaustively along the free axis (and a small set of zoom levels if enabled).
4. Apply hard constraints: no clipped face when any valid position avoids it; safe margin respected where possible.
5. Score remaining positions (saliency coverage, protected-region margin, composition) and select the best.
6. Classify risk and store the explanation.

No upscaling to create cropping room.

### 12.5 Risk classification

| Status | Condition |
|---|---|
| `likely_safe` | All protected regions inside the crop with configured margin; detection confidence adequate |
| `needs_review` | Protected regions near an edge, low detection confidence, or ambiguous composition |
| `crop_conflict` | No borderless crop retains all protected regions |

Thresholds are calibrated against labelled crops; a status is not a guarantee.

### 12.6 Bordered fit

Fits the full oriented image into a 3:2/2:3 canvas with configurable white or neutral borders; records the fit mode.

### 12.7 Manual editor

Drag along the valid axis, optional zoom within source bounds, crop and protected-region overlays, add/remove protected regions, undo/reset, compare with original, switch borderless/bordered, approve. Edits pass the §12.3 invariant.

### 12.8 Invalidation

A crop decision is invalidated (flagged, not deleted) when the source fingerprint, orientation rule, or oriented size changes. Manual crops are flagged for re-confirmation, never silently recomputed.

### 12.9 Safe margins

A configurable internal safe margin (default 3–5% of each dimension, to be confirmed against the chosen lab) keeps protected subjects away from trim edges. Future lab profiles may override it.

---

## 13. Print Export

### 13.1 Inputs

Approved photo IDs · approved crop decisions · output dimensions · JPEG quality · destination · filename strategy · lab profile.

### 13.2 Defaults

- JPEG, 8-bit, sRGB with embedded sRGB ICC profile.
- 1800×1200 / 1200×1800 px (nominal 4×6 at 300 PPI).
- High-quality encoding; orientation baked into pixels, EXIF Orientation = 1.
- Minimal metadata (§13.5).

### 13.3 Color

- Convert from the source color description to sRGB using LittleCMS (relative colorimetric with black-point compensation, or perceptual; chosen and documented in M4).
- HEIF `nclx` color descriptions (common on iPhone) are mapped to an equivalent ICC profile before conversion.
- High-bit-depth sources are dithered to 8-bit.
- **HDR:** iPhone HDR photos carry an SDR base image plus a gain map. Export uses the SDR base image and ignores the gain map; no tone-mapping is performed. This is documented to the user (prints may look flatter than the HDR on-screen rendering).

### 13.4 Resolution warning

Warn before export when the crop region's short side is below a configurable threshold (default 1200 px, ≈200 PPI at 4×6). No AI upscaling.

### 13.5 Metadata

Strip GPS, device serials/identifiers, MakerNotes, and other personal metadata by default. Preserving selected fields requires explicit opt-in.

### 13.6 Output organization and safety

```
<export root>/Japan_2024/
  print_ready/   JPN24_0001.jpg …
  manifests/     selection.csv, crop_decisions.json
  reports/       export_summary.json
```

- Destination may not be under a source root; warned if under a OneDrive sync root (§5.4).
- Deterministic filenames; never overwrite silently.
- Write to a temporary file, fsync, then atomic rename on the same volume.
- Retries detect and validate already-written outputs via `ExportItem`.

### 13.7 Lab profile

The manifest and summary state that images are pre-cropped. The export report reminds the user to disable lab auto-crop or "fit" processing.

### 13.8 Verification

Each output is re-decoded and checked for: aspect ratio, dimensions, embedded sRGB profile, absence of stripped fields, and expected count. Failures are recorded per item.

---

## 14. Data Model

SQLite is the authoritative store, in WAL mode, with a single writer thread fed by a queue from workers. Foreign keys, uniqueness constraints, and migrations are required. Pixels, thumbnails, and embeddings are stored as versioned cache files referenced from the database with integrity hashes.

### 14.1 Entities

| Entity | Key fields |
|---|---|
| **Event** | event_id, name, start/end dates, source_roots, settings_version, created/updated |
| **Photo** | photo_id, event_id, content_fingerprint, current_source_path, relative_path, filename, file_size, oriented_width/height, format, capture_timestamp, utc_offset, timestamp_source, device_id_hash, burst_uuid, content_identifier, cloud_state, import_status |
| **PhotoAnalysis** | photo_id, analysis_type, algorithm_version, model_version, execution_provider, input_fingerprint, configuration_hash, payload_ref, created_at |
| **SimilarityGroup** | group_id, event_id, group_type (exact / near / burst), algorithm_version |
| **GroupMembership** | group_id, photo_id, similarity_score, rank, rank_reasons |
| **LivePhotoPair** | photo_id, video_path, content_identifier, pairing_method |
| **DeviceClockOffset** | event_id, device_id_hash, offset_seconds, source (user/inferred) |
| **DecisionEvent** (append-only) | decision_id, photo_id, event_id, action, previous_state, source (user/import), timestamp, undoes_decision_id |
| **Selection** (derived view) | photo_id, current_review_status, last_decision_id |
| **ProtectedRegion** | region_id, photo_id, normalized_rect, kind (face/person/pet/salient/user), source (model/user), confidence, model_version |
| **CropDecision** | photo_id, source_fingerprint, orientation_rule, oriented_size, target_ratio, crop_mode, normalized_rect, risk_status, manual_override, review_status, algorithm_version, invalidated_at |
| **ProcessingJob** | job_id, event_id, stage, status, configuration_hash, started/completed, error_summary |
| **ProcessingItem** | job_id, photo_id, status (pending/done/failed/crashed/unavailable), attempts, last_error, updated_at |
| **ExportJob** | export_id, event_id, destination, settings, status, created/completed |
| **ExportItem** | export_id, photo_id, output_path, output_hash, status, verification_result |

### 14.2 Photo identity

Identity is the content fingerprint (SHA-256 of file bytes), with the path as a mutable locator. Rules:

- A file moved or renamed by OneDrive keeps its identity and decisions.
- A file whose bytes change becomes a new content version; prior decisions are carried forward and flagged for confirmation.
- The same content under two paths in one event is an exact-duplicate group.
- The same content in two events has independent per-event rows and decisions.

### 14.3 Deletion

Removing a project deletes its rows and cache files and runs `VACUUM` (or uses `secure_delete`) so data does not persist in free pages. Source files are never touched.

---

## 15. Processing, Caching, and Scheduling

### 15.1 Jobs

Each stage is a background job with start, cancel, resume, and retry-failures. Pause is optional for MVP. Progress and resumption are tracked per item in `ProcessingItem`. Cancellation leaves the database and cache consistent.

### 15.2 Cache keys

```
content_fingerprint · analysis_type · algorithm_version · model_version ·
execution_provider · orientation_rule · configuration_hash
```

Cheap filesystem metadata detects likely changes; the content fingerprint establishes identity.

### 15.3 Incremental processing

Reuse valid results for unchanged content · analyze new photos · reanalyze changed content · mark missing files without deleting decisions · invalidate only results whose key components changed.

### 15.4 Concurrency and memory

- Decoding in bounded subprocess pool; inference in-process with ONNX Runtime intra-op threads; one writer thread.
- A configurable memory budget bounds concurrent full-resolution decodes (a 48 MP 8-bit RGB image is ~146 MB decoded).
- Model sessions are released when a stage completes.

### 15.5 Accelerators

CPU first. Windows ML and DirectML are evaluated after the CPU baseline, against Python integration constraints and measured benefit. The active execution provider is recorded with results.

---

## 16. Privacy and Security

### 16.1 Guarantee

The application does not transmit photographs, thumbnails, embeddings, facial data, GPS, personal metadata, or event/family information. No network operation is permitted during processing. No telemetry.

### 16.2 Enforcement (layered)

1. No network-capable dependencies on the processing path; no QtWebEngine.
2. In-process network guard active during analysis (socket creation fails closed and is logged).
3. Optional outbound-block Windows Firewall rule for the application executable, created with user consent.
4. Blocked-network end-to-end test in CI and in the M4 pilot (§18.3).

Model packages or updates, if ever supported, use a separate, explicit, disclosed workflow outside analysis.

### 16.3 Privacy by design

Local storage only · no accounts or API keys · no third-party trackers · no cloud fallback · **no face embeddings, face clustering, or identity database**, including temporary use for grouping.

### 16.4 Threats addressed

| Threat | Control |
|---|---|
| Malformed/malicious images | Subprocess decoding, pixel caps, timeouts, pinned and patched decoders |
| Unintended hydration | Attribute-only Pass 1; recall attributes block opening |
| Writes to source folders | Path guard rejects any write under a source root |
| Exports syncing to cloud | Sync-root detection and warning |
| GPS leakage in exports | Default stripping; verification check |
| Sensitive paths in logs | Internal IDs in logs; paths excluded from diagnostic exports and crash dumps |
| Supply chain | Pinned, hashed dependencies and model files from verified sources |
| Residual data | Temp-file cleanup; `VACUUM`/`secure_delete` on project removal |
| Local exposure | Documented: caches are unencrypted; recommend BitLocker |

---

## 17. Observability

Local metrics: files discovered by format and cloud state · hydration-blocked count · decode failures and crashes · group counts · features computed · advanced analyses run · time per stage (decode vs inference separately) · cache hit rate · peak memory · recommendation distribution by day · manual overrides · crop risk distribution · export results.

Logs use internal IDs and exclude image content, embeddings, GPS, and folder names (which may contain family names). Every recommendation and crop is traceable to input content, versions, configuration, scores/reasons, and user decisions.

---

## 18. Testing and Evaluation

### 18.1 Unit tests

Crop invariant (property-based) · ratio and rounding · orientation rules per format · hashing · grouping linkage constraints · ranking reasons · album objective · cache keys and invalidation · decision-log derivation and undo · export naming.

### 18.2 Integration tests

Nested imports · cloud-state classification · dehydration mid-job · HEIC decoding and orientation (incl. mirrored/rotated fixtures) · JPEG orientations 1–8 · Live Photo pairing · missing/zone-less timestamps · multi-device clock offsets · invalid and crash-inducing files · worker restart · SQLite recovery after kill · full export pipeline with color verification.

### 18.3 Privacy and safety tests

- End-to-end run with networking blocked; outbound connections monitored.
- **Hydration test:** inventory over a folder containing online-only placeholders; assert no file's recall/offline attributes changed and no bytes were downloaded. Runs on a real OneDrive-synced Windows machine (cannot be fully simulated in hosted CI); attribute-handling logic is also unit-tested against a mocked attribute layer.
- **Original integrity:** hash all source files before and after a full run; expect zero changes.
- Exports contain no GPS or device identifiers.

### 18.4 Evaluation protocol

1. Before any AI output is shown, the user labels an evaluation subset of Japan 2024: duplicate/near-duplicate pairs, best photo per sampled group, photos they would print, and **critical moments** (definition written before labelling).
2. Labelled data is split into training (weight fitting, threshold calibration) and test subsets. Reported metrics use the test subset only.
3. A manual baseline (time and number of decisions to curate a fixed subset without the app) is recorded in M0/M1.
4. N for Precision@N and all thresholds are fixed before measurement.
5. Crop evaluation uses several hundred labelled outputs across portraits, groups, children, pets, landscapes, subjects near edges, separated subjects, and portrait-orientation photos; results reported with sample count and 95% confidence interval.

---

## 19. Acceptance Criteria

| Area | v1.1 measurement |
|---|---|
| OneDrive safety | Zero unintended hydrations during inventory, verified by the instrumented test (§18.3) |
| Original integrity | All source hashes unchanged after a complete run |
| Unique-photo protection | Recall of user-labelled critical moments in recommendations ∪ approved; count of critical photos ranked as non-top group alternatives |
| Grouping quality | Pairwise precision and recall against pre-labelled pairs |
| Within-group ranking | Agreement with the user's labelled best photo per group |
| Recommendation quality | Precision@N and user acceptance rate, N fixed before testing |
| Workload reduction | Manual decisions and elapsed review time vs. recorded baseline |
| Crop quality | `likely_safe` pass rate with sample count and confidence interval; zero unintentionally clipped faces in the reviewed final export |
| Export correctness | 100% of outputs pass §13.8 verification |
| Privacy | Zero unauthorized transmissions in blocked-network testing |
| Cost | Zero paid inference calls; local compute time recorded |
| Scale | 5,000-photo benchmark: peak RAM, time per stage, and correct resume after forced interruption |
| Recoverability | No lost decisions after restart or killed process |

Target values for each measurement are set at the end of M0, once the baseline and hardware are known. They are targets, not demonstrated capabilities.

---

## 20. Milestones

### M0 — Environment and feasibility
Hardware inventory · OneDrive layout and cloud-state detection prototype · decoder isolation proof of concept · format inventory (HEIC vs JPEG, presence of Apple MakerNotes) · model and license review · CPU benchmark of candidate models (decode and inference timed separately) · evaluation dataset labelled · manual curation baseline recorded.
**Exit:** sample processes locally without hydration or crashes; model shortlist chosen on evidence; acceptance target values set.

### M1 — Safe ingestion and first usable review
Two-pass inventory · isolated decoding contract · metadata, time, devices, Live Photos · content identity · SQLite with migrations and decision log · exact and pHash/time-based grouping · minimal approve/reject grid with persistent decisions.
**Exit:** user can curate a 300–500 photo batch faster than with Windows Photos; zero source changes.

### M2 — Local AI and recommendation engine
Embeddings and face/eyes detection on all eligible photos · embedding-based grouping (decisions persist across regrouping because they key on photos) · within-group ranking · album recommendation · selective advanced analysis · caching · comparison UI · 5,000-photo benchmark.
**Exit:** measurable improvement over pHash/time-only ranking on the test subset.

### M3 — Smart cropping (parallel workstream)
Geometry and invariant · high-resolution face protection · 1-D search · risk classification · manual editor · bordered output · crop evaluation.
**Starts** when the decoding contract and geometry types are stable (during M1–M2).
**Exit:** crop review effort decreases with no unacceptable clipping on the labelled set.

### M4 — Export and full Japan 2024 pilot
Color-managed export · metadata stripping · verification · manifests · privacy and integrity tests · full-event run · performance and quality measurement · prioritized backlog.
**Exit:** acceptance criteria met to the targets set in M0.

---

## 21. Major Risks

| Risk | Mitigation |
|---|---|
| Inventory hydrates OneDrive files | Attribute-only Pass 1; instrumented hydration test |
| Double rotation or orientation drift | Single decoding contract; per-format rules; versioned invalidation |
| Best expression lost in a burst | All photos analyzed; face-aware within-group ranking; comparison UI |
| Groups merge unrelated photos | Complete-linkage within time windows; calibrated thresholds; recall measured |
| Decoder crash or exploit | Subprocess isolation, caps, pinned patched libraries |
| Local inference too slow | CPU benchmark in M0; compact models; cached derivatives; accelerators evaluated later |
| License blocks a model or codec | License review gate before integration |
| Crops clip small or missed faces | High-resolution detection for approved photos; user-protected regions; conservative risk classes |
| Color shifts in prints | Managed P3→sRGB conversion; embedded profile; verification |
| Exports or caches end up in OneDrive | `%LOCALAPPDATA%` storage; sync-root detection |
| Model updates change recommendations | Versioned cache keys; human decisions authoritative |
| Evaluation biased by AI exposure | Labels created before AI output; train/test split |

---

## 22. Open Decisions (resolve in M0 unless noted)

1. Hardware profile (CPU, RAM, GPU/NPU, disk, Windows build).
2. OneDrive folder organization and transfer format (HEIC vs JPEG; MakerNotes present).
3. Embedding, face, and landmark models (benchmark + license).
4. Grouping and burst thresholds (calibrated on labels; M0–M2).
5. Selection preferences: people vs scenery vs chronological coverage.
6. Print lab, trim tolerance, and safe-margin default (before M4).
7. Distribution scope (family-only vs wider), which drives codec/model license requirements.
8. Model installation: bundled vs explicit offline package.
9. Cache storage budget.
10. Definition of "critical moment" and evaluation subset composition.

These do not block implementing safe inventory, the decoding contract, the data model, or the non-destructive storage architecture.

---

## 23. Definition of Done

The MVP is complete when a user can:

1. Select a local folder containing thousands of event photos.
2. Inventory it without hydrating online-only files or altering any source file.
3. Have every eligible photo analyzed locally, grouped, and ranked.
4. Review balanced, explained recommendations and compare alternatives.
5. Approve photos quickly with the keyboard and rescue anything the system ranked low.
6. Generate face-safe 4×6 crop proposals and adjust risky ones.
7. Export a verified, color-managed batch of print-ready JPEGs with GPS removed.
8. Close and reopen the application, or recover from a crash, without losing decisions.
9. Confirm that all original photos remain byte-identical.
10. Repeat the process for additional events.

**Principle:** Use the least expensive reliable computation to reduce repetitive work, preserve meaningful human decisions, and never sacrifice privacy or original memories for automation.
