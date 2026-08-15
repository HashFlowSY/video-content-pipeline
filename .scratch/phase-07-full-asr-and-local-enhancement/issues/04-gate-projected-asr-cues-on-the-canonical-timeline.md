# 04 -- Gate projected ASR cues on the canonical timeline

**What to build:** Deterministic gates admitting projected ASR cues as
candidate evidence: exact rational times inside actual stream coverage,
monotonic order, half-open intervals, no processing duplication, plausible
duration-to-text relation. Rejected cues keep structured reasons.

**Blocked by:** 03

**Status:** done
**Labels:** ready-for-agent

- [ ] Reuse the Phase 2 exact-time types and coverage evidence; no float
  accumulation, no container-duration guessing.
- [ ] Reject (never repair) out-of-coverage, non-monotonic, negative-duration,
  and duplicated cues; retain rejection diagnostics per cue.
- [ ] Map cue times across RawPtsTime / PartRelativeTime /
  CollectionVirtualTime per the existing rules; Part boundaries stay hard.
