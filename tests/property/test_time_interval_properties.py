"""Phase 10 Workstream A: property tests for the exact time/interval core.

These are the plan-mandated time-base invariants. They run under the
deterministic gate profile (imported for its registration side effect), so the
example sequence is a pure function of the source: two consecutive full runs
draw the identical examples and either both pass or both fail.

The properties prove, over generated signed PTS values, varied time bases, and
degenerate-adjacent intervals, that:

* ``RawPtsTime`` / ``PartRelativeTime`` / ``CollectionVirtualTime`` conversions
  are exact rationals with no float drift, and their defined inverses round-trip.
* ``HalfOpenInterval`` construction rejects empty/inverted bounds and its
  ``overlaps`` matches the set-theoretic half-open definition at shared
  boundaries.
* ``derive_stream_coverage`` merging is order-independent and idempotent under
  duplication, with an envelope and gaps consistent with the inputs.
* the monotonic cue order key is total, so cue order is stable under any input
  permutation even when intervals coincide.
"""

from __future__ import annotations

import random
from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.support import hypothesis_profiles  # noqa: F401  (registers the gate profile)
from video_content_pipeline.coverage import DecodedInterval, derive_stream_coverage
from video_content_pipeline.subtitles import NormalizedCue, RawCue, _cue_order_key
from video_content_pipeline.timecode import (
    ExactTime,
    HalfOpenInterval,
    PartCoverageStart,
    PartRelativeTime,
    RawPtsTime,
    TimeValidationError,
)
from video_content_pipeline.timeline import CollectionTimeline, TimelinePart

# --- strategies -------------------------------------------------------------

_PTS_BOUND = 10**6
_DEN_BOUND = 10**6


@st.composite
def exact_times(
    draw: st.DrawFn,
    *,
    min_numerator: int = -_PTS_BOUND,
    max_numerator: int = _PTS_BOUND,
) -> ExactTime:
    """Draw an arbitrary exact rational time, un-normalized numerator/denominator."""

    numerator = draw(st.integers(min_value=min_numerator, max_value=max_numerator))
    denominator = draw(st.integers(min_value=1, max_value=_DEN_BOUND))
    return ExactTime(numerator, denominator)


@st.composite
def time_bases(draw: st.DrawFn) -> ExactTime:
    """Draw a strictly positive time base such as ``1/1000`` or ``1/48000``."""

    numerator = draw(st.integers(min_value=1, max_value=1_000))
    denominator = draw(st.integers(min_value=1, max_value=_DEN_BOUND))
    return ExactTime(numerator, denominator)


@st.composite
def raw_pts_times(draw: st.DrawFn) -> RawPtsTime:
    """Draw a signed raw PTS anchored to a positive time base."""

    raw_pts = draw(st.integers(min_value=-_PTS_BOUND, max_value=_PTS_BOUND))
    return RawPtsTime(raw_pts=raw_pts, time_base=draw(time_bases()))


@st.composite
def half_open_intervals(draw: st.DrawFn) -> HalfOpenInterval:
    """Draw a non-empty half-open interval by extending a start by a positive span."""

    start = draw(exact_times())
    span = draw(exact_times(min_numerator=1))
    return HalfOpenInterval(start=start, end=start + span)


@st.composite
def decoded_interval_lists(draw: st.DrawFn) -> list[DecodedInterval]:
    """Draw a non-empty list of complete decoded intervals."""

    intervals = draw(st.lists(half_open_intervals(), min_size=1, max_size=8))
    return [DecodedInterval(start=interval.start, end=interval.end) for interval in intervals]


# --- RawPtsTime / PartRelativeTime / CollectionVirtualTime exactness --------


@given(raw_pts_times())
def test_raw_pts_time_is_the_exact_rational_product_without_float_drift(
    raw_time: RawPtsTime,
) -> None:
    expected = raw_time.raw_pts * raw_time.time_base.as_fraction()

    assert raw_time.time.as_fraction() == expected
    assert isinstance(raw_time.time.as_fraction(), Fraction)


