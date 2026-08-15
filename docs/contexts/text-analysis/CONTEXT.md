# Text Analysis Context

This Context owns cue-bound semantic organization and text-derived reporting.
It depends on media, source-planning, and subtitle evidence. Audio analysis is
an optional informing context: its absence never authorizes invented facts.
Operational mechanics and exact thresholds remain in the linked specifications
and ADRs.

Relevant global decisions include [ADR 0040](../../adr/0040-require-cue-level-evidence-for-phase-6-facts.md),
[ADR 0041](../../adr/0041-keep-phase-6-text-analysis-in-immutable-workspaces.md),
[ADR 0042](../../adr/0042-use-context-map-and-domain-owned-glossaries.md), and
[ADR 0046](../../adr/0046-recompute-affected-parts-with-carried-forward-analysis.md).

## Language

**SemanticSegment**:
A non-empty Part-local group of PresentationCues with exactly-once cue ownership
and cited source evidence.
_Avoid_: generated paragraph

**Chapter**:
An optional consecutive sequence of verified SemanticSegments from one Part.
_Avoid_: cross-Part chapter

**Phase 6 textual fact source**:
The subtitle text cited by a factual claim; audio evidence may provide context
but does not replace the text source.
_Avoid_: model knowledge

**Phase 6 evidence input and citation basis**:
PresentationCues provide semantic content while formal claims cite
NormalizedCue identities.
_Avoid_: timestamp-only evidence

**Cue-level factual citation**:
An explicit citation to one or more NormalizedCue identities supporting a fact.
_Avoid_: segment-only citation

**Semantic-segment cue ownership**:
The rule that every PresentationCue belongs to exactly one formal
SemanticSegment.
_Avoid_: duplicated segment membership

**Cue-bound semantic boundary**:
A segment boundary selected only between existing PresentationCues.
_Avoid_: invented time split

**Unsupported generated claim**:
A proposed factual item whose cited cues are absent, invalid, or insufficient;
it remains diagnostic and is excluded from formal content.
_Avoid_: plausible uncited fact

**Verified segment-derived summary**:
A chapter or collection summary whose entries cite verified segment identities.
_Avoid_: uncited summary

**Cue-supported segment title**:
A concise segment title with an explicit NormalizedCue citation.
_Avoid_: topic inferred without evidence

**Phase 6 immutable text-analysis workspace**:
The immutable evidence set associated with one text-analysis attempt.
_Avoid_: mutable report folder

**Explicit text-analysis command boundary**:
The domain boundary that starts or resumes one text-analysis attempt from
retained evidence.
_Avoid_: background generation

**Text-analysis input revalidation**:
The comparison of all bound input identities before a text-analysis attempt
proceeds.
_Avoid_: warning-only drift

**Optional audio-analysis context**:
Validated audio structure and risk evidence that may inform organization and
limitations but cannot independently support a subtitle-derived fact.
_Avoid_: required audio transcript

**Part-bounded semantic aggregation**:
The rule that segments and chapters stay within one Part while collection
summaries may cite multiple Parts with identity retained.
_Avoid_: cross-Part segment

**Text-model output projection**:
The versioned structured interpretation of a retained adapter or model output.
_Avoid_: raw output as fact

**Deterministically adjudicated semantic boundary**:
A model-proposed cue boundary accepted by stable rules after range, ownership,
and coverage checks.
_Avoid_: automatic theme invention

**Two-level text-analysis failure handling**:
An invalid whole projection fails the attempt; invalid individual items remain
diagnostics while independently verified items continue.
_Avoid_: schema guessing

**Cue-supported question-and-answer structure**:
Optional Q&A fields emitted only when cited subtitle text establishes the
relationship.
_Avoid_: diarization-created question

**Cue-supported person and role**:
A person or role supported by cited self-identification, naming, or explicit
user metadata; anonymous speaker labels do not establish identity.
_Avoid_: voice-inferred role

**Cue-supported structured detail**:
A numeric value, entity, example, condition, caveat, or unresolved item with a
NormalizedCue citation and no external inference.
_Avoid_: enriched entity

**Cue-preserved source contradiction**:
Incompatible cited source claims retained separately without choosing which is
true.
_Avoid_: model-resolved contradiction

**Cue-supported unresolved question**:
A question explicitly raised in cited subtitles with no answer in the validated
evidence scope.
_Avoid_: model-invented follow-up

**Subtitle-unavailable text Part**:
A Part without a valid Primary subtitle track that retains its span and emits no
invented segment or fact.
_Avoid_: empty semantic segment

