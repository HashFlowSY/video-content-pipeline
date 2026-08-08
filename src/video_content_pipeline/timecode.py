"""Exact time values for the deterministic Phase 2 core."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from functools import total_ordering


class TimeValidationError(ValueError):
    """A time-domain validation failure with a machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@total_ordering
@dataclass(frozen=True)
class ExactTime:
    """An exact rational time value with a normalized numerator and denominator."""

    numerator: int
    denominator: int = 1

    def __post_init__(self) -> None:
        value = Fraction(self.numerator, self.denominator)
        object.__setattr__(self, "numerator", value.numerator)
        object.__setattr__(self, "denominator", value.denominator)

    def __add__(self, other: ExactTime) -> ExactTime:
        return self._from_fraction(self.as_fraction() + other.as_fraction())

    def __sub__(self, other: ExactTime) -> ExactTime:
        return self._from_fraction(self.as_fraction() - other.as_fraction())

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, ExactTime):
            return NotImplemented
        return self.as_fraction() < other.as_fraction()

    def as_fraction(self) -> Fraction:
        """Return the standard-library exact value for internal arithmetic."""

        return Fraction(self.numerator, self.denominator)

    @classmethod
    def _from_fraction(cls, value: Fraction) -> ExactTime:
        return cls(value.numerator, value.denominator)


@dataclass(frozen=True)
class RawPtsTime:
    """Signed source timing retained as a raw PTS and exact stream time base."""

    raw_pts: int
    time_base: ExactTime

    @property
    def time(self) -> ExactTime:
        """Return the exact source coordinate without normalizing its origin."""

        return ExactTime(self.raw_pts * self.time_base.numerator, self.time_base.denominator)


@dataclass(frozen=True)
class HalfOpenInterval:
    """An exact time interval with an excluded end boundary."""

    start: ExactTime
    end: ExactTime

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise TimeValidationError("interval_invalid", "start must be before end")

    def overlaps(self, other: HalfOpenInterval) -> bool:
        """Return whether this interval intersects another positive-length interval."""

        return self.start < other.end and other.start < self.end


@dataclass(frozen=True)
class PartCoverageStart:
    """The observed raw source boundary that anchors a Part's export time."""

    raw_pts_time: RawPtsTime

    @property
    def time(self) -> ExactTime:
        """Return the exact observed source coordinate of the Part boundary."""

        return self.raw_pts_time.time


@dataclass(frozen=True)
class PartRelativeTime:
    """A non-authoritative export coordinate traceable to a raw PTS value."""

    raw_pts_time: RawPtsTime
    coverage_start: PartCoverageStart

    def __post_init__(self) -> None:
        if self.time < ExactTime(0):
            raise TimeValidationError(
                "part_relative_negative", "Part-relative time must not be negative"
            )

    @property
    def time(self) -> ExactTime:
        """Return the exact export coordinate derived from retained source evidence."""

        return self.raw_pts_time.time - self.coverage_start.time

    @classmethod
    def from_raw(
        cls, raw_pts_time: RawPtsTime, coverage_start: PartCoverageStart
    ) -> PartRelativeTime:
        """Translate a raw source coordinate by its Part coverage start."""

        return cls(
            raw_pts_time=raw_pts_time,
            coverage_start=coverage_start,
        )
