"""Strict Phase 3 inspection evidence captured only from SourceArtifacts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from video_content_pipeline.coverage import (
    CoverageDiagnostic,
    DecodedInterval,
    StreamCoverage,
    derive_stream_coverage,
)
from video_content_pipeline.external_tools import PinnedExternalTool, run_tool
from video_content_pipeline.probe import ProbeDocument, ProbeProjection, project_probe_document
from video_content_pipeline.source import SourceArtifact
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval, RawPtsTime


class InspectionError(ValueError):
    """A source-inspection failure with a machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class ProbeCaptureError(InspectionError):
    """A failed probe invocation that still preserves its emitted documents."""

    def __init__(
        self,
        reason: str,
        message: str,
        structural_document: ProbeDocument | None,
        coverage_document: ProbeDocument | None,
    ) -> None:
        super().__init__(reason, message)
        self.structural_document = structural_document
        self.coverage_document = coverage_document


@dataclass(frozen=True)
class SubtitleTrackCandidate:
    """Metadata-only source subtitle evidence; never subtitle text."""

    stream_index: int
    language: str | None
    container_format: str | None
    origin: str
    available: bool

    def as_json(self) -> dict[str, object]:
        return {
            "stream_index": self.stream_index,
            "language": self.language,
            "container_format": self.container_format,
            "origin": self.origin,
            "available": self.available,
        }


@dataclass(frozen=True)
class InspectionEvidence:
    """Retained raw probe documents and their strict decision projection."""

    structural_document: ProbeDocument
    coverage_document: ProbeDocument
    projection: ProbeProjection
    coverage_by_stream: dict[int, StreamCoverage]
    subtitle_tracks: tuple[SubtitleTrackCandidate, ...]


@dataclass(frozen=True)
class PlanInspectionEvidence:
    """Immutable inspection evidence retained by a PlanReport for one source."""

    source_id: str
    structural_document: ProbeDocument | None
    coverage_document: ProbeDocument | None
    coverage_by_stream: tuple[tuple[int, StreamCoverage], ...]
    subtitle_tracks: tuple[SubtitleTrackCandidate, ...]

    @classmethod
    def from_documents(
        cls,
        source_id: str,
        structural_document: ProbeDocument,
        coverage_document: ProbeDocument,
    ) -> PlanInspectionEvidence:
        """Retain raw ProbeDocuments before their strict decision projection succeeds."""

        return cls(source_id, structural_document, coverage_document, (), ())

    @classmethod
    def from_capture_error(cls, source_id: str, error: ProbeCaptureError) -> PlanInspectionEvidence:
        """Associate retained partial output with the source whose probe failed."""

        return cls(source_id, error.structural_document, error.coverage_document, (), ())

    @classmethod
    def from_inspection(
        cls, source_id: str, evidence: InspectionEvidence
    ) -> PlanInspectionEvidence:
        """Discard the derived projection while retaining its source decision evidence."""

        return cls(
            source_id,
            evidence.structural_document,
            evidence.coverage_document,
            tuple(sorted(evidence.coverage_by_stream.items())),
            evidence.subtitle_tracks,
        )

    def as_json(self) -> dict[str, object]:
        """Serialize raw probe documents and exact decision evidence without subtitle text."""

        return {
            "source_id": self.source_id,
            "structural_probe_document": _probe_document_as_json(self.structural_document),
            "coverage_probe_document": _probe_document_as_json(self.coverage_document),
            "stream_coverage": [
                _stream_coverage_as_json(stream_index, coverage)
                for stream_index, coverage in self.coverage_by_stream
            ],
            "subtitle_track_candidates": [track.as_json() for track in self.subtitle_tracks],
        }

    @classmethod
    def from_json(cls, value: object) -> PlanInspectionEvidence:
        """Load retained evidence without deriving coverage from any metadata fallback."""

        if not isinstance(value, Mapping):
            raise ValueError("Plan inspection evidence must be a JSON object.")
        source_id = _required_string(value.get("source_id"))
        structural = _probe_document_from_json(value.get("structural_probe_document"))
        coverage = _probe_document_from_json(value.get("coverage_probe_document"))
        stream_values = value.get("stream_coverage")
        subtitle_values = value.get("subtitle_track_candidates")
        if not isinstance(stream_values, list) or not isinstance(subtitle_values, list):
            raise ValueError("Plan inspection evidence has invalid collections.")
        coverage_by_stream: list[tuple[int, StreamCoverage]] = []
        stream_indexes: set[int] = set()
        for stream_value in stream_values:
            stream_index, stream_coverage = _stream_coverage_from_json(stream_value)
            if stream_index in stream_indexes:
                raise ValueError("Plan inspection evidence repeats a stream index.")
            stream_indexes.add(stream_index)
            coverage_by_stream.append((stream_index, stream_coverage))
        return cls(
            source_id,
            structural,
            coverage,
            tuple(sorted(coverage_by_stream)),
            tuple(_subtitle_track_from_json(track) for track in subtitle_values),
        )


