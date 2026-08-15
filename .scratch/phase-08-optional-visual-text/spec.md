# Phase 8: Optional Visual-Text

## Domain routing

Begin with the [Context Map](../../CONTEXT-MAP.md), then read [Media
Foundation](../../docs/contexts/media-foundation/CONTEXT.md), [Source
Planning](../../docs/contexts/source-planning/CONTEXT.md), and
[Visual-Text](../../docs/contexts/visual-text/CONTEXT.md). [Audio
Analysis](../../docs/contexts/audio-analysis/CONTEXT.md) is optional
(embedded-media suspicion only). [Text
Analysis](../../docs/contexts/text-analysis/CONTEXT.md) participates through
Affected-Part re-analysis and owns the Host-read comment upgrade (ADR 0049).
Visual-text does not depend on subtitles (ADR 0047).

Type: enhancement
Status: ready-for-agent
Labels: ready-for-agent
Phase: 8
Published: 2026-08-15

## Problem Statement

After Phase 7, everything the pipeline can cite comes from subtitle tracks or
ASR. For presentations, screen demos, and interviews with question cards, the
most information-dense evidence is on screen: slide text, document text,
participant nicknames, questions being answered. Today those are invisible —
and a naive fix (screenshot everything, run a vision model) would violate the
project's guarantees: frames of user media are sensitive, general VLM output
is uncited narrative, and any automatic full-collection sweep breaks the
principle that heavy work happens only on explicit demand. Visual evidence
must enter the system opt-in, deterministic where possible, fully retained,
never published as frames, and never promoted to fact by the visual side
itself.

## Solution

One explicit command boundary. `vcp visual-text` runs on an explicitly given
scope (`--all`, Parts, or Part-relative second ranges; no scope is an error),
performs deterministic page-change detection and adaptive frame sampling with
pinned ffmpeg and versioned rules, then pauses at an OCR resource confirmation
showing selected frame counts and estimates. Only an explicit
`resume-visual-text` decision runs OCR — the single model capability
(`ocr_primary`) — producing OCR evidence items with Part, PTS, Part-local
`visual_page_id`, and confidence, deterministically classified (page text,
speaker supplement, background UI) with platform noise retained as
non-evidence. A retained visual-text report feeds text-analysis through
affected-Part re-analysis, where page changes inform boundaries, OCR items
gain exactly-once segment ownership, and host-read comments may be upgraded to
formal evidence with recorded basis. All contracts are proven offline with
Controlled offline OCR adapters and hash-pinned synthetic fixtures; no model
is downloaded or executed and no frame of user media is extracted in this
phase.

## User Stories

1. As a pipeline user, I want visual-text off by default with no frame
   extraction, so that my media is never sampled without my asking.
2. As a pipeline user, I want to enable visual-text for the whole collection,
   named Parts, or time ranges — explicitly, with no default scope — so that
   an accidental unscoped run is impossible.
3. As a pipeline user, I want detection and sampling to run first and the
   attempt to pause with frame counts and resource estimates before OCR, so
   that I approve the heavy step knowingly.
4. As a pipeline user, I want to decline OCR at the pause and still keep the
   page index, so that a cheap structural result is never held hostage by the
   expensive step.
5. As a pipeline user, I want every OCR result to carry Part, PTS, page ID,
   and confidence, so that any visual fact can be traced to a exact place.
6. As a pipeline user, I want slide and document text distinguished from
   speaker supplements and background UI, so that page content is not polluted
   by chrome.
7. As a pipeline user, I want danmaku, watermarks, logos, and gift prompts
   excluded from formal evidence but retained and labeled, so that noise
   filtering is auditable rather than silent.
8. As a pipeline user, I want the same input and rule versions to always
   select the same frames and pages, so that reruns are reproducible.
9. As a pipeline user, I want page identity local to each Part, so that
   re-running one Part can never invalidate another Part's visual evidence.
10. As a pipeline user, I want first-appearance and reappearance records per
    page, so that "the speaker returned to this slide" is visible evidence.
