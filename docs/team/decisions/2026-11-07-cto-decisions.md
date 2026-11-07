# CTO Decisions: 2026-11-07

Answers from the CTO (product owner) to the Architect's questions after team meeting 1 and the backlog review. These decisions take precedence over earlier plans where they conflict.

## Decisions

| # | Decision | Effect |
|---|---|---|
| C1 | **Two tracks.** Track A (*holiday release*) hardens the POC and produces printable 4×6 JPEGs of Japan 2024 by **2026-11-27**, so prints can be made by **2026-12-01**. Track B (production app per spec) continues in parallel and becomes the main track after Dec 1. | Sprint 1–3 are re-cut around Track A. M1 and later milestones are replanned. |
| C2 | **Track A is fully local.** No Azure or LLM calls for the Dec 1 release. An optional Azure tier (Azure AI Vision, Azure OpenAI, on the CTO's own subscription) may be proposed **after Dec 1** through a new ADR, with guardrails: opt-in per event; downscaled, metadata-stripped images only; results cached by content; budget alerts and an in-app call cap; Azure OpenAI abuse-monitoring retention addressed. | ADR-0001 stands for Track A. |
| C3 | **Runs on an Azure VM in the CTO's subscription**, not on a personal PC. Photos live in the CTO's own tenant. | [ADR-0009](../../adr/0009-owner-controlled-azure-vm.md); VM setup guide. |
| C4 | **"No random people in the background" uses a heuristic, never identity.** People who are smaller, less sharp and away from the main subjects count as background. Ranking penalizes them, and cropping removes them where possible. No face recognition. | Spec §2.3 and §16.3 unchanged. New ranking and crop requirements (spec v1.2). |
| C5 | **Japan 2024 is the Track A run.** The CTO will see AI picks for it. Track B's unbiased evaluation (baseline and labels before any AI exposure, D1) uses a **different event**, chosen later. | D1 applies to the Track B evaluation event, not to Japan 2024. |
| C6 | **Claude Code may run on the VM, with deny rules** so agents never read photos or app data. Agents operate the pipeline through commands that print aggregates only. | `.claude/settings.json` deny rules; AGENTS.md rule. |
| C7 | **Printing:** Walgreens. In-store kiosk or USB is preferred over online upload, so no online photo library is created. If online ordering is used, the uploaded copies are deleted after the order. | Spec §22.6. |
| C8 | **Reporting lines.** The CTO is at the top. The Architect reports to the CTO. The PM, Tech Lead and Data Scientist report to the Architect. The Systems, Quality, UX and Security engineers report to the Tech Lead; Security's veto escalates to the Architect. Most work runs autonomously in Claude Code; the CTO is involved only for decisions, owner-only actions, and anything touching privacy or cost. | Team charter. |
| C9 | **Product answers.** §22.5: favour family moments and good photos, with no preference for people vs scenery. §22.7: personal use. §22.10, critical moment: forward-facing family moments, no blinking, sharp, decent background, no unrelated people (unless cropped out). Print count: as many as the selection supports; no budget limit. No owner-time limits, but the owner's involvement should stay low. | Spec v1.2 §3, §10, §22. |
| C10 | **GitHub housekeeping is delegated to the Architect.** | Labels synced by workflow; repository settings that need admin rights are listed for the CTO. |
