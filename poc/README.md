# Family Photo Curator — Proof of Concept

> **Throwaway code.** This POC exists to retire technical risk and give the build team a working reference. It is not production code and does not follow the production layering in `src/`. Port the ideas, not the files. See [HANDOFF.md](HANDOFF.md).

It runs the whole pipeline end to end on a real folder:

```
scan (attributes only) → ingest in isolated subprocesses (hash, decode once, derivatives)
→ lightweight features on every photo → group (exact / near / burst, complete linkage)
→ rank + recommend → review UI (keyboard, undo) → 4×6 crops with face protection
→ sRGB export with metadata stripping, atomic writes and verification → integrity check
```

---

## Run it on your PC (Windows, PowerShell)

```powershell
cd Smart-Photos
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r poc\requirements.txt
cd poc

# 1. Analyse a folder (read-only). App data goes to %LOCALAPPDATA%\FamilyPhotoCurator-POC
python -m fpc_poc run --event "Japan 2024" "C:\Users\<you>\OneDrive\Pictures\Japan 2024"

# 2. Review: A approve · X reject · U clear · Ctrl+Z undo · C compare group · P propose crops
#    B bordered/borderless · drag the dashed crop to override it
python -m fpc_poc review --event "Japan 2024"

# 3. Export approved photos (refuses source folders; warns for OneDrive folders)
python -m fpc_poc export --event "Japan 2024" --out "C:\Prints"

# Anytime
python -m fpc_poc status    --event "Japan 2024"
python -m fpc_poc integrity --event "Japan 2024"   # re-hash originals; exit 1 if any changed
python -m fpc_poc summary   --event "Japan 2024"   # aggregate-only JSON (counts, timings) for agents
```

