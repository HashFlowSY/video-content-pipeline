"""Strict Phase 3 inspection evidence captured only from SourceArtifacts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from video_content_pipeline.coverage import DecodedInterval, StreamCoverage, derive_stream_coverage
from video_content_pipeline.external_tools import PinnedExternalTool, run_tool
from video_content_pipeline.probe import ProbeDocument, ProbeProjection, project_probe_document
from video_content_pipeline.source import SourceArtifact
from video_content_pipeline.timecode import RawPtsTime


class InspectionError(ValueError):
    """A source-inspection failure with a machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class SubtitleTrackCandidate:
    """Metadata-only source subtitle evidence; never subtitle text."""

    stream_index: int
    language: str | None
    codec_name: str | None
    origin: str
    available: bool

    def as_json(self) -> dict[str, object]:
        return {
            "stream_index": self.stream_index,
            "language": self.language,
            "codec_name": self.codec_name,
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


def capture_probe_documents(
    ffprobe: PinnedExternalTool, artifact: SourceArtifact, evidence_directory: Path
) -> tuple[ProbeDocument, ProbeDocument]:
    """Capture unchanged structural and packet-level JSON under one evidence directory."""

    evidence_directory.mkdir(parents=True, exist_ok=True)
    structural = _run_probe(
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
    coverage = _run_probe(
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
    structural_path = evidence_directory / "structural.ffprobe.json"
    coverage_path = evidence_directory / "coverage.ffprobe.json"
    _write_once(structural_path, structural)
    _write_once(coverage_path, coverage)
    return ProbeDocument(structural), ProbeDocument(coverage)


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
                codec_name=(
                    stream.get("codec_name") if isinstance(stream.get("codec_name"), str) else None
                ),
                origin="unknown",
                available=True,
            )
        )
    return tuple(candidates)


def _run_probe(ffprobe: PinnedExternalTool, arguments: tuple[str, ...]) -> str:
    result = run_tool((str(ffprobe.path), *arguments))
    if result.returncode != 0:
        raise InspectionError(
            "ffprobe_failed", f"FFprobe exited {result.returncode}: {result.stderr.strip()}"
        )
    _json_object(result.stdout, "ffprobe_output_invalid")
    return result.stdout


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
