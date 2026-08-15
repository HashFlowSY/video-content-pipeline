# 02 — Establish the visual-text command boundary and immutable workspace

**What to build:** The Explicit visual-text command boundary: `vcp visual-text
<plan-id>` with mandatory explicit scope (`--all`, `--part`, `--range` in
Part-relative seconds), full input revalidation, and a new Immutable
visual-text workspace with an authoritative `visual-report.json` — so that a
user can start a scoped attempt, an unscoped invocation is an error rather
than an implied full sweep, and any input drift blocks the attempt.

**Blocked by:** 01 — revalidation must check the Controlled offline OCR
adapter identity or an eligible real-model identity.

**Status:** done
**Labels:** ready-for-agent

- [x] `vcp visual-text <plan-id>` accepts `--all`, repeated `--part
  <part-id>`, and repeated `--range <part-id>:<start>-<end>` (seconds on the
  Part-relative clock) plus `--json`; an invocation with no scope argument
  errors without creating a workspace.
- [x] Revalidation before execution covers confirmed RunPlan and
  SourceArtifact hashes, every named Part and range against retained Part
  identities and actual stream coverage, adapter or eligible model identity,
  and detection/sampling/classification rule versions; any drift blocks the
  attempt with a structured reason.
- [x] Each attempt creates a new Immutable visual-text workspace and
  `visual-report.json`; no attempt overwrites prior evidence, and readable
  artifacts remain in the workspace unpublished.
- [x] The report records capability state, rule versions, scope, statuses
  (`complete`/`partial`/`failed`), limitations, and diagnostic pointers.

## Comments

Implemented in commit 40c074b feat: establish the visual-text command boundary and workspace. Acceptance criteria were checked at phase
closure on the maintainer's instruction, anchored to the current-head
verification (pytest 700 passed; ruff and mypy clean; 30 confirmed exit-gate
booleans in docs/PHASE_08_INVENTORY.json).
