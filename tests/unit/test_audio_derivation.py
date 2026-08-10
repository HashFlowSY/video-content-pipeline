from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from video_content_pipeline.audio_analysis import AnalysisAudioStreamSelection
from video_content_pipeline.audio_derivation import (
    AnalysisAudioDerivationError,
    DerivativeTimeMapping,
    PreprocessingProfile,
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
    assert captured_arguments[captured_arguments.index("-ss") + 1] == "-1/2"
    assert captured_arguments[captured_arguments.index("-t") + 1] == "2/1"
    assert derivative.as_json()["ffmpeg"]["sha256"] == "c" * 64
    assert derivative.as_json()["preprocessing_profile"]["id"] == _profile().profile_id
    assert destination.with_suffix(".mapping.json").exists()


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
