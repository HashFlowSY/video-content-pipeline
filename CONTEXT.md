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

**Phase 3 source-intake and planning boundary**:
The authorized stage for safe local-file and public-URL intake, multi-Part
planning, preflight inspection, resource estimation, and immutable RunPlans;
it excludes ASR, OCR, model downloads, and content generation.
_Avoid_: processing run, model pipeline, real-world validation

**Source access authorization**:
The per-RunPlan permission to read one explicitly supplied local path or access
one explicitly supplied public URL; it never authorizes filesystem discovery,
cookies, credentials, login state, or private sources.
_Avoid_: project-wide media access, ambient network permission

**RunPlan**:
The immutable executable declaration of a preflight-approved source scope,
chosen URL access mode, planned work, resource envelope, and unavailable
prerequisites.
_Avoid_: processing run, mutable job configuration, failed plan

**PlanReport**:
The immutable diagnostic outcome of one planning attempt, whether it produces
an executable RunPlan or is blocked by source, preflight, or resource evidence.
_Avoid_: executable plan, unrecorded planning failure

**Plan confirmation**:
The user's explicit approval of one still-valid preflight-approved PlanReport,
which alone creates its immutable executable RunPlan and plan ID.
_Avoid_: implicit approval, auto-created plan ID, stale confirmation

**Report revalidation**:
The confirmation-time comparison of a PlanReport's SourceArtifact hashes,
external-tool identities, disk headroom, and planning configuration with their
current evidence; any difference makes the report stale.
_Avoid_: mutable report, best-effort confirmation, stale-plan execution

**Decode preflight confirmation**:
The user's explicit approval, after a lightweight probe presents its estimated
cost, to perform the full linear decode validation required to finish planning.
_Avoid_: automatic full decode, RunPlan confirmation

**Phase-bounded estimate**:
A three-point resource estimate backed only by measurements or configuration
evidence available in the current phase; unavailable later stages receive no
invented numeric estimate.
_Avoid_: speculative end-to-end estimate, fabricated total

**Decode throughput profile**:
A versioned project-local configuration that maps probe evidence to a
low-confidence three-point full-decode estimate until matching measured history
is available.
_Avoid_: hidden source benchmark, unqualified performance claim

**Disk headroom**:
The mandatory free-space reserve of the greater of 1 GiB or five percent of a
plan's deterministic disk increment, required before source acquisition begins.
_Avoid_: exact-fit disk check, best-effort acquisition

**URL access mode**:
The explicit `filtered` or `direct` authorization recorded in a RunPlan before
accessing one public URL; omitted, failed, or incompatible modes are rejected
without fallback.
_Avoid_: automatic URL mode, best-effort downloader fallback

**Host escalation**:
An attempted URL redirect, discovered media host, or HTTPS downgrade outside a
RunPlan's authorized host; it blocks acquisition until the user confirms it.
_Avoid_: trusted redirect, transparent CDN fallback

**Insecure HTTP authorization**:
The explicit allowance for one initial `http://` URL; without it, public URL
intake accepts HTTPS only and a permitted HTTP source is reported as lacking
transport-integrity verification.
_Avoid_: implicit HTTP fallback, transport-equivalent URL

**Redacted source provenance**:
The persistent origin description of an authorized URL containing only scheme,
host, and path, with query and fragment removed; the raw URL exists only during
its planning process.
_Avoid_: stored signed URL, replayable source locator, URL audit log

**Pinned external tool**:
A non-project binary allowed only as a read-only prerequisite after its path,
version, and content hash are recorded and revalidated before use.
_Avoid_: managed project tool, auto-updated dependency, implicit install

**Inspection toolchain**:
The revalidated FFprobe binary that creates a ProbeDocument only from a
snapshot SourceArtifact during Phase 3 preflight.
_Avoid_: fixture-only probe, live-path probe, guessed media metadata

**Full decode validation**:
The complete linear decode of every audio and video stream in a SourceArtifact,
performed only after Decode preflight confirmation and without writing derived
media.
_Avoid_: sample decode, metadata-only validation, media transformation

**Decode validation toolchain**:
The revalidated FFmpeg binary that performs Full decode validation on a
SourceArtifact with null output only.
_Avoid_: fixture generator, media converter, derived-media producer

**SourceArtifact**:
The project-owned, content-addressed immutable copy of one authorized source
after its original and copied bytes have matched by hash.
_Avoid_: live source path, linked input, mutable source file

**Local source candidate**:
One explicitly supplied regular local file eligible for snapshotting; links,
directories, devices, pipes, and standard input are never source candidates.
_Avoid_: path discovery, symlinked media, streaming input

**Media-qualified source**:
A SourceArtifact whose strict ProbeProjection proves at least one usable audio
or video stream and whose required StreamCoverage can be established.
_Avoid_: extension-approved file, unprobed media, metadata-only duration

**SubtitleTrackCandidate**:
The metadata-only description of one source subtitle track, including identity,
language, origin, container format, and availability; it contains no subtitle
text during Phase 3.
_Avoid_: acquired subtitle, parsed subtitle, selected subtitle

**Embedded subtitle payload**:
The raw subtitle bitstream extracted from one embedded SubtitleTrackCandidate
inside an immutable SourceArtifact; it is the only subtitle-text input in
Phase 4.
_Avoid_: fetched platform subtitle, discovered sidecar, external subtitle URL

**Primary subtitle track**:
The single highest-ranked valid embedded subtitle track for one Part. Invalid
candidates remain retained evidence and diagnostics; they cannot become a
fallback through repair, merging, or partial publication.
_Avoid_: merged track, repaired candidate, collection-wide primary track

**Subtitle track selection ambiguity**:
The state in which multiple valid embedded subtitle tracks exist but retained
RunPlan evidence cannot establish a unique preference. It requires the user to
select a stream index and is never resolved by default disposition or index.
_Avoid_: automatic tie-break, default-track assumption, lowest-index fallback

**Source subtitle artifact**:
A deterministic UTF-8 SRT or WebVTT serialization of selected NormalizedCues.
It permits only format-level normalization and never removes text, style tags,
or duplicate display tokens; the raw Embedded subtitle payload remains audit
evidence.
_Avoid_: original subtitle payload, readable subtitle, corrected transcript

