# Phase 6 Specification: Evidence-Bound Semantic Segmentation and Summaries

## Domain routing

Begin with the [Context Map](../CONTEXT-MAP.md), then read [Media
Foundation](contexts/media-foundation/CONTEXT.md), [Source
Planning](contexts/source-planning/CONTEXT.md), [Subtitles](contexts/subtitles/CONTEXT.md),
and [Text Analysis](contexts/text-analysis/CONTEXT.md). [Audio
Analysis](contexts/audio-analysis/CONTEXT.md) is an optional context: its
validated evidence may inform organization and limitations, but it is not
required for basic subtitle-derived claims.

## Status

`completed_and_verified_offline`. Implementation and the Phase 6 CLI contract
are complete. Verification remained restricted to offline engineering checks
with retained synthetic structured-text fixtures and a Controlled offline text
adapter: no model was downloaded, installed, or invoked, no user media or
network was accessed, `outputs/` was not written, and the phase claims no
domain quality, `model_audited`, `human_verified`, real-world testing, or
production validation.

## Objective

From a revalidated confirmed RunPlan and retained subtitle evidence, create an
immutable, machine-readable Text analysis report containing auditable semantic
segments, detailed content, optional question-and-answer structure, chapters,
and collection summaries. Formal facts remain source-bound: model output is a
candidate and never evidence authority.

## Public Contract

```text
vcp analyze-text <plan-id> --subtitle-report <report-id>
  [--audio-report <report-id>] [--json]
vcp resume-text-analysis <report-id> --decision <decision> [--json]
```

- `vcp analyze-text` is the sole Phase 6 start command. It creates a new
  immutable text-analysis workspace and `text-analysis-report.json`.
- `vcp resume-text-analysis` must name a retained report and an explicit
  user decision. It never auto-resumes or silently changes identity-bound
  inputs.
- The authoritative report is JSON. Readable Markdown is deterministically
  rendered from verified JSON, remains in the workspace, and is not published
  until the future, separately authorized publication stage.

## Input And Revalidation Contract

Before an attempt may read text-model input, it must exactly revalidate:

- confirmed RunPlan and SourceArtifact hashes;
- retained Subtitle candidate report and every selected Primary subtitle track;
- PresentationCue and NormalizedCue rules and hashes;
- text prompt template, output schema, and evidence-rule versions and hashes;
- Controlled offline text adapter identity; and
- optional Audio analysis report hash and its bound input identities.

Any drift blocks the attempt and requires a new attempt. Audio analysis is
optional: its validated structural and risk evidence may inform organization
and limitations, never independently support a factual claim. Its absence must
be recorded as `audio_analysis=not_available` and
`audio_completeness=not_verified`.

## Evidence And Segmentation Contract

- The adapter may read PresentationCues to avoid proven rolling-display
  duplication. Every formal factual item and every semantic-segment title must
  cite one or more NormalizedCue IDs; a time range or segment ID alone is not
  adequate factual provenance.
- A PresentationCue belongs to exactly one SemanticSegment. A NormalizedCue may
  support multiple independently cited factual items.
- Formal segment boundaries occur only between PresentationCues. A model may
  propose candidate cue-pair boundaries, but deterministic adjudication rejects
  out-of-range, duplicate, empty, and coverage-breaking proposals.
- The adjudicator never invents a theme boundary. If no valid proposal remains,
  it uses a conservative single SemanticSegment for that Part, retains every
  cue exactly once, and marks the report `partial`.
- Technical processing blocks may overlap only as context transport. They are
  not formal segments, chapters, or citation ranges. Cross-block candidates are
  deduplicated by complete cue identity before one adjudicator processes them.
- SemanticSegments and chapters cannot cross a Part boundary. A chapter is an
  optional consecutive sequence of verified segments from one Part. A collection
  summary may cite segments from multiple Parts while retaining Part identity.
- No fixed duration, cue-count, or token-count target defines a segment or
  chapter.

## Structured Content Contract

- Segment titles, detailed content, numeric values, entities, examples,
  conditions, caveats, questions, answers, unresolved items, chapter entries,
  and collection-summary entries are independently validated.
- A missing, invalid, or unsupported cue citation removes only that item as
  `unsupported_generated_claim`; raw output and rejection evidence remain
  diagnostic. A valid projection may therefore yield a partial set of verified
  content.
- An incomplete or schema-invalid Text-model output projection is
  `model_output_invalid` and invalidates the complete attempt; defaults,
  guesses, and partial projections cannot become formal content.
- Q&A fields exist only where cited subtitle text establishes the relationship.
  Narrative, ambiguous dialogue, or diarization alone cannot create Q&A.
