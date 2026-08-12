# 05 -- Validate evidence-bound segment content

**What to build:** Segment content is published only when every item has valid
NormalizedCue provenance and conforms to the Phase 6 factual boundaries.

**Blocked by:** 04 -- Adjudicate cue-bound semantic segments.

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] Require Cue-level factual citations for titles, details, numbers,
  entities, examples, conditions, caveats, and all other factual items.
- [ ] Support only cue-established Q&A, people/roles, contradictions, and
  unresolved questions; reject voice-inferred roles, truth decisions, external
  facts, conversions, and invented questions.
- [ ] Retain `unsupported_generated_claim` diagnostics per rejected item
  without silently repairing citations or discarding independently verified
  content.
