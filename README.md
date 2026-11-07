# Family Photo Curator

A local-first Windows desktop application for selecting, smart-cropping, and exporting family photos for 4×6 printing, without sending images or derived data to any cloud service.

**Status:** Specification, high-level design and proof of concept complete. Next gate: Milestone 0 (environment and feasibility). `src/` is the production scaffold; `poc/` is throwaway reference code.

## Documentation

| | |
|---|---|
| [Technical Specification v1.1](docs/spec/technical-specification-v1.1.md) | Requirements; current implementation authority |
| [High-level design](docs/design/README.md) | Architecture, data flow, sequences, technical direction |
| [Architecture Decision Records](docs/adr/README.md) | Key decisions and their rationale |
| [v1.0 review disposition](docs/reviews/v1.0-review-disposition.md) | Review findings and how they were resolved |
| [Proof of concept](poc/README.md) | Working end-to-end prototype, findings, measurements |
| [POC handoff](poc/HANDOFF.md) | Port map, contracts and work packages for the build team |
| [AGENTS.md](AGENTS.md) | Rules for coding agents |
| [Team charter](docs/team/README.md) | Agent roles, decision rights, workflow and PR gate (skills in `.claude/skills/`) |

## Repository layout

```
src/family_photo_curator/
  ui/            PySide6 presentation (optional "ui" extra)
  services/      use-case facade and composition root
  workers/       job manager, item ledger, DB writer queue
  core/          pure domain and pipeline logic + ports
  adapters/      cloud files, decoder pool, ExifTool, inference, imaging
  persistence/   SQLite schema, migrations, repositories
  security/      source path guard, network guard
  diagnostics/   privacy-safe logging, metrics, benchmarks
models/manifests/  approved model manifests (weights are never committed)
tests/           architecture, unit, integration, regression, privacy, performance
docs/            spec, design, ADRs, reviews
poc/             throwaway proof of concept (see poc/README.md)
```

Layer dependencies and the no-network rule are enforced by `tests/architecture/`.

## Development

Requires Python 3.12 (3.13 is also accepted).

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"        # add ",ui" for the desktop UI
ruff check .
pytest
```

Test markers: `onedrive` (needs a real OneDrive-synced folder), `windows_only`, and `slow`. CI runs `pytest -m "not onedrive and not slow"` on `windows-latest`.

Never commit personal photos or model weights. The `.gitignore` blocks common image, video, and model formats.