11. As a pipeline user, I want suspected embedded-video intervals marked at
    low confidence with their evidential basis (picture-plus-audio or
    picture-only) recorded, so that a guess is never dressed as a fact.
12. As a pipeline user, I want no clothing, environment, object, or action
    descriptions and no VLM visual summaries, so that visual output stays
    within cited on-screen text.
13. As a pipeline user, I want formal outputs to contain no screenshots and
    frames to stay workspace-internal, so that publishing text never leaks
    frames.
14. As a pipeline user, I want every extracted frame kept in the inventory
    with its selection reason, deletable only with my explicit cleanup
    authorization, so that nothing is silently discarded.
15. As a pipeline user, I want chapters and summaries recomputed through
    affected-Part re-analysis after visual evidence lands, so that reading
    entry points reflect it without re-running unaffected Parts.
16. As a pipeline user, I want a host-selected or host-read comment upgraded
    to formal evidence with page time and selection basis recorded, so that
    deliberate on-screen quotes count without opening the door to all chat.
17. As a pipeline user, I want each attempt in a new immutable workspace with
    resume only by report ID plus explicit decision, so that no attempt
    overwrites evidence or continues without my say-so.
18. As a pipeline user, I want a run exceeding the approved resource envelope
    to pause rather than silently change candidate, resolution, or batch, so
    that quality trade-offs stay mine.
19. As an auditor, I want detection, sampling, and classification rule
    versions in provenance, so that every selection decision replays.
20. As an auditor, I want raw OCR output retained as restricted diagnostics
    and entering the system only through a versioned projection, so that
    malformed output can never leak into evidence.
21. As an auditor, I want the offline verification report to assert that no
    model execution, model acquisition, network access, frame extraction, or
    output publication was attempted, so that the phase's claims are
    machine-checkable.
22. As a future real-model integrator, I want a provider-neutral
    `ocr_primary` capability with registry-based eligibility and RapidOCR
    registered as a metadata-only research candidate, so that swapping in a
    real OCR engine changes configuration, not semantics.
23. As a future real-model integrator, I want general vision models entirely
    absent from this contract — not even an optional slot — so that the "no
    required VLM" exit gate stays unambiguous.
24. As a pipeline user with no eligible OCR capability, I want an immutable
    `model_acquisition_required` report with the page index retained, so that
    acquisition remains a separately authorized decision.

## Implementation Decisions

- A new `visual-text` bounded context owns visual evidence (ADR 0047). Direct
  dependency `source-planning` (transitively `media-foundation`);
  `audio-analysis` optional (embedded-media suspicion only); no `subtitles`
  dependency. Vocabulary lives in the Visual-Text Context; this spec uses it
  throughout.
- One start command with explicit scope and a resume counterpart, mirroring
  the Phase 5/6/7 CLI pattern: `vcp visual-text <plan-id> (--all | --part |
  --range <part-id>:<start>-<end>)... [--json]` and `vcp resume-visual-text
  <report-id> --decision <decision> [--json]`. `--range` reuses the Phase 7
  Part-relative seconds semantics. Unscoped invocation errors.
- Two internal gates: deterministic detection/sampling, then the OCR resource
  confirmation pause; OCR runs only on an explicit affirmative decision, and
  a decline retains the page index with zero visual facts.
- Detection, sampling, and classification are deterministic: pinned ffmpeg
  (ADR 0001) plus versioned rules; text-region change is approximated by
  Text-value proxy metrics, never detection-stage OCR. `ocr_primary` is the
  only model capability.
- Page identity is Part-local (ADR 0048) with appearance records; cross-Part
  correlation is consumer-side.
- OCR output enters via a versioned projection (`model_output_invalid` fails
  the attempt); items carry Part/PTS/page/confidence and pass timing and
  coverage gates with structured rejection reasons.
- Versioned OCR-item classification rules produce page text / speaker
  supplement / background UI; Excluded visual items are retained non-evidence;
  low confidence marks `classification_uncertain` (never forced).
