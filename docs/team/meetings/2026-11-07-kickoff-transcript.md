# Team Meeting 1: Transcript

Condensed record of the kickoff discussion on 2026-11-07. Each role was a separate agent; contributions are lightly edited for length. Decisions are in the [minutes](2026-11-07-kickoff.md).

## Architect (opening)

Welcome, everyone. Where we are: spec v1.1 is approved, high-level design and 8 ADRs are on main, and a quick-and-dirty POC runs end to end (inventory without hydration, crash-isolated decoding, full-collection features, grouping, ranking, review UI with undo, face-protected 4×6 crops, verified sRGB export, integrity check). Windows CI for lint and architecture tests is green; the POC tests are running on Windows now for the first time.

The POC's biggest finding: OpenCV Haar detectors are not acceptable. They produce false faces and miss pets, and those errors flow into ranking and cropping. Other open items: real iPhone HEIC behaviour (orientation, nclx colour, Apple MakerNotes), OneDrive hydration on the real PC, hung decoders, and uncalibrated thresholds.

Agenda: (1) product framing from PM; (2) each role: top risks, what you need from others, your first tasks, and any change to your own role definition; (3) decisions and action items for the first sprint.

My proposal for sprint 1: WP-0 Discovery (run the POC on the product owner's PC and Japan 2024 folder, detector and embedding benchmarks with licence review, labelled evaluation subset), WP-C shared contracts, porting the synthetic fixture generator into tests/fixtures, and CI hardening. Nothing in src/ beyond contracts until WP-C is reviewed.

## Product Manager (opening)

Thanks. I'll start with who this is for, because it decides most of our trade-offs. The user is a parent with roughly 500 GB of iPhone photos in OneDrive. They care about meaningful moments more than technical perfection, and they want low effort, privacy for their kids' photos, zero recurring cost, and the ability to undo anything (spec §3). None of them will judge us on model accuracy. They'll ask whether this got Japan 2024 into prints faster than Windows Photos without risking a single original.

So for the first real use, success is the M1 exit criterion and nothing more ambitious. The owner curates a 300–500 photo batch from Japan 2024, does it faster than with Windows Photos, and we change zero source files. The full Japan event is the M4 pilot, not this one. Under §19 we don't promise any number until M0 sets the targets, so please keep "it'll be 3× faster" out of PRs and status updates.

What must stay true: originals byte-identical, no hydration, human decisions always override recommendations, everything offline. Also no face identity or clustering, even if someone thinks naming the kids would be handy. That's deferred in §2.3, and I'll decline it as a change request.

On sprint 1, I agree with the Architect's scope, with one push-back on order. "Useful before sophisticated" needs two product items in sprint 1, and neither is code. First, the **manual baseline**: we time the owner curating a batch in Windows Photos. Second, the **evaluation labels**, including critical moments and the best photo per group. Both must happen *before* the owner sees any POC ranking or crop output, because §21 lists AI-exposure bias as a risk. So WP-0 should run the POC headless on Japan 2024, and nobody shows the owner its picks until labelling and the baseline are done. I'll also write the WP-6 user-test protocol and acceptance criteria this sprint, so "faster than Windows Photos" is measured the same way both times.

Open questions for the product owner:
- **By end of week 1:** §22.7 distribution scope (family-only or wider), because it gates the licence review the Data Scientist and Security need. Also §22.10, what counts as a "critical moment", because labelling can't start without it.
- **By the end of M0:** §22.5 selection preferences (people vs scenery vs day coverage), the N for Precision@N, §22.8 how models get installed, and §22.9 the cache budget.
- **Before M4:** §22.6 print lab and safe margins.

I'll run that list with the owner.

Three questions:
1. **Data Scientist:** how many labelled photos and groups can we realistically get from the owner, and is that enough for confidence intervals on grouping recall and `likely_safe` crops? I'd rather plan labelling sessions now than find a shortfall in M2.
2. **UX Engineer:** can you and I agree a baseline protocol that the M1 grid test can repeat exactly: same batch size, same "done" definition, decisions counted the same way?
3. **Tech Lead:** with nothing in `src/` before WP-C review, when is the earliest realistic date for a WP-6 grid build the owner can try? I'd like to book the pilot session against that date, not guess it.

## Round 1

### Tech Lead
I agree with the Architect's scope, and I'm adopting the PM's ordering as a hard dependency: no issue that shows the owner POC picks or crops goes to `status:ready` until the baseline and labels are done.
Sprint 1 backlog: (1) WP-0 run POC headless on owner's PC, no UI output shown — Systems, M, blocked on owner session. (2) WP-0 OneDrive hydration test on the real machine — Security + Systems, S, after #1. (3) WP-0 collect HEIC samples into a private non-git corpus — Systems, S. (4) WP-0 detector/embedding benchmark + licence review — DS + Security, L, blocked on §22.7. (5) Manual Windows Photos baseline + WP-6 test protocol — PM + UX, S, before anything is shown. (6) WP-13 labelling sessions and evaluation subset — DS, M, blocked on §22.10. (7) WP-C contracts in core/ and core/ports/ — Architect, L. (8) Port synthetic generator to tests/fixtures — QE, S. (9) CI hardening — QE, S.
WP-6 date: one-week sprints; WP-C merged end of sprint 1; sprint 2 WP-1/2/3; sprint 3 WP-4/5; sprint 4 WP-6 grid. Week 4 earliest dry run, book the owner pilot for week 6. The M1 grid needs no ML and shows no rankings.
Risks: contract churn after WP-C (freeze at merge; changes get own PR, dependents blocked); review bottleneck on me plus Windows/real-device gaps (cap two open PRs per role; QE pre-reviews tests; PRs must state what wasn't run on the owner's PC).
Needs: Architect WP-C draft mid-sprint and gating my own PRs; QE on #8/#9 day one; PM owner availability windows.
Role changes: one-week sprints with Friday written status; `needs:owner` label so owner time is scheduled work.
Question to Systems: can WP-3's decoder pool start against a draft WP-C in sprint 2 without rework, or do you need ports frozen first?

### Data Scientist
Labels before exposure — and I'll hold the POC's headless results myself until labelling closes.
Volumes: at ~95%, a 95% Wilson CI is ~89–98% at n=100 and ~92–97% at n=300; below ~300 we can't tell variants apart. Group pairs aren't independent, so I'll bootstrap by group. Realistic from one owner: session 1 critical moments (60 min, ~50–100 photos; even 50/50 gives a lower bound ~93%, always wide); session 2 ~600 same/different pairs (40 min); session 3 best-of-group on ~150 groups with a 10% repeat set for self-consistency (45 min); crops wait for M3 with risk class hidden. ~3 hours total, split train/test up front.
Shortlist (benchmarks run on owner's PC, weights hash-checked against draft manifests): faces YuNet, MediaPipe BlazeFace (Apache-2.0), SCRFD (code MIT, weights non-commercial — the ADR-0007 trap); eyes via MediaPipe Face Mesh or small eye-state classifier; pets/people YOLOX, NanoDet or RT-DETR (Apache-2.0), not Ultralytics (AGPL); embeddings SigLIP (Apache-2.0, also gives zero-shot tags) vs DINOv2; OpenCLIP has LAION provenance questions.
"Replaces Haar" = vs Haar on owner labels: fewer false faces per 100 faceless photos; higher face recall (small faces separately), pet recall, zero cat-as-human; fewer false crop_conflicts; CPU time below decode time. Targets set end of M0.
Risks: too few labels / single-labeller noise; licence-clean models weaker, eyes-open weakest, blink may be soft signal only.
Needs: Security licence verdicts on weights and training data; Systems offline benchmark harness; PM written critical-moment definition; owner 3 sessions in 2 weeks.
Role: "no LLMs" is right but SigLIP tags use text prompts — tag prompts get versioned and evaluated like config. I own the label protocol: pre-registered metrics, self-consistency, no AI output to owner before labels.
Question to Security: verdicts on YuNet, SCRFD, SigLIP and YOLOX weights including training-data terms by end of week 1, assuming family-only if §22.7 is open?

### Systems Engineer
Agree; headless WP-0 suits my benchmark. POC weaknesses: ProcessPoolExecutor can't kill hung workers; after a crash it reruns each unfinished job in a fresh spawned pool (seconds per file on Windows); per-item commits from main thread; cache keyed on SHA-256 only (no algorithm/model/orientation version, gap vs design 02 §4.1); no memory budget (four 48 MP decodes at once).
Build instead: our own workers with per-task deadline, kill-and-respawn, crash ledger, two-strikes quarantine; workers stay alive; semaphore caps full-res decodes; single writer thread batching every 50 items or 500 ms, result and ledger status in one transaction.
WP-C proposals for Architect approval: `ItemResult` (status enum ok/unavailable/error/crashed/timeout/quarantined, error code, decode vs compute timings, payload; no free-form dicts); `CacheKey` frozen value object with every 02 §4.1 component and canonical serialization; `DecoderPort.decode(request, deadline) -> ItemResult`.
M0 benchmark script run by owner on Japan 2024: hardware, versions, per-stage timings (decode separate from inference), peak working set per worker, cold vs warm cache; one local JSON with IDs and numbers only; owner reviews before sharing; skips online-only files and reports the count.
Risks: hung/hostile decoder or mid-job dehydration wedging a job; memory and inference cost once DNN detectors replace Haar.
Needs: DS candidate models early; Security agreed pixel caps and timeouts; QE hang and kill-mid-commit fixtures; TL WP-12 packaging from sprint 2.
Role: diagnostics are local only, never "telemetry"; I own the lock file and the build.
Question to Architect: is a hard per-item decode deadline plus two-strikes quarantine an ADR-0003 implementation detail, or its own ADR?

### Quality Engineer
Originals byte-identical and no hydration are my acceptance tests. Fixtures: port the generator into tests/fixtures as a seeded builder with a manifest of expected outcomes. Two gaps: POC HEICs lack real irot/imir and nclx; the "crash" file relies on an env-var marker in product code — a test backdoor. Add JPEG orientations 1–8, HEIC with real irot/imir (and EXIF disagreeing), Display-P3 nclx, genuinely malformed bytes, a hung-decoder case, more realistic faces incl. Haar near-misses, slow-marked 5,000-photo scale mode. Only permissive sample images, licences listed; CI check fails on any committed image not in the manifest.
Standing suites before src/: integrity (hash fixture tree before/after), privacy (socket guard + EXIF scan of exports), recoverability (kill, resume, decision log superset) — written against a small runner interface, run against the POC now, then as contract tests against WP-C ports.
CI: Windows + Linux matrix, agreed Python version (3.12 vs 3.13 mismatch), pip caching, coverage, nightly slow job, manual/self-hosted run on owner's PC for -m onedrive. Hosted CI cannot prove non-hydration.
Risks: false confidence from synthetic HEICs; the OneDrive check silently stops happening.
Needs: Systems HEIC recipe with real irot/nclx and the decoder timeout contract; Security sign-off on guard and fixture licences; DS synthetic stand-ins for detector-fooling images; PM confirm pilot integrity runs count as §18.3 evidence.
Role: owns fixture manifest and owner-PC OneDrive run log.
Question to Systems: can you commit to a timeout-and-kill contract in WP-C so I write the hung-decoder test against the port now?

### UX Engineer
Baseline protocol: two comparable ~400-photo batches from different days (matched on bursts/near-duplicates), uncurated. Task "choose the photos you'd print". Windows Photos heart = approve; grid A/X. A decision = owner's final state per photo (undo doesn't add). Clock starts at first photo with thumbnails warmed in both tools, stops at "done". Record approved count, later changes, 1–5 effort. 5-minute warm-up on a throwaway batch each. Randomize batch-to-tool. Windows Photos must run on a local copy (opening OneDrive folder hydrates). M1 grid shows no rankings.
POC UI: keep layout, overlay colours, single-key decisions, details pane. Fix: reload() rebuilds grid every keypress and loads icons synchronously; decisions go widget→SQLite; re-rank/crops block main thread without progress; "Compare" is just a filter. M1 scope: virtualized grid, async thumbnails, A/X/U, arrows, F full screen, restart-safe undo, import-problems view, visible save state; comparison in M2.
Contract needed in WP-C: list_photos(event, view) pages; request_thumbnail(id, size, priority)/cancel_thumbnail → bytes or reason (cloud-only, decode failed, timeout); record_decision → confirmed/failed (drives rollback); undo_last; review_summary; JobProgress(done,total,failed), finished, cancelled, cancel_job. Plain callbacks; Qt adapter inside ui. Build against FakeReviewService with configurable latency/failures.
Risks: cold-cache thumbnails for thousands of HEICs; owner's limited time (one sitting per tool).
Needs: TL contract in WP-6 issue; Systems thumbnail budget + priority queue; QE pytest-qt offscreen on Windows CI; Architect sign-off that UI gets bytes not cache paths.
Role: co-own usability protocol with PM; guardrail that no UI action may trigger hydration (incl. any "open in Photos").
Question to Systems: can the decoder pool reprioritise and cancel queued thumbnail requests as the user scrolls, or do I need a separate thumbnail queue in services?

### Security & Privacy Engineer
Support no-POC-output-before-labels; less POC output on that PC is less to clean up.
POC right: export refused inside source roots/OneDrive; no EXIF in exports and verified; temp+fsync+replace; recalled files never opened. Gaps: guard.py patches only socket.socket/create_connection/getaddrinfo — misses socketpair, fromfd, _socket, pre-imported refs, native code: a tripwire not a control. Paths leak: integrity prints full paths; decode errors keep f"{e}" (filenames) into photos.error and export_summary.json; run logs the data dir (Windows username). DB stores has_gps flag only — keep it that way. selection.csv in the export folder (the one most likely handed to a print lab) carries capture times and source SHA-256s. Interrupted exports leave .tmp files. Generator writes Tokyo GPS — fixtures must label it synthetic.
WP-0 safeguards: data dir under %LOCALAPPDATA% outside OneDrive, deleted/archived after; headless, no --out, no --auto-approve; only aggregate numbers return to the team (counts, timings, error-class histograms) — no paths, folder names, thumbnails, crops, GPS, timestamps; agents never receive personal photos or caches; a one-page "what may leave the PC" checklist the owner signs.
Licences: pillow-heif bundles libheif/libde265 (LGPL) and possibly x265 (GPL) — confirm what the wheel ships; we only decode. HEVC patents low risk for family-only; wider distribution needs a decoder strategy and likely legal advice. Model weights checked for licence and training-data terms.
Risks: personal data leaking through diagnostics; a licence/patent choice that blocks us later.
Needs: Systems — error records keep exception class + code only, no message text; QE log-sanitization test + malformed corpus in CI; DS licence/provenance before any download; TL hashed lock file, no unpinned installs in CI.
Role: add WP-0 data-handling sign-off.
Question to DS: run every candidate with weights verified against an ADR-0007 manifest on the owner's PC, with only aggregate metrics leaving the machine?

## Architect (interjection before Round 2)

Two quick answers so we don't go in circles. Systems: a hard per-item decode deadline plus a two-strikes quarantine is an implementation of ADR-0003, not a new architectural decision. Put it in the WP-3 low-level design and reference it from the `DecoderPort` contract; the deadline being a required parameter is the part I care about, and that goes in WP-C. UX: yes, the UI receives encoded bytes, never cache paths, and no UI action may trigger hydration — I'll add that as an architectural invariant. Python: we target 3.12 (spec and CI); local 3.13 is fine for development but CI is the reference. Round 2, please: answer what was asked of you, and push back where you disagree with another role.

## Round 2

### UX Engineer
Book the owner pilot for week 6, but redefine week 4: with a FakeReviewService I build the grid in sprints 2–3, so sprint 4 is integration and fixes. The week-4 dry run is PM and me running the full protocol on the synthetic event, owner not involved; if it shows a stall or save bug, we move week 6 rather than burn the owner's sitting.
Push back on reusing my grid for labelling: it won't exist in time, and every hour the owner spends in our UI is practice Windows Photos never gets, biasing the speed test. Use a deliberately plain labelling tool kept out of src/ with a different keymap (reuse my thumbnail-loading code); pairs need a side-by-side view anyway.
Local copy for Windows Photos: made by the owner outside our tool, a copy never a move, to a local non-OneDrive folder, deleted after; making it hydrates those ~400 files once — record that so it isn't attributed to our app.
Sprint 1: baseline protocol with PM; WP-6 UI–service contract draft; FakeReviewService design; keyboard map and M1 grid wireframe for PM sign-off.

### Data Scientist
To Security: yes — every candidate runs on the owner's PC with weights checked against an ADR-0007 manifest carrying the licence and training-data verdict; nothing downloaded before the verdict; only aggregate numbers leave (rates with n and CI, timings, error histograms). Label files stay on the PC too — content hashes of family photos aren't aggregate and leave only with Security approval.
To UX/TL: push back on order — labelling familiarises the owner with photos and biases the timing comparison. Baseline timing first (weeks 1–2), labelling weeks 2–3, and the two timed ~400-photo batches stay out of pairs and best-of-group sessions. Labels finish three weeks before the week-6 pilot; ~4.5 owner hours total; book now.
To QE: yes — synthetic adversarial stand-ins (Haar near-miss textures, faces on posters/screens, cats in face-like poses, tiny faces in groups, faces cut by the frame edge), procedural or CC0, licences in the fixture manifest; used for regression and plumbing only, never to report accuracy.
Sprint 1: pre-registered evaluation protocol and labelling plan; adversarial fixtures for QE; candidate list with sources for Security by mid-week; benchmark spec for the Systems harness; labelling sessions scheduled after the baseline.

### Tech Lead
Sprint 1 as asked doesn't fit one week. QE: seeded generator + manifest, orientations 1–8, integrity and privacy suites against the POC now; recoverability, real irot/nclx HEICs, hung decoder and 5,000-photo scale move to sprint 2 after the DecoderPort deadline is in WP-C. DS: one owner session in sprint 1 (critical moments); pairs and best-of-group and the benchmark run move to sprint 2; this week licence verdicts and the harness. Systems: hydration test moves to QE (owns the OneDrive run log); error sanitization is part of ItemResult (class + code, no message). UX: UI–service contract goes into WP-C as a draft owned by UX + TL, Architect signs off bytes-only; FakeReviewService in sprint 2. All owner-PC work in one sitting gated by Security's checklist.
Final sprint 1 backlog: 1 data-handling checklist signed by owner (Security, XS, blocks 6–8); 2 hashed lock file (Systems, S); 3 CI 3.12 Windows+Linux, markers, pip cache (QE, S); 4 WP-C types and ports (Architect, M, draft mid-week); 5 WP-C UI–service contract draft (UX + TL, S); 6 manual baseline + WP-6 protocol (PM + UX, S); 7 WP-0 headless POC run + HEIC samples on owner's PC (Systems, M); 8 WP-0 hydration test (QE, S, same sitting); 9 model licence and training-data verdicts (Security, S); 10 offline benchmark harness (Systems, S; run in sprint 2); 11 minimal labelling tool + critical-moments session 1 (DS, S; blocked on §22.10); 12 seeded fixture generator + integrity and privacy suites vs POC (QE, M).
Sprint 2: benchmark run, labelling sessions 2–3, recoverability suite with hang/HEIC fixtures, FakeReviewService, WP-12 start, WP-1/2/3 after WP-C merges.

### Quality Engineer
Security: yes. Log-sanitization via a canary token (e.g. CANARY_7Q3) in every generated folder and file name; test fails if it appears in logs, photos.error, export_summary.json, selection.csv or diagnostics JSON. Malformed corpus generated from a seed (truncations, bit flips, bad segment lengths, absurd dimensions, decompression bombs), not downloaded.
UX: pytest-qt offscreen on Windows with PySide6 pinned, from the first ui PR.
Systems: both fixtures, but the env-var crash backdoor goes and nothing replaces it in product code: crash and hang become a FaultyDecoder test adapter behind DecoderPort; kill-mid-commit is a real TerminateProcess from the harness, timed by watching the SQLite file. No test seams in src/.
Architect: 3.12 only accepted. TL: QE pre-review is time-boxed to one working day against a written checklist, and Systems reviews QE's own test PRs.
Sprint 1: seeded fixture builder + manifest; integrity, privacy and canary suites vs POC; generated malformed corpus; FaultyDecoder + kill-mid-commit harness; CI Windows+Linux 3.12, caching, nightly slow, manual onedrive job; test-review checklist.

### Systems Engineer
TL: yes, WP-3 can start in sprint 2 against draft WP-C if two things are frozen first: DecoderPort.decode(request, deadline) and the ItemResult status enum.
QE: committed — deadline is required; on expiry the worker is killed within a fixed grace period, result `timeout`, pool respawns and continues; two failures → `quarantined`. Write the hung-decoder test against the port now; agree to remove the env-var marker.
UX: one pool, scheduling in front of it in a services thumbnail scheduler. Queued tasks cancellable; in-flight decodes finish and are cached (killing costs a respawn). One worker reserved for interactive requests so ingest can't starve scrolling. Cached thumbnails never touch the pool.
Security: agreed — exception class + error code only; message text never stored, logged or exported, including the benchmark JSON.
Pushback: inference timing must come from my harness, not a separate DS script, so decode and inference are measured on the same hardware, cache state and versions.
Sprint 1: draft ItemResult, CacheKey, DecoderPort for WP-C; offline benchmark harness; private HEIC corpus + recipe for real irot/imir/nclx fixtures; WP-3 LLD (deadline, kill, quarantine); hydration test with Security on the owner's PC.

### Product Manager
Accept the UX protocol with three changes: no randomisation possible (grid doesn't exist until week 6) — batch A to Windows Photos now, batch B to the grid in week 6, matched on bursts/near-duplicates; batch B stays out of all labelling; the Windows Photos local copy lives outside OneDrive and is deleted after, under Security's checklist.
QE: pilot integrity runs count as §18.3 evidence only if instrumented (the -m onedrive run on the owner's PC, recorded in the run log); the before/after hash check counts toward the §19 integrity criterion.
DS: accept ~3 hours, provided pairs are shown without scores or rankings and session 1 waits for the written critical-moment definition (I'll write it with the owner this week).
TL: accept the week-6 pilot with week-4 dry run on synthetic data; if WP-C slips, the pilot slips — we don't shrink M1 to hold the date.
Owner asks (~7 h over 6 weeks): week 1 checklist sign-off + host headless POC run, benchmark and hydration test (~1 h); week 2 Windows Photos baseline on batch A (~1 h); weeks 2–3 three labelling sessions (~3 h); end of M0 sign off target values; week 6 M1 pilot on batch B (~1 h).
Scope creep: SigLIP zero-shot tags fine as internal signals; anything user-visible (tags, search, captions) is a change request. Nothing beyond the M1 grid in front of the owner before week 6.
Owner decisions, in priority order: §22.10 critical moment (week 1); §22.7 distribution (week 1); checklist and session windows (week 1); §22.5 selection preferences, N for Precision@N, §22.8 model installation, §22.9 cache budget (end of M0); §22.6 print lab and safe margins (before M4).

### Security & Privacy Engineer
DS: written verdicts by end of week 1. Provisional, assuming family-only: YuNet MIT (OpenCV Zoo), trained on WIDER FACE — terms to read, likely green. SigLIP Apache-2.0, trained on non-public WebLI — opaque provenance, clean licence, green with that recorded in the manifest. YOLOX Apache-2.0; COCO annotations CC BY 4.0, Flickr images mixed — green. SCRFD amber: InsightFace weights are non-commercial research only; fine to benchmark, can't get an ADR-0007 approval that survives §22.7 widening; ships only if nothing else is close. Pushback: raw POC results stay on the owner's PC; the DS holds aggregates only.
UX: the local copy is fine if the owner makes it in Explorer with no involvement from our tools; not Desktop or Documents (Known Folder Move may sync them); Windows Photos people/face grouping switched off; copy deleted after. Goes on my checklist.
Systems: benchmark JSON OK on three conditions — no file SHA-256s (a hash links back to the original; use per-run opaque IDs), no machine name or username, and an automated scrubber test, not just owner review. Starting caps: 120 MP decoded, 30 s deadline per decode.
QE: yes to the privacy suite, plus guard tests covering socketpair and fromfd, a scan of logs and manifests for paths, username and GPS, and an export scan that also covers XMP, IPTC and MakerNotes. Remove the env-var crash backdoor.
Commitments: model and codec licence verdicts by end of week 1; the owner's WP-0 "what may leave the PC" checklist; caps and timeouts to Systems; fixture licence sign-off; hardened guard spec; lock-file and CI review with the Tech Lead.

## Architect (close)
Decisions D1–D14 and the sprint 1 backlog are recorded in the minutes (2026-11-07-kickoff.md). Thank you all.