**Readable subtitle artifact**:
A display-oriented serialization derived from selected PresentationCues. It may
remove only non-semantic presentation markup and token ranges supported by a
recorded PresentationCorrection.
_Avoid_: source subtitle, rewritten transcript, untracked cleanup

**Text subtitle payload**:
An Embedded subtitle payload with text semantics supported in Phase 4: SRT,
WebVTT, or `mov_text`. It can be converted to the accepted cue format before
atomic validation while its original payload is retained.
_Avoid_: image subtitle, OCR input, unsupported styled subtitle

**Image subtitle payload**:
An Embedded subtitle payload whose glyphs are encoded as images, such as PGS
or VobSub. It is unavailable for Phase 4 subtitle processing and remains
retained source evidence without OCR or approximate text conversion.
_Avoid_: text subtitle, inferred OCR text, empty subtitle track

**Partial subtitle collection**:
A MediaCollection whose subtitle artifacts cover only Parts with a Primary
subtitle track. Unavailable Parts retain their CollectionVirtualTime span in
reports but contribute no invented cue, silence marker, or subtitle text.
_Avoid_: complete collection subtitle, caption gap filled with silence, zero-length Part

**Subtitle processing authorization**:
The Phase 4 authority to process a confirmed RunPlan after its SourceArtifacts,
Pinned external tool, and versioned subtitle rules still exactly match recorded
evidence. It appends processing evidence without mutating the RunPlan.
_Avoid_: implicit processing permission, mutable plan, warning-only revalidation

**Subtitle candidate workspace**:
The project-owned `work/<source-id>/<subtitle-run-id>/` evidence area holding
raw subtitle payloads, extraction records, validation results, and candidate
artifacts before publication. It is not a RunBundle and no artifact inside it
is rewritten during later publication.
_Avoid_: output directory, disposable extraction cache, publish-time conversion

**Readable markup whitelist**:
The closed `b`, `i`, `u`, and `font` tags that Readable subtitle artifacts may
remove as non-semantic presentation markup. Every other tag is preserved and
reported as `unhandled_markup`.
_Avoid_: generic tag stripping, inferred style cleanup, speaker-tag removal

**Subtitle cue clock**:
The PartRelativeTime coordinate used by an extracted subtitle payload. Each cue
maps to authoritative RawPtsTime only by adding the Part coverage start; no
scaling, drift correction, or non-linear timestamp transformation is allowed.
_Avoid_: subtitle-specific timeline, duration scaling, inferred synchronization

**Part playback coverage**:
The union of observed DecodedIntervals across every usable audio and video
stream in one Part. It is the subtitle-validation boundary and retains gaps
where no usable stream is present.
_Avoid_: container duration, audio-only coverage, assumed continuous playback

**Caption time coverage**:
The duration of the union of valid Primary subtitle track cue intervals divided
by Part playback coverage duration. It measures displayed-caption time only and
is always reported with `audio_completeness=not_verified`.
_Avoid_: transcript completeness, speech coverage, subtitle accuracy score

**Subtitle candidate report**:
The immutable Phase 4 record of every extracted candidate payload, validation
outcome, and selection eligibility for one confirmed RunPlan. It may enter
`awaiting_subtitle_selection` when user input is required to choose a Primary
subtitle track.
_Avoid_: RunPlan, mutable track list, implicit user preference

**Subtitle workspace preflight**:
The Phase 4 disk check that estimates retained subtitle-candidate growth from
packet evidence, reserves the greater of 1 GiB or five percent of that growth,
and applies a versioned 256 MiB per-candidate extraction byte ceiling. A
ceiling breach retains failed evidence as `extraction_size_limit` without
deleting it.
_Avoid_: Phase 3 source preflight, unbounded extraction, cleanup-on-failure

**Explicit subtitle decoding**:
The user-recorded decoder selection for a non-UTF-8 Embedded subtitle payload.
Phase 4 automatically accepts only BOM-marked UTF-8/UTF-16 or strictly valid
UTF-8; ambiguous bytes remain source evidence and cannot be repaired by
replacement characters or encoding guesses.
_Avoid_: charset auto-detection, lossy replacement decoding, best-looking text

**Subtitle unavailable requires ASR plan**:
The Phase 4 status for a Part with no valid Primary subtitle track after all
allowed subtitle processing. It reports retained evidence and diagnostics but
does not estimate, configure, download, or run ASR.
_Avoid_: automatic ASR fallback, empty transcript, implicit model planning

**Format projection loss**:
The recorded omission of a cue layout setting that a requested export format
cannot represent, such as WebVTT positioning in SRT. It never permits loss of
visible text, timestamps, cue order, or retained source evidence.
_Avoid_: silent conversion loss, text normalization, unsupported-style deletion

**Subtitle extraction attempt**:
One immutable write attempt for an Embedded subtitle payload. Only a
complete, size-bounded, hash-recorded attempt becomes a parseable candidate;
failed, timed-out, or interrupted writes remain `incomplete` diagnostics and
are never overwritten or selected.
_Avoid_: resumable partial payload, overwritten retry, unverified extraction

**Character-preserving subtitle normalization**:
The lossless conversion of decoded cue line endings to LF while preserving all
other Unicode code points, whitespace, punctuation, and case. It excludes
Unicode normalization, width conversion, whitespace collapse, and text repair.
_Avoid_: typography cleanup, canonical Unicode rewriting, automatic proofreading

**MediaCollection**:
The ordered set of Parts intentionally supplied for one logical content item;
its Part order is authoritative for collection virtual time.
_Avoid_: auto-discovered playlist, unordered source set

**Manual collection session**:
The user-directed assembly of a MediaCollection by submitting one public URL
at a time in presentation order and closing the collection with `结束`.
_Avoid_: playlist discovery, inferred Part ordering

**Collection closure**:
The user's `结束` signal that freezes a manual collection's URL sequence and
permits its authorized batch validation and source acquisition.
_Avoid_: incremental download, partially acquired collection

**Duplicate Part**:
Two collection entries whose acquired SourceArtifacts have the same content
hash; their presence blocks a RunPlan until the user supplies a non-duplicate
sequence.
_Avoid_: duplicate URL only, silently collapsed Part

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

