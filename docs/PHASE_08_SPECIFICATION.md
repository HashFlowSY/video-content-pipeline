# Phase 8 Specification: Optional Visual-Text

## Domain routing

Begin with the [Context Map](../CONTEXT-MAP.md), then read [Media
Foundation](contexts/media-foundation/CONTEXT.md), [Source
Planning](contexts/source-planning/CONTEXT.md), and
[Visual-Text](contexts/visual-text/CONTEXT.md). [Audio
Analysis](contexts/audio-analysis/CONTEXT.md) is an optional informing
context (embedded-media suspicion only). [Text
Analysis](contexts/text-analysis/CONTEXT.md) participates through
affected-Part re-analysis and owns the Host-read comment upgrade (ADR 0049).
Visual-text does not depend on subtitles (ADR 0047).

## Status

`approved_for_implementation_planning` (grilling consensus approved
2026-08-15). Verification will be offline only: no model is downloaded,
installed, or invoked, no user media or network is accessed, no frame is
extracted from user media, `outputs/` is not written, and the phase will claim
no domain quality, `model_audited`, `human_verified`, real-world testing, or
production validation.

## Objective

From a revalidated confirmed RunPlan and an explicit user-scoped enablement,
create immutable visual-text evidence: deterministic page-change detection and
adaptive frame sampling, Part-local page indices with appearance records, and
gated OCR evidence items — all classified deterministically, fully retained,
and feeding text-analysis through affected-Part re-analysis. OCR output is
candidate evidence and never fact authority; visual-text produces evidence and
never cross-modal facts.

## Public Contract

```text
vcp visual-text <plan-id>
  (--all | --part <part-id> | --range <part-id>:<start>-<end>)... [--json]
vcp resume-visual-text <report-id> --decision <decision> [--json]
```

- `vcp visual-text` is the sole start command (Explicit visual-text command
  boundary). Scope is always explicit: `--all` selects the whole collection,
  and an invocation without any scope argument is an error — never an implied
  full sweep. `--range` uses the Phase 7 semantics: seconds on the
  Part-relative clock. The command creates a new Immutable visual-text
  workspace and `visual-report.json`.
- The pipeline has two internal gates: deterministic detection and sampling
  run first; the attempt then stops at the OCR resource confirmation pause
  (selected frame counts, estimated time, memory, and disk) and OCR executes
  only after `resume-visual-text` records an explicit affirmative decision.
  A declined decision retains the page index with zero visual facts.
- `resume-visual-text` must name a retained report and an explicit user
  decision; it never auto-resumes or silently changes identity-bound inputs.
- The authoritative reports are JSON. Readable artifacts are deterministically
  rendered, remain in the workspace, and are not published until the future,
  separately authorized publication stage. Frames are Unpublished internal
  frames and never appear in formal outputs.
- A run that never enables visual-text records `ocr=not_requested`
  (OCR-not-requested record): no frame extraction, no detection, no visual
  facts; picture-only intervals are recorded as unanalyzed visual content.

## Input And Revalidation Contract

Before an attempt may execute, it must exactly revalidate:

- confirmed RunPlan and SourceArtifact hashes;
- every user-named Part and range against retained Part identities and actual
  stream coverage;
- the retained Audio analysis report hash and bound input identities when one
  is supplied (optional; used only by embedded-media suspicion);
- Controlled offline OCR adapter identity, or eligible real-model identity;
  and
- versioned detection, sampling, and classification rule versions.

Any drift blocks the attempt and requires a new attempt.

## Capability And Eligibility Contract

- The Visual-text capability contract is provider-neutral and contains exactly
  one model capability: `ocr_primary` (ADR 0036 precedent). Detection,
  sampling, and classification are deterministic and never model capabilities
  (ADR 0047).
- RapidOCR (or a compatible free local candidate) is registered in
  `models/registry.json` as a research candidate with license, source,
  revision, and eligibility fields — metadata only. Real local OCR evaluation
  (page text, digits, mixed Chinese/English) is a recorded deviation from the
  phase plan's original work item 3: it is deferred to an explicitly
  authorized model-prototype session before real-world testing.
- General vision models (Qwen3-VL or any VLM) are entirely outside this
  contract — not a required dependency, not an optional capability slot.
  Segment-level visual semantics is a future, separately decided stage; see
  [the capability assessment](../research/qwen3-vl-8b-capability-assessment.md).
- No eligible capability yields an immutable
  `model_acquisition_required` report with a retained page index and no OCR
  evidence.

## Detection And Sampling Contract

- Frame extraction uses only the pinned ffmpeg toolchain (ADR 0001).
- Deterministic page-change detection and Versioned frame-sampling rules
  select frames from stability, Text-value proxy metrics (edge density,
  region-scoped frame difference — never detection-stage OCR), and page
  changes. The same input and rule versions always select the same frames.
- Every extracted frame — selected for OCR or not — enters the Retained frame
  inventory with the reason it was or was not selected. Deletion requires
  explicit user cleanup authorization; nothing is discarded pipeline-side.
- Disk and time estimates for extraction and OCR follow the Phase 3
  phase-bounded estimate and disk-headroom patterns.

## Visual Evidence Contract

- A Visual page is Part-local (ADR 0048): `visual_page_id` is scoped to one
  Part, with Page appearance records for first appearance and every
  reappearance. Cross-Part correlation belongs to consumers.
- OCR output enters only through a versioned output projection; an incomplete
  or schema-invalid projection is `model_output_invalid` and invalidates the
  attempt. Raw output is restricted local audit evidence.
- Every OCR evidence item carries Part, PTS, `visual_page_id`, and
  confidence. Items violating timing or coverage gates are rejected with
  reasons, never silently repaired.
