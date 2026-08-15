# 05 — Version the OCR adapter, output projection, and item gates

**What to build:** The Controlled offline OCR adapter and the versioned path
by which OCR output becomes evidence — so that after an affirmative OCR
decision, every OCR evidence item in the report carries Part, PTS,
`visual_page_id`, and confidence, malformed output invalidates the attempt
instead of leaking, and items violating timing or coverage gates are rejected
with recorded reasons.

**Blocked by:** 01, 04 — OCR runs under the registered capability and only
after the confirmation pause.

**Status:** done
**Labels:** ready-for-agent

- [x] The Controlled offline OCR adapter is described by implementation
  version and fixed input/output fixture hashes; it is not a model asset and
  cannot earn a real-model quality qualification.
- [x] OCR output enters only through a versioned output projection; an
  incomplete or schema-invalid projection is `model_output_invalid` and
  invalidates the attempt, with raw output retained as restricted local audit
  evidence.
- [x] Every projected OCR evidence item carries Part, PTS, `visual_page_id`,
  and confidence; items with times outside actual stream coverage or
  inconsistent with their page's appearance records are rejected with
  structured reasons, never silently repaired.
- [x] OCR text keeps its source language, including mixed Chinese/English.

## Comments

Implemented in commit 21fa4c1 feat: version the OCR adapter, projection, and item gates. Acceptance criteria were checked at phase
closure on the maintainer's instruction, anchored to the current-head
verification (pytest 700 passed; ruff and mypy clean; 30 confirmed exit-gate
booleans in docs/PHASE_08_INVENTORY.json).
