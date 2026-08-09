# 08 -- Apply proven rolling-caption de-duplication

Category: enhancement
Status: resolved
Labels: enhancement

**What to build:** Conservative presentation-only rolling-caption handling
that removes tokens only when the approved exact local proof exists and keeps
all uncertain repetition visible with provenance.

**Blocked by:** 07 -- Produce ordered PresentationCue exports.

- [x] Token removal requires same-Part, same-track, stable-order adjacency,
  exact contiguous normalized overlap, strict later extension, and overlapping
  or contiguous cue intervals.
- [x] A full-text duplicate is omitted only when both exact endpoints also
  match; fuzzy, semantic, and edit-distance similarity never authorizes
  deletion.
- [x] Tests distinguish proven rolling accumulation, exact duplicates, real
  spoken repetition, and `possible_duplicate`, while recording correction and
  source-token provenance.

## Comments

2026-08-09: Implemented immutable presentation corrections and diagnostics.
The library removes only exact normalized suffix/prefix overlap from
stable-order adjacent cues in the same Part and track when intervals overlap or
touch; it omits full duplicates only with exact endpoints. Other observed exact
repetition remains visible with `possible_duplicate` provenance. TDD red
failed first for the absent interfaces; the focused subtitle suite passed with
17 tests, and Ruff, strict Mypy, and the full suite passed with 44 tests. No
media tools, network, package, model, user-media, or CLI action occurred.
