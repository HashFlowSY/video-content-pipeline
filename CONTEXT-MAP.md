# Context Map

This is the canonical entry point for the project's domain language. Read this
map first, then read the Context that owns the proposed change and its listed
dependencies. Definitions live in exactly one owner Context; consumers link to
that owner instead of copying a second definition. Runtime setup vocabulary is
governed separately in [Runtime Governance](docs/RUNTIME_GOVERNANCE.md).

## Contexts

### media-foundation

* Purpose: establishes deterministic media evidence, exact clocks, coverage,
  cue representations, and the boundary that can later publish verified work.
* Dependencies: none
* Direct dependencies: none
* Transitive dependencies: none
* Context file: [contexts/media-foundation/CONTEXT.md](docs/contexts/media-foundation/CONTEXT.md)
* Owned vocabulary: see the owner index below.
* Relevant global ADRs: ADRs 0001–0014, 0023, 0026, 0042.

### source-planning

* Purpose: turns explicitly authorized local or public sources into inspected,
  resource-bounded, immutable plans and ordered collections.
* Dependencies: `media-foundation`
* Direct dependencies: `media-foundation`
* Transitive dependencies: none beyond `media-foundation`
* Context file: [contexts/source-planning/CONTEXT.md](docs/contexts/source-planning/CONTEXT.md)
* Owned vocabulary: see the owner index below.
* Relevant global ADRs: ADRs 0015–0025, 0042.

### subtitles

* Purpose: retains embedded subtitle candidates, validates them atomically,
  selects a Primary track, and derives source/readable evidence.
* Dependencies: `media-foundation`, `source-planning`
* Direct dependencies: `source-planning`
* Transitive dependencies: `media-foundation`
* Context file: [contexts/subtitles/CONTEXT.md](docs/contexts/subtitles/CONTEXT.md)
* Owned vocabulary: see the owner index below.
* Relevant global ADRs: ADRs 0003–0006, 0008–0010, 0025–0026, 0042.

### audio-analysis

* Purpose: evaluates selected analysis audio through calibrated alignment, voice
  activity, and anonymous speaker evidence without changing subtitle source.
* Dependencies: `media-foundation`, `source-planning`, `subtitles`
* Direct dependencies: `subtitles`
* Transitive dependencies: `media-foundation`, `source-planning`
* Context file: [contexts/audio-analysis/CONTEXT.md](docs/contexts/audio-analysis/CONTEXT.md)
* Owned vocabulary: see the owner index below.
* Relevant global ADRs: ADRs 0026–0039, 0042.

### text-analysis

* Purpose: organizes subtitle-derived evidence into cue-bound semantic segments,
  chapters, and cited summaries; audio analysis may inform limitations but is
  optional for basic subtitle-derived claims.
* Dependencies: `media-foundation`, `source-planning`, `subtitles`
* Optional context: `audio-analysis` (its absence does not block
  subtitle-derived claims)
* Direct dependencies: `subtitles`
* Transitive dependencies: `media-foundation`, `source-planning`; optional
  `audio-analysis` and its dependencies
* Context file: [contexts/text-analysis/CONTEXT.md](docs/contexts/text-analysis/CONTEXT.md)
* Owned vocabulary: see the owner index below.
* Relevant global ADRs: ADRs 0026, 0034–0039, 0040–0042.

## Dependency routing

The conceptual dependency route is `media-foundation → source-planning →
subtitles → audio-analysis → text-analysis`. Text analysis consumes the first
three contexts and may optionally consume audio-analysis evidence; audio
analysis is therefore not a required prerequisite for subtitle-derived text
claims. The direct and transitive fields above make that exception explicit.

When a change crosses contexts, name every affected owner and read each owner's
relevant ADR index. Add a new global ADR to `docs/adr/` and this map's index when
the decision governs more than one context; the ADR tree remains global and
link-stable.

## Owner index

Each entry below has one owner. The linked Context is the only place where its
definition may be changed.

### media-foundation

- `Synthetic media fixture` → `media-foundation`
- `Fixture recipe` → `media-foundation`
- `Dependency-free Phase 2 core` → `media-foundation`
- `Phase 2 library boundary` → `media-foundation`
- `Fixture toolchain` → `media-foundation`
- `RawPtsTime` → `media-foundation`
- `PartRelativeTime` → `media-foundation`
- `CollectionVirtualTime` → `media-foundation`
- `Raw PTS` → `media-foundation`
- `Serialization envelope` → `media-foundation`
- `DecodedInterval` → `media-foundation`
- `StreamCoverage` → `media-foundation`
- `ProbeDocument` → `media-foundation`
- `ProbeProjection` → `media-foundation`
- `Coverage ProbeDocument` → `media-foundation`
- `RawCue` → `media-foundation`
- `NormalizedCue` → `media-foundation`
- `PresentationCue` → `media-foundation`
- `Monotonic cue order` → `media-foundation`
- `Proven rolling overlap` → `media-foundation`
- `Phase 3 test boundary` → `media-foundation`
- `Phase 5 offline engineering boundary` → `media-foundation`
- `Phase 5 offline verification boundary` → `media-foundation`
- `Phase 6 offline text-verification boundary` → `media-foundation`
- `RunBundle` → `media-foundation`
- `Publication boundary` → `media-foundation`
- `Future publication stage` → `media-foundation`

