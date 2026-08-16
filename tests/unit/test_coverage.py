from __future__ import annotations

import pytest

from video_content_pipeline.coverage import DecodedInterval, derive_stream_coverage
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval, TimeValidationError


def test_coverage_uses_exact_outer_envelope_and_retains_internal_gaps() -> None:
    result = derive_stream_coverage(
        (
            DecodedInterval(start=ExactTime(4), end=ExactTime(6)),
            DecodedInterval(start=ExactTime(0), end=ExactTime(2)),
            DecodedInterval(start=ExactTime(1), end=ExactTime(3)),
        )
    )

    assert result.coverage == HalfOpenInterval(start=ExactTime(0), end=ExactTime(6))
    assert result.gaps == (HalfOpenInterval(start=ExactTime(3), end=ExactTime(4)),)
    assert result.diagnostics == ()


def test_coverage_preserves_a_negative_priming_like_stream_start() -> None:
    result = derive_stream_coverage(
        (DecodedInterval(start=ExactTime(-21, 1_000), end=ExactTime(1, 2)),)
    )

    assert result.coverage == HalfOpenInterval(start=ExactTime(-21, 1_000), end=ExactTime(1, 2))
    assert result.gaps == ()


def test_coverage_remains_stream_specific_when_stream_starts_differ() -> None:
    audio_coverage = derive_stream_coverage(
        (DecodedInterval(start=ExactTime(-1, 100), end=ExactTime(2)),)
    )
    video_coverage = derive_stream_coverage(
        (DecodedInterval(start=ExactTime(0), end=ExactTime(2)),)
    )

    assert audio_coverage.coverage == HalfOpenInterval(start=ExactTime(-1, 100), end=ExactTime(2))
    assert video_coverage.coverage == HalfOpenInterval(start=ExactTime(0), end=ExactTime(2))


def test_incomplete_decoded_endpoint_makes_coverage_indeterminate() -> None:
    result = derive_stream_coverage(
        (
            DecodedInterval(start=ExactTime(0), end=ExactTime(1)),
            DecodedInterval(start=ExactTime(1), end=None),
        )
    )

    assert result.coverage is None
    assert result.gaps == ()
    assert [diagnostic.reason for diagnostic in result.diagnostics] == ["coverage_indeterminate"]
    assert result.diagnostics[0].path == "decoded_intervals[1].end"


def test_coverage_is_identical_across_input_orderings() -> None:
    ordered = derive_stream_coverage(
        (
            DecodedInterval(start=ExactTime(0), end=ExactTime(2)),
            DecodedInterval(start=ExactTime(5), end=ExactTime(7)),
            DecodedInterval(start=ExactTime(9), end=ExactTime(10)),
        )
    )
    shuffled = derive_stream_coverage(
        (
            DecodedInterval(start=ExactTime(9), end=ExactTime(10)),
            DecodedInterval(start=ExactTime(0), end=ExactTime(2)),
            DecodedInterval(start=ExactTime(5), end=ExactTime(7)),
        )
    )

    assert ordered == shuffled
    assert ordered.gaps == (
        HalfOpenInterval(start=ExactTime(2), end=ExactTime(5)),
        HalfOpenInterval(start=ExactTime(7), end=ExactTime(9)),
    )


def test_coverage_is_unchanged_by_duplicated_intervals() -> None:
    intervals = (
        DecodedInterval(start=ExactTime(0), end=ExactTime(2)),
        DecodedInterval(start=ExactTime(5), end=ExactTime(7)),
    )

    assert derive_stream_coverage(intervals) == derive_stream_coverage(intervals + intervals)


def test_coverage_absorbs_a_fully_contained_interval_without_a_gap() -> None:
    result = derive_stream_coverage(
        (
            DecodedInterval(start=ExactTime(0), end=ExactTime(10)),
            DecodedInterval(start=ExactTime(3), end=ExactTime(4)),
        )
    )

    assert result.coverage == HalfOpenInterval(start=ExactTime(0), end=ExactTime(10))
    assert result.gaps == ()


def test_decoded_interval_rejects_a_zero_length_span_at_construction() -> None:
    with pytest.raises(TimeValidationError, match="start must be before end") as error:
        DecodedInterval(start=ExactTime(2), end=ExactTime(2))

    assert error.value.reason == "interval_invalid"


def test_coverage_treats_adjacent_intervals_as_contiguous() -> None:
    result = derive_stream_coverage(
        (
            DecodedInterval(start=ExactTime(0), end=ExactTime(2)),
            DecodedInterval(start=ExactTime(2), end=ExactTime(5)),
        )
    )

    assert result.coverage == HalfOpenInterval(start=ExactTime(0), end=ExactTime(5))
    assert result.gaps == ()


def test_coverage_rejects_contradictory_duration_metadata_as_an_input() -> None:
    result = derive_stream_coverage((DecodedInterval(start=ExactTime(0), end=ExactTime(2)),))

    assert result.coverage == HalfOpenInterval(start=ExactTime(0), end=ExactTime(2))
    with pytest.raises(TypeError, match="unexpected keyword argument 'duration_metadata'"):
        derive_stream_coverage(  # type: ignore[call-arg]
            (DecodedInterval(start=ExactTime(0), end=ExactTime(2)),),
            duration_metadata=ExactTime(999),
        )
