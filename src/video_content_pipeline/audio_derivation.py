"""Deterministic, provenance-bound audio derivatives for Phase 5 adapters."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from video_content_pipeline.coverage import StreamCoverage
from video_content_pipeline.external_tools import (
    ExternalToolError,
    PinnedExternalTool,
    revalidate_external_tool,
    run_tool,
)
from video_content_pipeline.source import SourceArtifact, sha256_file
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval


class AnalysisAudioDerivationError(ValueError):
    """A failed deterministic audio-derivation precondition."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class _AudioSelection(Protocol):
    @property
    def source_id(self) -> str: ...

    @property
    def stream_index(self) -> int: ...

    @property
    def structural_evidence_sha256(self) -> str: ...

    @property
    def coverage_evidence_sha256(self) -> str: ...


@dataclass(frozen=True)
class PreprocessingProfile:
    """Versioned, explicit waveform preparation settings."""

    profile_id: str
    sample_rate: int
    channel_count: int
    loudness_mode: str
    chunk_samples: int

    def __post_init__(self) -> None:
        if not self.profile_id or self.sample_rate <= 0 or self.channel_count <= 0:
            raise AnalysisAudioDerivationError(
                "preprocessing_profile_invalid",
                "Preprocessing profile identity and audio dimensions must be positive.",
            )
        if self.loudness_mode != "preserve":
            raise AnalysisAudioDerivationError(
                "preprocessing_profile_invalid",
                "Preprocessing profile must explicitly preserve source loudness.",
            )
        if self.chunk_samples <= 0:
            raise AnalysisAudioDerivationError(
                "preprocessing_profile_invalid", "Chunk size must be positive."
            )

    @classmethod
    def from_json(cls, value: object) -> PreprocessingProfile:
        if not isinstance(value, Mapping) or value.get("schema_version") != 1:
            raise AnalysisAudioDerivationError(
                "preprocessing_profile_invalid", "Preprocessing profile schema is invalid."
            )
        try:
            return cls(
                profile_id=_required_string(value, "id"),
                sample_rate=_required_positive_integer(value, "sample_rate"),
                channel_count=_required_positive_integer(value, "channel_count"),
                loudness_mode=_required_string(value, "loudness_mode"),
                chunk_samples=_required_positive_integer(value, "chunk_samples"),
            )
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, AnalysisAudioDerivationError):
                raise
            raise AnalysisAudioDerivationError(
                "preprocessing_profile_invalid", "Preprocessing profile fields are invalid."
            ) from error

    def as_json(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "id": self.profile_id,
            "sample_rate": self.sample_rate,
            "channel_count": self.channel_count,
            "loudness_mode": self.loudness_mode,
            "chunk_samples": self.chunk_samples,
        }

    @property
    def sha256(self) -> str:
        return _sha256_json(self.as_json())


@dataclass(frozen=True)
class DerivativeTimeMapping:
    """Exact mapping from derivative sample boundaries to source time."""

    source_interval: HalfOpenInterval
    sample_rate: int
    sample_count: int

    def __post_init__(self) -> None:
        if self.sample_rate <= 0 or self.sample_count <= 0:
            raise AnalysisAudioDerivationError(
                "derivative_mapping_invalid", "Derivative sample dimensions must be positive."
            )
        expected = (self.source_interval.end - self.source_interval.start).as_fraction()
        if expected * self.sample_rate != self.sample_count:
            raise AnalysisAudioDerivationError(
                "derivative_boundary_unmappable",
                "The source interval cannot be represented exactly at the profile sample rate.",
            )

    def source_time_for_sample(self, sample_index: int) -> ExactTime:
        self._validate_boundary(sample_index)
        return self.source_interval.start + ExactTime(sample_index, self.sample_rate)

    def source_interval_for_samples(self, start_sample: int, end_sample: int) -> HalfOpenInterval:
        self._validate_boundary(start_sample)
        self._validate_boundary(end_sample)
        if start_sample >= end_sample:
            raise AnalysisAudioDerivationError(
                "derivative_boundary_unmappable", "A derivative sample interval must be positive."
            )
        return HalfOpenInterval(
            self.source_time_for_sample(start_sample), self.source_time_for_sample(end_sample)
        )

    def as_json(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "coordinate": "raw_pts_time",
            "source_interval": _interval_as_json(self.source_interval),
            "sample_rate": self.sample_rate,
            "sample_count": self.sample_count,
        }

    def _validate_boundary(self, sample_index: int) -> None:
        if not isinstance(sample_index, int) or isinstance(sample_index, bool):
            raise AnalysisAudioDerivationError(
                "derivative_boundary_unmappable", "Derivative sample boundaries must be integers."
            )
        if sample_index < 0 or sample_index > self.sample_count:
            raise AnalysisAudioDerivationError(
                "derivative_boundary_unmappable",
                "A derivative sample boundary is outside the derivative.",
            )


@dataclass(frozen=True)
class AnalysisAudioDerivative:
    """Hash-recorded derivative and its exact source-clock mapping."""

    source_id: str
    stream_index: int
    path: Path
    sha256: str
    byte_count: int
    source_artifact_sha256: str
    structural_evidence_sha256: str
    coverage_evidence_sha256: str
    ffmpeg_identity: dict[str, str]
    preprocessing_profile: PreprocessingProfile
    mapping: DerivativeTimeMapping

    def as_json(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "source_id": self.source_id,
            "stream_index": self.stream_index,
            "path": self.path.as_posix(),
            "sha256": self.sha256,
            "byte_count": self.byte_count,
            "source_artifact_sha256": self.source_artifact_sha256,
            "structural_evidence_sha256": self.structural_evidence_sha256,
            "coverage_evidence_sha256": self.coverage_evidence_sha256,
            "ffmpeg": dict(self.ffmpeg_identity),
            "preprocessing_profile": {
                **self.preprocessing_profile.as_json(),
                "sha256": self.preprocessing_profile.sha256,
            },
            "mapping": self.mapping.as_json(),
        }


