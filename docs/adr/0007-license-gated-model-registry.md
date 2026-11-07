# ADR-0007: License-gated model registry

- **Status:** Accepted
- **Date:** 2026-11-07
- **Spec reference:** v1.1 §4.3

## Context

Several popular vision models carry licenses unsuitable for distribution (for example, non-commercial pretrained face-detector weights, or AGPL detection frameworks). Model files are also a supply-chain risk.

## Decision

A model may be loaded only if it has a manifest in `models/manifests/` recording its id and version, source, SHA-256, license and approving review, input/output contract, and benchmarked execution providers. Weights are verified against the hash at install and load time. Weights are never committed to the repository.

## Consequences

- Models are swappable through configuration and versioned in cache keys.
- License review is a gate in M0 and before any distribution beyond the family.
- Some higher-accuracy models may be unavailable. Each capability keeps more than one candidate.

## Alternatives considered

| Option | Why not |
|---|---|
| Download models on first run | Network dependency (ADR-0001); unverified provenance |
| Bundle any well-known model | License and supply-chain exposure |