def capture_probe_documents(
    ffprobe: PinnedExternalTool, artifact: SourceArtifact, evidence_directory: Path
) -> tuple[ProbeDocument, ProbeDocument]:
    """Capture unchanged structural and packet-level JSON under one evidence directory."""

    evidence_directory.mkdir(parents=True, exist_ok=True)
    structural, structural_error = _run_probe(
        ffprobe,
        (
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(artifact.media_path),
        ),
    )
    structural_path = evidence_directory / "structural.ffprobe.json"
    try:
        _write_once(structural_path, structural.raw_json)
    except InspectionError as error:
        raise ProbeCaptureError(error.reason, str(error), structural, None) from error
    if structural_error is not None:
        raise ProbeCaptureError(structural_error.reason, str(structural_error), structural, None)
    coverage, coverage_error = _run_probe(
        ffprobe,
        (
            "-v",
            "error",
            "-show_streams",
            "-show_packets",
            "-of",
            "json",
            str(artifact.media_path),
        ),
    )
    coverage_path = evidence_directory / "coverage.ffprobe.json"
    try:
        _write_once(coverage_path, coverage.raw_json)
    except InspectionError as error:
        raise ProbeCaptureError(error.reason, str(error), structural, coverage) from error
    if coverage_error is not None:
        raise ProbeCaptureError(coverage_error.reason, str(coverage_error), structural, coverage)
    return structural, coverage


def inspect_documents(
    structural_document: ProbeDocument, coverage_document: ProbeDocument
) -> InspectionEvidence:
    """Project strict evidence, coverage, and subtitle metadata without fallback inference."""

    projection_result = project_probe_document(structural_document)
    if projection_result.projection is None:
        raise InspectionError(
            "probe_invalid", "Structural ProbeDocument has no valid typed projection."
        )
    coverage_by_stream = derive_packet_coverages(coverage_document, projection_result.projection)
    return InspectionEvidence(
        structural_document=structural_document,
        coverage_document=coverage_document,
        projection=projection_result.projection,
        coverage_by_stream=coverage_by_stream,
        subtitle_tracks=enumerate_subtitle_track_candidates(structural_document),
    )


