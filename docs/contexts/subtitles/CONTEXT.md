# Subtitles Context

This Context owns embedded subtitle evidence, atomic candidate validation,
Primary-track selection, and source/readable artifacts. It consumes the exact
clocks and source plans from [Media Foundation](../media-foundation/CONTEXT.md)
and [Source Planning](../source-planning/CONTEXT.md).
Operational mechanics and exact thresholds remain in the linked specifications
and ADRs.

Relevant global decisions include [ADR 0003](../../adr/0003-reject-invalid-subtitle-tracks-atomically.md),
[ADR 0004](../../adr/0004-separate-subtitle-cue-representations.md), and
[ADR 0025](../../adr/0025-revalidate-before-subtitle-processing.md).

## Language

**SubtitleTrackCandidate**:
A metadata-only description of one embedded subtitle stream and its availability.
_Avoid_: parsed subtitle

**Embedded subtitle payload**:
The raw subtitle bitstream extracted from one immutable SourceArtifact.
_Avoid_: sidecar subtitle

**Primary subtitle track**:
The unique highest-ranked valid embedded subtitle track for one Part.
_Avoid_: merged track

**Subtitle track selection ambiguity**:
The state where retained evidence cannot choose one valid track uniquely and an
explicit stream selection is required.
_Avoid_: default-track assumption

**Source subtitle artifact**:
A deterministic lossless serialization of selected NormalizedCues in an
accepted subtitle format.
_Avoid_: readable subtitle

**Readable subtitle artifact**:
A display-oriented serialization derived from PresentationCues with recorded
presentation-only corrections.
_Avoid_: corrected transcript

**Text subtitle payload**:
An embedded subtitle payload whose format carries text semantics supported by
the subtitle rules.
_Avoid_: image subtitle

**Image subtitle payload**:
An embedded subtitle payload whose glyphs are encoded as images and is
unavailable for text processing.
_Avoid_: OCR transcript

**Partial subtitle collection**:
A MediaCollection with subtitle artifacts only for Parts that have a Primary
subtitle track; unavailable spans remain explicit.
_Avoid_: completed transcript

**Subtitle processing authorization**:
Authority to process a confirmed RunPlan after bound source, tool, and rules
evidence revalidates.
_Avoid_: implicit processing permission

**Subtitle candidate workspace**:
The immutable evidence area for extracted payloads, validation, corrections,
and candidate artifacts before publication.
_Avoid_: disposable extraction cache

**Readable markup whitelist**:
The closed set of presentation tags eligible for removal from readable output.
_Avoid_: generic tag stripping

**Subtitle cue clock**:
The Part-relative clock used by extracted subtitle cues before exact mapping to
source time.
_Avoid_: subtitle-specific timeline

**Part playback coverage**:
The union of usable audio and video decoded intervals that bounds subtitle cue
validity.
_Avoid_: container duration

**Primary subtitle coverage**:
The union of valid cue intervals belonging to the selected Primary subtitle
track for one Part.
_Avoid_: transcript completeness, audio coverage

**Caption time coverage**:
The union duration of valid Primary-track cue intervals divided by Part
playback coverage duration.
_Avoid_: transcript completeness

**Subtitle candidate report**:
The immutable record of every extracted candidate, validation result, and
selection eligibility for a confirmed RunPlan.
_Avoid_: mutable track list

**Subtitle workspace preflight**:
The capacity check and per-candidate bound applied before subtitle extraction.
_Avoid_: unbounded extraction

**Explicit subtitle decoding**:
A user-recorded decoder choice for bytes that deterministic decoding cannot
classify automatically.
_Avoid_: charset guessing

**Subtitle unavailable requires ASR plan**:
The evidence state for a Part with no valid Primary track; it creates no
automatic ASR action or invented text.
_Avoid_: empty transcript

**Format projection loss**:
A retained record of layout information that a requested subtitle export
format cannot represent.
_Avoid_: silent conversion loss

**Subtitle extraction attempt**:
One retained attempt to produce a subtitle candidate, including its outcome.
_Avoid_: overwritten retry

**Character-preserving subtitle normalization**:
Lossless conversion of cue line endings to LF while retaining all other text
code points and tokens.
_Avoid_: text cleanup

**Atomic subtitle track**:
A candidate accepted only when every cue parses and validates; one failure
invalidates the complete track.
_Avoid_: partially recovered track

**PresentationCorrection**:
An immutable record linking a presentation-only markup or rolling-token change
to the source cue and exact supporting evidence.
_Avoid_: untracked cleanup
