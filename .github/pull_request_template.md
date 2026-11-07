## Summary

<!-- What changed and why, in two or three sentences. -->

Closes #

## Role and work package

- Role: <!-- architect / pm / tech-lead / data-scientist / systems / quality / ux / security -->
- Work package: <!-- WP-x from poc/HANDOFF.md -->

## How it was tested

| Where | Result |
|---|---|
| `ruff check .` + `pytest` (CI) | |
| Windows CI | |
| Real Windows PC / OneDrive / iPhone files | <!-- say "not tested" if not --> |

## Not covered / known gaps

<!-- Be explicit. "Nothing" is rarely true. -->

## Checklist

- [ ] Follows [AGENTS.md](../AGENTS.md) non-negotiables
- [ ] Tests added or updated for new behaviour
- [ ] Spec / design / ADR updated if behaviour or architecture changed
- [ ] Touches `core/`, `docs/adr/`, `docs/spec/`, `docs/design/` or `models/manifests/` → `needs:architect`
- [ ] Touches inventory, decoding, export, logging or the network guard → `needs:security`
- [ ] UI change → screenshots attached