**Coverage ProbeDocument**:
The packet-level immutable FFprobe JSON retained for one SourceArtifact to
derive exact stream coverage from PTS evidence rather than container duration.
_Avoid_: metadata duration, sampled packet evidence, inferred coverage

**Phase 3 test boundary**:
The offline verification boundary that uses project-owned synthetic sources and
controlled downloader substitutes, never user media or real network sources.
_Avoid_: live integration fixture, production validation, URL smoke test

**Phase 5 offline engineering boundary**:
The initial Phase 5 scope for forced alignment, voice activity detection, and
speaker diarization contracts and synthetic verification; model downloads and
real-media prototypes remain separately authorized.
_Avoid_: implicit model acquisition, real-media Phase 5 validation, model-ready pipeline

**Phase 5 analysis partition**:
Voice activity detection and speaker diarization apply to every Part in a
confirmed RunPlan, while forced alignment applies only to a Part with a Primary
subtitle track; neither analysis creates or substitutes an ASR transcript.
_Avoid_: subtitle-only audio analysis, VAD transcript, automatic ASR fallback

**Adopted alignment timing view**:
An immutable derivative of a Source subtitle artifact that retains its exact
text and records only the cue times accepted from AlignmentCandidates; it never
rewrites the Phase 4 source or readable candidate artifacts.
_Avoid_: updated source subtitle, overwritten candidate artifact, corrected transcript

**Cue-level alignment adoption**:
The independent acceptance or rejection of an AlignmentCandidate for one cue.
The resulting Adopted alignment timing view is valid only after its complete
mixed sequence passes global monotonicity, playback-coverage, and duration
reasonableness gates.
_Avoid_: track-only alignment decision, independently published cue time, partial global validation

**Alignment-untrusted Part**:
A Part whose proposed Adopted alignment timing view fails a global validity
gate. All candidate times from that alignment attempt are rejected, its
original cue times remain authoritative, and its retained diagnostics identify
the failed gate without selective automatic rollback.
_Avoid_: best-effort candidate subset, silently repaired alignment, adopted partial view

**Alignment failure fingerprint**:
The exact combination of a SourceArtifact identity, Primary subtitle track,
alignment model and rules identity, and failed global gate. Its second retained
occurrence blocks further equivalent attempts as `alignment_diagnosis_required`.
_Avoid_: generic retry count, cross-configuration failure, transient warning

**Alignment failure diagnosis**:
A structured explanation of a repeated Alignment failure fingerprint based only
on retained subtitle, candidate-time, model-output, VAD, and gate evidence. It
may conclude `root_cause_inconclusive` and never reruns a model, downloads an
asset, or reads new media evidence.
_Avoid_: diagnostic retry, hidden media probe, inferred root cause

**Alignment calibration requirement**:
The condition that an alignment model may retain candidate times but cannot
produce an Adopted alignment timing view until its confidence threshold has
been validated in an offline calibration profile.
_Avoid_: universal confidence threshold, uncalibrated adoption, score-only proof

**Alignment calibration profile**:
The retained offline evidence that validates an alignment adoption threshold for
one exact model asset hash, inference backend and version, quantization or
precision, device class, and alignment rules fingerprint. Any identity change
invalidates its adoption eligibility.
_Avoid_: model-name calibration, portable threshold, configuration-agnostic score

**Synthetic alignment calibration**:
An Alignment calibration profile evaluated only against project-owned synthetic
media. It permits adopted timing views only in synthetic verification and never
qualifies real-source time adoption.
_Avoid_: production calibration, real-media acceptance, field-quality proof

**Voice activity interval**:
An audio-evidence interval classified only as `speech_likely`, `non_speech`, or
`indeterminate`. It contains no transcript claim; subtitle coverage is compared
against it separately to derive an uncovered-speech risk.
_Avoid_: VAD transcript, silent caption gap, caption-completeness score

**Audio-coverage-constrained VAD**:
Voice activity intervals expressed as RawPtsTime half-open intervals wholly
within usable audio DecodedIntervals. Audio coverage gaps, missing usable audio,
and undecidable ranges remain `indeterminate`, never `non_speech`.
_Avoid_: container-duration silence, video-derived silence, inferred quiet interval

**VAD calibration requirement**:
The condition that a VAD model may retain candidate segments and scores but
cannot classify a formal Voice activity interval as `speech_likely` or
`non_speech`, nor derive uncovered-speech risk, until a model-specific
calibration profile validates its thresholds.
_Avoid_: uncalibrated speech flag, score-only silence claim, implicit ASR trigger

**Uncovered-speech risk evidence**:
Every non-empty intersection of a calibrated `speech_likely` Voice activity
interval and absence of Primary subtitle coverage. A versioned, calibrated
duration threshold may elevate a continuous interval to a material risk or ASR
planning recommendation but never suppresses the retained evidence.
_Avoid_: discarded short speech, VAD transcript, automatic ASR run

**Audio-state-indeterminate risk**:
An overlap between absent Primary subtitle coverage and an `indeterminate` Voice
activity interval. It reports unresolved audio evidence without claiming
uncovered speech, silence, or an ASR recommendation.
_Avoid_: uncertain speech, inferred silence, ASR trigger

**Part-local speaker label**:
An anonymous diarization label stable only within one Part, such as
`part-03:speaker-01`. It carries no cross-Part, cross-run, or real-person
identity claim.
_Avoid_: global speaker identity, voiceprint identity, inferred person name

**SpeakerTurn**:
One independently retained diarization interval with a Part-local speaker
label, RawPtsTime half-open boundaries, and confidence evidence. Overlapping
turns remain separate evidence and are never forced into a single speaker,
trimmed, or merged.
_Avoid_: exclusive speaking floor, serialized dialogue, merged overlap

**Role candidate**:
A non-identity label such as host, guest, or questioner supported only by
referenced subtitle text or explicit user metadata. It remains a candidate and
cannot be inferred from voice characteristics or diarization labels alone.
_Avoid_: voice-inferred role, gendered label, speaker identity