def derive_packet_coverages(
    document: ProbeDocument, projection: ProbeProjection
) -> dict[int, StreamCoverage]:
    """Derive each audio/video StreamCoverage from complete packet PTS evidence."""

    decoded = _json_object(document.raw_json, "coverage_probe_invalid")
    packets = decoded.get("packets")
    if not isinstance(packets, list):
        raise InspectionError(
            "coverage_probe_invalid", "Coverage ProbeDocument must contain packets."
        )
    time_bases = {stream.index: stream.time_base for stream in projection.streams}
    media_indexes = {
        stream.index for stream in projection.streams if stream.codec_type in {"audio", "video"}
    }
    observed: dict[int, list[DecodedInterval]] = {
        stream_index: [] for stream_index in media_indexes
    }
    for ordinal, packet in enumerate(packets):
        if not isinstance(packet, Mapping):
            raise InspectionError(
                "coverage_packet_invalid", f"Packet {ordinal} must be a JSON object."
            )
        stream_index = packet.get("stream_index")
        if stream_index not in media_indexes:
            continue
        pts = _integer(packet.get("pts"))
        duration = _integer(packet.get("duration"))
        if pts is None or duration is None or duration <= 0:
            raise InspectionError(
                "coverage_packet_invalid",
                f"Packet {ordinal} needs an integer PTS and positive duration for coverage.",
            )
        time_base = time_bases[stream_index]
        observed[stream_index].append(
            DecodedInterval(
                start=RawPtsTime(pts, time_base).time,
                end=RawPtsTime(pts + duration, time_base).time,
            )
        )
    return {
        stream_index: derive_stream_coverage(intervals)
        for stream_index, intervals in observed.items()
    }


def enumerate_subtitle_track_candidates(
    document: ProbeDocument,
) -> tuple[SubtitleTrackCandidate, ...]:
    """Enumerate source subtitle stream metadata without fetching subtitle payloads."""

    decoded = _json_object(document.raw_json, "structural_probe_invalid")
    streams = decoded.get("streams")
    if not isinstance(streams, list):
        raise InspectionError(
            "structural_probe_invalid", "Structural ProbeDocument must contain streams."
        )
    container = decoded.get("format")
    container_format = (
        container.get("format_name")
        if isinstance(container, Mapping) and isinstance(container.get("format_name"), str)
        else None
    )
    candidates: list[SubtitleTrackCandidate] = []
    for ordinal, stream in enumerate(streams):
        if not isinstance(stream, Mapping) or stream.get("codec_type") != "subtitle":
            continue
        index = stream.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            raise InspectionError(
                "subtitle_track_invalid", f"Subtitle stream {ordinal} has no integer index."
            )
        tags = stream.get("tags")
        language = tags.get("language") if isinstance(tags, Mapping) else None
        candidates.append(
            SubtitleTrackCandidate(
                stream_index=index,
                language=language if isinstance(language, str) else None,
                container_format=container_format,
                origin="embedded",
                available=True,
            )
        )
    return tuple(candidates)


def _run_probe(
    ffprobe: PinnedExternalTool, arguments: tuple[str, ...]
) -> tuple[ProbeDocument, InspectionError | None]:
    result = run_tool((str(ffprobe.path), *arguments))
    document = ProbeDocument(result.stdout)
    if result.returncode != 0:
        return document, InspectionError(
            "ffprobe_failed", f"FFprobe exited {result.returncode}: {result.stderr.strip()}"
        )
    return document, None


def _write_once(path: Path, contents: str) -> None:
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing != contents:
            raise InspectionError(
                "probe_document_conflict", f"Existing probe evidence differs: {path}"
            )
        return
    path.write_text(contents, encoding="utf-8")


def _json_object(raw_json: str, reason: str) -> Mapping[str, object]:
    try:
        decoded = json.loads(raw_json)
    except json.JSONDecodeError as error:
        raise InspectionError(reason, "Probe output must be valid JSON.") from error
    if not isinstance(decoded, Mapping):
        raise InspectionError(reason, "Probe output must be a JSON object.")
    return decoded