**Persistent subtitle audio-completeness limitation**:
The required `audio_completeness=not_verified` notice on subtitle-derived
reports, regardless of optional audio evidence.
_Avoid_: alignment-proved completeness

**Controlled offline text adapter**:
The fixed substitute capability used to verify text-analysis contracts without
claiming real-world model quality.
_Avoid_: synthetic model qualification

**Text-model identity invalidation**:
The state in which a changed model or generation-rule identity makes prior
text-analysis evidence non-reusable.
_Avoid_: inherited qualification

**Text-analysis unavailable result and offline exit gate**:
The recorded outcome when no eligible text-analysis capability is available,
together with the boundary that ends the current verification scope.
_Avoid_: empty content success

**Phase 6 report language boundary**:
Generated report prose defaults to Chinese while cited source text retains its
original language.
_Avoid_: translated source evidence

**Technical text-processing block**:
A context-fitting cue collection that is not itself a segment, chapter, or
citation range.
_Avoid_: block-as-segment

**Length-unconstrained semantic segment**:
A cue-bound segment with no fixed duration, token, or cue-count target.
_Avoid_: fixed-window segment

**Part-local chapter aggregation**:
An optional chapter assembled from consecutive verified segments in one Part.
_Avoid_: forced chapter count

**Text-analysis decision pause boundary**:
A domain state in which a pending choice prevents a determinate analysis result.
_Avoid_: pause on every rejection

**Serialized text-model execution**:
A sequencing relationship in which one text-model analysis completes its
evidence record before another begins.
_Avoid_: concurrent large-model load

**Text-analysis resource-envelope pause**:
A domain state in which a planned analysis exceeds its approved resource
envelope.
_Avoid_: silent context reduction

**Text-generation attempt provenance**:
The identity record linking a generated text candidate to its inputs and
generation rules.
_Avoid_: unrecorded variance

**No automatic text-generation retry**:
The distinction between one failed generation attempt and any later explicitly
requested attempt.
_Avoid_: transparent retry

**Text analysis report**:
The authoritative immutable machine-readable record of segments, chapters,
citations, statuses, limitations, and diagnostics.
_Avoid_: Markdown authority

**Text-analysis diagnostic visibility**:
The report-facing view of diagnostics that locates retained evidence without
treating diagnostics as formal content.
_Avoid_: raw generated claim

**Restricted raw text-model diagnostic**:
Raw model or adapter output retained as audit evidence but excluded from formal
content.
_Avoid_: published raw generation

**Versioned text prompt template**:
The versioned input template that determines the text presented for one
generation attempt.
_Avoid_: prompt hash alone

**Versioned Phase 6 generation rules**:
The versioned rules governing text generation and evidence interpretation for
one attempt.
_Avoid_: retroactive rule upgrade

**Versioned text-report renderer**:
The versioned presentation definition that turns a verified text report into
readable output.
_Avoid_: edited Markdown authority

**Text analysis report status**:
The complete, partial, or failed state determined by verified segments,
unavailable Parts, fallback, pending decisions, and whole-attempt failures.
_Avoid_: failed-on-one-claim

**Conservative single-segment fallback**:
The one-segment-per-Part outcome used when no valid proposed boundary remains;
it preserves cue ownership and reports partial status.
_Avoid_: successful segmentation claim

**Offline citation-support oracle**:
The reference relationship that identifies which source cues support a
candidate citation in controlled verification.
_Avoid_: synthetic quality scorer

**Append-only human text-analysis review**:
An independent review record that may label only its reviewed scope and cannot
rewrite model output or evidence.
_Avoid_: blanket human approval

**Phase 6 offline human-review boundary**:
The boundary between structural review evidence and a claim of real-world human
verification.
_Avoid_: synthetic quality certification

**Phase 6 offline fixture coverage**:
The set of controlled cases used to exercise text-analysis evidence boundaries.
_Avoid_: happy-path-only fixture

**Phase 6 deterministic contract verification**:
The repeatable verification of text-analysis evidence structure without judging
prose quality.
_Avoid_: subjective summary benchmark

**Affected-Part re-analysis**:
A new text-analysis attempt that regenerates only the Parts whose cue evidence
basis changed after transcription or enhancement.
_Avoid_: whole-collection re-run

**Carried-forward analysis Part**:
A Part whose verified prior analysis is reused in a new attempt with an
explicit provenance link to the retained source report.
_Avoid_: silently copied segments
