# 05 -- Assemble compact CollectionVirtualTime

Category: enhancement
Status: ready-for-agent
Labels: enhancement, ready-for-agent

**What to build:** A collection-facing mapping that concatenates ordered Part
coverage into contiguous `CollectionVirtualTime` while preserving each Part's
authoritative raw coordinate and hard boundary.

**Blocked by:** 04 -- Derive StreamCoverage from DecodedIntervals.

- [ ] The first Part begins at collection virtual time zero and every later
  Part begins at the previous Part's exact coverage endpoint.
- [ ] Encoder-origin PTS gaps and unrelated container duration do not create
  artificial collection gaps.
- [ ] Tests cover nonzero and negative source PTS origins, compact mapping, and
  the prohibition on cross-Part cue merging.
