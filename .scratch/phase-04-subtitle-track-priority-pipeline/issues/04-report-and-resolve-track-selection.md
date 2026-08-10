# 04 -- Preserve bounded subtitle-processing failures

**What to build:** A user receives durable, truthful diagnostics for ambiguous
encodings, unsupported subtitle types, extraction interruption, size limits,
and resource failures, and can explicitly resolve an allowed encoding choice.

**Blocked by:** 01 -- Process one verified subtitle track end to end; 03 -- Resolve ambiguous subtitle-track selection explicitly.

**Status:** resolved
**Labels:** ready-for-agent

- [x] Ambiguous encoding waits for explicit recorded decoder choice; lossy
  replacement and charset guessing never create a valid candidate.
- [x] Unsupported image/ASS tracks, source or tool drift, disk preflight
  failure, output limits, timeouts, and interruptions retain diagnostics and
  never become selectable candidates.
- [x] Retried extraction uses a new immutable attempt while prior incomplete
  evidence remains retained and excluded from parsing or selection.

## Comments

2026-08-10: Added bounded failure handling to the subtitles CLI. Candidate reports
now retain an attempt ID, source codec, and decoder evidence. Strict UTF-8 and
BOM-marked UTF-8/UTF-16 remain automatic; other encodings require an explicit
--decoder part-id=stream-index=encoding choice, which can resume a retained
ambiguous payload without re-extraction. Workspace disk preflight, 256 MiB
payload limit, 300-second timeout, interruption, unsupported codecs, and
revalidation drift all produce retained non-selectable diagnostics. Re-running
extraction creates a new immutable workspace attempt and leaves incomplete
payload bytes untouched. Offline verification passed: 131 tests, Ruff,
formatter check, and strict Mypy. The review follow-up added an FFmpeg
filesystem cap, retained resource-failure diagnostics, and rejected decoder
choices for non-ambiguous streams.
