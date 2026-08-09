# 09 -- Approve synthetic fixture corpus

Category: enhancement
Status: resolved
Labels: enhancement

**What to build:** A concrete, reviewable plan for the retained synthetic media
fixture corpus that covers the completed Phase 2 time, probe, coverage, and
subtitle contracts before any media tool is invoked.

**Blocked by:** 08 -- Apply proven rolling-caption de-duplication.

- [x] Define versioned fixture recipes and expected evidence for differing
  stream starts, gaps, signed PTS, compact Part mapping, and subtitle
  round-trip cases.
- [x] State FFmpeg and FFprobe identity, exact planned invocations, expected
  output sizes, hashes, probe evidence, resource limits, and retention rules.
- [x] Receive explicit fixture-generation approval; no media fixture is
  generated, downloaded, replaced, or deleted before that approval.

## Comments

2026-08-09: Submitted `plans/PHASE-02/09-FIXTURE-CORPUS-PROPOSAL.md` for
explicit approval. It defines three project-owned media fixtures and three
literal subtitle inputs, fixed FFmpeg/FFprobe paths and version, complete
planned command arguments, expected evidence, size limits, resource bounds,
and immutable retention. No Python, FFmpeg, FFprobe, fixture, package,
download, model, paid API, user-media, or CLI command ran. Exact byte counts
and SHA-256 values remain intentionally unassigned until Ticket 10 is
explicitly authorized to generate the retained bytes; the approved manifest
must record them immediately after generation. The ticket now awaits the
fixture-generation approval stated in the proposal.

2026-08-09: Explicit approval received: "Approve Ticket 09's Phase 2 fixture
corpus proposal. Ticket 10 may create and execute only the listed project-local
fixture recipe and FFmpeg/FFprobe commands, retain every output and failure,
and record actual hashes and evidence. It may not download, overwrite, delete,
access user media, or add a media-facing CLI." Ticket 09 is resolved and
Ticket 10 is unblocked. No media tool or fixture-generation command ran while
recording this approval.
