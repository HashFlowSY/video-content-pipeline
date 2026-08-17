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
* Optional contexts: `audio-analysis` and `visual-text` (their absence does
  not block subtitle-derived claims)
* Direct dependencies: `subtitles`
* Transitive dependencies: `media-foundation`, `source-planning`; optional
  `audio-analysis`, `visual-text`, and their dependencies
* Context file: [contexts/text-analysis/CONTEXT.md](docs/contexts/text-analysis/CONTEXT.md)
* Owned vocabulary: see the owner index below.
* Relevant global ADRs: ADRs 0026, 0034–0039, 0040–0042, 0046, 0049.

### transcription

* Purpose: turns confirmed plans and retained subtitle and audio-analysis
  evidence into gate-checked ASR text evidence — full verbatim transcripts and
  interval-scoped enhanced subtitles — through suspicious-interval detection,
  independent review, and deterministic arbitration.
* Dependencies: `media-foundation`, `source-planning`, `subtitles`, `audio-analysis`
* Direct dependencies: `subtitles`, `audio-analysis`
* Transitive dependencies: `media-foundation`, `source-planning`
* Dependency note: `audio-analysis` is a required dependency (ADR 0043), not
  optional as it is for `text-analysis`.
* Context file: [contexts/transcription/CONTEXT.md](docs/contexts/transcription/CONTEXT.md)
* Owned vocabulary: see the owner index below.
* Relevant global ADRs: ADRs 0026, 0036–0037, 0042–0046.

### visual-text

* Purpose: produces optional on-screen text evidence — deterministic
  page-change detection, adaptive frame sampling, Part-local page indices, and
  OCR evidence items — without deciding cross-modal facts.
* Dependencies: `media-foundation`, `source-planning`
* Optional context: `audio-analysis` (used only by embedded-media suspicion;
  its absence does not block visual evidence)
* Direct dependencies: `source-planning`
* Transitive dependencies: `media-foundation`; optional `audio-analysis` and
  its dependencies
* Context file: [contexts/visual-text/CONTEXT.md](docs/contexts/visual-text/CONTEXT.md)
* Owned vocabulary: see the owner index below.
* Relevant global ADRs: ADRs 0001, 0036–0037, 0042, 0047–0049.

### orchestration

* Purpose: composes the evidence Contexts into recoverable, auditable runs —
  run identity and state, stage units with invalidation keys, process control
  and crash recovery, candidate staging, atomic publication, and the published
  RunBundle with manifest, reports, inventory, and latest pointer.
* Dependencies: `media-foundation`, `source-planning`, `subtitles`, `audio-analysis`, `text-analysis`, `transcription`, `visual-text`
* Direct dependencies: `source-planning`, `subtitles`, `audio-analysis`, `text-analysis`, `transcription`, `visual-text`
* Transitive dependencies: `media-foundation`
* Context file: [contexts/orchestration/CONTEXT.md](docs/contexts/orchestration/CONTEXT.md)
* Owned vocabulary: see the owner index below.
* Relevant global ADRs: ADRs 0026, 0032, 0034, 0041–0042, 0046, 0050–0053, 0055.

## Dependency routing

The conceptual dependency route is `media-foundation → source-planning →
subtitles → audio-analysis → text-analysis`. Text analysis consumes the first
three contexts and may optionally consume audio-analysis evidence; audio
analysis is therefore not a required prerequisite for subtitle-derived text
claims. The direct and transitive fields above make that exception explicit.

The transcription route is `subtitles + audio-analysis → transcription →
text-analysis`. Transcription requires audio-analysis evidence (ADR 0043) and
produces a changed cue basis; text-analysis consumes that basis through
affected-Part re-analysis (ADR 0046) while remaining fully usable without
transcription for subtitle-derived claims.

The visual-text route is `source-planning → visual-text → text-analysis`.
Visual-text is explicitly enabled, optionally consumes audio-analysis evidence
(picture-plus-audio embedded-media suspicion), and never depends on subtitles;
text-analysis consumes retained visual-text reports as an optional evidence
input through affected-Part re-analysis (ADR 0046) and owns the host-read
comment upgrade (ADR 0049).

The orchestration route consumes every other Context: `source-planning →
orchestration` for plan revalidation, the evidence routes above for stage
composition, and `media-foundation` timing views for publication projection
(ADR 0026). Orchestration invokes the per-phase functions in-process, adopts
only its own run's recorded stage outputs (ADR 0052), and is the only Context
that writes `outputs/` (ADR 0050, ADR 0051). Transcription and visual-text
stages execute conditionally by run mode, but their vocabulary is a full
dependency of the orchestration contract.

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
- `Real media` → `media-foundation`
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
- `Phase 7 offline transcription-verification boundary` → `media-foundation`
- `Phase 8 offline visual-verification boundary` → `media-foundation`
- `Phase 9 offline orchestration-verification boundary` → `media-foundation`
- `Phase 10 offline engineering-verification boundary` → `media-foundation`

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
- `Front-loaded plan choices` → `source-planning`

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
- `Text-semantics capability contract` → `text-analysis`
- `Model-acquisition-required text-analysis result` → `text-analysis`
- `Text-semantics decoding calibration` → `text-analysis`
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
- `Affected-Part re-analysis` → `text-analysis`
- `Carried-forward analysis Part` → `text-analysis`
- `Optional visual-text context` → `text-analysis`
- `Host-read comment upgrade` → `text-analysis`

