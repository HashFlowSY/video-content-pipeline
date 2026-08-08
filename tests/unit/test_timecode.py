from __future__ import annotations

import pytest

from video_content_pipeline.timecode import (
    ExactTime,
    HalfOpenInterval,
    PartCoverageStart,
    PartRelativeTime,
    RawPtsTime,
    TimeValidationError,
)


def test_exact_time_arithmetic_and_ordering_use_normalized_rational_values() -> None:
    one_third = ExactTime(1, 3)
    one_sixth = ExactTime(1, 6)

    assert one_third + one_sixth == ExactTime(1, 2)
    assert one_third - one_sixth == ExactTime(1, 6)
    assert ExactTime(2, 4) == ExactTime(1, 2)
    assert one_sixth < one_third


def test_raw_pts_time_preserves_negative_source_evidence_exactly() -> None:
    raw_time = RawPtsTime(raw_pts=-5, time_base=ExactTime(1, 2))

    assert raw_time.raw_pts == -5
    assert raw_time.time == ExactTime(-5, 2)


def test_adjacent_half_open_intervals_do_not_overlap() -> None:
    first = HalfOpenInterval(start=ExactTime(-1, 2), end=ExactTime(0))
    second = HalfOpenInterval(start=ExactTime(0), end=ExactTime(1, 2))

    assert first.start == ExactTime(-1, 2)
    assert first.end == second.start
    assert first.overlaps(second) is False


@pytest.mark.parametrize(
    ("start", "end"),
    [(ExactTime(0), ExactTime(0)), (ExactTime(1), ExactTime(0))],
)
def test_half_open_interval_rejects_zero_length_and_inverted_bounds(
    start: ExactTime, end: ExactTime
) -> None:
    with pytest.raises(TimeValidationError, match="start must be before end") as error:
        HalfOpenInterval(start=start, end=end)

    assert error.value.reason == "interval_invalid"


def test_part_relative_time_translates_from_coverage_start_without_losing_raw_pts() -> None:
    raw_time = RawPtsTime(raw_pts=-450, time_base=ExactTime(1, 900))
    coverage_start = PartCoverageStart(
        raw_pts_time=RawPtsTime(raw_pts=-900, time_base=ExactTime(1, 900))
    )

    relative_time = PartRelativeTime.from_raw(raw_time, coverage_start=coverage_start)

    assert relative_time.time == ExactTime(1, 2)
    assert relative_time.coverage_start == coverage_start
    assert relative_time.coverage_start.raw_pts_time.time == ExactTime(-1)
    assert relative_time.raw_pts_time == raw_time
    assert relative_time.raw_pts_time.time == ExactTime(-1, 2)


def test_part_relative_time_rejects_source_time_before_coverage_start() -> None:
    raw_time = RawPtsTime(raw_pts=-901, time_base=ExactTime(1, 900))
    coverage_start = PartCoverageStart(
        raw_pts_time=RawPtsTime(raw_pts=-900, time_base=ExactTime(1, 900))
    )

    with pytest.raises(TimeValidationError, match="must not be negative") as error:
        PartRelativeTime.from_raw(raw_time, coverage_start=coverage_start)

    assert error.value.reason == "part_relative_negative"


def test_repeated_exact_translation_matches_the_direct_raw_source_coordinate() -> None:
    raw_time = RawPtsTime(raw_pts=-1_001, time_base=ExactTime(1, 900))
    coverage_start = PartCoverageStart(
        raw_pts_time=RawPtsTime(raw_pts=-1_800, time_base=ExactTime(1, 900))
    )

    relative_time = PartRelativeTime.from_raw(raw_time, coverage_start)

    assert relative_time.time + coverage_start.time == raw_time.time
    assert (raw_time.time - coverage_start.time) + coverage_start.time == raw_time.time