**Diarization calibration requirement**:
The condition that a speaker-diarization model may retain raw clustering
candidates and scores but cannot publish formal SpeakerTurns, stable Part-local
labels, or Role candidates until a model-specific calibration profile validates
its execution configuration.
_Avoid_: uncalibrated speaker turn, provisional identity, raw-cluster role

**Diarization calibration profile**:
The retained calibration evidence for one exact diarization model asset hash,
inference backend and version, quantization or precision, device class, and
rules fingerprint. A synthetic-only profile qualifies only synthetic
verification and is invalidated by any bound-identity change.
_Avoid_: portable speaker threshold, model-name-only profile, real-source proof

**Phase 5 heavy-analysis sequence**:
The serial execution order VAD, full-audio forced alignment, then speaker
diarization. Each stage retains its output, resource measurement, and model
unload evidence before the next model may load.
_Avoid_: parallel model loading, VAD-clipped alignment, unrecorded unload

**Model-release-unverified pause**:
The user-decision state entered when a completed heavy-analysis stage lacks
credible model-unload evidence. It retains completed artifacts and diagnostics,
blocks later model loads, and performs no cleanup, retry, or recovery action
until the user explicitly chooses one.
_Avoid_: forced cleanup, automatic retry, continued model loading

**Resource-envelope-exceeded pause**:
The user-decision state entered before a heavy-analysis model starts when its
high resource estimate exceeds the 24 GB envelope. It retains the estimate and
candidate configurations but does not automatically change batch size,
precision, or model.
_Avoid_: automatic quantization downgrade, implicit model substitution, over-limit execution

**Phase 5 processing authorization**:
The authority to run Phase 5 heavy analysis only after exact revalidation of
SourceArtifact hashes, audio coverage evidence, Primary subtitle track and
candidate report, selected model assets, and rules and calibration profiles.
Any drift requires a new plan rather than continuation on stale evidence.
_Avoid_: stale analysis run, warning-only drift, mutable prior plan

**Explicit model acquisition approval**:
The user's per-model authorization to acquire one proposed immutable model
asset. The preceding acquisition plan must identify official source, license,
revision, hash, bytes, project-local path, offline runtime, resource estimate,
and credential-isolation evidence; without approval it authorizes no download.
_Avoid_: phase-wide download consent, model-name approval, implied retry download

**Audio analysis workspace**:
The immutable project-owned evidence area for one Phase 5 analysis attempt. It
retains raw model outputs, calibration and gate results, VAD, alignment and
diarization candidates, and diagnostics; it is not an output directory and
Phase 9 may only promote verified artifacts from it.
_Avoid_: RunBundle, disposable model cache, publish-time regeneration

**Partial audio analysis report**:
The immutable report for an Audio analysis workspace where one or more later
stages paused or blocked after an earlier stage produced independently valid
evidence. It retains valid completed-stage artifacts and identifies missing
stages and the user decision required to continue.
_Avoid_: all-or-nothing attempt, discarded valid analysis, hidden incomplete stage

**Long-silence evidence**:
A continuous calibrated `non_speech` Voice activity interval exceeding a
versioned, calibrated duration threshold. `indeterminate` audio, coverage gaps,
and absent subtitles cannot contribute to or join a long-silence interval.
_Avoid_: caption gap silence, inferred audio gap, stitched quiet period

**Audio analysis clock**:
The RawPtsTime authority for every Voice activity interval, SpeakerTurn,
uncovered-speech risk, and Long-silence evidence. PartRelativeTime and
CollectionVirtualTime are derived views only; no Part boundary, coverage gap,
or negative PTS is scaled, filled, or clamped.
_Avoid_: normalized audio timeline, continuous collection PTS, zero-clamped time

**Audio analysis report**:
The immutable machine-readable outcome of one `vcp analyze-audio` attempt from
a confirmed RunPlan and retained subtitle candidate report. It states completed,
partial, blocked, or user-decision states; any resume explicitly references the
report and a user decision.
_Avoid_: mutable run status, interactive shell session, implicit resume

**Model-acquisition-required result**:
The per-capability result emitted when `vcp analyze-audio` lacks an explicitly
approved, identity-pinned model asset that can run offline. It proposes no
model, downloads no asset, and runs no substitute implementation.
_Avoid_: automatic model fallback, background acquisition, implicit runtime dependency

**Forced-alignment candidate**:
A model proposed for evaluation against the Phase 5 forced-alignment capability
contract. `Qwen3-ForcedAligner-0.6B` is one candidate only; it is neither a
mandatory dependency nor an approved acquisition.
_Avoid_: required Qwen dependency, default download, selected aligner

**Phase 5 capability contract**:
The model-independent interface and evidence obligations for one of forced
alignment, VAD, or speaker diarization. A model is selected only by passing the
same license, offline-runtime, resource, privacy, and calibration gates as its
alternatives.
_Avoid_: vendor-specific pipeline, model-name API, implicit preferred model

**Phase 5 model eligibility**:
The pre-acquisition candidate-matrix result `eligible`, `blocked`, or
`unsupported`. An eligible model has official source and acceptable license,
fixed revision and hash, fully offline runtime, no runtime credential or
telemetry path, a project-local dependency plan, and evidence of fitting the
24 GB envelope.
_Avoid_: downloadable candidate, unreviewed dependency, provisional eligibility

**Credential-gated model candidate**:
A candidate whose initial acquisition requires an account token, acceptance of
remote platform terms, or browser credentials. It is `blocked` even when its
installed runtime can later operate offline.
_Avoid_: manually authenticated project download, offline-after-login exception, credential prompt

**VAD candidate**:
A model proposed for evaluation against the Phase 5 VAD capability contract.
Silero VAD is one candidate only; it is neither a mandatory dependency nor an
approved acquisition, and its runtime dependencies and calibration remain
separate eligibility gates.
_Avoid_: required Silero dependency, default VAD download, selected VAD

**Diarization capability vacancy**:
The state in which no speaker-diarization candidate satisfies Phase 5 model
eligibility. The capability reports `model_acquisition_required` and does not
substitute a credential-gated or otherwise ineligible implementation.
_Avoid_: pyannote exception, implicit diarization fallback, missing-model retry

