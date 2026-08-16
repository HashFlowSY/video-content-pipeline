# 04 — Property-test time-base invariants and deepen time/interval units

**What to build:** The plan-mandated property layer for time: hypothesis
strategies generating time bases, signed raw PTS values, and
`HalfOpenInterval`s, plus deterministic-profile property tests proving:
exactness (no float drift) of RawPtsTime → PartRelativeTime →
CollectionVirtualTime conversions and their inverses where defined;
half-open interval algebra (emptiness, containment, intersection edge
cases at shared boundaries); coverage merging idempotence and
order-independence; monotonic cue order stability under permutation.
Alongside, deepen the thin conventional unit tests in
`tests/unit/test_timecode.py` / `test_coverage.py` /
`test_collection_timeline.py` for concrete regression anchors (negative
PTS, zero-length intervals, extreme time bases). Any genuine bug the
properties expose is fixed in this ticket with stage/schema version
discipline observed.

**Blocked by:** 01
**Status:** open
**Labels:** ready-for-agent

- [ ] Strategies cover signed PTS (incl. negative), varied time bases,
      degenerate intervals
- [ ] Round-trip exactness properties pass under the deterministic profile
- [ ] Coverage merge idempotence/order-independence properties pass
- [ ] Conventional unit additions land in the three thin files
- [ ] Suite green; deterministic across two consecutive full runs

## Comments