### source-planning

- `Phase 3 source-intake and planning boundary` → `source-planning`
- `Source access authorization` → `source-planning`
- `Part` → `source-planning`
- `RunPlan` → `source-planning`
- `PlanReport` → `source-planning`
- `Plan confirmation` → `source-planning`
- `Report revalidation` → `source-planning`
- `Decode preflight confirmation` → `source-planning`
- `Phase-bounded estimate` → `source-planning`
- `Decode throughput profile` → `source-planning`
- `Disk headroom` → `source-planning`
- `URL access mode` → `source-planning`
- `Host escalation` → `source-planning`
- `Insecure HTTP authorization` → `source-planning`
- `Redacted source provenance` → `source-planning`
- `Pinned external tool` → `source-planning`
- `Inspection toolchain` → `source-planning`
- `Full decode validation` → `source-planning`
- `Decode validation toolchain` → `source-planning`
- `SourceArtifact` → `source-planning`
- `Local source candidate` → `source-planning`
- `Media-qualified source` → `source-planning`
- `MediaCollection` → `source-planning`
- `Manual collection session` → `source-planning`
- `Collection closure` → `source-planning`
- `Duplicate Part` → `source-planning`

### subtitles

- `SubtitleTrackCandidate` → `subtitles`
- `Embedded subtitle payload` → `subtitles`
- `Primary subtitle track` → `subtitles`
- `Subtitle track selection ambiguity` → `subtitles`
- `Source subtitle artifact` → `subtitles`
- `Readable subtitle artifact` → `subtitles`
- `Text subtitle payload` → `subtitles`
- `Image subtitle payload` → `subtitles`
- `Partial subtitle collection` → `subtitles`
- `Subtitle processing authorization` → `subtitles`
- `Subtitle candidate workspace` → `subtitles`
- `Readable markup whitelist` → `subtitles`
- `Subtitle cue clock` → `subtitles`
- `Part playback coverage` → `subtitles`
- `Primary subtitle coverage` → `subtitles`
- `Caption time coverage` → `subtitles`
- `Subtitle candidate report` → `subtitles`
- `Subtitle workspace preflight` → `subtitles`
- `Explicit subtitle decoding` → `subtitles`
- `Subtitle unavailable requires ASR plan` → `subtitles`
- `Format projection loss` → `subtitles`
- `Subtitle extraction attempt` → `subtitles`
- `Character-preserving subtitle normalization` → `subtitles`
- `Atomic subtitle track` → `subtitles`
- `PresentationCorrection` → `subtitles`

### audio-analysis

- `Phase 5 analysis partition` → `audio-analysis`
- `Adopted alignment timing view` → `audio-analysis`
- `AlignmentCandidate` → `audio-analysis`
- `Cue-level alignment adoption` → `audio-analysis`
- `Alignment-untrusted Part` → `audio-analysis`
- `Alignment failure fingerprint` → `audio-analysis`
- `Alignment failure diagnosis` → `audio-analysis`
- `Alignment calibration requirement` → `audio-analysis`
- `Alignment calibration profile` → `audio-analysis`
- `Synthetic alignment calibration` → `audio-analysis`
- `Voice activity interval` → `audio-analysis`
- `Audio-coverage-constrained VAD` → `audio-analysis`
- `VAD calibration requirement` → `audio-analysis`
- `Uncovered-speech risk evidence` → `audio-analysis`
- `Audio-state-indeterminate risk` → `audio-analysis`
- `Part-local speaker label` → `audio-analysis`
- `SpeakerTurn` → `audio-analysis`
- `Role candidate` → `audio-analysis`
- `Diarization calibration requirement` → `audio-analysis`
- `Diarization calibration profile` → `audio-analysis`
- `Phase 5 heavy-analysis sequence` → `audio-analysis`
- `Model-release-unverified pause` → `audio-analysis`
- `Resource-envelope-exceeded pause` → `audio-analysis`
- `Phase 5 processing authorization` → `audio-analysis`
- `Explicit model acquisition approval` → `audio-analysis`
- `Audio analysis workspace` → `audio-analysis`
- `Partial audio analysis report` → `audio-analysis`
- `Long-silence evidence` → `audio-analysis`
- `Audio analysis clock` → `audio-analysis`
- `Audio analysis report` → `audio-analysis`
- `Model-acquisition-required result` → `audio-analysis`
- `Forced-alignment candidate` → `audio-analysis`
- `Phase 5 capability contract` → `audio-analysis`
- `Phase 5 model eligibility` → `audio-analysis`
- `Credential-gated model candidate` → `audio-analysis`
- `VAD candidate` → `audio-analysis`
- `Diarization capability vacancy` → `audio-analysis`
- `Model-output projection` → `audio-analysis`
- `Model-output-invalid result` → `audio-analysis`
- `Alignment text-contract violation` → `audio-analysis`
- `Order-preserving alignment view` → `audio-analysis`
- `Alignment-candidate-rejected cue` → `audio-analysis`
- `Language-aware alignment duration rule` → `audio-analysis`
- `Analysis audio stream` → `audio-analysis`
- `Analysis audio selection record` → `audio-analysis`
- `Analysis audio derivative` → `audio-analysis`
- `Analysis audio derivation toolchain` → `audio-analysis`
- `Derivative-to-source time mapping` → `audio-analysis`
- `Complete VAD partition` → `audio-analysis`
- `Diarization-VAD conflict` → `audio-analysis`
- `Alignment-VAD conflict` → `audio-analysis`
- `Calibration evaluation record` → `audio-analysis`
- `Calibration-failed result` → `audio-analysis`

