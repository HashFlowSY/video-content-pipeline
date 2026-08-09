# Phase 3: Source Intake, Planning, and Resource Estimation

Type: enhancement
Status: resolved
Labels: enhancement
Phase: 3
Published: 2026-08-09

## Problem Statement

The pipeline has a verified deterministic core, but it cannot yet accept a
source safely. A user needs to hand it one local media file, one public URL, or
an explicitly ordered multi-Part collection and receive evidence-backed planning
information without accidentally authorizing filesystem discovery, network
fallback, model downloads, media mutation, or content processing.

The user also needs a planning result that can honestly distinguish between
work that is safe to perform now, work that needs a separate confirmation, and
work that is unavailable in the current phase. A container duration, a filename
extension, an unpinned binary, or a URL with a hidden token cannot be treated as
enough evidence to begin a future run.

## Solution

Provide a dependency-free Phase 3 planning surface centered on `vcp plan`.
Local media becomes a project-owned SourceArtifact only after a double hash.
Public URLs are handled under explicit URL access mode, host, transport, and
redaction rules. A Manual collection session records the user-supplied Part
order and only permits access after `结束` closes the collection.

For every attempt, retain a PlanReport. It contains SourceArtifact identity,
Pinned external-tool evidence, strict ProbeDocument and Coverage ProbeDocument
results, metadata-only SubtitleTrackCandidate information, deterministic disk
headroom, and a Phase-bounded estimate. A user separately authorizes Full
decode validation only after seeing its three-point estimate. A final Plan
confirmation revalidates all evidence before creating an immutable RunPlan.

The primary test seam is the `vcp plan` CLI contract, exercised against a
temporary project root with SourceArtifacts and controlled external-tool
substitutes. That seam captures the observable authorization, reporting, state,
and persistence behavior without requiring a live URL or user media. Existing
time, ProbeDocument, StreamCoverage, and CLI-environment tests remain the lower
level prior art for focused regression coverage.

## User Stories

1. As a local-media user, I want to submit one explicit regular file, so that
   the pipeline never scans unrelated folders for media.
2. As a local-media user, I want directories, symbolic links, pipes, devices,
   and standard input rejected, so that a source is a stable byte sequence.
3. As an auditor, I want the original local input hashed before and after its
   copy, so that a SourceArtifact proves its bytes did not change in transit.
4. As a local-media user, I want my original file left untouched, so that
   planning never changes or moves my source.
5. As an auditor, I want identical input bytes to reuse one SourceArtifact, so
   that content identity is based on evidence rather than a changing path.
6. As a planner, I want insufficient disk headroom reported before acquisition,
   so that the project does not start an input copy it cannot safely retain.
7. As a public-URL user, I want to name `filtered` or `direct` explicitly, so
   that the requested network boundary is visible and reviewable.
8. As a public-URL user, I want a missing or failed URL access mode rejected,
   so that the pipeline cannot silently widen my authorization.
9. As a privacy-conscious user, I want raw URLs, query parameters, fragments,
   credentials, and signed locators excluded from persistent records, so that
   a report does not become a replayable secret.
10. As a security-conscious user, I want HTTP to require separate approval,
    so that a plaintext source is never mistaken for a transport-verified one.
11. As a security-conscious user, I want a redirect, new media host, or HTTPS
    downgrade surfaced as a Host escalation, so that it cannot happen silently.
12. As a collection user, I want to submit multi-Part URLs one at a time in my
    intended presentation order, so that CollectionVirtualTime follows the
    content sequence I actually mean.
13. As a collection user, I want the interface to remind me to submit links in
    strict order and use `结束` to close the set, so that partial input does not
    cause early network activity.
14. As a collection user, I want duplicate URLs and Duplicate Parts detected,
    so that the same content is not silently counted twice on the timeline.
15. As an auditor, I want each Pinned external tool recorded by path, version,
    and hash, so that a plan can detect environmental drift.
16. As a planner, I want strict structural ProbeDocuments and packet-level
    Coverage ProbeDocuments, so that stream evidence never falls back to a
    filename, text output, or metadata-duration guess.
17. As a timeline consumer, I want media qualification based on usable stream
    evidence and determinate StreamCoverage, so that later timing work starts
    from observed PTS boundaries.
18. As a subtitle user, I want Phase 3 to list SubtitleTrackCandidates without
    acquiring subtitle text, so that subtitle processing remains in Phase 4.
19. As a user of a potentially long source, I want a three-point decode estimate
    before Full decode validation begins, so that I control the expensive step.
20. As a user, I want initial estimates marked low confidence when they come
    from a Decode throughput profile rather than matching observed history.
21. As an auditor, I want Full decode validation to produce no derived media,
    so that planning verifies decodability without becoming a processing run.
22. As a user, I want every successful or blocked planning attempt retained as a
    PlanReport, so that failure does not erase the evidence explaining it.
23. As a user, I want `plan decode` and final Plan confirmation to be separate,
    so that approving preflight work is not confused with approving a RunPlan.
24. As an auditor, I want a stale PlanReport rejected when its SourceArtifact,
    tool identity, disk headroom, or configuration changes, so that a RunPlan
    always refers to evidence that still holds.
25. As a user, I want unavailable later-stage model work labeled
    `unavailable/not_estimated`, so that no ASR, OCR, or model download is
    implied by a Phase 3 plan.
26. As a maintainer, I want the full behavior tested offline at the `vcp plan`
    seam, so that the contract is repeatable without live URLs or user media.
