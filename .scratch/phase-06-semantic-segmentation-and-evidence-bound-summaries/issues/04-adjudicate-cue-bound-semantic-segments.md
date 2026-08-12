# 04 -- Adjudicate cue-bound semantic segments

**What to build:** Valid model-proposed cue-pair boundaries become formal
SemanticSegments with exactly-once PresentationCue ownership.

**Blocked by:** 03 -- Version text generation and rendering contracts.

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] Permit final boundaries only between PresentationCues; reject duplicate,
  empty, out-of-range, and coverage-breaking candidates.
- [ ] Deduplicate overlapping technical-block candidates by complete cue ID and
  preserve Part boundaries.
- [ ] Use only the one-segment-per-Part conservative fallback when no valid
  candidate remains, retaining reason and `partial` status.