- People and roles require cited self-identification/naming in subtitles or
  explicit user metadata. Anonymous Part-local speaker labels never establish
  identity or social role.
- No unit conversion, numeric inference, entity disambiguation, external fact,
  or external knowledge is allowed.
- Contradictory source claims remain separately attributed and cited; the
  pipeline never chooses truth. An unresolved question must be explicitly
  raised in cited subtitles and lack an answer within validated evidence.
- A Part without a valid Primary subtitle track is
  `text_content=unavailable`: retain its CollectionVirtualTime range and
  reason, emit no invented segment or facts, and declare the omitted range in
  collection aggregation.

## Provenance, Artifacts, And Diagnostics

Each attempt writes an immutable workspace that retains input bindings, prompt
identity, adapter identity, execution-resource measurement, raw output,
versioned Text-model output projection, validation results, diagnostics, JSON
report, and rendered Markdown report. The raw output is restricted local audit
evidence, excluded from formal reports and default publication; any
export requires separate explicit authorization.

The Controlled offline text adapter records implementation version, fixed input
and output fixture hashes, prompt hash, sampling-configuration hash, and
projection-schema hash. It is not a model asset and cannot earn a real-model
quality qualification. A future real model requires a new attempt whenever its
asset, revision, quantization, backend, prompt, sampling, projection schema, or
evidence rules change.

## Status, Resource, And Recovery Contract

- `complete`: every Part with a valid Primary subtitle track has at least one
  verified SemanticSegment and no decision is pending.
- `partial`: one or more subtitle-unavailable Parts, conservative fallback,
  or a later decision pause exists after validated content was retained.
- `failed`: revalidation, whole projection, or execution fails before any
  verified segment exists.
- Rejected individual content does not alone lower report status.
- No adapter or model available creates an immutable
  `controlled_adapter_unavailable` or `model_acquisition_required` report
  with no SemanticSegments.
- No controlled or real generation automatically retries. A retry is an
  explicitly user-started new attempt and never overwrites prior evidence.
- A future real model obeys the global one-large-model rule: Phase 5 release
  evidence must be complete before loading it, and it must record its resource
  measurement and unload evidence. A conservative estimate over 24 GiB writes
  `resource_envelope_exceeded` and pauses; it cannot silently alter model,
  quantization, context, or sampling.

## Reporting And Language

The JSON report contains segments, chapters, collection aggregation, all cue
and segment citations, limitations, statuses, artifact hashes, and diagnostic
pointers. The readable report defaults to Chinese for titles and prose; cited
source text and excerpts remain in their original language, including mixed
Chinese/English. Translation is out of scope and must later be a separate
artifact.

Readable output summarizes unsupported-item counts, unavailable Parts,
limitations, and pending decisions. It does not include raw generated text or
item-level validation dumps. Every subtitle-derived report front page, Part
summary, chapter summary, and collection summary declares
`audio_completeness=not_verified`, regardless of alignment or VAD evidence.

## Offline Test Contract

Tests use only hash-pinned synthetic structured-text fixtures and controlled
adapters. They assert deterministic contract properties rather than prose
quality: schema validity, citation and fixture-oracle relationships, cue
existence, exactly-once ownership, Part boundaries, statuses, hashes,
immutability, diagnostics, and no-side-effect guarantees.

Minimum coverage includes mixed Chinese/English, rolling repetitions, legal
cue overlap, no-subtitle Parts, multi-Part collections, valid/invalid citations,
invalid complete projections, unsupported items, Q&A/person/number/
contradiction/unresolved structures, technical-block crossings, fallback,
revalidation drift, unavailable adapters, decision pauses, resource pauses, and
append-only synthetic human-review records. The latter proves only record shape;
this phase emits no `human_verified` result.

## Out Of Scope

- Model acquisition, installation, download, runtime setup, or real-model call.
- User media, external network, browser state, credentials, cookies, paid APIs,
  or external knowledge retrieval.
- ASR, OCR, visual-text, translation, factual correction, semantic truth
  adjudication, real human review, and real-world quality scoring.
- RunBundle publication, `outputs/` writes, cleanup, deletion, or a
  `production_validated` state.

## Related Decisions

This specification uses the Phase 6 vocabulary in the [Context Map](../CONTEXT-MAP.md)
and [Text Analysis Context](contexts/text-analysis/CONTEXT.md) and is
governed in particular by
[ADR 0040](adr/0040-require-cue-level-evidence-for-phase-6-facts.md) and
[ADR 0041](adr/0041-keep-phase-6-text-analysis-in-immutable-workspaces.md).