### text-analysis

- `SemanticSegment` → `text-analysis`
- `Chapter` → `text-analysis`
- `Phase 6 textual fact source` → `text-analysis`
- `Phase 6 evidence input and citation basis` → `text-analysis`
- `Cue-level factual citation` → `text-analysis`
- `Semantic-segment cue ownership` → `text-analysis`
- `Cue-bound semantic boundary` → `text-analysis`
- `Unsupported generated claim` → `text-analysis`
- `Verified segment-derived summary` → `text-analysis`
- `Cue-supported segment title` → `text-analysis`
- `Phase 6 immutable text-analysis workspace` → `text-analysis`
- `Explicit text-analysis command boundary` → `text-analysis`
- `Text-analysis input revalidation` → `text-analysis`
- `Optional audio-analysis context` → `text-analysis`
- `Part-bounded semantic aggregation` → `text-analysis`
- `Text-model output projection` → `text-analysis`
- `Deterministically adjudicated semantic boundary` → `text-analysis`
- `Two-level text-analysis failure handling` → `text-analysis`
- `Cue-supported question-and-answer structure` → `text-analysis`
- `Cue-supported person and role` → `text-analysis`
- `Cue-supported structured detail` → `text-analysis`
- `Cue-preserved source contradiction` → `text-analysis`
- `Cue-supported unresolved question` → `text-analysis`
- `Subtitle-unavailable text Part` → `text-analysis`
- `Persistent subtitle audio-completeness limitation` → `text-analysis`
- `Controlled offline text adapter` → `text-analysis`
- `Text-model identity invalidation` → `text-analysis`
- `Text-analysis unavailable result and offline exit gate` → `text-analysis`
- `Phase 6 report language boundary` → `text-analysis`
- `Technical text-processing block` → `text-analysis`
- `Length-unconstrained semantic segment` → `text-analysis`
- `Part-local chapter aggregation` → `text-analysis`
- `Text-analysis decision pause boundary` → `text-analysis`
- `Serialized text-model execution` → `text-analysis`
- `Text-analysis resource-envelope pause` → `text-analysis`
- `Text-generation attempt provenance` → `text-analysis`
- `No automatic text-generation retry` → `text-analysis`
- `Text analysis report` → `text-analysis`
- `Text-analysis diagnostic visibility` → `text-analysis`
- `Restricted raw text-model diagnostic` → `text-analysis`
- `Versioned text prompt template` → `text-analysis`
- `Versioned Phase 6 generation rules` → `text-analysis`
- `Versioned text-report renderer` → `text-analysis`
- `Text analysis report status` → `text-analysis`
- `Conservative single-segment fallback` → `text-analysis`
- `Offline citation-support oracle` → `text-analysis`
- `Append-only human text-analysis review` → `text-analysis`
- `Phase 6 offline human-review boundary` → `text-analysis`
- `Phase 6 offline fixture coverage` → `text-analysis`
- `Phase 6 deterministic contract verification` → `text-analysis`

## Reading and writing protocol

1. Read this map before exploration or domain work.
2. Load the affected Context and all required dependencies; load
   `audio-analysis` for text work only when audio evidence is in scope.
3. Define or revise a term only in its owner Context. Link to shared terms from
   dependent Contexts rather than restating them.