**Phase 5 offline verification boundary**:
The engineering-verification scope using only retained synthetic media, fixed
candidate-output fixtures, and controlled model-adapter substitutes. It excludes
model runtime installation, model downloads, real-model invocation, and user
media access.
_Avoid_: model smoke test, hidden dependency install, real-media validation

**Model-output projection**:
The versioned structured interpretation of one retained raw model output. Phase
5 gates read only the projection, while the raw output, projection, and adapter
version are independently hash-recorded for audit.
_Avoid_: normalized raw output, unversioned parser result, opaque gate input

**Model-output-invalid result**:
The capability result when a retained raw model output cannot be completely
projected into its versioned contract. It produces no formal audio, alignment,
or diarization evidence and never uses default values, field guesses, or a
partial projection.
_Avoid_: best-effort parser, inferred field, partial formal result

**Alignment text-contract violation**:
The result when an aligner output does anything other than propose start and end
times for existing Primary subtitle cue identities: it adds, removes, merges,
splits, or changes cue text. It produces neither an AlignmentCandidate nor an
independent transcript.
_Avoid_: aligner transcript, corrected cue text, alignment-based ASR

**Order-preserving alignment view**:
An Adopted alignment timing view that retains original cue `source_ordinal`
order while permitting valid overlapping intervals. It does not clip, force
non-overlap, or reorder cues to create a serialized timeline.
_Avoid_: de-overlapped subtitles, reordered alignment, exclusive cue sequence

**Alignment-candidate-rejected cue**:
A cue whose retained AlignmentCandidate has low calibrated confidence, lacks
confidence evidence, or falls outside usable audio coverage. Its original time
is used in the proposed view without interpolation or guessed timing.
_Avoid_: repaired cue time, interpolated alignment, discarded candidate evidence

**Language-aware alignment duration rule**:
A versioned rule in an Alignment calibration profile that evaluates cue-text
length against candidate duration for its supported language behavior. No
global character-count or word-count threshold substitutes for a missing rule.
_Avoid_: universal reading speed, language-blind duration gate, guessed language threshold

**Analysis audio stream**:
The one explicitly selected usable audio stream in a Part used by all Phase 5
audio analysis. If retained planning evidence cannot prove a unique selection,
the Part pauses as `awaiting_audio_stream_selection`; streams are never mixed,
merged, or selected by index default.
_Avoid_: mixed analysis audio, default audio stream, all-track VAD

**Analysis audio selection record**:
The immutable user choice `part-id=stream-index` bound to that audio stream's
codec, language and disposition metadata, and coverage-evidence hashes. Any
bound-evidence drift invalidates the selection and requires a new choice.
_Avoid_: persistent bare stream index, stale audio selection, mutable preference

**Analysis audio derivative**:
The hash-recorded deterministic audio representation of one Analysis audio
stream supplied to a Phase 5 model. A versioned preprocessing profile explicitly
defines sample rate, channel transformation, loudness treatment, and chunking;
no implicit resampling, downmixing, or normalization is permitted.
_Avoid_: library-default waveform, untracked downmix, mutable model input

**Analysis audio derivation toolchain**:
The revalidated pinned FFmpeg identity that creates an Analysis audio derivative
from its selected SourceArtifact stream. Models receive only the retained
derivative, never a direct library read of SourceArtifact bytes.
_Avoid_: model-owned decoder, direct source read, unpinned audio loader

**Derivative-to-source time mapping**:
The exact versioned mapping from an Analysis audio derivative's sample and chunk
coordinates to RawPtsTime. A model boundary without this mapping remains raw
output and cannot become formal VAD, alignment, or diarization evidence.
_Avoid_: float timestamp authority, guessed PTS offset, approximate audio clock

**Complete VAD partition**:
The partition of every known usable-audio coverage point into exactly one of
`speech_likely`, `non_speech`, or `indeterminate`. Model omissions, rounded
boundary gaps, and anomalous fragments become `indeterminate`, never implicit
`non_speech`.
_Avoid_: VAD gap as silence, partial audio-state map, default quiet interval

**Diarization-VAD conflict**:
The result when a raw diarization candidate overlaps calibrated `non_speech` or
`indeterminate` audio. The raw candidate remains retained, but it cannot become
a formal SpeakerTurn and is never trimmed or shifted to resolve the conflict.
_Avoid_: VAD-overridden turn, clipped speaker interval, silent model arbitration

**Alignment-VAD conflict**:
The result when an otherwise eligible AlignmentCandidate overlaps calibrated
`non_speech` audio. Its cue retains original time and the candidate is rejected;
an overlap with `indeterminate` audio is a reported risk only, not automatic
rejection.
_Avoid_: VAD-repaired alignment, speech-assumed unknown interval, ignored silence conflict

**Calibration evaluation record**:
The deterministic, hash-recorded evaluation that creates a calibration profile
from reference fixtures. It retains candidate output, expected result, chosen
thresholds, false-accept and false-reject summaries, and evaluator version; it
cannot be replaced by a manual calibration assertion.
_Avoid_: declared calibration, unversioned benchmark, unrecorded threshold tuning

**Calibration-failed result**:
The retained outcome when a candidate fails its deterministic Calibration
evaluation record. The pipeline does not automatically tune thresholds or retry;
any changed threshold, rule, or candidate combination is a new explicitly
authorized calibration experiment.
_Avoid_: automatic threshold search, silent retune, repeated calibration retry

**Phase 6 offline text-verification boundary**:
The Phase 6 engineering-verification scope that uses only retained synthetic
structured text fixtures and controlled text-model-adapter substitutes. It
excludes text-model download or installation, real-model invocation, user-media
access, and network access; it may prove evidence ownership, schema, and
unsupported-claim handling but not real-world summary quality.
_Avoid_: text-model smoke test, implicit model acquisition, real-media summary
validation

**Phase 6 textual fact source**:
The subtitle text cited by a Phase 6 factual claim. Phase 5 alignment, VAD,
speaker-turn, and caption-gap evidence may define segment structure, timing,
or uncertainty, but cannot independently support a factual claim. OCR remains
outside this source until explicitly enabled in Phase 8.
_Avoid_: voice-inferred fact, diarization-supported claim, uncited audio
conclusion

