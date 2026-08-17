"""The VAD subprocess seam: child handler shape + parent parsing (no real model)."""

from __future__ import annotations

from pathlib import Path

import pytest

from video_content_pipeline import vad_child, vad_engine
from video_content_pipeline.audio_analysis import (
    VadPartEvidence,
    VoiceActivityInterval,
    VoiceActivityState,
)
from video_content_pipeline.audio_derivation import DerivativeTimeMapping
from video_content_pipeline.model_runtime import EngineRequest, EngineResult
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
from video_content_pipeline.vad_engine import IsolatedVadResult, VadEngineError


def _mapping() -> DerivativeTimeMapping:
    return DerivativeTimeMapping(
        source_interval=HalfOpenInterval(ExactTime(0), ExactTime(2)),
        sample_rate=16000,
        sample_count=32000,
    )


def _canned_result() -> vad_engine.SileroVadResult:
    part = VadPartEvidence(
        source_id="part-a",
        stream_index=1,
        voice_activity_intervals=(
            VoiceActivityInterval(
                HalfOpenInterval(ExactTime(0), ExactTime(1)), VoiceActivityState.SPEECH_LIKELY
            ),
        ),
        uncovered_speech_risks=(),
        audio_state_indeterminate=(),
        long_silences=(),
    )
    return vad_engine.SileroVadResult(
        part_evidence=part,
        speech_runs_samples=((0, 16000),),
        chunks=(),
        model_asset_sha256="a" * 64,
        calibrated=True,
    )


def test_run_vad_handler_returns_report_shaped_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    from video_content_pipeline.audio_analysis import _vad_part_evidence_as_json

    canned = _canned_result()
    captured: dict[str, object] = {}

    def fake_analyze(project_root: Path, wav_path: Path, mapping: DerivativeTimeMapping, **kwargs):
        captured["project_root"] = project_root
        captured["wav_path"] = wav_path
        captured["mapping"] = mapping
        captured["kwargs"] = kwargs
        return canned

    monkeypatch.setattr(vad_engine, "analyze_derivative_vad", fake_analyze)

    request = EngineRequest(
        model_path="/proj",
        task={
            "wav_path": "/proj/work/stream-1.wav",
            "mapping": _mapping().as_json(),
            "source_id": "part-a",
            "stream_index": 1,
            "caption_intervals": [
                {
                    "start": {"numerator": 0, "denominator": 1},
                    "end": {"numerator": 1, "denominator": 1},
                }
            ],
        },
    )

    output = vad_child.run_vad(request)

    assert output["part_evidence"] == _vad_part_evidence_as_json(canned.part_evidence)
    assert output["speech_runs_samples"] == [[0, 16000]]
    assert output["model_asset_sha256"] == "a" * 64
    assert output["calibrated"] is True
    # The pinned derivative path and mapping reach the engine unchanged, and the
    # caption intervals are reconstructed for coverage-risk derivation.
    assert captured["project_root"] == Path("/proj")
    assert captured["wav_path"] == Path("/proj/work/stream-1.wav")
    assert captured["mapping"] == _mapping()
    assert captured["kwargs"]["caption_intervals"] == (
        HalfOpenInterval(ExactTime(0), ExactTime(1)),
    )


def test_run_isolated_vad_parses_child_result(monkeypatch: pytest.MonkeyPatch) -> None:
    from video_content_pipeline.audio_analysis import _vad_part_evidence_as_json

    part_json = _vad_part_evidence_as_json(_canned_result().part_evidence)

    def fake_subprocess(command, request, *, timeout_seconds):
        assert request.model_path == "/proj"
        return EngineResult(
            result={
                "part_evidence": part_json,
                "speech_runs_samples": [[0, 16000]],
                "model_asset_sha256": "a" * 64,
                "calibrated": True,
            },
            peak_memory_bytes=123_456_789,
            child_pid=4242,
        )

    monkeypatch.setattr(vad_engine, "run_engine_subprocess", fake_subprocess)

    result = vad_engine.run_isolated_vad(
        Path("/proj"),
        Path("/proj/work/stream-1.wav"),
        _mapping(),
        source_id="part-a",
        stream_index=1,
        command=["stub"],
    )

    assert isinstance(result, IsolatedVadResult)
    assert result.part_evidence == part_json
    assert result.speech_runs_samples == ((0, 16000),)
    assert result.model_asset_sha256 == "a" * 64
    assert result.calibrated is True
    assert result.peak_memory_bytes == 123_456_789


def test_run_isolated_vad_rejects_malformed_child_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_subprocess(command, request, *, timeout_seconds):
        return EngineResult(
            result={"part_evidence": {}, "speech_runs_samples": [[0, "x"]]},
            peak_memory_bytes=1,
            child_pid=1,
        )

    monkeypatch.setattr(vad_engine, "run_engine_subprocess", fake_subprocess)

    with pytest.raises(VadEngineError) as error:
        vad_engine.run_isolated_vad(
            Path("/proj"),
            Path("/proj/work/stream-1.wav"),
            _mapping(),
            source_id="part-a",
            stream_index=1,
            command=["stub"],
        )
    assert error.value.reason == "vad_output_invalid"
