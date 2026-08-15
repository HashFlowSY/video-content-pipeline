# 03 — Build the deterministic Part-local page index

**What to build:** Deterministic page-change detection, Versioned
frame-sampling rules, and Part-local visual page identity — so that a scoped
run produces a reproducible page index (which pages exist in each Part, when
each first appeared and reappeared) and a complete Retained frame inventory,
with no model involved anywhere in detection.

**Blocked by:** 02 — the index lands in the attempt's workspace and report.

**Status:** done
**Labels:** ready-for-agent

- [x] Detection and sampling consume controlled, hash-pinned synthetic
  frame-metric fixtures (stability, Text-value proxy metrics such as edge
  density and region-scoped frame difference, page-change signals); no
  detection-stage OCR and no frame extraction from user media.
- [x] The same input and rule versions always produce the same selected
  frames, the same pages, and the same appearance records; rule versions are
  recorded in provenance.
- [x] Every extracted frame — selected for OCR or not — enters the Retained
  frame inventory with the reason it was or was not selected; nothing is
  discarded pipeline-side, and frames are marked workspace-internal
  (Unpublished internal frame).
- [x] `visual_page_id` is scoped to exactly one Part (ADR 0048); Page
  appearance records capture first appearance and every reappearance with
  exact times; no cross-Part correlation is asserted.

## Comments

Implemented in commit 14ca258 feat: build the deterministic Part-local page index. Acceptance criteria were checked at phase
closure on the maintainer's instruction, anchored to the current-head
verification (pytest 700 passed; ruff and mypy clean; 30 confirmed exit-gate
booleans in docs/PHASE_08_INVENTORY.json).
