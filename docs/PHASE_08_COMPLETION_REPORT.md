# Phase 8 Completion Report

## Status

Phase 8, optional visual-text, is completed and verified in the project-local
offline environment. Per the phase exit gate, this is an engineering pass
only: domain quality is not verified because no real video frame was
processed and no real OCR model was downloaded or invoked. The project
remains in engineering development; `real_world_testing` and
`production_validated` are both `false`.

## Delivered Scope

- `vcp visual-text` and `vcp resume-visual-text` create and resume Immutable
  visual-text workspaces behind the Explicit visual-text command boundary:
  scope is always explicit (`--all`, `--part`, `--range` in Part-relative
  seconds; an unscoped invocation errors without creating a workspace), and
  every attempt exactly revalidates the confirmed RunPlan, SourceArtifact
  hashes, named Parts and ranges against retained identities and actual
  stream coverage, adapter or eligible model identity, and all rule versions.
- The provider-neutral Visual-text capability contract holds exactly one
  model capability, `ocr_primary` (ADR 0047), evaluated offline from the
  model registry; RapidOCR is registered as a metadata-only research
  candidate, no general vision model appears anywhere in the contract, and no
  eligible capability yields an immutable `model_acquisition_required` result
  that retains the page index with no OCR evidence.
- Deterministic page-change detection and Versioned frame-sampling rules
  select frames from stability, Text-value proxy metrics, and page-change
  signals over hash-pinned synthetic frame-metric fixtures; the same input
  and rule versions always produce the same selection, and every frame —
  selected or not — enters the Retained frame inventory with its reason,
  marked workspace-internal and unpublished.
- `visual_page_id` is Part-local (ADR 0048) with Page appearance records for
  first appearance and every reappearance; no cross-Part correlation is
  asserted.
- The OCR resource confirmation pause separates detection from OCR: frame
  counts and conservative estimates are presented, OCR never starts without
  an explicit affirmative decision, a decline retains the page index as a
  `partial` report with zero visual facts, and the Visual-text
  resource-envelope pause forbids silent candidate, resolution, or batch
  changes. OCR execution is serialized with all other heavy models.
- OCR output enters only through the versioned Controlled offline OCR adapter
  and output projection; an incomplete or schema-invalid projection is
  `model_output_invalid`, raw output stays restricted audit evidence, and
  every OCR evidence item carries Part, PTS, `visual_page_id`, and confidence
  behind timing and coverage gates with structured rejections.
- Versioned OCR-item classification rules distinguish page text, speaker
  supplements, and background UI; Excluded visual items (danmaku, chat,
  watermarks, logos, prompts, platform shell) are retained as non-evidence;
  low confidence marks `classification_uncertain`; Suspected embedded-media
  intervals stay low-confidence with a recorded picture-only or
  picture-plus-audio basis.
- Retained visual-text reports feed text-analysis as an Optional visual-text
  context input through affected-Part re-analysis (ADR 0046): page changes
  are candidate boundary evidence, formal OCR items have exactly-once
  SemanticSegment ownership, cited page facts require classified page-text
  evidence, and the Host-read comment upgrade (ADR 0049) promotes a
  background-UI comment only on cross-modal comparison, recording page time
  and selection basis with citations to both the OCR item and supporting
  cues.
- The Phase 8 CLI contract is proved offline end to end; the machine-checkable
  exit gates and file inventory are recorded in
  [PHASE_08_INVENTORY.json](PHASE_08_INVENTORY.json) (30 confirmed summary
  gates mapping the phase plan's 退出门禁 plus the specification's derived
  gates).

## Recorded Deviations

- The phase plan's original "evaluate local OCR candidates" work item was
  rewritten, per the approved specification, into offline capability
  contracts and registry eligibility evaluation. Real local OCR evaluation
  (page text, digits, mixed Chinese/English) is deferred to an explicitly
  authorized model-prototype session before real-world testing.
- The RapidOCR research candidate deliberately leaves eligibility fields
  unpopulated: truthful values (asset hashes, measured resource envelopes)
  require the real model assets the offline boundary forbids acquiring.

## Final Verification

The final commands ran from the project root through the project `.venv`.

| Gate | Result |
| --- | --- |
| `pytest -q` | 700 passed in 2.51s |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 101 files already formatted |
| `mypy src` | Success: no issues found in 43 source files |
| Phase inventory summary | 30 exit-gate booleans, all `true` |

Closure note: per-ticket intermediate gate outputs were not retained by the
implementing sessions; verification is anchored to the current-head run
above. Ticket status bookkeeping (all nine files, including acceptance
checkboxes) and the `project-state.json` `completed` transition were
performed at closure on the maintainer's explicit instruction, after the
inventory had recorded all exit gates as confirmed.

Verification used only project-owned synthetic fixtures and the Controlled
offline OCR adapter. It did not download, install, or invoke a model, extract
a frame from user media, access user media or a network, invoke a paid API,
write `outputs/`, or mark the project `production_validated`.
