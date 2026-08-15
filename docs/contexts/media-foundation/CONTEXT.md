# Media Foundation Context

This Context owns deterministic media evidence, exact source-time coordinates,
coverage, cue layers, and the future publication boundary. It is the base
vocabulary consumed by every later processing Context.
Operational mechanics and exact thresholds remain in the linked specifications
and ADRs.

Relevant global decisions include [ADR 0002](../../adr/0002-compact-coverage-based-virtual-timeline.md),
[ADR 0007](../../adr/0007-preserve-signed-raw-pts.md), and
[ADR 0010](../../adr/0010-derive-stream-coverage-from-decoded-intervals.md).

## Language

**Synthetic media fixture**:
A retained project-owned media artifact generated deterministically from a
versioned fixture recipe; it is not user source media.
_Avoid_: sample video, real video

**Fixture recipe**:
A versioned declarative description of one synthetic fixture and its expected
probe evidence.
_Avoid_: ad hoc test file

**Dependency-free Phase 2 core**:
The media-foundation scope that stands on its own before later processing
capabilities are introduced.
_Avoid_: incidental parser dependency

**Phase 2 library boundary**:
The boundary where deterministic media APIs are used without user-media intake.
_Avoid_: media command

**Fixture toolchain**:
The approved deterministic toolchain for creating and inspecting Synthetic
media fixtures.
_Avoid_: production media toolchain

**RawPtsTime**:
The exact signed source coordinate formed from a stream PTS and time base.
_Avoid_: normalized timestamp

**PartRelativeTime**:
The exact coordinate relative to a Part's coverage start, derived from
RawPtsTime without replacing it as source authority.
_Avoid_: part local time

**CollectionVirtualTime**:
The collection-facing coordinate formed by placing ordered Parts end to end
while retaining each Part's source-time evidence.
_Avoid_: global PTS

**Raw PTS**:
The signed integer presentation timestamp retained from a media stream,
including negative values.
_Avoid_: zero-clamped timestamp

**Serialization envelope**:
The outward millisecond representation used when exact intervals are exported.
_Avoid_: rounded source time

**DecodedInterval**:
An observed decodable stream interval with exact start and end boundaries.
_Avoid_: metadata duration

**StreamCoverage**:
The outer envelope of decoded intervals together with separately retained
internal gaps.
_Avoid_: continuous media range

**ProbeDocument**:
The immutable raw inspection record retained as media evidence.
_Avoid_: guessed probe result

**ProbeProjection**:
The typed decision-facing projection of a ProbeDocument; unknown fields remain
in the raw document.
_Avoid_: best-effort metadata

**Coverage ProbeDocument**:
The packet-level ProbeDocument used to derive stream coverage from observed PTS
evidence.
_Avoid_: sampled duration

**RawCue**:
An immutable parsed subtitle record retaining source text, timing, and order.
_Avoid_: edited cue

**NormalizedCue**:
An immutable lossless representation of a RawCue retaining every token.
_Avoid_: cleaned cue

**PresentationCue**:
An immutable display representation derived from a NormalizedCue with any
presentation-only change tied to source provenance.
_Avoid_: rewritten transcript

**Monotonic cue order**:
Stable cue ordering by start, end, and source ordinal that permits overlap.
_Avoid_: non-overlapping order

**Proven rolling overlap**:
An exact local token overlap and strict textual extension that supports a
presentation-only rolling-caption correction.
_Avoid_: fuzzy duplicate

**Phase 3 test boundary**:
The offline engineering boundary using project-owned evidence and controlled
substitutes for source-planning verification.
_Avoid_: live URL test

**Phase 5 offline engineering boundary**:
The synthetic, controlled-adapter boundary for audio-analysis contracts.
_Avoid_: real-media validation

**Phase 5 offline verification boundary**:
The evidence boundary that proves audio contracts without model acquisition or
real-model execution.
_Avoid_: model smoke test

**Phase 6 offline text-verification boundary**:
The synthetic structured-text boundary that proves text-analysis contracts
without a real model or external knowledge.
_Avoid_: semantic quality benchmark

**Phase 7 offline transcription-verification boundary**:
The synthetic boundary that proves transcription and enhancement contracts
without a real ASR model, user media, or network.
_Avoid_: accuracy benchmark

**Phase 8 offline visual-verification boundary**:
The synthetic boundary that proves visual-text contracts without a real OCR
model, frame extraction from user media, or network.
_Avoid_: OCR quality benchmark

**RunBundle**:
The immutable bundle that a future authorized publication stage may promote
from verified evidence and reports.
_Avoid_: analysis workspace

**Publication boundary**:
The separately authorized boundary that promotes verified artifacts into a
RunBundle; upstream Contexts retain evidence without publishing it.
_Avoid_: implicit output write

**Future publication stage**:
A later, separately authorized stage responsible for publication; the former
Phase 9 shorthand does not define a current processing contract.
_Avoid_: defined Phase 9 pipeline
