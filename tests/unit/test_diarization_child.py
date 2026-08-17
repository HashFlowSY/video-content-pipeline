"""The diarization subprocess seam: child handler shape + parent parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from video_content_pipeline import diarization_child, diarization_engine
from video_content_pipeline.audio_analysis import SpeakerTurnCandidate
from video_content_pipeline.audio_derivation import DerivativeTimeMapping
from video_content_pipeline.diarization_engine import (
    DiarizationEngineError,
    IsolatedDiarizationResult,
    SherpaDiarizationResult,
)
from video_content_pipeline.model_runtime import EngineRequest, EngineResult
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval


def _mapping() -> DerivativeTimeMapping:
    return DerivativeTimeMapping(
        source_interval=HalfOpenInterval(ExactTime(0), ExactTime(2)),
        sample_rate=16000,
        sample_count=32000,
    )


def _turn() -> SpeakerTurnCandidate:
    return SpeakerTurnCandidate(
        "cluster-0", HalfOpenInterval(ExactTime(0), ExactTime(1)), 1.0
    )


def test_run_diarization_handler_returns_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    canned = SherpaDiarizationResult(
        source_id="part-a",
        stream_index=1,
        part_label="part-01",
        raw_turns=(_turn(),),
        partition=None,
        segmentation_asset_sha256="a" * 64,
        embedding_asset_sha256="b" * 64,
        calibrated=True,
    )

    def fake_analyze(project_root, wav_path, mapping, **kwargs):
        assert project_root == Path("/proj")
        assert kwargs["part_label"] == "part-01"
        return canned

    monkeypatch.setattr(diarization_engine, "analyze_derivative_diarization", fake_analyze)

    request = EngineRequest(
        model_path="/proj",
        task={
            "wav_path": "/proj/work/stream-1.wav",
            "mapping": _mapping().as_json(),
            "source_id": "part-a",
            "stream_index": 1,
            "part_label": "part-01",
        },
    )

    output = diarization_child.run_diarization(request)

    assert output["raw_turns"] == [
        diarization_engine._speaker_turn_candidate_as_json(_turn())
    ]
    assert output["segmentation_asset_sha256"] == "a" * 64
    assert output["embedding_asset_sha256"] == "b" * 64
    assert output["calibrated"] is True


def test_run_isolated_diarization_round_trips_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    turn_json = diarization_engine._speaker_turn_candidate_as_json(_turn())

    def fake_subprocess(command, request, *, timeout_seconds):
        return EngineResult(
            result={
                "raw_turns": [turn_json],
                "segmentation_asset_sha256": "a" * 64,
                "embedding_asset_sha256": "b" * 64,
                "calibrated": True,
            },
            peak_memory_bytes=348_241_920,
            child_pid=99,
        )

    monkeypatch.setattr(diarization_engine, "run_engine_subprocess", fake_subprocess)

    result = diarization_engine.run_isolated_diarization(
        Path("/proj"),
        Path("/proj/work/stream-1.wav"),
        _mapping(),
        source_id="part-a",
        stream_index=1,
        part_label="part-01",
        command=["stub"],
    )

    assert isinstance(result, IsolatedDiarizationResult)
    assert result.raw_turns == (_turn(),)
    assert result.segmentation_asset_sha256 == "a" * 64
    assert result.peak_memory_bytes == 348_241_920


def test_run_isolated_diarization_rejects_malformed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_subprocess(command, request, *, timeout_seconds):
        return EngineResult(
            result={"raw_turns": [{"cluster_id": "", "interval": {}, "confidence": 1.0}]},
            peak_memory_bytes=1,
            child_pid=1,
        )

    monkeypatch.setattr(diarization_engine, "run_engine_subprocess", fake_subprocess)

    with pytest.raises(DiarizationEngineError) as error:
        diarization_engine.run_isolated_diarization(
            Path("/proj"),
            Path("/proj/work/stream-1.wav"),
            _mapping(),
            source_id="part-a",
            stream_index=1,
            part_label="part-01",
            command=["stub"],
        )
    assert error.value.reason == "diarization_output_invalid"
