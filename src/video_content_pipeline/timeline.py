"""Compact collection-time assembly from observed Part coverage."""

from __future__ import annotations

from dataclasses import dataclass

from video_content_pipeline.timecode import ExactTime, HalfOpenInterval, PartRelativeTime


class TimelineValidationError(ValueError):
    """A collection-timeline validation failure with a machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class TimelinePart:
    """One ordered Part with its exact observed source coverage."""

    part_id: str
    coverage: HalfOpenInterval

    @property
    def duration(self) -> ExactTime:
        """Return the observed coverage span used for compact concatenation."""

        return self.coverage.end - self.coverage.start


@dataclass(frozen=True)
class CollectionVirtualTime:
    """A compact coordinate that retains its owning Part and source evidence."""

    part_id: str
    time: ExactTime
    part_relative_time: PartRelativeTime


@dataclass(frozen=True)
class CollectionTimeline:
    """Ordered Part coverage assembled without absolute-PTS or duration gaps."""

    parts: tuple[TimelinePart, ...]

    def __post_init__(self) -> None:
        part_ids = tuple(part.part_id for part in self.parts)
        if len(set(part_ids)) != len(part_ids):
            raise TimelineValidationError(
                "duplicate_part_id", "Collection timeline Part identifiers must be unique"
            )

    def map_part_relative_time(
        self, part_id: str, part_relative_time: PartRelativeTime
    ) -> CollectionVirtualTime:
        """Map a retained Part coordinate to contiguous collection virtual time."""

        collection_start = ExactTime(0)
        for part in self.parts:
            if part.part_id == part_id:
                return self._map_in_part(part, collection_start, part_relative_time)
            collection_start += part.duration

        raise TimelineValidationError(
            "part_not_found", f"Collection timeline has no Part {part_id!r}"
        )

    @staticmethod
    def _map_in_part(
        part: TimelinePart,
        collection_start: ExactTime,
        part_relative_time: PartRelativeTime,
    ) -> CollectionVirtualTime:
        if part_relative_time.coverage_start.time != part.coverage.start:
            raise TimelineValidationError(
                "part_coverage_mismatch",
                "Part-relative coordinate coverage start does not match the Part coverage start",
            )
        if part.duration < part_relative_time.time:
            raise TimelineValidationError(
                "part_relative_out_of_coverage",
                "Part-relative coordinate is beyond the observed Part coverage endpoint",
            )

        return CollectionVirtualTime(
            part_id=part.part_id,
            time=collection_start + part_relative_time.time,
            part_relative_time=part_relative_time,
        )
