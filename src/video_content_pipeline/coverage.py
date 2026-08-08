"""Coverage evidence derived from observed decoded stream intervals."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from video_content_pipeline.timecode import ExactTime, HalfOpenInterval


@dataclass(frozen=True)
class DecodedInterval:
    """A decoded stream interval whose boundaries may be unavailable."""

    start: ExactTime | None
    end: ExactTime | None

    def __post_init__(self) -> None:
        if self.start is not None and self.end is not None:
            HalfOpenInterval(start=self.start, end=self.end)


@dataclass(frozen=True)
class CoverageDiagnostic:
    """A machine-readable reason why stream coverage cannot be determined."""

    reason: str
    path: str
    message: str


@dataclass(frozen=True)
class StreamCoverage:
    """The observed coverage envelope, its internal gaps, and diagnostics."""

    coverage: HalfOpenInterval | None
    gaps: tuple[HalfOpenInterval, ...]
    diagnostics: tuple[CoverageDiagnostic, ...]


def derive_stream_coverage(decoded_intervals: Sequence[DecodedInterval]) -> StreamCoverage:
    """Derive stream coverage solely from complete decoded-interval evidence."""

    intervals: list[HalfOpenInterval] = []
    for ordinal, decoded_interval in enumerate(decoded_intervals):
        if decoded_interval.start is None:
            return _indeterminate_result(
                f"decoded_intervals[{ordinal}].start",
                "Coverage requires an observed decoded interval start.",
            )
        if decoded_interval.end is None:
            return _indeterminate_result(
                f"decoded_intervals[{ordinal}].end",
                "Coverage requires an observed decoded interval end.",
            )
        intervals.append(HalfOpenInterval(decoded_interval.start, decoded_interval.end))

    if not intervals:
        return _indeterminate_result(
            "decoded_intervals",
            "Coverage requires at least one observed decoded interval.",
        )

    ordered_intervals = sorted(intervals, key=lambda interval: (interval.start, interval.end))
    gaps: list[HalfOpenInterval] = []
    current_end = ordered_intervals[0].end
    for interval in ordered_intervals[1:]:
        if current_end < interval.start:
            gaps.append(HalfOpenInterval(start=current_end, end=interval.start))
        if current_end < interval.end:
            current_end = interval.end

    return StreamCoverage(
        coverage=HalfOpenInterval(start=ordered_intervals[0].start, end=current_end),
        gaps=tuple(gaps),
        diagnostics=(),
    )


def _indeterminate_result(path: str, message: str) -> StreamCoverage:
    """Return a fail-closed coverage result without inferred bounds."""

    return StreamCoverage(
        coverage=None,
        gaps=(),
        diagnostics=(
            CoverageDiagnostic(
                reason="coverage_indeterminate",
                path=path,
                message=message,
            ),
        ),
    )
