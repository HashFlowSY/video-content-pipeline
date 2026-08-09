"""Atomic SRT and WebVTT subtitle evidence parsing for the Phase 2 core."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from video_content_pipeline.coverage import StreamCoverage
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval, TimeValidationError

_TIMESTAMP = re.compile(
    r"^(?:(?P<hours>\d{2,}):)?(?P<minutes>[0-5]\d):(?P<seconds>[0-5]\d)[,.](?P<milliseconds>\d{3})$"
)
_TOKEN = re.compile(r"\s+|\S+")
_VTT_DIRECTIVE = re.compile(r"^(?:NOTE|STYLE|REGION)(?:[ \t]|$)")


class SubtitleValidationError(ValueError):
    """Raised by cue value constructors when their invariants are violated."""


class SubtitleTrackStatus(StrEnum):
    """The atomic availability state of a subtitle track."""

    VALID = "valid"
    INVALID = "invalid"


@dataclass(frozen=True)
class SubtitleDiagnostic:
    """A machine-readable failure attached to one subtitle track."""

    reason: str
    path: str
    message: str
    source_ordinal: int | None = None


@dataclass(frozen=True)
class RawCue:
    """Immutable source cue evidence retained exactly after atomic validation."""

    source_text: str
    interval: HalfOpenInterval
    source_ordinal: int
    part_id: str
    track_id: str
    source_format: Literal["srt", "vtt"]
    identifier: str | None = None
    raw_start: str = ""
    raw_end: str = ""
    timing_settings: str = ""

    def __post_init__(self) -> None:
        if self.source_ordinal < 0:
            raise SubtitleValidationError("Cue source ordinal must not be negative.")
        if not self.part_id or not self.track_id:
            raise SubtitleValidationError("Cue Part and track identities must not be empty.")

    @property
    def text(self) -> str:
        """Compatibility alias for the retained source text."""

        return self.source_text

    @property
    def raw_text(self) -> str:
        """Return the original cue payload without normalization."""

        return self.source_text

    @property
    def source_index(self) -> int:
        """Compatibility alias for the stable source ordinal."""

        return self.source_ordinal

    @property
    def start(self) -> ExactTime:
        return self.interval.start

    @property
    def end(self) -> ExactTime:
        return self.interval.end


@dataclass(frozen=True)
class NormalizedCue:
    """Losslessly normalized cue text with every source token preserved."""

    raw_cue: RawCue
    text: str
    tokens: tuple[str, ...]

    @property
    def interval(self) -> HalfOpenInterval:
        return self.raw_cue.interval

    @property
    def source_ordinal(self) -> int:
        return self.raw_cue.source_ordinal

    @property
    def part_id(self) -> str:
        return self.raw_cue.part_id

    @property
    def track_id(self) -> str:
        return self.raw_cue.track_id


@dataclass(frozen=True)
class SerializationEnvelope:
    """A derived outward millisecond envelope for one exact cue interval."""

    exact_interval: HalfOpenInterval
    start_milliseconds: int
    end_milliseconds: int

    @classmethod
    def from_interval(cls, interval: HalfOpenInterval) -> SerializationEnvelope:
        """Floor the exact start and ceil the exact end without replacing source time."""

        return cls(
            exact_interval=interval,
            start_milliseconds=_floor_milliseconds(interval.start),
            end_milliseconds=_ceil_milliseconds(interval.end),
        )


@dataclass(frozen=True)
class PresentationCorrection:
    """A presentation-only token omission with exact source provenance."""

    reason: Literal["proven_rolling_overlap", "exact_duplicate_omitted"]
    source_ordinal: int
    source_token_range: tuple[int, int]
    compared_to_source_ordinal: int

    def __post_init__(self) -> None:
        start, end = self.source_token_range
        if start < 0 or start >= end:
            raise SubtitleValidationError(
                "Presentation correction must identify a non-empty token range."
            )


@dataclass(frozen=True)
class PresentationDiagnostic:
    """A retained ambiguity that did not authorize presentation token removal."""

    reason: Literal["possible_duplicate"]
    source_ordinal: int
    compared_to_source_ordinal: int


@dataclass(frozen=True)
class PresentationCue:
    """Immutable display evidence derived from one lossless normalized cue."""

    normalized_cue: NormalizedCue
    source_token_indexes: tuple[int, ...]
    corrections: tuple[PresentationCorrection, ...] = ()

    def __post_init__(self) -> None:
        expected_indexes = tuple(range(len(self.normalized_cue.tokens)))
        if self.source_token_indexes == expected_indexes:
            if self.corrections:
                raise SubtitleValidationError(
                    "Unchanged presentation cues cannot carry token corrections."
                )
            return
        if tuple(sorted(set(self.source_token_indexes))) != self.source_token_indexes or any(
            index not in expected_indexes for index in self.source_token_indexes
        ):
            raise SubtitleValidationError(
                "Presentation cue source-token indexes must be ordered, unique, and in range."
            )
        omitted_indexes = set(expected_indexes) - set(self.source_token_indexes)
        corrected_indexes: set[int] = set()
        for correction in self.corrections:
            if correction.source_ordinal != self.source_ordinal:
                raise SubtitleValidationError(
                    "Presentation correction must belong to its presentation cue."
                )
            start, end = correction.source_token_range
            if end > len(expected_indexes):
                raise SubtitleValidationError(
                    "Presentation correction token range is outside its source cue."
                )
            corrected_indexes.update(range(start, end))
        if omitted_indexes != corrected_indexes:
            raise SubtitleValidationError(
                "Presentation cue token omissions must be covered by correction provenance."
            )

    @property
    def text(self) -> str:
        """Return display text derived from the retained source-token indexes."""

        return "".join(self.normalized_cue.tokens[index] for index in self.source_token_indexes)

    @property
    def interval(self) -> HalfOpenInterval:
        return self.normalized_cue.interval

    @property
    def source_ordinal(self) -> int:
        return self.normalized_cue.source_ordinal

    @property
    def part_id(self) -> str:
        return self.normalized_cue.part_id

    @property
    def track_id(self) -> str:
        return self.normalized_cue.track_id

    @property
    def serialization_envelope(self) -> SerializationEnvelope:
        """Return the derived export envelope while retaining the exact interval."""

        return SerializationEnvelope.from_interval(self.interval)

    @property
    def timing_settings(self) -> str:
        """Return retained WebVTT timing settings for format-preserving export."""

        return self.normalized_cue.raw_cue.timing_settings


@dataclass(frozen=True)
class PresentationOutput:
    """Presentation cues plus all display-only corrections and ambiguities."""

    cues: tuple[PresentationCue, ...]
    corrections: tuple[PresentationCorrection, ...] = ()
    diagnostics: tuple[PresentationDiagnostic, ...] = ()


@dataclass(frozen=True)
class SubtitleTrack:
    """Raw source and atomically accepted cue layers for one subtitle track."""

    track_id: str
    part_id: str
    source_format: Literal["srt", "vtt"]
    raw_source: str
    status: SubtitleTrackStatus
    raw_cues: tuple[RawCue, ...]
    normalized_cues: tuple[NormalizedCue, ...]
    diagnostics: tuple[SubtitleDiagnostic, ...] = ()

    @property
    def valid(self) -> bool:
        return self.status is SubtitleTrackStatus.VALID

    @property
    def state(self) -> str:
        """Return the string state for callers that do not depend on the enum."""

        return self.status.value

    @property
    def parse_quality(self) -> str:
        """Compatibility alias for the atomic track state."""

        return self.status.value

    @property
    def raw_track(self) -> str:
        """Return the unchanged source track for audit storage."""

        return self.raw_source

    @property
    def cues(self) -> tuple[NormalizedCue, ...]:
        """Return accepted normalized cues; invalid tracks expose no output."""

        return self.normalized_cues


def _invalid_track(
    *,
    track_id: str,
    part_id: str,
    source_format: Literal["srt", "vtt"],
    raw_source: str,
    diagnostic: SubtitleDiagnostic,
) -> SubtitleTrack:
    return SubtitleTrack(
        track_id=track_id,
        part_id=part_id,
        source_format=source_format,
        raw_source=raw_source,
        status=SubtitleTrackStatus.INVALID,
        raw_cues=(),
        normalized_cues=(),
        diagnostics=(diagnostic,),
    )


def _timestamp(value: str, path: str) -> ExactTime:
    match = _TIMESTAMP.fullmatch(value)
    if match is None:
        raise ValueError(f"{path} has an invalid timestamp {value!r}.")
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes"))
    seconds = int(match.group("seconds"))
    milliseconds = int(match.group("milliseconds"))
    total = ((hours * 60 + minutes) * 60 + seconds) * 1000 + milliseconds
    return ExactTime(total, 1000)


def _blocks(source: str) -> tuple[str, ...]:
    normalized = source.lstrip("\ufeff")
    return tuple(
        block.strip("\r\n")
        for block in re.split(r"(?:\r\n[ \t]*\r\n|\r[ \t]*\r|\n[ \t]*\n)", normalized)
        if block.strip()
    )


def _timing_line(
    line: str, source_format: Literal["srt", "vtt"], ordinal: int
) -> tuple[str, str, str, HalfOpenInterval]:
    pieces = line.split("-->")
    if len(pieces) != 2:
        raise ValueError(f"Cue {ordinal} timing must contain exactly one --> separator.")
    raw_start = pieces[0].strip()
    right = pieces[1].strip()
    if not right:
        raise ValueError(f"Cue {ordinal} timing has no end timestamp.")
    end_parts = right.split(None, 1)
    raw_end = end_parts[0]
    settings = end_parts[1] if len(end_parts) == 2 else ""
    if source_format == "srt" and settings:
        raise ValueError(f"Cue {ordinal} contains unsupported SRT timing settings.")
    if source_format == "srt" and "." in raw_start + raw_end:
        raise ValueError(f"Cue {ordinal} SRT timestamps must use comma fractions.")
    if source_format == "vtt" and "," in raw_start + raw_end:
        raise ValueError(f"Cue {ordinal} WebVTT timestamps must use dot fractions.")
    start = _timestamp(raw_start, f"Cue {ordinal} start")
    end = _timestamp(raw_end, f"Cue {ordinal} end")
    try:
        interval = HalfOpenInterval(start, end)
    except TimeValidationError as error:
        raise ValueError(f"Cue {ordinal} must have positive duration.") from error
    return raw_start, raw_end, settings, interval


def _looks_like_timing(line: str) -> bool:
    """Distinguish a directive body containing ``-->`` from a cue timing line."""

    pieces = line.split("-->")
    if len(pieces) != 2:
        return False
    start = pieces[0].strip()
    end = pieces[1].strip().split(None, 1)[0] if pieces[1].strip() else ""
    return _TIMESTAMP.fullmatch(start) is not None and _TIMESTAMP.fullmatch(end) is not None


def _cue(
    block: str,
    source_format: Literal["srt", "vtt"],
    ordinal: int,
    part_id: str,
    track_id: str,
) -> RawCue:
    newline = "\r\n" if "\r\n" in block else "\r" if "\r" in block else "\n"
    lines = block.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    timing_position = next((index for index, line in enumerate(lines) if "-->" in line), None)
    if timing_position is None:
        raise ValueError(f"Cue {ordinal} has no timing line.")
    if timing_position > 1:
        raise ValueError(f"Cue {ordinal} has more than one identifier line.")
    identifier = lines[0].strip() if timing_position == 1 else None
    raw_start, raw_end, settings, interval = _timing_line(
        lines[timing_position], source_format, ordinal
    )
    text_lines = lines[timing_position + 1 :]
    if not text_lines or not any(line.strip() for line in text_lines):
        raise ValueError(f"Cue {ordinal} must contain subtitle text.")
    return RawCue(
        source_text=newline.join(text_lines),
        interval=interval,
        source_ordinal=ordinal,
        part_id=part_id,
        track_id=track_id,
        source_format=source_format,
        identifier=identifier,
        raw_start=raw_start,
        raw_end=raw_end,
        timing_settings=settings,
    )


def _coverage_interval(
    coverage: StreamCoverage | HalfOpenInterval,
) -> tuple[HalfOpenInterval, tuple[HalfOpenInterval, ...]] | None:
    if isinstance(coverage, HalfOpenInterval):
        return coverage, ()
    if coverage.coverage is None:
        return None
    return coverage.coverage, coverage.gaps


def _validate_coverage(
    cue: RawCue, coverage: StreamCoverage | HalfOpenInterval
) -> SubtitleDiagnostic | None:
    resolved = _coverage_interval(coverage)
    if resolved is None:
        return SubtitleDiagnostic(
            reason="coverage_indeterminate",
            path="coverage",
            message="Subtitle validation requires determinate stream coverage.",
            source_ordinal=cue.source_ordinal,
        )
    envelope, gaps = resolved
    if cue.start < envelope.start or envelope.end < cue.end:
        return SubtitleDiagnostic(
            reason="cue_out_of_coverage",
            path=f"cues[{cue.source_ordinal}].interval",
            message="Cue interval lies outside observed stream coverage.",
            source_ordinal=cue.source_ordinal,
        )
    if any(gap.start < cue.end and cue.start < gap.end for gap in gaps):
        return SubtitleDiagnostic(
            reason="cue_crosses_coverage_gap",
            path=f"cues[{cue.source_ordinal}].interval",
            message="Cue interval crosses an observed internal coverage gap.",
            source_ordinal=cue.source_ordinal,
        )
    return None


def _normalized_cue(cue: RawCue) -> NormalizedCue:
    text = cue.source_text.replace("\r\n", "\n").replace("\r", "\n")
    return NormalizedCue(raw_cue=cue, text=text, tokens=tuple(_TOKEN.findall(text)))


def _parse(
    source: str,
    source_format: Literal["srt", "vtt"],
    part_id: str,
    track_id: str,
    coverage: StreamCoverage | HalfOpenInterval | None,
) -> SubtitleTrack:
    if not part_id or not track_id:
        return _invalid_track(
            track_id=track_id,
            part_id=part_id,
            source_format=source_format,
            raw_source=source,
            diagnostic=SubtitleDiagnostic(
                "syntax_invalid", "track", "Part and track identities are required."
            ),
        )
    try:
        source_blocks = _blocks(source)
        if source_format == "vtt":
            source_without_bom = source.lstrip("\ufeff")
            header_end = re.search(r"\r\n|\r|\n", source_without_bom)
            header = (
                source_without_bom
                if header_end is None
                else source_without_bom[: header_end.start()]
            )
            valid_header = (
                header == "WEBVTT" or header.startswith("WEBVTT ") or header.startswith("WEBVTT\t")
            )
            if not valid_header:
                raise ValueError("WebVTT source must start with WEBVTT.")
            body = "" if header_end is None else source_without_bom[header_end.end() :]
            source_blocks = _blocks(body)
        raw_cues: list[RawCue] = []
        for block in source_blocks:
            first_line = block.split("\n", 1)[0].rstrip("\r")
            if source_format == "vtt" and _VTT_DIRECTIVE.match(first_line):
                rest = block.split("\n")[1:]
                if not rest or not _looks_like_timing(rest[0]):
                    continue
            raw_cues.append(_cue(block, source_format, len(raw_cues), part_id, track_id))
        if not raw_cues:
            raise ValueError("Subtitle track contains no cues.")
    except ValueError as error:
        ordinal_match = re.search(r"Cue (\d+)", str(error))
        ordinal = int(ordinal_match.group(1)) if ordinal_match else None
        reason = "duration_invalid" if "positive duration" in str(error) else "syntax_invalid"
        return _invalid_track(
            track_id=track_id,
            part_id=part_id,
            source_format=source_format,
            raw_source=source,
            diagnostic=SubtitleDiagnostic(
                reason,
                "cues" if ordinal is None else f"cues[{ordinal}]",
                str(error),
                ordinal,
            ),
        )

    if coverage is None:
        return _invalid_track(
            track_id=track_id,
            part_id=part_id,
            source_format=source_format,
            raw_source=source,
            diagnostic=SubtitleDiagnostic(
                "coverage_indeterminate",
                "coverage",
                "Subtitle acceptance requires determinate stream coverage.",
            ),
        )
    resolved = _coverage_interval(coverage)
    if resolved is None:
        return _invalid_track(
            track_id=track_id,
            part_id=part_id,
            source_format=source_format,
            raw_source=source,
            diagnostic=SubtitleDiagnostic(
                "coverage_indeterminate",
                "coverage",
                "Subtitle validation requires determinate stream coverage.",
            ),
        )
    for cue in raw_cues:
        diagnostic = _validate_coverage(cue, coverage)
        if diagnostic is not None:
            return _invalid_track(
                track_id=track_id,
                part_id=part_id,
                source_format=source_format,
                raw_source=source,
                diagnostic=diagnostic,
            )

    ordered = sorted(raw_cues, key=lambda cue: (cue.start, cue.end, cue.source_ordinal))
    normalized_cues = tuple(_normalized_cue(cue) for cue in ordered)
    return SubtitleTrack(
        track_id=track_id,
        part_id=part_id,
        source_format=source_format,
        raw_source=source,
        status=SubtitleTrackStatus.VALID,
        raw_cues=tuple(raw_cues),
        normalized_cues=normalized_cues,
    )


def parse_srt(
    source: str,
    part_id: str = "",
    track_id: str = "",
    coverage: StreamCoverage | HalfOpenInterval | None = None,
) -> SubtitleTrack:
    """Parse an SRT source into an atomic subtitle track result."""

    return _parse(source, "srt", part_id, track_id, coverage)


def parse_vtt(
    source: str,
    part_id: str = "",
    track_id: str = "",
    coverage: StreamCoverage | HalfOpenInterval | None = None,
) -> SubtitleTrack:
    """Parse a WebVTT source into an atomic subtitle track result."""

    return _parse(source, "vtt", part_id, track_id, coverage)


def parse_subtitles(
    source: str,
    source_format: Literal["srt", "vtt"],
    part_id: str = "",
    track_id: str = "",
    coverage: StreamCoverage | HalfOpenInterval | None = None,
) -> SubtitleTrack:
    """Dispatch parsing to the format-specific atomic parser."""

    if source_format == "srt":
        return parse_srt(source, part_id, track_id, coverage)
    if source_format == "vtt":
        return parse_vtt(source, part_id, track_id, coverage)
    raise SubtitleValidationError(f"Unsupported subtitle format: {source_format!r}.")


def accept_subtitle_track(
    source: str,
    source_format: Literal["srt", "vtt"],
    *,
    part_id: str,
    track_id: str,
    coverage: StreamCoverage | HalfOpenInterval,
) -> SubtitleTrack:
    """Parse and atomically accept a track only against supplied coverage evidence."""

    return parse_subtitles(source, source_format, part_id, track_id, coverage)


def presentation_cues(track: SubtitleTrack) -> tuple[PresentationCue, ...]:
    """Produce stable ordered presentation cues from atomically accepted evidence."""

    return presentation_output(track).cues


def presentation_output(track: SubtitleTrack) -> PresentationOutput:
    """Apply only exact local rolling-display corrections to accepted cue evidence."""

    if not track.valid:
        raise SubtitleValidationError("Presentation output requires an accepted subtitle track.")
    ordered = tuple(sorted(track.normalized_cues, key=_cue_order_key))
    visible_indexes = {cue.source_ordinal: tuple(range(len(cue.tokens))) for cue in ordered}
    cue_corrections: dict[int, list[PresentationCorrection]] = {
        cue.source_ordinal: [] for cue in ordered
    }
    corrections: list[PresentationCorrection] = []
    diagnostics: list[PresentationDiagnostic] = []
    omitted_cues: set[int] = set()

    for earlier, later in zip(ordered, ordered[1:]):
        if earlier.part_id != later.part_id or earlier.track_id != later.track_id:
            continue
        if _is_exact_duplicate(earlier, later):
            correction = PresentationCorrection(
                reason="exact_duplicate_omitted",
                source_ordinal=later.source_ordinal,
                source_token_range=(0, len(later.tokens)),
                compared_to_source_ordinal=earlier.source_ordinal,
            )
            corrections.append(correction)
            omitted_cues.add(later.source_ordinal)
            continue
        omission_end = _rolling_omission_end(earlier, later)
        if omission_end is not None:
            correction = PresentationCorrection(
                reason="proven_rolling_overlap",
                source_ordinal=later.source_ordinal,
                source_token_range=(0, omission_end),
                compared_to_source_ordinal=earlier.source_ordinal,
            )
            visible_indexes[later.source_ordinal] = tuple(range(omission_end, len(later.tokens)))
            cue_corrections[later.source_ordinal].append(correction)
            corrections.append(correction)
        elif _has_exact_token_overlap(earlier, later):
            diagnostics.append(
                PresentationDiagnostic(
                    reason="possible_duplicate",
                    source_ordinal=later.source_ordinal,
                    compared_to_source_ordinal=earlier.source_ordinal,
                )
            )

    return PresentationOutput(
        cues=tuple(
            PresentationCue(
                cue, visible_indexes[cue.source_ordinal], tuple(cue_corrections[cue.source_ordinal])
            )
            for cue in ordered
            if cue.source_ordinal not in omitted_cues
        ),
        corrections=tuple(corrections),
        diagnostics=tuple(diagnostics),
    )


def serialize_srt(cues: tuple[PresentationCue, ...]) -> str:
    """Serialize presentation cues as parseable outward-millisecond SRT."""

    blocks = tuple(
        f"{_cue_identifier(cue, ordinal)}\n"
        f"{_format_timestamp(cue.serialization_envelope.start_milliseconds, ',')} --> "
        f"{_format_timestamp(cue.serialization_envelope.end_milliseconds, ',')}\n"
        f"{cue.text}"
        for ordinal, cue in enumerate(_stable_presentation_order(cues), start=1)
    )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def serialize_vtt(cues: tuple[PresentationCue, ...]) -> str:
    """Serialize presentation cues as parseable outward-millisecond WebVTT."""

    blocks = tuple(
        f"{_cue_identifier(cue, ordinal)}\n"
        f"{_format_timestamp(cue.serialization_envelope.start_milliseconds, '.')} --> "
        f"{_format_timestamp(cue.serialization_envelope.end_milliseconds, '.')}"
        f"{(' ' + cue.timing_settings) if cue.timing_settings else ''}\n"
        f"{cue.text}"
        for ordinal, cue in enumerate(_stable_presentation_order(cues), start=1)
    )
    body = "\n\n".join(blocks)
    return f"WEBVTT\n\n{body}" + ("\n" if blocks else "")


def _stable_presentation_order(
    cues: tuple[PresentationCue, ...],
) -> tuple[PresentationCue, ...]:
    return tuple(sorted(cues, key=_cue_order_key))


def _cue_identifier(cue: PresentationCue, ordinal: int) -> str:
    """Preserve a retained source identifier or assign a deterministic fallback."""

    return cue.normalized_cue.raw_cue.identifier or str(ordinal)


def _cue_order_key(cue: NormalizedCue | PresentationCue) -> tuple[ExactTime, ExactTime, int]:
    return cue.interval.start, cue.interval.end, cue.source_ordinal


def _is_exact_duplicate(earlier: NormalizedCue, later: NormalizedCue) -> bool:
    return (
        earlier.text == later.text
        and earlier.interval.start == later.interval.start
        and earlier.interval.end == later.interval.end
    )


def _rolling_omission_end(earlier: NormalizedCue, later: NormalizedCue) -> int | None:
    """Return the later-source range owned by an exactly proven rolling overlap."""

    if not _intervals_overlap_or_touch(earlier.interval, later.interval):
        return None
    overlap_length = _contiguous_suffix_prefix_length(earlier.tokens, later.tokens)
    if overlap_length == 0 or overlap_length == len(later.tokens):
        return None
    if not any(not token.isspace() for token in later.tokens[:overlap_length]):
        return None
    return overlap_length


def _intervals_overlap_or_touch(earlier: HalfOpenInterval, later: HalfOpenInterval) -> bool:
    return earlier.overlaps(later) or earlier.end == later.start or later.end == earlier.start


def _contiguous_suffix_prefix_length(
    earlier_tokens: tuple[str, ...], later_tokens: tuple[str, ...]
) -> int:
    for length in range(min(len(earlier_tokens), len(later_tokens)), 0, -1):
        if earlier_tokens[-length:] == later_tokens[:length]:
            return length
    return 0


def _has_exact_token_overlap(earlier: NormalizedCue, later: NormalizedCue) -> bool:
    for earlier_start in range(len(earlier.tokens)):
        for later_start in range(len(later.tokens)):
            if earlier.tokens[earlier_start] != later.tokens[later_start]:
                continue
            if not earlier.tokens[earlier_start].isspace():
                return True
    return False


def _floor_milliseconds(time: ExactTime) -> int:
    milliseconds = time.as_fraction() * 1_000
    return milliseconds.numerator // milliseconds.denominator


def _ceil_milliseconds(time: ExactTime) -> int:
    milliseconds = time.as_fraction() * 1_000
    return -(-milliseconds.numerator // milliseconds.denominator)


def _format_timestamp(milliseconds: int, separator: Literal[",", "."]) -> str:
    if milliseconds < 0:
        raise SubtitleValidationError(
            "Subtitle serialization does not support negative timestamps."
        )
    seconds, millisecond_remainder = divmod(milliseconds, 1_000)
    minutes, second_remainder = divmod(seconds, 60)
    hours, minute_remainder = divmod(minutes, 60)
    return (
        f"{hours:02d}:{minute_remainder:02d}:{second_remainder:02d}"
        f"{separator}{millisecond_remainder:03d}"
    )


parse_subtitle_track = accept_subtitle_track
