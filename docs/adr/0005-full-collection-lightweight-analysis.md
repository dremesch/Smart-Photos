# ADR-0005: Full-collection lightweight analysis

- **Status:** Accepted
- **Date:** 2026-11-07
- **Spec reference:** v1.1 §7–9

## Context

The v1.0 design used deterministic technical-quality filtering to decide which photos would reach AI analysis. In family bursts, the deciding difference is usually eyes and expression, not sharpness. That design could pick a blinking frame and never show the AI the better one.

## Decision

- Every eligible local photo receives lightweight local analysis from a cached derivative: hashes, quality metrics, an embedding, face detection, and an eyes-open estimate.
- Grouping and within-group ranking use these features, with face-region sharpness and eyes-open signals in face groups.
- Expensive analysis is selective: ambiguous groups, recommendation borderline cases, and approved photos (for cropping).
- No photo loses eligibility because of quality. Low quality lowers a score; it never removes the photo.

## Consequences

- More local compute than v1.0. Whether total time is acceptable is measured in M0/M2, not assumed.
- Simpler mental model: there is no hidden "pre-filtered out" population.
- Embeddings and face detection become core dependencies from M2, so model selection and license review are on the critical path.

## Alternatives considered

| Option | Why not |
|---|---|
| Deterministic shortlist first (v1.0) | Systematically loses the best expression in bursts |
| Heavy models on every photo | Cost without proportional benefit |