4. In a cross-Context change, list every affected owner and relevant global ADR
   in the change record.

Historical inventories, completion reports, and archival snapshots retain the
layout that existed when they were recorded. The migration provenance for this
topology is [the Context-layout inventory](docs/CONTEXT_LAYOUT_MIGRATION_INVENTORY.json).

## Global ADR index

Decision records remain in one global, link-stable tree. Contexts index the
records relevant to their boundaries; the complete index is retained here so a
future ADR can be routed without creating a context-local tree.

- [ADR 0001](docs/adr/0001-use-existing-ffmpeg-and-ffprobe.md)
- [ADR 0002](docs/adr/0002-compact-coverage-based-virtual-timeline.md)
- [ADR 0003](docs/adr/0003-reject-invalid-subtitle-tracks-atomically.md)
- [ADR 0004](docs/adr/0004-separate-subtitle-cue-representations.md)
- [ADR 0005](docs/adr/0005-preserve-overlapping-subtitle-cues.md)
- [ADR 0006](docs/adr/0006-use-exact-local-proof-for-rolling-deduplication.md)
- [ADR 0007](docs/adr/0007-preserve-signed-raw-pts.md)
- [ADR 0008](docs/adr/0008-separate-source-part-and-collection-time.md)
- [ADR 0009](docs/adr/0009-use-outward-millisecond-serialization.md)
- [ADR 0010](docs/adr/0010-derive-stream-coverage-from-decoded-intervals.md)
- [ADR 0011](docs/adr/0011-parse-ffprobe-json-without-fallback-guessing.md)
- [ADR 0012](docs/adr/0012-retain-hash-pinned-synthetic-media-fixtures.md)
- [ADR 0013](docs/adr/0013-keep-the-phase-2-core-dependency-free.md)
- [ADR 0014](docs/adr/0014-keep-phase-2-media-apis-internal.md)
- [ADR 0015](docs/adr/0015-require-explicit-url-access-mode.md)
- [ADR 0016](docs/adr/0016-snapshot-local-sources-with-double-hash.md)
- [ADR 0017](docs/adr/0017-use-user-ordered-manual-collections.md)
- [ADR 0018](docs/adr/0018-accept-only-regular-local-source-files.md)
- [ADR 0019](docs/adr/0019-treat-yt-dlp-as-a-pinned-external-prerequisite.md)
- [ADR 0020](docs/adr/0020-revalidate-ffprobe-for-phase-3-preflight.md)
- [ADR 0021](docs/adr/0021-require-confirmed-full-decode-validation.md)
- [ADR 0022](docs/adr/0022-revalidate-ffmpeg-for-phase-3-decode-validation.md)
- [ADR 0023](docs/adr/0023-retain-packet-level-coverage-evidence.md)
- [ADR 0024](docs/adr/0024-revalidate-evidence-before-plan-confirmation.md)
- [ADR 0025](docs/adr/0025-revalidate-before-subtitle-processing.md)
- [ADR 0026](docs/adr/0026-keep-adopted-alignment-timing-derived.md)
- [ADR 0027](docs/adr/0027-require-model-specific-alignment-calibration.md)
- [ADR 0028](docs/adr/0028-separate-voice-activity-from-subtitle-coverage.md)
- [ADR 0029](docs/adr/0029-require-model-specific-vad-calibration.md)
- [ADR 0030](docs/adr/0030-keep-speaker-labels-part-local-and-anonymous.md)
- [ADR 0031](docs/adr/0031-require-model-specific-diarization-calibration.md)
- [ADR 0032](docs/adr/0032-serialize-phase-5-heavy-analysis.md)
- [ADR 0033](docs/adr/0033-revalidate-all-phase-5-analysis-inputs.md)
- [ADR 0034](docs/adr/0034-keep-phase-5-analysis-in-immutable-workspaces.md)
- [ADR 0035](docs/adr/0035-expose-phase-5-through-an-explicit-analysis-cli.md)
- [ADR 0036](docs/adr/0036-keep-phase-5-model-capabilities-provider-neutral.md)
- [ADR 0037](docs/adr/0037-verify-phase-5-with-controlled-offline-adapters.md)
- [ADR 0038](docs/adr/0038-require-explicit-analysis-audio-stream-selection.md)
- [ADR 0039](docs/adr/0039-require-deterministic-calibration-evaluation-records.md)
- [ADR 0040](docs/adr/0040-require-cue-level-evidence-for-phase-6-facts.md)
- [ADR 0041](docs/adr/0041-keep-phase-6-text-analysis-in-immutable-workspaces.md)
- [ADR 0042](docs/adr/0042-use-context-map-and-domain-owned-glossaries.md)
