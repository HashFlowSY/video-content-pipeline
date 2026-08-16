# 09 — Produce run reports, inventory, and the cleanup plan

**What to build:** The always-published audit layer: `quality-report.md/json`
aggregating per-stage gate outcomes from retained stage reports (plan §17,
no re-running), `processing-report.md` with the plan §18.1 required sections
including the fixed project-stage line and the cleanup section,
`run-inventory.json` with the plan §18.2 eleven-field per-path records, and
the Minimal RunBundle guarantee wiring every ordinary failure path into
publication of the six-piece floor.

**Blocked by:** 08

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] Every ordinary failure path — including failure before any stage unit
  completes — publishes the Minimal RunBundle (manifest, processing report,
  run inventory, both quality reports, diagnostics with events snapshot).
- [ ] `run-inventory.json` covers every used, created, modified, downloaded,
  and published path with `deletion_class` and `deletion_consequence`;
  models, caches, workspaces, staging, and published files all appear.
- [ ] `processing-report.md` contains all §18.1 sections; readable prose
  defaults to Chinese; the project-stage line is present.
- [ ] `quality-report.*` aggregates recorded gate outcomes and the
  projection's timing-view selections without re-executing gates.
- [ ] No cleanup command exists; no code path deletes user-visible files.
