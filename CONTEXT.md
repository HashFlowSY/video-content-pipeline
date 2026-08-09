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