**Phase 6 evidence input and citation basis**:
The Phase 6 model may read PresentationCues to avoid proven rolling-display
duplication, but every formal factual claim cites the corresponding
NormalizedCue identities and authoritative times. Readable subtitle artifacts
are human-facing only and are never model input or claim evidence.
_Avoid_: readable-subtitle citation, presentation-only provenance, model input
from display artifact

**Cue-level factual citation**:
The minimum provenance for a Phase 6 factual claim: one or more explicitly
named NormalizedCue identities. A segment membership or time range alone is
insufficient; a claim spanning statements cites every needed cue identity.
_Avoid_: segment-only citation, timestamp-only provenance, inferred nearby
evidence

**Semantic-segment cue ownership**:
The requirement that each PresentationCue belongs to exactly one formal
SemanticSegment, preventing omission and duplicate segment aggregation. This
does not restrict Cue-level factual citation: the same NormalizedCue may support
multiple factual claims when each use is explicitly cited.
_Avoid_: overlapping cue ownership, unassigned cue, single-use factual evidence

**Cue-bound semantic boundary**:
A formal SemanticSegment boundary selected only between existing PresentationCues.
A model may propose a natural breakpoint, but deterministic adjudication chooses
only a cue boundary and never creates a model-authored timestamp or splits a cue.
_Avoid_: mid-cue segment split, generated timing authority, arbitrary timestamp

**Unsupported generated claim**:
A Phase 6 factual output whose cited NormalizedCues are absent, invalid, or do
not support the stated claim. It is excluded from formal reports without
automatic rewriting, replacement, or guessed citations; its raw output and
rejection reason remain immutable diagnostic evidence while other verified
content may proceed.
_Avoid_: uncited published claim, automatic citation repair, all-or-nothing
report failure

**Verified segment-derived summary**:
A chapter or collection summary whose entries cite validated SemanticSegment
identities rather than repeating every NormalizedCue reference. Each cited
segment must retain its complete Cue-level factual citation chain; summaries
cannot cite raw model output or cross a Part boundary to form a segment.
_Avoid_: summary from unchecked generation, cue-less segment summary,
cross-Part segment

**Cue-supported segment title**:
A SemanticSegment title with an explicit NormalizedCue citation. It may be a
concise topic label rather than a full factual sentence, but cannot introduce
an unsupported theme. If no subtitle text supports a title, the segment uses a
deterministic neutral label such as `第 2 段`.
_Avoid_: inferred thematic title, uncited heading, fabricated segment topic

**Phase 6 immutable text-analysis workspace**:
The project-owned immutable work area for one Phase 6 attempt. It retains raw
controlled-adapter output, versioned structured projection, prompt and sampling
configuration, validation results, and diagnostics without modifying prior
evidence. It does not write `outputs/`; Phase 9 alone may publish verified
artifacts without regenerating or rewriting them.
_Avoid_: mutable text report, direct RunBundle publication, overwritten model
output

**Explicit text-analysis command boundary**:
`vcp analyze-text` is the sole public command that starts a Phase 6 text
analysis attempt from explicitly named retained inputs and emits an immutable
text analysis report. `vcp resume-text-analysis` is a separate command that
names the retained report and the user's required decision before continuation.
_Avoid_: implicit text analysis, automatic resume, hidden recovery choice

**Text-analysis input revalidation**:
The pre-execution verification of the confirmed RunPlan and SourceArtifact
hashes, subtitle candidate report and selected Primary subtitle track,
PresentationCue and NormalizedCue rules and hashes, prompt and schema version,
and controlled text-adapter identity. An optional Audio analysis report also
requires its input binding and report hash to match. Any drift blocks the
attempt and requires a new one.
_Avoid_: stale text analysis, warning-only input drift, inherited audio report

**Optional audio-analysis context**:
An optional Phase 5 Audio analysis report that may contribute only its
validated structural and risk evidence to Phase 6. Its absence permits
subtitle-based text analysis with `audio_analysis=not_available` and
`audio_completeness=not_verified`; a partial report contributes no inference
about any missing stage.
_Avoid_: required audio analysis, assumed audio completeness, inferred missing
audio state

**Part-bounded semantic aggregation**:
The rule that SemanticSegments and chapters remain within one Part. A collection
summary may cite validated segments from multiple Parts while retaining each
Part identity, but it cannot create a continuous cross-Part segment or time
range.
_Avoid_: cross-Part semantic segment, merged chapter timeline, continuous
collection timestamp

**Text-model output projection**:
The shared versioned structured contract for a controlled Phase 6 adapter and
any future real text model. It contains candidate segment boundaries, titles,
segment detail, question-and-answer structure, summaries, and each cited cue
identity, with raw model output retained independently. A missing required field
or invalid schema is `model_output_invalid`: no defaults, guessed values, or
partial projection become formal text evidence.
_Avoid_: model-specific report schema, partial projected output, inferred
missing field

**Deterministically adjudicated semantic boundary**:
A formal cue-bound segment boundary selected from model-proposed cue-pair
candidates by stable versioned rules. The adjudicator rejects out-of-range,
duplicate, empty, or coverage-breaking candidates and does not invent a theme
boundary; when no valid candidate remains, it may use only the conservative
single-segment-per-Part fallback to retain exactly-once cue ownership.
_Avoid_: generated boundary repair, implicit theme segmentation, unassigned cue

**Two-level text-analysis failure handling**:
An invalid or incomplete Text-model output projection invalidates the entire
attempt as `model_output_invalid`. After a valid projection exists, each title,
factual item, question-and-answer element, or summary entry is validated
independently; failed items become retained diagnostics while independently
verified content remains in the same report.
_Avoid_: partial schema recovery, all-or-nothing validated report, silent item
repair

**Cue-supported question-and-answer structure**:
Optional Phase 6 question-and-answer fields emitted only when cited subtitle
text establishes the question-and-answer relationship. Ordinary narration,
ambiguous dialogue, or a speaker-label change alone stays as detailed segment
content and is never forced into a question-and-answer form.
_Avoid_: inferred Q&A, diarization-created question, mandatory interview schema