27. As a maintainer, I want source, tool, probe, and plan artifacts retained in
    the Phase 3 inventory, so that later review can reconstruct what happened.
28. As a product owner, I want the phase to remain explicitly unvalidated for
    production, so that engineering progress is not confused with real-world
    quality acceptance.

## Implementation Decisions

- The implementation uses Python's standard library and already locked quality
  tools only. No runtime package, lockfile, model, or downloader acquisition is
  part of this feature.
- A SourceArtifact is content-addressed by SHA-256, project-owned, and read
  only after the original and copied bytes match. Failed pending bytes are
  retained rather than silently deleted.
- Disk headroom is deterministic planned growth plus the greater of 1 GiB or
  five percent of planned growth. Insufficient headroom blocks before source
  acquisition.
- Local candidates are explicit regular files only. Their original path is not
  required in persistent SourceArtifact metadata.
- URL authorization is explicit per source. Persistent provenance contains
  only redacted scheme, host, and path; no raw URL serializes into PlanReport,
  RunPlan, diagnostics, or inventory.
- Manual collection is an in-memory interaction boundary. It validates link
  shape and duplicates locally, uses input order as Part order, and permits
  batch source access only after `结束`.
- URL acquisition cannot use an automatic fallback from `filtered` to `direct`.
  A host change or HTTPS downgrade is a separate authorization event.
- Existing FFprobe, FFmpeg, and yt-dlp executables are external prerequisites,
  not managed project dependencies. Any permitted use records and revalidates
  path, version, and SHA-256 identity.
- Inspection preserves raw structural and packet-level JSON, projects known
  fields through the Phase 2 strict parser, derives coverage from PTS evidence,
  and exposes SubtitleTrackCandidates as metadata only.
- Full decode validation is a null-output FFmpeg operation over every audio and
  video stream. It runs only after the explicit decode confirmation command.
- Decode estimates use a versioned Decode throughput profile initially and
  transition to matching observed project history when that evidence exists.
- PlanReports have report IDs and immutable storage independent from RunPlans.
  A report may be blocked, awaiting decode confirmation, or ready for final
  confirmation. Only a ready, revalidated report can create a plan ID.
- URL acquisition first obtains downloader metadata without writing media,
  validates every declared destination against the explicit host and transport
  authorization, and only then permits a pinned downloader to write under
  project-local temporary paths. All downloader traffic goes through a local
  authorization proxy that rejects host escalation and HTTPS downgrade during
  both metadata and transfer. `filtered` rejects when no separately configured
  filtered transport exists; it never falls back to `direct`. Automated
  verification uses controlled downloader substitutes only; no live downloader
  is invoked in tests.

## Testing Decisions

- The highest and primary seam is the `vcp plan` CLI. Tests should observe
  emitted status, report/plan IDs, persistent artifacts, and diagnostics rather
  than private helper sequencing.
- Controlled external-tool substitutes and temporary project roots are used for
  CLI behavior. They verify argv construction, identity drift, blocked states,
  and confirmation transitions without opening a network connection.
- SourceArtifact behavior is tested with regular temporary files, including
  double-hash identity, reuse, non-regular rejection, and original-file
  preservation.
- URL and Manual collection behavior is tested with parsed strings only. Tests
  cover explicit modes, HTTP opt-in, redaction, host escalation, ordering,
  `结束`, and duplicate rejection without calling a downloader.
- Probe and coverage behavior builds on the existing Phase 2 ProbeDocument and
  StreamCoverage unit/integration precedent. Tests use retained synthetic JSON
  or controlled records and assert exact PTS-derived results.
- Decode tests inspect three-point estimates and null-output command behavior;
  a fixture-backed decode is run only through the explicit confirmation path.
- Plan persistence tests assert immutability, report/plan separation, stale
  evidence rejection, and no raw-URL persistence.
- Existing environment CLI, fixture-backed core, timecode, coverage, subtitle,
  and timeline suites remain required regression gates.

## Out of Scope

- ASR, forced alignment, VAD, diarization, OCR, LLMs, subtitle-text retrieval,
  semantic segmentation, summaries, and model download or management.
- `vcp run`, RunBundles, recovery orchestration, output publication, and
  production validation.
- Browser state, cookies, credentials, private links, DRM, CAPTCHA, paid APIs,
  automatic playlist discovery, and automatic URL-mode fallback.
- Real URL tests, user-media test fixtures, external content acquisition in
  automated tests, and automatic cleanup of input, cache, diagnostics, or
  reports.

## Further Notes

- The conversation resolved the domain vocabulary and long-lived boundaries in
  the project context and ADR set; this spec intentionally uses those canonical
  terms.
- The main test seam is recorded here as the synthesis of the already adopted
  Phase 3 approach. It does not require another user interview.
- The implementation is complete and verified through the local-source, URL,
  collection, inspection, estimate, decode-confirmation, and final-plan CLI
  contracts. A retained synthetic fixture has produced a report awaiting its
  own Decode preflight confirmation; that report remains user-gated and is not
  required to establish the implemented Phase 3 capability.
- The Phase 3 inventory is the source of truth for created, modified, read,
  and retained evidence. The project remains in engineering development and
  must not be marked `production_validated`.

## Comments

2026-08-10: Phase 3 completed and verified. All seven child tickets are
resolved. The final project-local gate recorded 111 passing tests, passing
Ruff and format checks, and strict Mypy with no issues. Verification remained
offline and used controlled external-tool substitutes or retained synthetic
fixtures only; no user media, live URL, model, paid API, or production
validation was used.
