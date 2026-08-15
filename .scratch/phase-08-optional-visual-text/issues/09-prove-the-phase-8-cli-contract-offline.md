# 09 — Prove the Phase 8 CLI contract offline with an exit-gate inventory

**What to build:** The offline verification closing the phase: end-to-end CLI
contract tests over hash-pinned synthetic fixtures and the Controlled offline
OCR adapter, plus the machine-checkable exit-gate inventory — so that every
claim the phase makes is asserted by a test and recorded as a
`*_confirmed` boolean.

**Blocked by:** 01, 02, 03, 04, 05, 06, 07, 08 — it closes over the whole
contract.

**Status:** done
**Labels:** ready-for-agent

- [x] End-to-end scenarios cover at minimum: unscoped-invocation error, each
  scope form, revalidation drift, sampling determinism, full frame retention
  with reasons, Part-local page identity and reappearance, projection
  invalidity, item gate rejections, every classification class plus
  `classification_uncertain` and excluded items, both OCR-pause decisions,
  the resource-envelope pause, `model_acquisition_required` with retained
  page index, picture-only versus picture-plus-audio suspicion basis, the
  host-read comment upgrade record, affected-Part selection and
  carry-forward, immutability, mixed Chinese/English OCR text, and
  multi-Part collections.
- [x] Absence semantics are asserted: a run that never enables visual-text
  records `ocr=not_requested`, extracts no frames, and produces no visual
  facts, with picture-only intervals recorded as unanalyzed visual content.
- [x] Every offline run asserts the guarantees block: `model_execution`,
  `model_acquisition`, `network_access`, `frame_extraction`,
  `outputs_publication` all `not_attempted`.
- [x] The phase inventory records `*_confirmed` exit-gate booleans mapping
  the phase plan's 退出门禁 list plus the derived gates: default runs extract
  no frames; formal outputs contain no screenshots; no visual facts when OCR
  is off; every OCR result carries Part, PTS, page ID, and confidence; no
  general vision model is a required dependency; scope is always explicit;
  all frames are inventoried; page identity is Part-local; all rules are
  versioned.

## Comments

Implemented in commit 1b2ae5e feat: prove the Phase 8 CLI contract offline with an exit-gate inventory. Acceptance criteria were checked at phase
closure on the maintainer's instruction, anchored to the current-head
verification (pytest 700 passed; ruff and mypy clean; 30 confirmed exit-gate
booleans in docs/PHASE_08_INVENTORY.json).