**Cue-supported person and role**:
A person or role emitted in Phase 6 only when cited subtitle text explicitly
self-identifies or names the person or role, or when explicit user metadata
supplies it. Anonymous Part-local speaker labels may be retained as labels but
cannot establish a social role or real identity.
_Avoid_: voice-inferred role, diarization-based identity, inferred host label

**Cue-supported structured detail**:
A numeric value, entity, example, condition, caveat, or unresolved item emitted
as an independent Phase 6 factual field with at least one NormalizedCue
citation. The model may not perform unit conversion, numeric inference, entity
disambiguation, or external supplementation; when a citation supports only
part, only that supported portion is emitted.
_Avoid_: calculated number, enriched entity, inferred caveat, external fact

**Cue-preserved source contradiction**:
Two or more incompatible claims in subtitle evidence that Phase 6 reports
without choosing truth or merging them into a corrected conclusion. The report
attributes each claim to the video and cites every conflicting NormalizedCue;
any explicit conflict notice cites both sides.
_Avoid_: model-resolved contradiction, corrected video claim, one-sided conflict

**Cue-supported unresolved question**:
A question explicitly raised in cited subtitle text for which the validated
evidence scope contains no answer or conclusion. It cannot arise solely from a
model's uncertainty, omitted subtitle coverage, or information it wishes the
video had supplied.
_Avoid_: model-invented follow-up, caption-gap question, implicit unknown

**Subtitle-unavailable text Part**:
A Part with no valid Primary subtitle track after allowed subtitle processing.
Phase 6 retains its CollectionVirtualTime range and unavailable reason as
`text_content=unavailable`, but emits no SemanticSegment, content fact, or
summary for it. Other Parts may proceed, and any collection summary declares
the omitted range.
_Avoid_: empty semantic segment, inferred text from audio, silent partial
collection

**Persistent subtitle audio-completeness limitation**:
The required `audio_completeness=not_verified` notice on the report front page,
Part summaries, chapter summaries, and collection summary whenever text content
derives from a subtitle track rather than a future verified full ASR. Phase 5
alignment or VAD evidence does not remove this limitation.
_Avoid_: alignment-proved transcript completeness, VAD-proved caption coverage,
unqualified subtitle summary

**Controlled offline text adapter**:
The Phase 6 engineering-verification substitute for a real text model. It is
identified by its implementation version, fixed input and output fixture hashes,
projection schema, prompt template, and sampling-configuration hashes. It
proves only the engineering contract, is not entered as a real model asset, and
cannot earn `model_audited` status or a real-world quality qualification.
_Avoid_: synthetic model registration, implied model qualification, fixture-only
quality claim

**Text-model identity invalidation**:
The rule that a change to a future real text model asset, revision, quantization,
inference backend, prompt, sampling configuration, projection schema, or
evidence rule requires a new Phase 6 text-analysis attempt and new verification.
Controlled-adapter results cannot be reused as real-model proof or upgraded to
real-world quality status.
_Avoid_: inherited model qualification, stale text report, adapter-to-model
promotion

**Text-analysis unavailable result and offline exit gate**:
When no controlled adapter or explicitly authorized real model is available,
`vcp analyze-text` retains an immutable report with
`model_acquisition_required` or `controlled_adapter_unavailable` and produces
no SemanticSegments. Phase 6 can exit only after controlled-adapter contract,
evidence validation, diagnostics, and offline tests pass; its result is
`passed_offline`, never a domain-quality or production validation claim.
_Avoid_: empty content report, implied model fallback, offline quality approval

**Phase 6 report language boundary**:
Semantic-segment titles, detailed content, question-and-answer structures,
chapter summaries, and collection summaries default to Chinese. NormalizedCue
text and evidence excerpts retain their source language, including mixed
Chinese and English; translation cannot alter source subtitles and, if later
introduced, is a separate artifact.
_Avoid_: translated source evidence, English-forced mixed-language subtitle,
embedded translation artifact

**Technical text-processing block**:
A possibly overlapping cue collection used only to fit Phase 6 model context.
It is not a SemanticSegment, chapter, or citation range. Cross-block candidates
are deduplicated by complete cue identities and passed to one adjudicator, so
each PresentationCue still gains exactly one formal SemanticSegment owner.
_Avoid_: block-as-segment, block citation, duplicate cross-block ownership

**Length-unconstrained semantic segment**:
A non-empty SemanticSegment with cue-bound boundaries and exactly-once
PresentationCue ownership, without a fixed duration, token, or cue-count target.
Short and long segments are both valid when the adjudicated evidence warrants
them; the report retains the model proposal and adjudication reason.
_Avoid_: fixed-window segment, target-length chapter, duration-based split

**Part-local chapter aggregation**:
An optional chapter formed from one or more consecutive verified SemanticSegments
in the same Part. It retains every member segment identity, and its title and
summary cite those members. A chapter may contain one segment or be absent;
fixed counts, durations, and cosmetic table-of-contents grouping are forbidden.
_Avoid_: cross-Part chapter, forced chapter count, unreferenced chapter summary

**Text-analysis decision pause boundary**:
Phase 6 pauses only for a user choice that changes model, configuration, or
evidence identity, including future model selection or download, a
resource-envelope configuration change, prompt or schema change, or replacement
of an invalid adapter. Invalid individual content, absent valid semantic
candidates, and subtitle-unavailable Parts are evidence outcomes that create a
partial report without pausing.
_Avoid_: pause on ordinary validation result, implicit model replacement,
automatic configuration change

**Serialized text-model execution**:
A future real Phase 6 text model runs under the global one-large-model rule and
loads only after Phase 5 heavy audio models have recorded complete release
evidence. It records resource measurements and its own unload evidence before
any later large model may load. A controlled offline adapter loads no model but
still records its execution resource measurement.
_Avoid_: concurrent audio-and-text model load, unmeasured text execution,
unverified model release

**Text-analysis resource-envelope pause**:
When a future real text model's conservative pre-execution estimate exceeds the
24 GiB envelope, Phase 6 retains an immutable
`resource_envelope_exceeded` report and pauses for a user decision. It never
automatically change quantization, context size, model, or sampling. A controlled
adapter records its own resource result honestly without pretending to trigger a
real-model downgrade path.
_Avoid_: automatic quantization downgrade, hidden context reduction, silent
model replacement

