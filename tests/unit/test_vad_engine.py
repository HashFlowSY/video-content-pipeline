"""Model-free tests for the real silero-vad engine (Phase 11 ticket 06).

These cover the parts that never touch onnxruntime: the ADR 0029 calibration
gate, the pure probability-to-speech shaping, the tiled partition projection,
and the typed asset-acquisition failures. Real inference over the pinned asset
is exercised by the offline integration test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_content_pipeline.audio_derivation import DerivativeTimeMapping
from video_content_pipeline.coverage import StreamCoverage
from video_content_pipeline.model_acquisition import build_file_manifest, manifest_asset_sha256
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
from video_content_pipeline.vad_engine import (
    CANDIDATE_ID,
    SileroVadCalibration,
    VadEngineError,
    VoiceActivityState,
    candidate_segments_from_speech_runs,
    derive_vad_part_evidence,
    indeterminate_segments,
    load_silero_asset,
    load_silero_calibration,
    resolve_silero_candidate,
    speech_runs_from_probabilities,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _mapping(sample_rate: int, sample_count: int) -> DerivativeTimeMapping:
    return DerivativeTimeMapping(
        HalfOpenInterval(ExactTime(0), ExactTime(sample_count, sample_rate)),
        sample_rate,
        sample_count,
    )


def _calibration(**overrides: object) -> SileroVadCalibration:
    base: dict[str, object] = {
        "calibration_version": "test",
        "model_asset_sha256": "a" * 64,
        "sample_rate": 16000,
        "window_samples": 4,
        "speech_probability_threshold": 0.5,
        "min_speech_samples": 8,
        "min_silence_samples": 4,
        "speech_pad_samples": 0,
        "uncovered_speech_duration": ExactTime(1),
        "long_silence_duration": ExactTime(1),
    }
    base.update(overrides)
    return SileroVadCalibration(**base)  # type: ignore[arg-type]


# --- calibration gate (ADR 0029) ----------------------------------------------


def test_shipped_calibration_loads_and_binds_to_the_registry_asset() -> None:
    candidate = resolve_silero_candidate(REPO_ROOT)
    calibration = load_silero_calibration(
        REPO_ROOT, expected_asset_sha256=str(candidate["asset_sha256"])
    )
    assert calibration.model_asset_sha256 == candidate["asset_sha256"]
    assert calibration.sample_rate == 16000
    assert calibration.window_samples == 512
    assert 0 < calibration.speech_probability_threshold < 1


def test_calibration_rejects_a_mismatched_model_asset() -> None:
    with pytest.raises(VadEngineError) as excinfo:
        load_silero_calibration(REPO_ROOT, expected_asset_sha256="b" * 64)
    assert excinfo.value.reason == "vad_calibration_model_mismatch"


def test_calibration_rejects_a_bad_schema(tmp_path: Path) -> None:
    (tmp_path / "config" / "audio-analysis").mkdir(parents=True)
    (tmp_path / "config" / "audio-analysis" / "silero-vad-calibration.json").write_text(
        json.dumps({"schema_version": 2}), encoding="utf-8"
    )
    with pytest.raises(VadEngineError) as excinfo:
        load_silero_calibration(tmp_path)
    assert excinfo.value.reason == "vad_calibration_invalid"


def test_calibration_post_init_guards_direct_construction() -> None:
    # A directly constructed record is validated just like a parsed one.
    with pytest.raises(VadEngineError) as excinfo:
        _calibration(speech_probability_threshold=1.5)
    assert excinfo.value.reason == "vad_calibration_invalid"


def test_calibration_round_trips_through_json() -> None:
    calibration = _calibration(window_samples=512)
    assert SileroVadCalibration.from_json(calibration.as_json()) == calibration


def test_calibration_rejects_a_non_16k_configuration(tmp_path: Path) -> None:
    record = json.loads(
        (REPO_ROOT / "config" / "audio-analysis" / "silero-vad-calibration.json").read_text()
    )
    record["sample_rate"] = 8000
    (tmp_path / "config" / "audio-analysis").mkdir(parents=True)
    (tmp_path / "config" / "audio-analysis" / "silero-vad-calibration.json").write_text(
        json.dumps(record), encoding="utf-8"
    )
    with pytest.raises(VadEngineError) as excinfo:
        load_silero_calibration(tmp_path)
    assert excinfo.value.reason == "vad_calibration_invalid"


# --- pure probability shaping -------------------------------------------------


def test_speech_runs_merge_short_silences_and_drop_short_runs() -> None:
    calibration = _calibration()
    # windows of 4 samples each; probabilities per window over 40 samples.
    probabilities = [0.9, 0.9, 0.1, 0.9, 0.9, 0.1, 0.1, 0.1, 0.9, 0.9]
    runs = speech_runs_from_probabilities(probabilities, 40, calibration)
    # [0,8) and [12,20) are 4 samples apart (== min_silence) -> merge to [0,20);
    # the 12-sample gap to [32,40) stays; both survivors exceed min_speech(8).
    assert runs == ((0, 20), (32, 40))


def test_speech_runs_drops_a_run_shorter_than_min_speech() -> None:
    calibration = _calibration()
    runs = speech_runs_from_probabilities([0.9, 0.1, 0.1, 0.1], 16, calibration)
    assert runs == ()


def test_speech_runs_pad_expands_and_re_merges() -> None:
    calibration = _calibration(min_speech_samples=1, min_silence_samples=0, speech_pad_samples=3)
    # Two isolated speech windows [0,4) and [8,12); pad 3 -> [0,7) and [5,15) overlap -> merge.
    runs = speech_runs_from_probabilities([0.9, 0.1, 0.9, 0.1], 16, calibration)
    assert runs == ((0, 15),)


def test_below_threshold_probabilities_yield_no_speech() -> None:
    calibration = _calibration()
    assert speech_runs_from_probabilities([0.1, 0.2, 0.49], 12, calibration) == ()


# --- projection ---------------------------------------------------------------


def test_candidate_segments_tile_the_whole_coverage() -> None:
    mapping = _mapping(16000, 16000)
    segments = candidate_segments_from_speech_runs(((4000, 12000),), mapping)
    assert [(s.interval.start, s.interval.end, s.state) for s in segments] == [
        (ExactTime(0), ExactTime(1, 4), VoiceActivityState.NON_SPEECH),
        (ExactTime(1, 4), ExactTime(3, 4), VoiceActivityState.SPEECH_LIKELY),
        (ExactTime(3, 4), ExactTime(1), VoiceActivityState.NON_SPEECH),
    ]


def test_calibrated_projection_fully_classifies_the_partition() -> None:
    mapping = _mapping(16000, 16000)
    segments = candidate_segments_from_speech_runs(((4000, 12000),), mapping)
    evidence = derive_vad_part_evidence(
        source_id="part-a",
        stream_index=0,
        audio_coverage=StreamCoverage(coverage=mapping.source_interval, gaps=(), diagnostics=()),
        candidate_segments=segments,
        caption_intervals=(),
        uncovered_speech_threshold=ExactTime(1, 100),
        long_silence_threshold=ExactTime(1, 100),
    )
    states = [item.state for item in evidence.voice_activity_intervals]
    assert VoiceActivityState.INDETERMINATE not in states
    assert VoiceActivityState.SPEECH_LIKELY in states


def test_indeterminate_segments_cover_the_whole_coverage() -> None:
    mapping = _mapping(16000, 32000)
    segments = indeterminate_segments(mapping)
    assert len(segments) == 1
    assert segments[0].state is VoiceActivityState.INDETERMINATE
    assert segments[0].interval == HalfOpenInterval(ExactTime(0), ExactTime(2))


# --- asset acquisition failure (never network) --------------------------------


def _write_registry(root: Path, candidate: dict[str, object]) -> None:
    (root / "models").mkdir(parents=True, exist_ok=True)
    (root / "models" / "registry.json").write_text(
        json.dumps({"schema_version": 2, "candidates": [candidate]}), encoding="utf-8"
    )


def test_missing_asset_tree_is_a_typed_failure(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        {
            "candidate_id": CANDIDATE_ID,
            "capability": "vad",
            "local_path": "models/snakers4/silero-vad/v6.2.1/",
            "asset_sha256": "0" * 64,
            "file_manifest": [{"path": "silero_vad.onnx", "size": 1, "sha256": "0" * 64}],
        },
    )
    with pytest.raises(VadEngineError) as excinfo:
        load_silero_asset(tmp_path)
    assert excinfo.value.reason == "vad_asset_unavailable"


def test_tampered_asset_is_a_typed_mismatch(tmp_path: Path) -> None:
    asset_root = tmp_path / "models" / "silero" / "v"
    asset_root.mkdir(parents=True)
    (asset_root / "silero_vad.onnx").write_bytes(b"the-audited-bytes")
    manifest = build_file_manifest(asset_root)
    asset_sha256 = manifest_asset_sha256(manifest)
    _write_registry(
        tmp_path,
        {
            "candidate_id": CANDIDATE_ID,
            "capability": "vad",
            "local_path": "models/silero/v/",
            "asset_sha256": asset_sha256,
            "file_manifest": manifest,
        },
    )
    # Tamper after pinning: the on-disk bytes no longer match the manifest.
    (asset_root / "silero_vad.onnx").write_bytes(b"tampered-different-bytes")
    with pytest.raises(VadEngineError) as excinfo:
        load_silero_asset(tmp_path)
    assert excinfo.value.reason == "vad_asset_mismatch"


def test_absent_candidate_is_a_typed_failure(tmp_path: Path) -> None:
    _write_registry(tmp_path, {"candidate_id": "other", "capability": "vad"})
    with pytest.raises(VadEngineError) as excinfo:
        resolve_silero_candidate(tmp_path)
    assert excinfo.value.reason == "vad_candidate_absent"