def prepare_analysis_audio(
    artifact: SourceArtifact,
    selection: _AudioSelection,
    coverage: StreamCoverage,
    ffmpeg: PinnedExternalTool,
    profile: PreprocessingProfile,
    destination: Path,
) -> AnalysisAudioDerivative:
    """Create one immutable Analysis audio derivative from a selected stream."""

    if ffmpeg.tool_id != "ffmpeg":
        raise AnalysisAudioDerivationError(
            "ffmpeg_identity_invalid", "Analysis audio derivation requires pinned FFmpeg."
        )
    if artifact.source_id != selection.source_id:
        raise AnalysisAudioDerivationError(
            "analysis_audio_selection_invalid", "Selection does not belong to the SourceArtifact."
        )
    if coverage.coverage is None or coverage.diagnostics or coverage.gaps:
        raise AnalysisAudioDerivationError(
            "derivative_boundary_unmappable",
            "Analysis audio requires one contiguous, diagnostically clean audio coverage interval.",
        )
    try:
        actual_hash, actual_size = sha256_file(artifact.media_path)
    except OSError as error:
        raise AnalysisAudioDerivationError(
            "source_artifact_unavailable", "The SourceArtifact cannot be read."
        ) from error
    if actual_hash != artifact.sha256 or actual_size != artifact.byte_count:
        raise AnalysisAudioDerivationError(
            "source_artifact_changed", "The SourceArtifact hash changed before derivation."
        )
    duration = (coverage.coverage.end - coverage.coverage.start).as_fraction()
    sample_count = duration * profile.sample_rate
    if sample_count.denominator != 1:
        raise AnalysisAudioDerivationError(
            "derivative_boundary_unmappable",
            "The profile sample rate cannot represent the observed source boundary exactly.",
        )
    mapping = DerivativeTimeMapping(coverage.coverage, profile.sample_rate, sample_count.numerator)
    destination = destination.resolve()
    if destination.exists():
        raise AnalysisAudioDerivationError(
            "derivative_exists",
            "Analysis audio derivatives are immutable and cannot be overwritten.",
        )
    try:
        revalidate_external_tool(ffmpeg)
        destination.parent.mkdir(parents=True, exist_ok=True)
        result = run_tool(
            (
                str(ffmpeg.path),
                "-v",
                "error",
                "-nostdin",
                "-copyts",
                "-start_at_zero",
                "-ss",
                _exact_timestamp(coverage.coverage.start),
                "-i",
                str(artifact.media_path),
                "-t",
                _exact_timestamp(coverage.coverage.end - coverage.coverage.start),
                "-map",
                f"0:{selection.stream_index}",
                "-vn",
                "-sn",
                "-dn",
                "-ar",
                str(profile.sample_rate),
                "-ac",
                str(profile.channel_count),
                "-c:a",
                "pcm_s16le",
                "-f",
                "wav",
                str(destination),
            )
        )
    except (ExternalToolError, OSError, ValueError) as error:
        reason = getattr(error, "reason", "ffmpeg_derivation_failed")
        raise AnalysisAudioDerivationError(reason, str(error)) from error
    if result.returncode != 0 or not destination.is_file():
        raise AnalysisAudioDerivationError(
            "ffmpeg_derivation_failed",
            result.stderr.strip() or "Pinned FFmpeg did not produce the derivative.",
        )
    derivative_hash, derivative_size = sha256_file(destination)
    derivative = AnalysisAudioDerivative(
        source_id=selection.source_id,
        stream_index=selection.stream_index,
        path=destination,
        sha256=derivative_hash,
        byte_count=derivative_size,
        source_artifact_sha256=artifact.sha256,
        structural_evidence_sha256=selection.structural_evidence_sha256,
        coverage_evidence_sha256=selection.coverage_evidence_sha256,
        ffmpeg_identity=ffmpeg.as_json(),
        preprocessing_profile=profile,
        mapping=mapping,
    )
    mapping_path = destination.with_suffix(".mapping.json")
    if mapping_path.exists():
        raise AnalysisAudioDerivationError(
            "derivative_exists",
            "Derivative mapping evidence already exists and cannot be overwritten.",
        )
    mapping_path.write_text(json.dumps(derivative.as_json(), sort_keys=True, indent=2) + "\n")
    return derivative


def _required_string(value: Mapping[str, object], key: str) -> str:
    item = value[key]
    if not isinstance(item, str) or not item:
        raise ValueError(key)
    return item


def _required_positive_integer(value: Mapping[str, object], key: str) -> int:
    item = value[key]
    if not isinstance(item, int) or isinstance(item, bool) or item <= 0:
        raise ValueError(key)
    return item


def _interval_as_json(interval: HalfOpenInterval) -> dict[str, object]:
    return {
        "start": {"numerator": interval.start.numerator, "denominator": interval.start.denominator},
        "end": {"numerator": interval.end.numerator, "denominator": interval.end.denominator},
    }


def _sha256_json(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(payload).hexdigest()


def _exact_timestamp(value: ExactTime) -> str:
    """Serialize a rational FFmpeg timestamp without floating-point rounding."""

    return f"{value.numerator}/{value.denominator}"
