# 09 — Produce run reports, inventory, and the cleanup plan

**What to build:** The always-published audit layer: `quality-report.md/json`
aggregating per-stage gate outcomes from retained stage reports (plan §17,
no re-running), `processing-report.md` with the plan §18.1 required sections
including the fixed project-stage line and the cleanup section,
`run-inventory.json` with the plan §18.2 eleven-field per-path records, and
the Minimal RunBundle guarantee wiring every ordinary failure path into
publication of the six-piece floor.

**Blocked by:** 08

**Status:** done
**Labels:** ready-for-agent

- [x] Every ordinary failure path — including failure before any stage unit
  completes — publishes the Minimal RunBundle (manifest, processing report,
  run inventory, both quality reports, diagnostics with events snapshot).
- [x] `run-inventory.json` covers every used, created, modified, downloaded,
  and published path with `deletion_class` and `deletion_consequence`;
  models, caches, workspaces, staging, and published files all appear.
- [x] `processing-report.md` contains all §18.1 sections; readable prose
  defaults to Chinese; the project-stage line is present.
- [x] `quality-report.*` aggregates recorded gate outcomes and the
  projection's timing-view selections without re-executing gates.
- [x] No cleanup command exists; no code path deletes user-visible files.

## Comments

Implemented in commit 7238177 feat: produce run reports, inventory, and cleanup
plan (Phase 9 ticket 09). Acceptance criteria were checked at phase closure on
the maintainer's instruction, anchored to the current-head verification (pytest
1034 passed; ruff and mypy clean; 21 confirmed exit-gate booleans in
docs/PHASE_09_INVENTORY.json).