def _integer(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _stream_coverage_as_json(stream_index: int, coverage: StreamCoverage) -> dict[str, object]:
    return {
        "stream_index": stream_index,
        "coverage": _interval_as_json(coverage.coverage),
        "gaps": [_interval_as_json(gap) for gap in coverage.gaps],
        "diagnostics": [
            {"reason": diagnostic.reason, "path": diagnostic.path, "message": diagnostic.message}
            for diagnostic in coverage.diagnostics
        ],
    }


def _stream_coverage_from_json(value: object) -> tuple[int, StreamCoverage]:
    if not isinstance(value, Mapping):
        raise ValueError("Stream coverage must be a JSON object.")
    stream_index = _required_integer(value.get("stream_index"))
    coverage = _interval_from_json(value.get("coverage"))
    gaps = _interval_list_from_json(value.get("gaps"))
    diagnostic_values = value.get("diagnostics")
    if not isinstance(diagnostic_values, list):
        raise ValueError("Stream coverage diagnostics must be a JSON array.")
    diagnostics = tuple(
        _coverage_diagnostic_from_json(diagnostic) for diagnostic in diagnostic_values
    )
    return stream_index, StreamCoverage(coverage, gaps, diagnostics)


def _probe_document_as_json(document: ProbeDocument | None) -> dict[str, str | None]:
    return {"raw_json": document.raw_json if document is not None else None}


def _probe_document_from_json(value: object) -> ProbeDocument | None:
    if not isinstance(value, Mapping):
        raise ValueError("Retained ProbeDocument must be a JSON object.")
    raw_json = value.get("raw_json")
    if raw_json is not None and not isinstance(raw_json, str):
        raise ValueError("Retained ProbeDocument raw JSON must be a string or null.")
    return ProbeDocument(raw_json) if raw_json is not None else None


def _interval_as_json(interval: HalfOpenInterval | None) -> dict[str, object] | None:
    if interval is None:
        return None
    return {
        "start": _exact_time_as_json(interval.start),
        "end": _exact_time_as_json(interval.end),
    }


def _interval_from_json(value: object) -> HalfOpenInterval | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("Coverage interval must be a JSON object or null.")
    return HalfOpenInterval(
        _exact_time_from_json(value.get("start")), _exact_time_from_json(value.get("end"))
    )


def _interval_list_from_json(value: object) -> tuple[HalfOpenInterval, ...]:
    if not isinstance(value, list):
        raise ValueError("Coverage gaps must be a JSON array.")
    gaps = tuple(_interval_from_json(interval) for interval in value)
    if any(interval is None for interval in gaps):
        raise ValueError("Coverage gaps must not be null.")
    return tuple(interval for interval in gaps if interval is not None)


def _exact_time_as_json(value: ExactTime) -> dict[str, int]:
    return {"numerator": value.numerator, "denominator": value.denominator}


def _exact_time_from_json(value: object) -> ExactTime:
    if not isinstance(value, Mapping):
        raise ValueError("Exact time must be a JSON object.")
    return ExactTime(
        _required_integer(value.get("numerator")), _required_integer(value.get("denominator"))
    )


def _coverage_diagnostic_from_json(value: object) -> CoverageDiagnostic:
    if not isinstance(value, Mapping):
        raise ValueError("Coverage diagnostic must be a JSON object.")
    return CoverageDiagnostic(
        _required_string(value.get("reason")),
        _required_string(value.get("path")),
        _required_string(value.get("message")),
    )


def _subtitle_track_from_json(value: object) -> SubtitleTrackCandidate:
    if not isinstance(value, Mapping):
        raise ValueError("Subtitle track candidate must be a JSON object.")
    language = value.get("language")
    container_format = value.get("container_format")
    available = value.get("available")
    if language is not None and not isinstance(language, str):
        raise ValueError("Subtitle language must be a string or null.")
    if container_format is not None and not isinstance(container_format, str):
        raise ValueError("Subtitle container format must be a string or null.")
    if not isinstance(available, bool):
        raise ValueError("Subtitle availability must be a boolean.")
    return SubtitleTrackCandidate(
        _required_integer(value.get("stream_index")),
        language,
        container_format,
        _required_string(value.get("origin")),
        available,
    )


def _required_string(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("A required evidence string is missing.")
    return value


def _required_integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("A required evidence integer is missing.")
    return value
