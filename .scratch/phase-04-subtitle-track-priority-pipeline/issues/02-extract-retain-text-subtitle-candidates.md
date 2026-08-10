# 02 -- Produce common-format readable subtitles

**What to build:** A user with a selected SRT, WebVTT, or `mov_text` subtitle
track receives source VTT/SRT compatibility exports and a readable subtitle
view whose allowed transformations are traceable.

**Blocked by:** 01 -- Process one verified subtitle track end to end.

**Status:** resolved
**Labels:** ready-for-agent

- [x] The workflow accepts each supported text subtitle format and preserves
  visible text, cue time, and cue order in source artifacts.
- [x] SRT format limitations are exposed as recorded projection loss, never as
  silent deletion of source text or time.
- [x] Readable output removes only approved presentation markup and proven
  rolling overlap, with each correction linked to source provenance.

## Comments

2026-08-10: Implemented source-preserving WebVTT and SRT artifacts for accepted
SRT, WebVTT, and `mov_text` candidates. WebVTT layout settings retained in the
source VTT artifact are recorded as `format_projection_loss` in the SRT
projection. Readable VTT removes only verified rolling overlap and closed
`b`, `i`, `u`, and `font` tags; every removal records source cue, token, and
character provenance, while other markup remains visible with diagnostics.
