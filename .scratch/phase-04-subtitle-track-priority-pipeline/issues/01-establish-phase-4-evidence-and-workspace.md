# 01 -- Process one verified subtitle track end to end

**What to build:** A user can run `vcp subtitles` for a confirmed RunPlan with
one UTF-8 embedded SRT track and receive a revalidated, time-checked source
subtitle candidate plus its auditable candidate report.

**Blocked by:** None -- can start immediately.

**Status:** resolved
**Labels:** ready-for-agent

- [x] The CLI rejects an unconfirmed or drifted plan without reading subtitle
  bytes and records the blocking diagnosis.
- [x] One supported UTF-8 candidate is extracted into a retained workspace
  attempt, mapped to exact coverage, and atomically accepted or rejected.
- [x] The CLI returns a machine-readable candidate report and source candidate
  artifact without accessing a network, model, sidecar, or output publication.

## Comments

2026-08-10: Implemented the first Phase 4 vertical slice through `vcp subtitles
<plan-id> --json`. Confirmed RunPlans now revalidate SourceArtifact hashes,
pinned FFmpeg identity, and versioned subtitle rules before any subtitle
extraction. A supported embedded SRT track is extracted through argv-only
FFmpeg into a unique immutable workspace, strict-UTF-8 decoded, atomically
checked against playback coverage, and retained as raw payload plus mapped
source-candidate evidence. Unknown or drifted plans, unsupported tracks, and
partial failed extraction attempts return machine-readable retained reports
without network, model, sidecar, publication, or real FFmpeg use in tests.
Offline verification passed: 116 tests, Ruff, format check, and strict Mypy.