**Text-generation attempt provenance**:
The immutable record for one Phase 6 generation attempt: complete model
identity, prompt, sampling configuration, input cue manifest and hashes, raw
output, and output-projection hash. Text equality across attempts is not
required, but changing a seed or any sampling setting creates a new attempt
without overwriting the earlier report.
_Avoid_: unrecorded generation variance, overwritten retry, deterministic-text
claim

**No automatic text-generation retry**:
A failed controlled-adapter or future real-model generation is retained as its
own immutable attempt and never rerun automatically. Even with identical
configuration, a retry requires an explicit user-started new attempt so changed
output or resource state cannot overwrite the original evidence.
_Avoid_: transparent retry, overwritten failure, automatic model rerun

**Text analysis report**:
The immutable machine-readable `text-analysis-report.json` for one Phase 6
attempt. It is the authoritative record of structured segments, chapters,
collection summary, cue and segment citations, statuses, limitations, and
diagnostic pointers. Any readable Markdown is deterministically rendered from
the verified JSON and remains in the same workspace until Phase 9 publication.
_Avoid_: Markdown-as-authority, independently edited report, pre-publication
RunBundle

**Text-analysis diagnostic visibility**:
The readable Phase 6 report exposes only actionable summaries: counts of
removed unsupported content, subtitle-unavailable Parts, input and audio
limitations, and decision-pause reasons. Raw model output, rejected text, and
item-level validation details remain in immutable workspace diagnostics reached
by stable identifiers, never mixed into formal content prose.
_Avoid_: raw generated claim in report body, hidden diagnostics, user-facing
validation dump

**Restricted raw text-model diagnostic**:
The unmodified raw text-model or controlled-adapter output retained only as
project-local audit evidence. It is excluded from formal reports and default
Phase 9 publication; any later export for user inspection requires separate
explicit authorization because it can contain unverified assertions.
_Avoid_: published raw generation, implicit diagnostic export, validated-content
assumption

**Versioned text prompt template**:
A project-managed complete Phase 6 prompt template retained with a path, version,
and SHA-256. Each analysis attempt binds that template plus the hash of its
actual rendered prompt and a cue-input manifest; source subtitle text belongs
only in the attempt input, never copied into the shared template.
_Avoid_: prompt hash without template, unversioned prompt change, source text in
shared configuration

**Versioned Phase 6 generation rules**:
The independently versioned prompt template, output schema, and evidence rules
that define one Phase 6 attempt. Any content change creates new versions and a
new immutable attempt; engineering fixes may add versions but never overwrite a
past version or reinterpret a retained report.
_Avoid_: mutable prompt revision, retroactive report change, implicit rule
upgrade

**Versioned text-report renderer**:
The hash-recorded renderer that deterministically creates a readable Markdown
report from one verified Text analysis report JSON. A formatting-only renderer
update may create and retain an additional Markdown rendition without changing
the JSON, facts, citations, or states; any semantic rendering change requires a
new text-analysis attempt.
_Avoid_: edited Markdown authority, overwritten rendition, semantic formatting
fix

**Text analysis report status**:
A Phase 6 report is `complete` when every Part with a valid Primary subtitle
track has at least one verified SemanticSegment and no user decision is pending.
It is `partial` when subtitle-unavailable Parts exist, a Part uses the
conservative single-segment fallback, or a later stage pauses after validated
content exists. It is `failed` when input revalidation, whole output
projection, or execution fails before any verified segment exists. Rejected
individual content remains a diagnostic and does not alone lower report status.
_Avoid_: failed-on-one-claim, complete-with-unavailable-Part, hidden fallback

**Conservative single-segment fallback**:
The deterministic one-SemanticSegment-per-Part outcome used only when no valid
model-proposed semantic boundary remains. It preserves complete cue ownership
but does not establish semantic segmentation quality, so the Text analysis
report is always `partial`.
_Avoid_: successful semantic segmentation, complete fallback report, invented
boundary

**Offline citation-support oracle**:
The pre-annotated synthetic-fixture relationship used in Phase 6 offline tests
to determine whether a cited cue supports a candidate item. Current verification
checks cue existence, field scope, explicit counterexamples, and structural
consistency only; it does not add a second model or semantic-similarity threshold.
Real-model semantic-support review requires separate authorization.
_Avoid_: synthetic semantic scorer, self-validating model, implicit real-model
fact check

**Append-only human text-analysis review**:
An independent immutable review record for a future real-model text analysis. It
identifies the reviewer, scope, time, reference material, and accepted or
rejected items; it may label only the reviewed scope `human_verified` and
cannot rewrite model output, cue evidence, diagnostics, or unreviewed content.
_Avoid_: edited model report, blanket human approval, mutable review result

**Phase 6 offline human-review boundary**:
The current Phase 6 scope defines and tests the append-only human-review record
only with synthetic fixtures. It runs no real review and emits no
`human_verified` result; real human review waits for separately authorized real
models and media.
_Avoid_: synthetic human approval, current-phase real review, implied quality
certification

**Phase 6 offline fixture coverage**:
The minimum controlled-fixture set proving mixed Chinese and English, rolling
repetition, legal overlapping cues, subtitle-unavailable Parts, multi-Part
collections, valid and invalid cue citations, invalid whole projections,
individually unsupported claims, question-and-answer/person/number/
contradiction/unresolved-question structures, technical-block boundaries,
single-segment fallback, input drift, resource pauses, and unavailable adapters.
It proves contract behavior only, not real-world content quality.
_Avoid_: happy-path-only fixture, synthetic quality claim, uncovered failure
state

**Phase 6 deterministic contract verification**:
The offline test standard for Phase 6: assert schemas, cue-identity existence,
fixture-defined citation relationships, exactly-once cue ownership, Part
boundaries, diagnostic states, hashes, and immutability. It does not score the
controlled adapter's wording, segmentation taste, or summary quality as a
real-model capability claim.
_Avoid_: synthetic prose benchmark, fixture-derived quality score, subjective
golden summary

**ProbeProjection**:
The typed projection of a ProbeDocument used for decisions; unknown fields
remain only in the raw document.
_Avoid_: best-effort probe, inferred metadata
