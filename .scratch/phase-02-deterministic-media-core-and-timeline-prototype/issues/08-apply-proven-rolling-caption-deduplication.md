# 08 -- Apply proven rolling-caption de-duplication

Category: enhancement
Status: ready-for-agent
Labels: enhancement, ready-for-agent

**What to build:** Conservative presentation-only rolling-caption handling
that removes tokens only when the approved exact local proof exists and keeps
all uncertain repetition visible with provenance.

**Blocked by:** 07 -- Produce ordered PresentationCue exports.

- [ ] Token removal requires same-Part, same-track, stable-order adjacency,
  exact contiguous normalized overlap, strict later extension, and overlapping
  or contiguous cue intervals.
- [ ] A full-text duplicate is omitted only when both exact endpoints also
  match; fuzzy, semantic, and edit-distance similarity never authorizes
  deletion.
- [ ] Tests distinguish proven rolling accumulation, exact duplicates, real
  spoken repetition, and `possible_duplicate`, while recording correction and
  source-token provenance.
