from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from video_content_pipeline.audio_analysis import AnalysisAudioStreamSelection
from video_content_pipeline.audio_derivation import (
    AnalysisAudioDerivationError,
    DerivativeTimeMapping,
    PreprocessingProfile,
    _exact_timestamp,
    prepare_analysis_audio,
)
from video_content_pipeline.coverage import StreamCoverage
from video_content_pipeline.external_tools import PinnedExternalTool
from video_content_pipeline.source import SourceArtifact
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval


def _selection() -> AnalysisAudioStreamSelection:
    return AnalysisAudioStreamSelection(
        source_id="part-a",
        stream_index=2,
        codec="aac",
        language="en",
        disposition={"default": 1},
        structural_evidence_sha256="a" * 64,
        coverage_evidence_sha256="b" * 64,
    )


def _profile() -> PreprocessingProfile:
    return PreprocessingProfile(
        profile_id="phase-5-analysis-audio-v1",
        sample_rate=48_000,
        channel_count=1,
        loudness_mode="preserve",
        chunk_samples=48_000,
    )


def test_preprocessing_profile_rejects_implicit_audio_transforms() -> None:
    with pytest.raises(AnalysisAudioDerivationError, match="loudness") as error:
        PreprocessingProfile(
            profile_id="phase-5-v1",
            sample_rate=48_000,
            channel_count=1,
            loudness_mode="ebu-r128",
            chunk_samples=48_000,
        )

    assert error.value.reason == "preprocessing_profile_invalid"


def test_derivative_time_mapping_uses_exact_source_boundaries() -> None:
    mapping = DerivativeTimeMapping(
        source_interval=HalfOpenInterval(ExactTime(-1, 2), ExactTime(3, 2)),
        sample_rate=2,
        sample_count=4,
    )

    assert mapping.source_time_for_sample(0) == ExactTime(-1, 2)
    assert mapping.source_time_for_sample(3) == ExactTime(1)
    assert mapping.source_interval_for_samples(1, 3) == HalfOpenInterval(ExactTime(0), ExactTime(1))

    with pytest.raises(AnalysisAudioDerivationError, match="outside") as error:
        mapping.source_interval_for_samples(3, 5)
    assert error.value.reason == "derivative_boundary_unmappable"


def test_derivative_time_mapping_round_trips_through_json() -> None:
    mapping = DerivativeTimeMapping(
        source_interval=HalfOpenInterval(ExactTime(-1, 2), ExactTime(3, 2)),
        sample_rate=2,
        sample_count=4,
    )

    assert DerivativeTimeMapping.from_json(mapping.as_json()) == mapping


def test_derivative_time_mapping_from_json_rejects_malformed_payloads() -> None:
    valid = DerivativeTimeMapping(
        source_interval=HalfOpenInterval(ExactTime(0), ExactTime(1)),
        sample_rate=1,
        sample_count=1,
    ).as_json()

    for mutation in ({"sample_rate": "1"}, {"sample_count": None}, {"source_interval": {}}):
        payload = {**valid, **mutation}
        with pytest.raises(AnalysisAudioDerivationError) as error:
            DerivativeTimeMapping.from_json(payload)
        assert error.value.reason == "derivative_mapping_invalid"


def test_sample_for_source_time_is_the_rounding_inverse() -> None:
    mapping = DerivativeTimeMapping(
        source_interval=HalfOpenInterval(ExactTime(-1, 2), ExactTime(3, 2)),
        sample_rate=2,
        sample_count=4,
    )

    # Exact boundaries round-trip through the forward mapping.
    for sample in range(mapping.sample_count + 1):
        assert mapping.sample_for_source_time(mapping.source_time_for_sample(sample)) == sample

    # A time between two samples rounds to the nearest, and an out-of-coverage time
    # is returned unclamped (the caller decides how to clamp).
    assert mapping.sample_for_source_time(ExactTime(1, 4)) == 2  # 0.25 s -> sample 1.5 -> 2
    assert mapping.sample_for_source_time(ExactTime(5, 2)) == 6  # past the coverage end


def test_prepare_analysis_audio_revalidates_ffmpeg_and_retains_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = tmp_path / "source.mkv"
    source_path.write_bytes(b"source")
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    artifact = SourceArtifact("part-a", source_hash, source_path.stat().st_size, source_path)
    coverage = StreamCoverage(
        coverage=HalfOpenInterval(ExactTime(-1, 2), ExactTime(3, 2)), gaps=(), diagnostics=()
    )
    ffmpeg = PinnedExternalTool("ffmpeg", tmp_path / "ffmpeg", "fixture", "c" * 64)
    destination = tmp_path / "work" / "analysis.wav"
    captured_arguments: list[str] = []

    monkeypatch.setattr(
        "video_content_pipeline.audio_derivation.revalidate_external_tool", lambda _: None
    )

    def fake_run(arguments: tuple[str, ...], *, timeout_seconds: int | None = None):
        captured_arguments.extend(arguments)
        destination.write_bytes(b"derived")
        return type("Result", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr("video_content_pipeline.audio_derivation.run_tool", fake_run)

    derivative = prepare_analysis_audio(
        artifact, _selection(), coverage, ffmpeg, _profile(), destination
    )

    assert derivative.source_artifact_sha256 == source_hash
    assert derivative.path == destination
    assert derivative.mapping.sample_count == 96_000
    # FFmpeg's -ss/-t take seconds as a decimal, not a "num/den" rational.
    assert captured_arguments[captured_arguments.index("-ss") + 1] == "-0.5"
    assert captured_arguments[captured_arguments.index("-t") + 1] == "2"
    assert derivative.as_json()["ffmpeg"]["sha256"] == "c" * 64
    assert derivative.as_json()["preprocessing_profile"]["id"] == _profile().profile_id
    assert destination.with_suffix(".mapping.json").exists()


def test_exact_timestamp_serializes_ffmpeg_decimal_seconds() -> None:
    # FFmpeg's -ss/-t reject a "num/den" rational; they take seconds as a decimal.
    assert _exact_timestamp(ExactTime(0, 1)) == "0"
    assert _exact_timestamp(ExactTime(3, 1)) == "3"
    assert _exact_timestamp(ExactTime(-1, 2)) == "-0.5"
    assert _exact_timestamp(ExactTime(3, 1000)) == "0.003"  # millisecond time base
    # A non-terminating expansion is capped at FFmpeg's own resolution, never lost.
    capped = _exact_timestamp(ExactTime(1, 3))
    assert capped.startswith("0.3333333") and len(capped) <= len("0.") + 9


def test_prepare_analysis_audio_rejects_coverage_gaps_before_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ffmpeg = PinnedExternalTool("ffmpeg", tmp_path / "ffmpeg", "fixture", "c" * 64)
    coverage = StreamCoverage(
        coverage=HalfOpenInterval(ExactTime(0), ExactTime(2)),
        gaps=(HalfOpenInterval(ExactTime(1), ExactTime(3, 2)),),
        diagnostics=(),
    )
    with pytest.raises(AnalysisAudioDerivationError) as error:
        prepare_analysis_audio(
            SourceArtifact("part-a", "a" * 64, 1, tmp_path / "source"),
            _selection(),
            coverage,
            ffmpeg,
            _profile(),
            tmp_path / "work" / "analysis.wav",
        )
    assert error.value.reason == "derivative_boundary_unmappable"
