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
from video_content_pipeline.timeline import (
    CollectionTimeline,
    TimelinePart,
    TimelineValidationError,
)


def test_collection_timeline_compacts_nonzero_and_negative_pts_origins() -> None:
    first = TimelinePart(
        part_id="part-1",
        coverage=HalfOpenInterval(start=ExactTime(-2), end=ExactTime(1)),
    )
    second = TimelinePart(
        part_id="part-2",
        coverage=HalfOpenInterval(start=ExactTime(10), end=ExactTime(15)),
    )
    timeline = CollectionTimeline(parts=(first, second))

    first_start = timeline.map_part_relative_time(
        "part-1",
        PartRelativeTime.from_raw(
            RawPtsTime(raw_pts=-2_000, time_base=ExactTime(1, 1_000)),
            PartCoverageStart(RawPtsTime(raw_pts=-2_000, time_base=ExactTime(1, 1_000))),
        ),
    )
    first_time = timeline.map_part_relative_time(
        "part-1",
        PartRelativeTime.from_raw(
            RawPtsTime(raw_pts=-1_500, time_base=ExactTime(1, 1_000)),
            PartCoverageStart(RawPtsTime(raw_pts=-2_000, time_base=ExactTime(1, 1_000))),
        ),
    )
    second_time = timeline.map_part_relative_time(
        "part-2",
        PartRelativeTime.from_raw(
            RawPtsTime(raw_pts=12_000, time_base=ExactTime(1, 1_000)),
            PartCoverageStart(RawPtsTime(raw_pts=10_000, time_base=ExactTime(1, 1_000))),
        ),
    )

    assert first_start.time == ExactTime(0)
    assert first_time.time == ExactTime(1, 2)
    assert second_time.time == ExactTime(5)
    assert second_time.part_relative_time.raw_pts_time.raw_pts == 12_000
    assert second_time.part_relative_time.raw_pts_time.time_base == ExactTime(1, 1_000)


def test_collection_timeline_retains_hard_part_boundary_at_a_shared_virtual_time() -> None:
    first = TimelinePart(
        part_id="part-1",
        coverage=HalfOpenInterval(start=ExactTime(4), end=ExactTime(6)),
    )
    second = TimelinePart(
        part_id="part-2",
        coverage=HalfOpenInterval(start=ExactTime(-8), end=ExactTime(-5)),
    )
    timeline = CollectionTimeline(parts=(first, second))

    first_end = timeline.map_part_relative_time(
        "part-1",
        PartRelativeTime.from_raw(
            RawPtsTime(raw_pts=6, time_base=ExactTime(1)),
            PartCoverageStart(RawPtsTime(raw_pts=4, time_base=ExactTime(1))),
        ),
    )
    second_start = timeline.map_part_relative_time(
        "part-2",
        PartRelativeTime.from_raw(
            RawPtsTime(raw_pts=-8, time_base=ExactTime(1)),
            PartCoverageStart(RawPtsTime(raw_pts=-8, time_base=ExactTime(1))),
        ),
    )

    assert first_end.time == second_start.time == ExactTime(2)
    assert first_end.part_id == "part-1"
    assert second_start.part_id == "part-2"
    assert first_end.part_relative_time.raw_pts_time != second_start.part_relative_time.raw_pts_time


def test_collection_timeline_accumulates_exact_offsets_across_three_parts() -> None:
    parts = tuple(
        TimelinePart(
            part_id=f"part-{index}",
            coverage=HalfOpenInterval(
                start=ExactTime(index * 100),
                end=ExactTime(index * 100 + 2),
            ),
        )
        for index in range(3)
    )
    timeline = CollectionTimeline(parts=parts)

    third_start = timeline.map_part_relative_time(
        "part-2",
        PartRelativeTime.from_raw(
            RawPtsTime(raw_pts=200, time_base=ExactTime(1)),
            PartCoverageStart(RawPtsTime(raw_pts=200, time_base=ExactTime(1))),
        ),
    )

    # Two preceding 2-second Parts place part-2's start at collection time 4.
    assert third_start.time == ExactTime(4)


def test_timeline_part_cannot_be_built_from_zero_length_coverage() -> None:
    with pytest.raises(TimeValidationError, match="start must be before end") as error:
        TimelinePart(
            part_id="degenerate",
            coverage=HalfOpenInterval(start=ExactTime(5), end=ExactTime(5)),
        )

    assert error.value.reason == "interval_invalid"


def test_collection_timeline_accepts_a_coordinate_exactly_at_the_coverage_endpoint() -> None:
    timeline = CollectionTimeline(
        parts=(
            TimelinePart(
                part_id="only",
                coverage=HalfOpenInterval(start=ExactTime(0), end=ExactTime(3)),
            ),
        )
    )

    endpoint = timeline.map_part_relative_time(
        "only",
        PartRelativeTime.from_raw(
            RawPtsTime(raw_pts=3, time_base=ExactTime(1)),
            PartCoverageStart(RawPtsTime(raw_pts=0, time_base=ExactTime(1))),
        ),
    )

    assert endpoint.time == ExactTime(3)


def test_collection_timeline_maps_exactly_under_an_extreme_time_base() -> None:
    time_base = ExactTime(1, 48_000)
    timeline = CollectionTimeline(
        parts=(
            TimelinePart(
                part_id="hi-rate",
                coverage=HalfOpenInterval(
                    start=RawPtsTime(-48_000, time_base).time,
                    end=RawPtsTime(48_000, time_base).time,
                ),
            ),
        )
    )

    midpoint = timeline.map_part_relative_time(
        "hi-rate",
        PartRelativeTime.from_raw(
            RawPtsTime(raw_pts=1, time_base=time_base),
            PartCoverageStart(RawPtsTime(raw_pts=-48_000, time_base=time_base)),
        ),
    )

    assert midpoint.time == ExactTime(48_001, 48_000)


def test_collection_timeline_rejects_a_coordinate_from_another_part_or_beyond_coverage() -> None:
    timeline = CollectionTimeline(
        parts=(
            TimelinePart(
                part_id="part-1",
                coverage=HalfOpenInterval(start=ExactTime(0), end=ExactTime(2)),
            ),
        )
    )
    another_part_coordinate = PartRelativeTime.from_raw(
        RawPtsTime(raw_pts=1, time_base=ExactTime(1)),
        PartCoverageStart(RawPtsTime(raw_pts=-1, time_base=ExactTime(1))),
    )
    beyond_coverage = PartRelativeTime.from_raw(
        RawPtsTime(raw_pts=3, time_base=ExactTime(1)),
        PartCoverageStart(RawPtsTime(raw_pts=0, time_base=ExactTime(1))),
    )

    with pytest.raises(TimelineValidationError, match="coverage start") as mismatch:
        timeline.map_part_relative_time("part-1", another_part_coordinate)
    with pytest.raises(TimelineValidationError, match="coverage endpoint") as beyond:
        timeline.map_part_relative_time("part-1", beyond_coverage)

    assert mismatch.value.reason == "part_coverage_mismatch"
    assert beyond.value.reason == "part_relative_out_of_coverage"
