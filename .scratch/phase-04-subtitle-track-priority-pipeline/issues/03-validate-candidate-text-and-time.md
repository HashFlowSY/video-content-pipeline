# 03 -- Resolve ambiguous subtitle-track selection explicitly

**What to build:** A user with multiple valid embedded subtitle tracks receives
an immutable `awaiting_subtitle_selection` report and can explicitly select a
Part and stream to resume candidate generation.

**Blocked by:** 01 -- Process one verified subtitle track end to end.

**Status:** resolved
**Labels:** ready-for-agent

- [x] The initial CLI operation validates all candidates independently and
  never resolves a valid tie by order, disposition, or interactive guessing.
- [x] An explicit Part/stream selection appends evidence and resumes only its
  retained candidate report after revalidation.
- [x] Invalid candidates remain diagnostic evidence and cannot become a
  repaired or merged fallback.

## Comments

2026-08-10: `vcp subtitles <plan-id>` now enters
`awaiting_subtitle_selection` when a Part has multiple valid embedded tracks,
retaining every candidate and a per-Part diagnostic. The explicit
`--resume <report-id> --select <part-id>=<stream-index>` path revalidates the
RunPlan and subtitle rules before it writes a new immutable child report with
the selection evidence; it never re-extracts bytes. Controlled CLI tests prove
the pause/resume path, revalidation block, and invalid-track rejection.