@given(raw_pts_times(), st.integers(min_value=0, max_value=2 * _PTS_BOUND))
def test_part_relative_time_round_trips_through_its_coverage_start(
    coverage_raw: RawPtsTime, forward_ticks: int
) -> None:
    # Anchor a source coordinate at or after the coverage start on the same time
    # base so the Part-relative value is defined (non-negative).
    source_raw = RawPtsTime(
        raw_pts=coverage_raw.raw_pts + forward_ticks,
        time_base=coverage_raw.time_base,
    )
    coverage_start = PartCoverageStart(raw_pts_time=coverage_raw)

    relative = PartRelativeTime.from_raw(source_raw, coverage_start)

    assert relative.time == source_raw.time - coverage_raw.time
    # The inverse translation recovers the original source coordinate exactly.
    assert relative.time + coverage_start.time == source_raw.time
    assert relative.raw_pts_time == source_raw


@st.composite
def timeline_with_coordinate(
    draw: st.DrawFn,
) -> tuple[CollectionTimeline, str, PartRelativeTime, ExactTime]:
    """Draw a multi-Part timeline plus a valid coordinate inside a chosen Part."""

    part_count = draw(st.integers(min_value=1, max_value=4))
    time_base = draw(time_bases())
    parts: list[TimelinePart] = []
    starts: list[int] = []
    lengths: list[int] = []
    for index in range(part_count):
        start_pts = draw(st.integers(min_value=-_PTS_BOUND, max_value=_PTS_BOUND))
        length_ticks = draw(st.integers(min_value=1, max_value=_PTS_BOUND))
        coverage = HalfOpenInterval(
            start=RawPtsTime(start_pts, time_base).time,
            end=RawPtsTime(start_pts + length_ticks, time_base).time,
        )
        parts.append(TimelinePart(part_id=f"part-{index}", coverage=coverage))
        starts.append(start_pts)
        lengths.append(length_ticks)

    target = draw(st.integers(min_value=0, max_value=part_count - 1))
    # A raw PTS anywhere within the target Part's inclusive tick span keeps the
    # Part-relative value within observed coverage.
    inside_pts = draw(
        st.integers(min_value=starts[target], max_value=starts[target] + lengths[target])
    )
    coverage_start = PartCoverageStart(RawPtsTime(starts[target], time_base))
    relative = PartRelativeTime.from_raw(RawPtsTime(inside_pts, time_base), coverage_start)

    collection_start = ExactTime(0)
    for length in lengths[:target]:
        collection_start += RawPtsTime(length, time_base).time

    return CollectionTimeline(parts=tuple(parts)), parts[target].part_id, relative, collection_start


@given(timeline_with_coordinate())
def test_collection_virtual_time_offsets_by_the_exact_preceding_duration(
    case: tuple[CollectionTimeline, str, PartRelativeTime, ExactTime],
) -> None:
    timeline, part_id, relative, expected_collection_start = case

    virtual = timeline.map_part_relative_time(part_id, relative)

    assert virtual.part_id == part_id
    assert virtual.time == expected_collection_start + relative.time
    # The coordinate retains its exact source evidence for the inverse mapping.
    assert virtual.part_relative_time is relative
    assert virtual.time - expected_collection_start == relative.time


# --- HalfOpenInterval algebra ----------------------------------------------


@given(exact_times(), exact_times())
def test_half_open_interval_is_non_empty_or_rejected(a: ExactTime, b: ExactTime) -> None:
    if a < b:
        interval = HalfOpenInterval(start=a, end=b)
        assert interval.start < interval.end
    else:
        with pytest.raises(TimeValidationError) as error:
            HalfOpenInterval(start=a, end=b)
        assert error.value.reason == "interval_invalid"


@given(half_open_intervals(), half_open_intervals())
def test_overlaps_matches_the_half_open_set_definition_and_is_symmetric(
    first: HalfOpenInterval, second: HalfOpenInterval
) -> None:
    expected = max(first.start, second.start) < min(first.end, second.end)

    assert first.overlaps(second) is expected
    assert second.overlaps(first) is expected


@given(half_open_intervals())
def test_overlaps_is_reflexive_and_adjacent_intervals_never_overlap(
    interval: HalfOpenInterval,
) -> None:
    assert interval.overlaps(interval) is True

    span = interval.end - interval.start
    after = HalfOpenInterval(start=interval.end, end=interval.end + span)
    # Sharing only the excluded end boundary is not an overlap.
    assert interval.overlaps(after) is False
    assert after.overlaps(interval) is False