**Output is aggregate-only** (issue #13, D6, D7). Commands print counts and timings, never paths, file names, capture times, hashes or exception messages. `photos.error` stores `status:CODE:ExceptionClass` (for example `error:E_DECODE:OSError`). The list of changed originals from `integrity` goes to `<data>/reports/integrity_changed.json`. An uncaught exception prints only `fpc_poc: error class=<Name> code=E_UNCAUGHT` (exit 3). Library warnings print only `fpc_poc: warning class=<Category> code=W_LIB`, in the main process and in decoder workers. Workers ignore Ctrl+C (the parent handles cancel), and any other worker stderr text is replaced by one `code=W_STDERR` line.

**The crash log deliberately leaves out exception messages** (Security ruling on PR #39). `<data>/logs/crash.log` records the class, the code and package-relative frames (`fpc_poc/pipeline.py:123 in export`). Messages from Pillow, pillow-heif and `OSError` carry source paths, so triage uses the class, the frames and a synthetic repro.

The export folder carries output names and opaque photo IDs only. Source details stay in the database. Print-ready JPEGs contain only SOI, APP0 (JFIF), APP2 (ICC), DQT, SOF, DHT, DRI, SOS, RSTn and EOI; `verify` reports `METADATA_PRESENT` for anything else (COM, APP1 EXIF/XMP, APP13 IPTC). Export refuses any overlap between the export folder and a source folder, in either direction. Leftover `{PREFIX}_NNNN.tmp` files from an interrupted export are removed on the next export; nothing else is deleted.

**The event label appears on the USB.** `--event` becomes the export folder name and the file-name prefix (`Holiday 2026` gives `Holiday_2026/print_ready/HOLIDAY2_0001.jpg`), and the print lab sees both. Use a generic label such as "Holiday 2026", never a name with people, places or dates you would not hand to the lab. The label is always typed by the user; it is never defaulted from a source folder name.

**Online-only OneDrive files are not downloaded.** They show as `unavailable` (see `status`, and *Import problems* in the UI). To include them, right-click the folder in Explorer, choose **Always keep on this device**, wait for the sync, and run `run` again. Already processed photos are skipped.

Re-running is safe and incremental. Interrupting with Ctrl+C or closing the window loses no decisions.

**Hung or crashing decoders cannot stall a run** (issue #11, S2-D8, D4). Ingest, features and export run in long-lived `spawn` worker processes, each with its own pipe pair and one task in flight. A worker still busy `deadline + 5 s` after dispatch is killed (`Process.kill()`, which is TerminateProcess on Windows) and respawned; the item gets status `timeout` and code `E_TIMEOUT`. A worker that dies mid-task gives `crashed` / `E_CRASH`. Every other item completes. Deadlines are in `config.py`: 30 s for ingest (decode), 60 s for features and for export, 5 s grace; `FPC_POC_DECODE_DEADLINE_S`, `FPC_POC_FEATURE_DEADLINE_S`, `FPC_POC_EXPORT_DEADLINE_S` and `FPC_POC_KILL_GRACE_S` override them. The clock starts when a task is sent to a ready worker, so `spawn` start-up time never counts against it.

Before each task is sent, an *attempt started* row is committed to the `attempts` table; the result handler writes its outcome in the same transaction as the result. A **strike** is a timeout, a crash, or an attempt row left open by a parent that was killed mid-task (power loss, TerminateProcess). A `timeout` or `crashed` item is retried on the next `run`; **two strikes** make it `quarantined` (`E_QUARANTINED`), and later runs skip it until `decode.DECODER_VERSION` (or `features.VERSION` for the features stage) changes. A graceful Ctrl+C closes in-flight attempts as `interrupted`, which is not a strike. `status` and `summary` count `timeout`, `crashed` and `quarantined`. A worker also exits by itself when its parent dies, so a hung decoder does not outlive a killed run (it needs the GIL to do so; a native hang that holds the GIL would survive the parent).

### Try it without personal photos

```powershell
python tools\make_sample_event.py "$env:TEMP\fpc-sample"   # add --canary TOKEN to plant leak canaries
$env:FPC_POC_CRASH_MARKER = "CRASHME"       # makes one file crash the decoder on purpose
$env:FPC_POC_HANG_MARKER = "HANGME"         # makes one file hang the decoder (killed after the deadline)
python -m fpc_poc --data "$env:TEMP\fpc-data" run --event Sample "$env:TEMP\fpc-sample" --auto-approve 8 --out "$env:TEMP\fpc-prints"
python -m fpc_poc --data "$env:TEMP\fpc-data" review --event Sample
```

`--auto-approve N` is a demo shortcut that approves the top N recommendations, so crops and export can be shown without clicking.

### Tests

```powershell
pytest poc\tests -q      # 268 tests, including an end-to-end run on the synthetic event and the canary leak scan
```

---

## What the POC proved

| Risk (spec v1.1 / ADR) | Result | Evidence |
|---|---|---|
| Inventory must not hydrate OneDrive (ADR-0004) | Pass 1 uses `os.scandir` and `st_file_attributes` only. Recall flags block every later open. Attributes are re-checked before each read. | `inventory.py`, `test_cloud_state`. **Needs a real OneDrive check (M0).** |
| One decoding contract, orientation applied once (ADR-0006) | JPEG EXIF 6 portrait decodes to 1512×2016. With pillow-heif 1.8, HEIC transforms are applied and EXIF orientation is reset to 1, so double rotation cannot happen with this library version. | `test_e2e_orientation`, manual probe |
| A decoder crash must not kill the batch (ADR-0003) | A file that aborts its worker is marked `crashed`. Every other file completes. | `test_e2e_hostile_files_isolated` |
| A hung decoder must not stall the batch (issue #11, S2-D8) | A file that hangs its worker is killed after deadline plus grace and marked `timeout`; the worker is respawned and every other file completes. Two strikes quarantine an item until the decoder version changes, including a strike left by a parent killed mid-task. | `test_e2e_hung_decoder_times_out_and_every_other_item_completes`, `test_isolation.py` |
| Corrupt, excluded and Live Photo files | A truncated JPEG gives `import_error`. PNG, MOV and TXT are `excluded`. HEIC and MOV pairs are linked. | same |
| Full-collection analysis picks the right burst frame (ADR-0005) | In a 6-photo group, the closed-eyes frame ranks **last**. The sharp eyes-open frames rank top and the near-tie is flagged ambiguous. A second phone's shot joins as `near`, never `burst`. | `test_e2e_burst_blink_not_chosen` |
| Duplicates | The exact copy is grouped as `exact`. The messaging-app copy (800×600, no EXIF) is grouped with its original and ranks below it. | `test_e2e_exact_and_messaging_copies_grouped` |
| Crop geometry is exact (spec §12.3) | Validated over 200 random sizes. The v1.0 example is rejected as not 3:2. 4032×3024 gives 4032×2688. | `test_crop_invariant_random`, `test_spec_v10_example_is_rejected` |
| Faces beat other regions in a conflict | The head at the top edge is kept, the false "pet" is given up, and the photo is flagged `crop_conflict`. | `test_face_beats_pet_when_both_cannot_fit` |
| Export correctness and privacy | 1800×1200 JPEG with an embedded sRGB ICC profile, no EXIF or GPS. Temp file then atomic rename, never overwrite, re-decode verification. Refuses export into a source folder. | `test_e2e_exports_verified_and_stripped`, `test_e2e_export_refuses_source_folder` |
| Originals never change | All sources re-hashed after a full run: zero changes | `test_e2e_originals_untouched`, `integrity` command |
| Decisions are durable, with undo | Append-only log; undo appends; state is a projection of the log | `test_decisions_append_only_with_undo` |
| Resume after interruption | Killed mid-ingest: 13 of 48 committed. The rerun processed only the 35 pending, and 33 of those were cache hits from derivatives written before the kill. | manual run (below) |
| Nothing leaks to agents or the print lab (D6, D7, S2-D11) | Canaries in a folder name, file names, EXIF, MakerNote, XMP and the ICC description never appear in stdout, stderr, DB error fields, logs, crash dumps or the export folder; no absolute paths or timestamps either. Failures report counts only. | `test_leaks.py` |
| No network | A socket guard is installed in the main process and every worker | `guard.py` |

## Measurements (indicative only)

Cloud container, 4 vCPU, 48 synthetic 12 MP photos (one third HEIC):

| Stage | 1 worker | 4 workers |
|---|---|---|
| Ingest: hash + decode + 1024 px derivative + thumbnail | 0.55 s/photo | 0.15 s/photo |
| Features: Haar faces/eyes/cats, pHash, quality, toy embedding | 0.27 s/photo | 0.20 s/photo |

Straight-line extrapolation for 5,000 photos with 4 workers is about 30 minutes for the first run, and seconds for re-runs. **Decode dominates**, as the design assumed. Feature extraction scaled poorly across processes here (OpenCV threading plus spawn start-up). This needs profiling on the target PC in M0. None of these numbers are commitments.

## What the POC exposed (input to M0)

1. **Haar cascades are not good enough. This is the main finding.** They report false-positive "faces" in blurred backgrounds, miss cats, and call a cat a human face. Eyes-open from Haar is a crude proxy. These errors flow straight into ranking (a false face with "closed eyes" lowers a good photo's score) and into cropping (a false protected region causes a false `crop_conflict`). **M0 must select a DNN face detector with landmarks (YuNet-class) and a pet detector.** The POC already loads `models/face_detection_yunet*.onnx` if one is present. The download is blocked in the build container, so it is untested.
2. **The embedding is a placeholder.** Near-duplicate grouping currently leans on pHash plus a colour/layout vector. A real embedding model (SigLIP/DINOv2-class) is needed for "same scene, different framing" and for diversity.
3. **Crops need high-resolution detection.** Faces are detected on the 1024 px derivative, so small faces in group shots will be missed. Spec §12.4 requires high-resolution or tiled detection for approved photos. The POC does not do this.
4. **HEIF colour is incomplete.** Only ICC profiles are converted. `nclx`-only HEIC (common on iPhone, Display P3) is treated as sRGB. Real iPhone files are needed to validate this.
5. **Hung decoders: handled in the POC since issue #11** (deadline, kill, respawn, two-strikes quarantine). The production decoder pool (Track B, S2-03) still needs its own design (WP-3 LLD, #33), and Windows TerminateProcess and `spawn` respawn cost must be measured on the target PC.
6. **Timestamps**: naive parse only. There are no per-device clock offsets, and Apple `BurstUUID` / `ContentIdentifier` are not read because ExifTool is not wired in.
7. **The OneDrive guard uses `%OneDrive%` environment variables**, not the registry sync-root list.
8. **Ranking weights and grouping thresholds are guesses**, tuned only on synthetic data.

## Layout

```
poc/
  fpc_poc/
    __main__.py   CLI: run, rank, crops, export, review, status, integrity, summary; top-level handler
    leakscan.py   canary / absolute-path / timestamp scanner (counts only), reused by the pre-USB gate
    quiet.py      sanitised warnings and worker stderr, SIGINT ignored, pool worker entry point
    config.py     paths and uncalibrated thresholds
    inventory.py  pass 1: attributes only, cloud state, Live Photo pairing
    decode.py     load_oriented() contract, sRGB, ingest task, crash- and hang-isolated pool
    features.py   pHash, quality, Haar faces/eyes/cats, toy embedding, saliency
    grouping.py   exact / near / burst, complete linkage in time windows
    ranking.py    per-photo score + reasons, greedy album recommendation
    crop.py       exact 3:2 geometry, invariant, 1-D search, risk classes
    export.py     render, sRGB ICC, strip metadata, atomic write, verify
    pipeline.py   stage orchestration; main process is the single DB writer
    db.py         flat SQLite schema, append-only decisions + projection, attempt ledger
    guard.py      socket guard
    ui.py         PySide6 review grid, preview with overlays, crop drag
  tools/make_sample_event.py   synthetic event covering every risky path
  tests/test_poc.py            unit + end-to-end tests
  tests/test_leaks.py          canary and leak tests (issue #13)
  tests/test_isolation.py      deadline, kill, respawn and two-strikes quarantine (issue #11)
```