### transcription

- `Transcription capability contract` → `transcription`
- `Independent-model review requirement` → `transcription`
- `Model-acquisition-required transcription result` → `transcription`
- `Controlled offline ASR adapter` → `transcription`
- `Verbatim transcription artifact` → `transcription`
- `Enhanced subtitle artifact` → `transcription`
- `Cue-level transcription provenance` → `transcription`
- `Audio-completeness upgrade` → `transcription`
- `Transcription attempt provenance` → `transcription`
- `Immutable transcription workspace` → `transcription`
- `Suspicious interval` → `transcription`
- `Versioned suspicion detection rules` → `transcription`
- `Deterministic transcription arbitration` → `transcription`
- `Unresolved transcription conflict` → `transcription`
- `Gate-checked interval replacement` → `transcription`
- `Explicit transcription command boundary` → `transcription`
- `Full-ASR resource confirmation pause` → `transcription`
- `Transcription resource-envelope pause` → `transcription`
- `Serialized ASR execution` → `transcription`

### visual-text

- `Visual-text capability contract` → `visual-text`
- `Controlled offline OCR adapter` → `visual-text`
- `Model-acquisition-required visual-text result` → `visual-text`
- `Deterministic page-change detection` → `visual-text`
- `Versioned frame-sampling rules` → `visual-text`
- `Text-value proxy metric` → `visual-text`
- `Visual page` → `visual-text`
- `Part-local visual page identity` → `visual-text`
- `Page appearance record` → `visual-text`
- `OCR evidence item` → `visual-text`
- `Versioned OCR-item classification rules` → `visual-text`
- `Excluded visual item` → `visual-text`
- `Classification-uncertain visual item` → `visual-text`
- `Suspected embedded-media interval` → `visual-text`
- `Retained frame inventory` → `visual-text`
- `Unpublished internal frame` → `visual-text`
- `Explicit visual-text command boundary` → `visual-text`
- `OCR resource confirmation pause` → `visual-text`
- `Visual-text resource-envelope pause` → `visual-text`
- `Immutable visual-text workspace` → `visual-text`
- `Serialized OCR execution` → `visual-text`
- `OCR-not-requested record` → `visual-text`

### orchestration

- `Run` → `orchestration`
- `Run identity` → `orchestration`
- `Run state document` → `orchestration`
- `Run events journal` → `orchestration`
- `Single-writer run state` → `orchestration`
- `Stage unit` → `orchestration`
- `Stage invalidation key` → `orchestration`
- `Stage version` → `orchestration`
- `Run-scoped adoption` → `orchestration`
- `Heavy-task lock` → `orchestration`
- `Control request` → `orchestration`
- `Run decision pause` → `orchestration`
- `Crash recovery` → `orchestration`
- `Non-interactive run execution` → `orchestration`
- `Publication projection` → `orchestration`
- `Staging area` → `orchestration`
- `Atomic publish` → `orchestration`
- `RunBundle` → `orchestration`
- `Publication boundary` → `orchestration`
- `RunBundle manifest` → `orchestration`
- `Minimal RunBundle` → `orchestration`
- `Latest pointer` → `orchestration`
- `Run inventory` → `orchestration`
- `Cleanup plan` → `orchestration`
- `Improvement run` → `orchestration`
- `Explicit orchestration command boundary` → `orchestration`
- `Golden run` → `orchestration`
- `Fault point` → `orchestration`
- `Fault class` → `orchestration`
- `Fault matrix` → `orchestration`
- `Model runtime subprocess` → `orchestration`

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
- [ADR 0043](docs/adr/0043-introduce-a-transcription-context-with-required-audio-analysis.md)
- [ADR 0044](docs/adr/0044-use-deterministic-transcription-arbitration-with-retained-conflicts.md)
- [ADR 0045](docs/adr/0045-use-gate-checked-interval-replacement-for-enhanced-subtitles.md)
- [ADR 0046](docs/adr/0046-recompute-affected-parts-with-carried-forward-analysis.md)
- [ADR 0047](docs/adr/0047-introduce-a-visual-text-context-with-deterministic-detection-and-ocr-only-model-capability.md)
- [ADR 0048](docs/adr/0048-keep-visual-page-identity-part-local.md)
- [ADR 0049](docs/adr/0049-separate-visual-evidence-classification-from-fact-upgrade.md)
- [ADR 0050](docs/adr/0050-introduce-an-orchestration-context-that-owns-runs-and-publication.md)
- [ADR 0051](docs/adr/0051-publish-runbundles-by-whole-directory-atomic-rename.md)
- [ADR 0052](docs/adr/0052-adopt-stage-outputs-run-scoped-with-stage-versioned-invalidation-keys.md)
- [ADR 0053](docs/adr/0053-use-single-writer-run-state-with-file-based-control-requests.md)
- [ADR 0054](docs/adr/0054-verify-engineering-with-synthetic-media-and-a-deterministic-fault-matrix.md)
- [ADR 0055](docs/adr/0055-run-mlx-model-engines-in-per-stage-subprocesses.md)
- [ADR 0056](docs/adr/0056-require-model-specific-text-semantics-calibration.md)
