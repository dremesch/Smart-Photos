# High-Level Design

Architecture-level design for Family Photo Curator. It implements [Technical Specification v1.1](../spec/technical-specification-v1.1.md). Lower-level design (schemas, interfaces, algorithms, thresholds) is written per milestone. The list of deferred topics is in [04 §8](04-technical-direction.md#8-deferred-to-low-level-design).

| Document | Contents |
|---|---|
| [01 — Architecture overview](01-architecture-overview.md) | Drivers, system context, runtime processes, layers, dependency rules, deployment, trust boundaries, invariants |
| [02 — Data flow](02-data-flow.md) | Data classification, level 0 and level 1 data-flow diagrams, stores, lifecycle, retention |
| [03 — Sequence diagrams](03-sequence-diagrams.md) | Inventory, analysis with decoder isolation, resume, review and undo, cropping, export, model upgrade |
| [04 — Technical direction](04-technical-direction.md) | Principles, decision summary, technology direction, quality strategies, roadmap, risks, deferred topics |
| [ADRs](../adr/README.md) | Individual architecture decision records |

Diagrams are written in Mermaid and render on GitHub.
