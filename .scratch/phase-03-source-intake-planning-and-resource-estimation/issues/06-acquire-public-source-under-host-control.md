# 06 -- Acquire public source under host control

**What to build:** A user-approved single public URL or closed Manual
collection can acquire SourceArtifacts only within its explicit authorization,
then follows the same evidence, estimate, and PlanReport path as local input.

**Blocked by:** 02 -- Probe a media-qualified local plan; 05 -- Authorize URL
and manual collection.

**Status:** resolved

- [x] The downloader is a Pinned external tool with project-local cache and
  temporary paths, no browser state, and no automatic update or fallback.
- [x] An unapproved redirect, media host, or HTTPS downgrade stops acquisition
  with a retained diagnostic rather than silently continuing.
- [x] Acquired bytes become SourceArtifacts and duplicate content becomes a
  Duplicate Part rather than a repeated timeline entry.
- [x] A successful URL source enters the same ProbeDocument, StreamCoverage,
  estimate, and confirmation workflow as a local source.

## Comments

2026-08-10: Implemented controlled public acquisition. The pinned yt-dlp
workflow routes metadata and transfer through an in-process same-host proxy,
uses project-local cache and temporary paths, then snapshots a `public_url`
SourceArtifact. `filtered` blocks without a separately configured filtered
transport and never falls back to direct. URL and closed Manual collections now
use the same strict ProbeDocument, StreamCoverage, estimate, and confirmation
flow as local sources. Duplicate acquired content blocks as `duplicate_part`
without repeating a SourceArtifact. Offline controlled-tool tests cover
success, host escalation, HTTPS downgrade, size mismatch, provenance redaction,
and duplicate content.
