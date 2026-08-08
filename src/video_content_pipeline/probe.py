"""Typed projections of retained FFprobe JSON evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from video_content_pipeline.timecode import ExactTime


@dataclass(frozen=True)
class ProbeDocument:
    """Unchanged raw JSON evidence emitted by FFprobe."""

    raw_json: str


@dataclass(frozen=True)
class ProbeDiagnostic:
    """A machine-readable reason why probe evidence cannot be used."""

    reason: str
    path: str
    message: str


@dataclass(frozen=True)
class ProbeStream:
    """The required, typed evidence for one FFprobe stream."""

    index: int
    codec_type: str
    time_base: ExactTime


@dataclass(frozen=True)
class ProbeProjection:
    """Known FFprobe fields that are safe for downstream decisions."""

    streams: tuple[ProbeStream, ...]


@dataclass(frozen=True)
class ProbeProjectionResult:
    """The preserved source document, optional projection, and diagnostics."""

    document: ProbeDocument
    projection: ProbeProjection | None
    diagnostics: tuple[ProbeDiagnostic, ...]


def project_probe_document(document: ProbeDocument) -> ProbeProjectionResult:
    """Project required JSON evidence without text or metadata fallbacks."""

    try:
        decoded = json.loads(document.raw_json)
    except json.JSONDecodeError:
        return _invalid_result(document, "$", "Probe evidence must be valid JSON.")

    if not isinstance(decoded, Mapping):
        return _invalid_result(document, "$", "Probe evidence must be a JSON object.")

    streams_value = decoded.get("streams")
    if not isinstance(streams_value, list) or not streams_value:
        return _invalid_result(document, "streams", "Probe evidence must contain streams.")

    streams: list[ProbeStream] = []
    for ordinal, stream_value in enumerate(streams_value):
        stream_path = f"streams[{ordinal}]"
        if not isinstance(stream_value, Mapping):
            return _invalid_result(document, stream_path, "A stream must be a JSON object.")

        index = stream_value.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            return _invalid_result(
                document, f"{stream_path}.index", "Stream index must be an integer."
            )

        codec_type = stream_value.get("codec_type")
        if not isinstance(codec_type, str) or not codec_type:
            return _invalid_result(
                document,
                f"{stream_path}.codec_type",
                "Stream codec type must be a non-empty string.",
            )

        time_base = _parse_time_base(stream_value.get("time_base"))
        if time_base is None:
            return _invalid_result(
                document,
                f"{stream_path}.time_base",
                "Stream time base must be a positive integer ratio.",
            )

        streams.append(ProbeStream(index=index, codec_type=codec_type, time_base=time_base))

    return ProbeProjectionResult(
        document=document,
        projection=ProbeProjection(streams=tuple(streams)),
        diagnostics=(),
    )


def _parse_time_base(value: object) -> ExactTime | None:
    if not isinstance(value, str):
        return None

    parts = value.split("/")
    if len(parts) != 2:
        return None

    try:
        numerator, denominator = (int(part) for part in parts)
    except ValueError:
        return None

    if numerator <= 0 or denominator <= 0:
        return None
    return ExactTime(numerator, denominator)


def _invalid_result(document: ProbeDocument, path: str, message: str) -> ProbeProjectionResult:
    """Return the audit evidence with no guessed decision projection."""

    return ProbeProjectionResult(
        document=document,
        projection=None,
        diagnostics=(
            ProbeDiagnostic(reason="probe_invalid", path=path, message=message),
            ProbeDiagnostic(
                reason="coverage_indeterminate",
                path=path,
                message="Coverage cannot be derived from invalid probe evidence.",
            ),
        ),
    )