- The Host-read comment upgrade belongs to text-analysis (ADR 0049), executed
  during affected-Part re-analysis (ADR 0046), where the visual-text report is
  an optional evidence input, page changes are boundary evidence, and each OCR
  item is owned by exactly one formal segment.
- Suspected embedded-media intervals are low-confidence only; picture-only
  basis is permitted without an audio report and the basis is always recorded.
- All frames are retained in the inventory with selection reasons; deletion
  needs explicit user cleanup authorization; frames are never published.
- RapidOCR (or compatible free local candidate) registered metadata-only;
  real OCR evaluation is a recorded deviation deferred to an authorized
  model-prototype session. VLMs are entirely out of contract.
- Pauses, statuses (`complete`/`partial`/`failed`), serialized heavy
  execution, no-auto-retry, immutable workspaces, and report language follow
  the Phase 7 contract shapes unchanged.

## Testing Decisions

- A good test asserts externally observable contract behavior at the CLI
  boundary — report JSON, statuses, pauses, guarantees, workspace
  immutability, artifact hashes — never internal call sequences.
- Primary seam: the two CLI commands, tested end-to-end with hash-pinned
  synthetic fixtures and Controlled offline OCR adapters, following the
  Phase 5–7 CLI contract integration tests.
- Second seam (data, existing pattern): the controlled-adapter descriptor
  with fixture hashes, built inline in a temporary project root exactly as
  the Phase 6/7 adapter tests do.
- Unit seams only for the deterministic core: page-change detection, sampling
  rules, page fingerprinting and appearance records, projection validation,
  item gates, classification rules, embedded-media basis provenance, and
  affected-Part selection are pure functions tested directly.
- Every offline run asserts the guarantees block: `model_execution`,
  `model_acquisition`, `network_access`, `frame_extraction`,
  `outputs_publication` all `not_attempted`.
- Minimum scenario coverage: unscoped-invocation error, each scope form,
  revalidation drift, sampling determinism, full frame retention with
  reasons, Part-local page identity and reappearance, projection invalidity,
  item gate rejections, every classification class plus
  `classification_uncertain` and excluded items, both OCR-pause decisions,
  resource-envelope pause, `model_acquisition_required` with retained page
  index, picture-only versus picture-plus-audio suspicion basis, host-read
  comment upgrade record, affected-Part selection and carry-forward,
  immutability, mixed Chinese/English OCR text, and multi-Part collections.
- The phase inventory records machine-checkable `*_confirmed` exit-gate
  booleans mapping the phase plan's 退出门禁 list plus the derived gates
  (explicit scope, full frame inventory, Part-local page identity, versioned
  rules, zero VLM dependency).

## Out of Scope

- Model acquisition, installation, download, runtime setup, or any real OCR
  execution — including the phase plan's original "evaluate local OCR
  candidates" work item, deferred as a recorded deviation.
- Real OCR accuracy, language coverage, memory measurement, or CER on real
  frames; frame extraction from user media.
- General-VLM capabilities, visual summaries, segment-level visual semantics,
  and cross-Part page correlation.
- Speaker-name inference (OCR names stay name candidates only) and
  translation.
- RunBundle publication, `outputs/` writes, cleanup, deletion, `vcp improve`
  orchestration (Phase 9), and any `production_validated` claim.

## Further Notes

The authoritative phase contract is
[docs/PHASE_08_SPECIFICATION.md](../../docs/PHASE_08_SPECIFICATION.md)
(`approved_for_implementation_planning`, grilling consensus approved
2026-08-15). Atomic implementation tickets are `issues/01`–`issues/09` in
this directory with dependencies noted per ticket (breakdown approved
2026-08-15); `issues/09` is the closing offline exit-gate inventory.
Governing decisions:
ADR 0047–0049, with affected-Part re-analysis from ADR 0046 and the offline
boundary inherited from ADR 0036–0037. Vocabulary owners: the Visual-Text
Context; Optional visual-text context and Host-read comment upgrade belong to
Text Analysis; the Phase 8 offline visual-verification boundary belongs to
Media Foundation.
