# 09 -- Recompute affected Parts with carried-forward provenance

**What to build:** Affected-Part re-analysis (ADR 0046): a new immutable
text-analysis attempt that regenerates affected Parts against the enhanced or
verbatim cue basis, carries unaffected Parts forward with explicit provenance
links, and recomputes chapters and the collection summary from the combined
set.

**Blocked by:** 07, 08

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] New attempt in a new immutable workspace; the prior report is never
  overwritten and every carried-forward Part links to its source report.
- [ ] Affected Parts obey all Phase 6 contracts unchanged (cue-bound
  boundaries, exactly-once ownership, cue-level citations, deterministic
  adjudication, conservative fallback).
- [ ] Chapters and collection aggregation recomputed over regenerated plus
  carried-forward Parts; omitted or unavailable Parts stay declared.
- [ ] Report records which Parts were regenerated, which were carried
  forward, and why.
