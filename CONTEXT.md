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

**ProbeProjection**:
The typed projection of a ProbeDocument used for decisions; unknown fields
remain only in the raw document.
_Avoid_: best-effort probe, inferred metadata
