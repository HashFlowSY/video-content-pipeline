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
**Status:** done
**Labels:** ready-for-agent

- [x] Strategies cover signed PTS (incl. negative), varied time bases,
      degenerate intervals
- [x] Round-trip exactness properties pass under the deterministic profile
- [x] Coverage merge idempotence/order-independence properties pass
- [x] Conventional unit additions land in the three thin files
- [x] Suite green; deterministic across two consecutive full runs

## Comments

Done in `4fd5fa0` (2026-08-16). New `tests/property/` layer
(`test_time_interval_properties.py`, 12 properties under the deterministic gate
profile) plus concrete regression anchors added to the three thin unit files.
No production bug surfaced — the exact-rational core (ExactTime/Fraction,
half-open coverage merge, `_cue_order_key` totality) held under all properties,
so the change is test-only. Suite 1090 green; ruff + mypy(src) clean; two-axis
code review clean (standards: dead `assume` + `rng: object` typing tidied; spec:
explicit zero-length unit anchor added to coverage and timeline contexts).
