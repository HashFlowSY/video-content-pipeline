# Video Content Pipeline Context

This context defines the project-specific terms that protect the boundary
between deterministic engineering fixtures and user-provided media.

## Language

**Synthetic media fixture**:
A retained, hash-pinned project-owned audio or video artifact generated
deterministically from a versioned fixture recipe, never user-provided source
media.
_Avoid_: sample video, real video

**Fixture recipe**:
A versioned declarative description of how FFmpeg creates one synthetic media
fixture and its expected probe evidence.
_Avoid_: runtime media generation, ad hoc test file

**Dependency-free Phase 2 core**:
The Phase 2 implementation boundary that uses only Python's standard library
and already locked test tooling, with no new runtime packages.
_Avoid_: incidental parser dependency, implicit package install

**Phase 2 library boundary**:
The rule that Phase 2 exposes no user-media CLI or source intake; its core is
invoked only by library APIs and deterministic fixture work.
_Avoid_: early `vcp plan`, media command

**Fixture toolchain**:
The approved FFmpeg and FFprobe pair used only to generate and probe synthetic
media fixtures during Phase 2.
_Avoid_: media runtime, input processor

**RawPtsTime**:
The exact signed source coordinate formed by a stream's raw PTS and time base.
_Avoid_: normalized timestamp, export timestamp

**PartRelativeTime**:
The exact non-negative coordinate formed by subtracting a Part's coverage start
from RawPtsTime; it is used for per-Part subtitle export.
_Avoid_: part local time, raw PTS

**CollectionVirtualTime**:
A contiguous collection-facing coordinate that shifts PartRelativeTime by
preceding Part coverage; it never replaces RawPtsTime as source authority.
_Avoid_: global PTS, concatenated container duration

**Atomic subtitle track**:
A subtitle candidate accepted only when every cue parses and validates; one
failure makes the complete track unavailable instead of partially recovered.
_Avoid_: salvaged subtitle file, partially valid track

**RawCue**:
An immutable parsed subtitle record that retains original text, timing, and
source coordinates.
_Avoid_: editable cue, cleaned cue

**NormalizedCue**:
An immutable losslessly normalized form of a RawCue that retains every token.
_Avoid_: deduplicated cue, display cue

**PresentationCue**:
An immutable display form of a NormalizedCue that may omit only proven
rolling-display tokens while retaining source-token provenance.
_Avoid_: rewritten source cue, cleaned subtitle

**Monotonic cue order**:
The stable ordering of cues by `(start, end, source_ordinal)`; it permits
overlapping intervals and does not mean that cues cannot overlap.
_Avoid_: non-overlapping cue order, serialized speech

**Proven rolling overlap**:
An exact normalized token overlap between stable-order adjacent cues in one
Part and subtitle track, with a strict textual extension and overlapping or
contiguous intervals.
_Avoid_: fuzzy duplicate, semantic duplicate

**Raw PTS**:
The signed integer presentation timestamp reported by a media stream; negative
values remain valid source evidence and are never clamped to zero.
_Avoid_: normalized timestamp, non-negative timestamp

**Serialization envelope**:
The millisecond subtitle interval made by flooring an exact start and ceiling
an exact end; its sub-millisecond outward extension never replaces source time.
_Avoid_: canonical time range, rounded source time

**DecodedInterval**:
An observed, decodable stream interval with exact start and end boundaries.
_Avoid_: metadata duration, inferred interval

**StreamCoverage**:
The outer envelope of DecodedIntervals plus separately recorded internal gaps;
it is indeterminate when a needed boundary is unknown.
_Avoid_: container duration, continuous media range

**ProbeDocument**:
The immutable raw JSON emitted by FFprobe and retained as media-inspection
evidence.
_Avoid_: parsed probe result, text probe output

**ProbeProjection**:
The typed projection of a ProbeDocument used for decisions; unknown fields
remain only in the raw document.
_Avoid_: best-effort probe, inferred metadata
