# Rules for coding agents

These rules apply to every agent (and human) working in this repository.

## Read first

1. [Spec v1.1](docs/spec/technical-specification-v1.1.md): what to build.
2. [High-level design](docs/design/README.md) and [ADRs](docs/adr/README.md): how it fits together.
3. [POC handoff](poc/HANDOFF.md): your work package, its dependencies, and the POC code to learn from.
4. [Team charter](docs/team/README.md): roles, decision rights, labels and the PR gate. Take on your role by invoking its skill from `.claude/skills/` (`architect`, `product-manager`, `tech-lead`, `data-scientist`, `systems-engineer`, `quality-engineer`, `ux-engineer`, `security-privacy-engineer`).

## Non-negotiables

- **Never write, move, rename or delete anything under a user's photo folders.** Tests must assert originals are byte-identical.
- **Never open a file whose cloud-recall attributes are set** (ADR-0004).
- **No network code.** No HTTP clients, telemetry, model downloads or cloud SDKs in `src/`. `tests/architecture/test_no_network.py` enforces this.
- **Respect the layers** (design 01 §5). `tests/architecture/test_layering.py` enforces this. Only `ui` may import Qt.
- **All pixels come from the decoding contract** (ADR-0006). Do not call `Image.open` on source files anywhere else.
- **Human decisions are append-only** (ADR-0008). Never update or delete a decision row.
- **No face embeddings, face clustering or identity features.**
- **No personal photos or model weights in git.** Use `poc/tools/make_sample_event.py` or synthetic fixtures.
- **Models load only via an approved manifest** in `models/manifests/` (ADR-0007).

## Working agreements

- One work package per branch and PR. Keep PRs small and focused, and fill in the PR template.
- Every PR needs `approved:tech-lead`; contract, ADR, spec, design and manifest changes also need `approved:architect`. The `PR gate` check enforces this, and new commits reset approvals. Only the product owner merges to `main`.
- Changing a shared contract (`core/` types, `core/ports/`) needs its own PR, with the change explained.
- A decision that is hard to reverse needs an ADR (`docs/adr/template.md`).
- Update the spec or design docs in the same PR when behaviour changes.
- Before pushing: `ruff check .` and `pytest`. Mark tests that need Windows or OneDrive with `windows_only` / `onedrive`, and long ones with `slow`.
- Report results honestly. If something is untested on Windows or on real iPhone files, say so in the PR.
