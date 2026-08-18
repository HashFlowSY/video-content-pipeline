"""The real-engine audio bridge: evidence mapping, qualification, runtime controls.

These exercise the real derive functions and their helpers directly, monkeypatching
the isolated engine runners so no model is loaded. The full analyze_audio spine is
covered by the offline tests; here the focus is the real-path mapping onto the
identical formal-evidence / stage_execution contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from video_content_pipeline import audio_analysis
from video_content_pipeline.audio_analysis import (
    AnalysisAudioStreamSelection,
    AudioAnalysisError,
    SpeakerTurnCandidate,
    VadPartEvidence,
    VoiceActivityInterval,
    VoiceActivityState,
    _candidate_qualifies,
    _derive_diarization_evidence_real,
    _derive_vad_evidence_real,
    _real_calibration_profile,
    _real_derivatives_by_key,
    _real_runtime_controls,
    _record_stage_execution,
)
from video_content_pipeline.audio_derivation import DerivativeTimeMapping
from video_content_pipeline.real_engine_adapter import RealEngineSelection
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _selection(source_id: str = "part-a", stream_index: int = 1) -> AnalysisAudioStreamSelection:
    return AnalysisAudioStreamSelection(
        source_id=source_id,
        stream_index=stream_index,
        codec="aac",
        language="en",
        disposition={"default": 1},
        structural_evidence_sha256="a" * 64,
        coverage_evidence_sha256="b" * 64,
    )


def _mapping() -> DerivativeTimeMapping:
    return DerivativeTimeMapping(
        source_interval=HalfOpenInterval(ExactTime(0), ExactTime(2)),
        sample_rate=16000,
        sample_count=32000,
    )


def _vad_part(source_id: str = "part-a") -> VadPartEvidence:
    return VadPartEvidence(
        source_id=source_id,
        stream_index=1,
        voice_activity_intervals=(
            VoiceActivityInterval(
                HalfOpenInterval(ExactTime(0), ExactTime(2)), VoiceActivityState.SPEECH_LIKELY
            ),
        ),
        uncovered_speech_risks=(),
        audio_state_indeterminate=(),
        long_silences=(),
    )


# --- _real_calibration_profile -----------------------------------------------


def test_real_calibration_profile_accepts_real_sample_confirmed_prefix() -> None:
    # vad config is 'real_sample_confirmed'; diarization is 'real_sample_confirmed_with_note'.
    for capability in ("vad", "forced_alignment", "diarization"):
        profile = _real_calibration_profile(PROJECT_ROOT, capability)
        assert set(profile) == {"path", "sha256", "byte_count"}
        assert profile["path"].endswith(".json")


def test_real_calibration_profile_rejects_synthetic(tmp_path: pytest.TempPathFactory) -> None:
    root = Path(tmp_path)  # type: ignore[arg-type]
    config = root / "config" / "audio-analysis"
    config.mkdir(parents=True)
    (config / "silero-vad-calibration.json").write_text(
        '{"qualification_scope": "synthetic_verification_only"}', encoding="utf-8"
    )
    with pytest.raises(AudioAnalysisError) as error:
        _real_calibration_profile(root, "vad")
    assert error.value.reason == "calibration_scope_insufficient"


# --- _real_derivatives_by_key ------------------------------------------------


def test_real_derivatives_by_key_reconstructs_path_and_mapping() -> None:
    derivative = {
        "source_id": "part-a",
        "stream_index": 1,
        "path": "/proj/work/stream-1.wav",
        "mapping": _mapping().as_json(),
    }
    result = _real_derivatives_by_key((derivative,))
    assert result[("part-a", 1)] == (Path("/proj/work/stream-1.wav"), _mapping())


def test_real_derivatives_by_key_rejects_malformed() -> None:
    with pytest.raises(AudioAnalysisError) as error:
        _real_derivatives_by_key(({"source_id": "part-a", "stream_index": 1},))
    assert error.value.reason == "analysis_audio_derivative_invalid"


# --- qualification (real-aware) ----------------------------------------------


def test_candidate_qualifies_real_by_eligibility_only() -> None:
    real = RealEngineSelection(project_root=PROJECT_ROOT, capabilities=frozenset({"vad"}))
    eligible_no_fixture = {"state": "eligible"}
    # Offline needs the fixture projected/qualified gate; real needs only eligibility.
    assert _candidate_qualifies(eligible_no_fixture, "vad", real) is True
    assert _candidate_qualifies(eligible_no_fixture, "vad", None) is False


# --- _derive_vad_evidence_real -----------------------------------------------


def test_derive_vad_evidence_real_maps_engine_output(monkeypatch: pytest.MonkeyPatch) -> None:
    from video_content_pipeline import vad_engine
    from video_content_pipeline.audio_analysis import _vad_part_evidence_as_json

    monkeypatch.setattr(audio_analysis, "_primary_caption_intervals", lambda *a, **k: ())

    isolated = vad_engine.IsolatedVadResult(
        part_evidence=_vad_part_evidence_as_json(_vad_part()),
        speech_runs_samples=((0, 16000),),
        model_asset_sha256="a" * 64,
        calibrated=True,
        peak_memory_bytes=124_551_168,
    )
    monkeypatch.setattr(vad_engine, "run_isolated_vad", lambda *a, **k: isolated)

    evidence, results, peak = _derive_vad_evidence_real(
        {"candidate_id": "silero-vad"},
        (_selection(),),
        subtitle_report=object(),  # unused: _primary_caption_intervals is stubbed
        project_root=PROJECT_ROOT,
        derivatives_by_key={("part-a", 1): (Path("stream-1.wav"), _mapping())},
    )

    assert evidence["capability"] == "vad"
    assert evidence["candidate_id"] == "silero-vad"
    assert evidence["parts"] == [_vad_part_evidence_as_json(_vad_part())]
    assert str(evidence["calibration_profile"]["path"]).endswith("silero-vad-calibration.json")
    assert results[("part-a", 1)] is isolated
    assert peak == 124_551_168


# --- _derive_diarization_evidence_real ---------------------------------------


def test_derive_diarization_evidence_real_applies_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    from video_content_pipeline import diarization_engine

    monkeypatch.setattr(audio_analysis, "_speaker_role_cues", lambda *a, **k: {})

    isolated = diarization_engine.IsolatedDiarizationResult(
        raw_turns=(
            SpeakerTurnCandidate("cluster-0", HalfOpenInterval(ExactTime(0), ExactTime(1)), 1.0),
        ),
        segmentation_asset_sha256="a" * 64,
        embedding_asset_sha256="b" * 64,
        calibrated=True,
        peak_memory_bytes=348_241_920,
    )
    monkeypatch.setattr(diarization_engine, "run_isolated_diarization", lambda *a, **k: isolated)

    # A real VAD partition marking the whole coverage speech-likely, and coverage
    # evidence for the usable-audio window.
    from video_content_pipeline.coverage import StreamCoverage

    class _Inspection:
        source_id = "part-a"
        coverage_by_stream = {
            1: StreamCoverage(HalfOpenInterval(ExactTime(0), ExactTime(2)), (), ())
        }

    vad_evidence = {
        "parts": [
            {
                "source_id": "part-a",
                "voice_activity_intervals": [
                    {
                        "interval": {
                            "start": {"numerator": 0, "denominator": 1},
                            "end": {"numerator": 2, "denominator": 1},
                        },
                        "state": "speech_likely",
                    }
                ],
            }
        ]
    }

    evidence, peak = _derive_diarization_evidence_real(
        {"candidate_id": "sherpa-onnx-pyannote-segmentation-3-0"},
        (_selection(),),
        (_Inspection(),),  # type: ignore[arg-type]
        subtitle_report=object(),
        plan=object(),  # unused: no user role metadata
        vad_evidence=vad_evidence,
        project_root=PROJECT_ROOT,
        workspace_path=PROJECT_ROOT / "does-not-write",
        user_role_metadata=(),
        derivatives_by_key={("part-a", 1): (Path("stream-1.wav"), _mapping())},
    )

    assert evidence["capability"] == "diarization"
    assert peak == 348_241_920
    part = evidence["parts"][0]
    assert part["source_id"] == "part-a"
    # The clean speech-likely turn is published; the real engine proposes no roles.
    assert len(part["speaker_turns"]) == 1
    assert part["role_candidates"] == []


# --- runtime controls / envelope --------------------------------------------


def test_real_runtime_controls_over_envelope_is_release_unverified(tmp_path: Path) -> None:
    candidate = {
        "capability": "vad",
        "candidate_id": "silero-vad",
        "eligibility_evidence": {"resource_high_bytes": 124_551_168},
    }
    # A peak within envelope completes; over-envelope fails closed.
    ok = _record_stage_execution(
        candidate, {"parts": []}, tmp_path / "ok", runtime_controls=_real_runtime_controls(100)
    )
    assert ok["state"] == "completed"
    over = _record_stage_execution(
        candidate,
        {"parts": []},
        tmp_path / "over",
        runtime_controls=_real_runtime_controls(999_999_999),
    )
    assert over["state"] == "release_unverified"