- Versioned OCR-item classification rules deterministically classify items as
  page text, speaker supplement, or background UI. Excluded visual items
  (danmaku, high-speed chat, unrelated watermarks, logos, follow/gift
  prompts, repeated platform shell) are retained in the workspace and marked
  non-evidence. Low-confidence classifications are marked
  `classification_uncertain`, never forced.
- Visual-text never performs the Host-read comment upgrade; that cross-modal
  fact decision is owned by text-analysis (ADR 0049).
- Suspected embedded-media intervals are low-confidence markers only, never
  confirmed facts. With a supplied audio-analysis report the basis is
  picture-plus-audio; without one, picture-only marking is permitted and the
  provenance must state the basis explicitly.
- No clothing, environment, object, or action description; no general-VLM
  visual summaries.

## Affected-Part Re-Analysis Contract

- After a visual-text report is retained, semantic recomputation follows
  ADR 0046: a new immutable text-analysis attempt regenerates only affected
  Parts; unaffected Parts are Carried-forward analysis Parts; chapters and
  the collection summary are recomputed from the combined set.
- The retained visual-text report is an Optional visual-text context input to
  text-analysis: page changes become candidate boundary evidence, each OCR
  evidence item is owned by exactly one formal semantic segment, and cited
  page facts appear only when OCR evidence exists.
- The Host-read comment upgrade runs here: a background-UI comment becomes
  formal evidence only on cross-modal comparison with cue text, recorded with
  page time and selection basis.
- Re-analysis never overwrites prior reports and obeys all Phase 6 contracts
  unchanged.

## Status, Resource, And Recovery Contract

- `complete`: every selected Part has a gated page index and, after an
  affirmative OCR decision, gated OCR evidence, with nothing pending.
- `partial`: some Parts failed gates, the user declined OCR after a page index
  was retained, or a decision pause exists after validated evidence was
  retained.
- `failed`: revalidation, whole projection, or execution fails before any
  gated evidence exists.
- Pauses are immutable recorded states: OCR resource confirmation pause,
  Visual-text resource-envelope pause (conservative estimate over the
  approved envelope; never silently alters candidate, resolution, or batch),
  and `model_acquisition_required`.
- Serialized OCR execution: OCR completes its evidence record and release
  before any other heavy model loads, sharing the single heavy-task queue.
- No automatic retry. A retry is an explicitly user-started new attempt and
  never overwrites prior evidence.

## Reporting And Language

The JSON report contains capability states, rule versions, page indices,
appearance records, gate and classification decisions, excluded items,
suspected embedded-media intervals with their evidential basis, artifact
hashes, statuses, limitations, and diagnostic pointers. OCR text keeps its
source language, including mixed Chinese/English; readable report prose
defaults to Chinese (Phase 6 report language boundary).

## Offline Test Contract

Tests use only hash-pinned synthetic fixtures and Controlled offline OCR
adapters, asserting deterministic contract properties: explicit-scope
enforcement (unscoped invocation errors), revalidation drift, detection and
sampling determinism (same input and rule versions, same selection), full
frame retention with selection reasons, Part-local page identity and
appearance records, projection validity, per-item Part/PTS/page/confidence
completeness, classification rules and `classification_uncertain`, excluded
items retained as non-evidence, embedded-media basis provenance
(picture-plus-audio versus picture-only), the OCR confirmation pause and both
resume decisions, the resource-envelope pause, `model_acquisition_required`,
affected-Part selection and carry-forward provenance, the host-read comment
upgrade record, immutability, statuses, hashes, and no-side-effect guarantees
(`model_execution`, `model_acquisition`, `network_access`, `frame_extraction`,
`outputs_publication` all `not_attempted`).

The exit gates map the phase plan's 退出门禁 list plus the derived gates from
this specification: default runs extract no frames; formal outputs contain no
screenshots; no visual facts exist when OCR is off; every OCR result carries
Part, PTS, page ID, and confidence; no general vision model is a required
dependency; scope is always explicit; all frames are inventoried; page
identity is Part-local; all rules are versioned.

## Out Of Scope

- Model acquisition, installation, download, runtime setup, or real OCR
  execution — including the phase plan's original "evaluate local OCR
  candidates" work item, deferred as a recorded deviation (see Capability And
  Eligibility Contract).
- Real OCR accuracy, language coverage, or memory measurement; CER on real
  frames.
- General-VLM capabilities, visual summaries, or segment-level visual
  semantics.
- Frame extraction from user media (offline verification uses synthetic
  fixtures only).
- Cross-Part page correlation, translation, and speaker-name inference (OCR
  names remain name candidates only, per the phase plan's speaker rules).
- RunBundle publication, `outputs/` writes, cleanup, deletion, or a
  `production_validated` state.

## Related Decisions

This specification uses the visual-text vocabulary in the
[Context Map](../CONTEXT-MAP.md) and
[Visual-Text Context](contexts/visual-text/CONTEXT.md) and is governed in
particular by
[ADR 0047](adr/0047-introduce-a-visual-text-context-with-deterministic-detection-and-ocr-only-model-capability.md),
[ADR 0048](adr/0048-keep-visual-page-identity-part-local.md), and
[ADR 0049](adr/0049-separate-visual-evidence-classification-from-fact-upgrade.md),
with affected-Part re-analysis from
[ADR 0046](adr/0046-recompute-affected-parts-with-carried-forward-analysis.md)
and the offline verification boundary inherited from
[ADR 0036](adr/0036-keep-phase-5-model-capabilities-provider-neutral.md) and
[ADR 0037](adr/0037-verify-phase-5-with-controlled-offline-adapters.md).
