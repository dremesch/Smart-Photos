# ADR-0008: SQLite authoritative store and append-only decisions

- **Status:** Accepted
- **Date:** 2026-11-07
- **Spec reference:** v1.1 §11.3, §14

## Context

Derived data (features, groups, rankings) is recomputable, but human decisions and manual crops are not. The app must survive crashes, re-analysis, and model upgrades without losing or silently changing user work. It must also support undo across restarts.

## Decision

- SQLite in WAL mode is the single authoritative store. All writes go through one writer thread.
- Human decisions are stored as an append-only `DecisionEvent` log. Current review state is a projection of that log.
- Large binary data (derivatives, thumbnails, embeddings) lives in a content-addressed file cache, referenced from the database with integrity hashes.
- Photo identity is the content fingerprint. Paths are mutable locators.

## Consequences

- Undo, audit trail, and "decisions survive re-analysis" follow from the structure.
- The cache can be cleared at any time. Only the database needs backup.
- The decision log grows without bound. That is negligible at this scale; compaction is a later option.
- Projections must be kept consistent with the log (low-level design for M1).

## Alternatives considered

| Option | Why not |
|---|---|
| Mutable status columns | Loses history and undo; easier to overwrite human work |
| Embeddings and thumbnails as SQLite BLOBs | Database bloat; slower backups |
| Per-event JSON files | No transactions; fragile under crashes |
