# Treat yt-dlp as a pinned external prerequisite

Phase 3 may use the already installed `yt-dlp` only as a read-only external
prerequisite. Each URL PlanReport records its path, version, and content hash;
the same evidence must validate before acquisition, and a missing or changed
binary blocks the RunPlan rather than being copied, downloaded, or updated.

## Considered Options

- Pinned external prerequisite: accepted because the available system binary
  can be audited without an unapproved tool download or global change.
- Project-managed downloader: deferred because acquiring and pinning a new
  project tool requires separate source and download authorization.