@given(half_open_intervals(), exact_times(min_numerator=1), exact_times(min_numerator=1))
def test_a_contained_interval_always_overlaps_its_container(
    outer: HalfOpenInterval, left_pad: ExactTime, right_pad: ExactTime
) -> None:
    # Grow the outer interval on both sides so a strictly-interior interval exists.
    container = HalfOpenInterval(start=outer.start - left_pad, end=outer.end + right_pad)

    assert container.start <= outer.start
    assert outer.end <= container.end
    assert container.overlaps(outer) is True
    assert outer.overlaps(container) is True


# --- coverage merge order-independence and idempotence ----------------------


@given(decoded_interval_lists(), st.randoms(use_true_random=False))
def test_coverage_is_independent_of_decoded_interval_order(
    intervals: list[DecodedInterval], rng: random.Random
) -> None:
    shuffled = list(intervals)
    # ``st.randoms`` gives a seeded Random so the shuffle replays deterministically.
    rng.shuffle(shuffled)

    assert derive_stream_coverage(shuffled) == derive_stream_coverage(intervals)


@given(decoded_interval_lists())
def test_coverage_is_idempotent_under_duplicated_intervals(
    intervals: list[DecodedInterval],
) -> None:
    once = derive_stream_coverage(intervals)
    twice = derive_stream_coverage([*intervals, *intervals])

    assert twice == once


@given(decoded_interval_lists())
def test_coverage_envelope_and_gaps_are_consistent_with_the_inputs(
    intervals: list[DecodedInterval],
) -> None:
    result = derive_stream_coverage(intervals)

    assert result.diagnostics == ()
    assert result.coverage is not None
    starts = [interval.start for interval in intervals]
    ends = [interval.end for interval in intervals]
    assert result.coverage.start == min(starts)
    assert result.coverage.end == max(ends)

    for gap in result.gaps:
        # Gaps are interior to the envelope...
        assert result.coverage.start <= gap.start
        assert gap.end <= result.coverage.end
        # ...and no observed interval covers any part of a gap.
        for interval in intervals:
            covering = HalfOpenInterval(start=interval.start, end=interval.end)
            assert not covering.overlaps(gap)


# --- monotonic cue order stability -----------------------------------------


def _normalized_cue(start: ExactTime, end: ExactTime, ordinal: int) -> NormalizedCue:
    raw = RawCue(
        source_text="x",
        interval=HalfOpenInterval(start=start, end=end),
        source_ordinal=ordinal,
        part_id="part",
        track_id="track",
        source_format="srt",
    )
    return NormalizedCue(raw_cue=raw, text="x", tokens=("x",))


@st.composite
def normalized_cue_lists(draw: st.DrawFn) -> list[NormalizedCue]:
    """Draw cues with unique ordinals but possibly coincident intervals."""

    intervals = draw(st.lists(half_open_intervals(), min_size=1, max_size=8))
    return [
        _normalized_cue(interval.start, interval.end, ordinal)
        for ordinal, interval in enumerate(intervals)
    ]


@given(normalized_cue_lists(), st.randoms(use_true_random=False))
def test_cue_order_is_total_and_stable_under_permutation(
    cues: list[NormalizedCue], rng: random.Random
) -> None:
    canonical = sorted(cues, key=_cue_order_key)
    shuffled = list(cues)
    rng.shuffle(shuffled)

    reordered = sorted(shuffled, key=_cue_order_key)
    keys = [_cue_order_key(cue) for cue in reordered]

    # Permutation-independent: any input order yields the identical sequence.
    assert [cue.source_ordinal for cue in reordered] == [cue.source_ordinal for cue in canonical]
    # Monotonic non-decreasing under the (start, end, ordinal) key.
    assert keys == sorted(keys)
    # The ordinal tie-breaker makes the key total: coincident intervals still
    # order deterministically rather than by insertion.
    assert len(set(keys)) == len(keys)


@given(exact_times(), exact_times(min_numerator=1), st.integers(min_value=1, max_value=1_000))
def test_coincident_interval_cues_order_by_source_ordinal(
    start: ExactTime, span: ExactTime, ordinal_gap: int
) -> None:
    end = start + span
    earlier = _normalized_cue(start, end, ordinal=0)
    later = _normalized_cue(start, end, ordinal=ordinal_gap)

    ordered = sorted([later, earlier], key=_cue_order_key)

    assert [cue.source_ordinal for cue in ordered] == [0, ordinal_gap]
