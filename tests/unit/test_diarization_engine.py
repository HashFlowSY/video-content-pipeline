"""Pure, model-free tests for the real diarization engine (Phase 11 ticket 07).

Segment shaping, the shared ADR 0030 / 0031 gate, calibration parsing, and the
typed asset failures never touch sherpa-onnx: they are exercised here with plain
data. Real inference lives in the offline integration test.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from video_content_pipeline.audio_analysis import (
    SpeakerTurnCandidate,
    VoiceActivityInterval,
    VoiceActivityState,
    partition_speaker_turns,
)
from video_content_pipeline.audio_derivation import DerivativeTimeMapping
from video_content_pipeline.diarization_engine import (
    DIARIZATION_SAMPLE_RATE,
    DiarizationEngineError,
    SherpaDiarizationCalibration,
    SherpaDiarizationResult,
    load_diarization_asset,
    load_diarization_calibration,
    read_wav_samples,
    resolve_diarization_candidate,
    speaker_turn_candidates_from_segments,
)
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval

SR = DIARIZATION_SAMPLE_RATE


def _mapping(seconds: int) -> DerivativeTimeMapping:
    sample_count = seconds * SR
    return DerivativeTimeMapping(
        HalfOpenInterval(ExactTime(0), ExactTime(sample_count, SR)), SR, sample_count
    )


def _speech(seconds: int) -> tuple[VoiceActivityInterval, ...]:
    return (
        VoiceActivityInterval(
            HalfOpenInterval(ExactTime(0), ExactTime(seconds)), VoiceActivityState.SPEECH_LIKELY
        ),
    )


# --- segment shaping ----------------------------------------------------------


def test_segments_map_to_source_time_with_anonymous_cluster_ids() -> None:
    mapping = _mapping(10)
    candidates = speaker_turn_candidates_from_segments([(0, 1.0, 2.0), (1, 3.0, 4.5)], mapping)

    assert [c.cluster_id for c in candidates] == ["speaker-0", "speaker-1"]
    assert candidates[0].interval == HalfOpenInterval(ExactTime(1), ExactTime(2))
    assert candidates[1].interval == HalfOpenInterval(ExactTime(3), ExactTime(9, 2))
    # A hard post-clustering assignment carries full confidence, not a soft score.
    assert all(c.confidence == 1.0 for c in candidates)


def test_overlapping_segments_stay_independent_candidates() -> None:
    # Concurrent speakers over [1, 2): the shaping never merges them.
    mapping = _mapping(5)
    candidates = speaker_turn_candidates_from_segments([(0, 0.0, 2.0), (1, 1.0, 3.0)], mapping)

    assert len(candidates) == 2
    assert candidates[0].cluster_id != candidates[1].cluster_id
    assert candidates[0].interval == HalfOpenInterval(ExactTime(0), ExactTime(2))
    assert candidates[1].interval == HalfOpenInterval(ExactTime(1), ExactTime(3))


def test_degenerate_and_out_of_range_segments_are_dropped_and_clamped() -> None:
    mapping = _mapping(5)
    candidates = speaker_turn_candidates_from_segments(
        [
            (0, 2.0, 2.0),  # zero length after snapping -> dropped
            (1, 4.0, 99.0),  # end past the derivative -> clamped to its end
        ],
        mapping,
    )

    assert len(candidates) == 1
    assert candidates[0].cluster_id == "speaker-1"
    assert candidates[0].interval == HalfOpenInterval(ExactTime(4), ExactTime(5))


# --- shared ADR 0030 gate: overlap preservation, conflict, confidence ---------


def test_gate_preserves_overlapping_turns_as_independent_speaker_turns() -> None:
    turns = (
        SpeakerTurnCandidate("speaker-1", HalfOpenInterval(ExactTime(0), ExactTime(2)), 1.0),
        SpeakerTurnCandidate("speaker-0", HalfOpenInterval(ExactTime(1), ExactTime(3)), 1.0),
    )
    partition = partition_speaker_turns(turns, "part-01", _speech(3), minimum_confidence=0.5)

    # Two published turns, distinct anonymous Part-local labels, overlap intact.
    assert partition.labels_by_cluster == {
        "speaker-0": "part-01:speaker-01",
        "speaker-1": "part-01:speaker-02",
    }
    published = sorted(partition.published, key=lambda turn: turn.speaker_label)
    assert [turn.speaker_label for turn in published] == [
        "part-01:speaker-01",
        "part-01:speaker-02",
    ]
    assert published[0].interval == HalfOpenInterval(ExactTime(1), ExactTime(3))
    assert published[1].interval == HalfOpenInterval(ExactTime(0), ExactTime(2))
    assert partition.conflicts == ()


def test_gate_routes_vad_conflicts_and_drops_low_confidence() -> None:
    intervals = (
        VoiceActivityInterval(
            HalfOpenInterval(ExactTime(0), ExactTime(3)), VoiceActivityState.SPEECH_LIKELY
        ),
        VoiceActivityInterval(
            HalfOpenInterval(ExactTime(3), ExactTime(6)), VoiceActivityState.NON_SPEECH
        ),
    )
    turns = (
        SpeakerTurnCandidate("a", HalfOpenInterval(ExactTime(0), ExactTime(2)), 0.9),  # published
        SpeakerTurnCandidate("b", HalfOpenInterval(ExactTime(4), ExactTime(5)), 0.9),  # conflict
        SpeakerTurnCandidate("c", HalfOpenInterval(ExactTime(0), ExactTime(1)), 0.2),  # dropped
    )
    partition = partition_speaker_turns(turns, "part-02", intervals, minimum_confidence=0.5)

    assert [turn.speaker_label for turn in partition.published] == ["part-02:speaker-01"]
    assert len(partition.conflicts) == 1
    assert partition.conflicts[0].candidate_speaker_label == "part-02:speaker-02"
    assert partition.conflicts[0].vad_states == ("non_speech",)


# --- calibration parsing / gate (ADR 0031) ------------------------------------


def _calibration_document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": 1,
        "calibration_version": "test-v1",
        "model_identity": {
            "segmentation_asset_sha256": "a" * 64,
            "embedding_asset_sha256": "b" * 64,
            "backend": "sherpa-onnx",
            "backend_version": "1.13.5",
            "precision": "fp32",
            "device_class": "test-cpu",
            "rules_fingerprint": "diarization-rules-v1",
        },
        "sample_rate": SR,
        "pipeline": {
            "num_clusters": -1,
            "cluster_threshold": 0.5,
            "min_duration_on": 0.3,
            "min_duration_off": 0.5,
            "num_threads": 1,
        },
        "thresholds": {"minimum_confidence": 0.5},
    }
    document.update(overrides)
    return document


def test_calibration_round_trips_through_json() -> None:
    calibration = SherpaDiarizationCalibration.from_json(_calibration_document())
    assert calibration.segmentation_asset_sha256 == "a" * 64
    assert calibration.embedding_asset_sha256 == "b" * 64
    assert calibration.minimum_confidence == 0.5
    reparsed = SherpaDiarizationCalibration.from_json(calibration.as_json())
    assert reparsed == calibration


@pytest.mark.parametrize(
    "document",
    [
        {"schema_version": 2},
        _calibration_document(thresholds={"minimum_confidence": 1.5}),
        _calibration_document(pipeline={"num_clusters": 0}),
    ],
)
def test_calibration_rejects_invalid_documents(document: dict[str, object]) -> None:
    with pytest.raises(DiarizationEngineError) as error:
        SherpaDiarizationCalibration.from_json(document)
    assert error.value.reason == "diarization_calibration_invalid"


def test_calibration_loader_rejects_mismatched_assets(tmp_path: Path) -> None:
    _write_calibration(tmp_path, _calibration_document())
    with pytest.raises(DiarizationEngineError) as error:
        load_diarization_calibration(
            tmp_path,
            expected_segmentation_sha256="a" * 64,
            expected_embedding_sha256="wrong",
        )
    assert error.value.reason == "diarization_calibration_model_mismatch"


def test_calibration_loader_rejects_wrong_sample_rate(tmp_path: Path) -> None:
    _write_calibration(tmp_path, _calibration_document(sample_rate=8000))
    with pytest.raises(DiarizationEngineError) as error:
        load_diarization_calibration(tmp_path)
    assert error.value.reason == "diarization_calibration_invalid"


def _write_calibration(project_root: Path, document: dict[str, object]) -> None:
    path = project_root / "config" / "audio-analysis" / "sherpa-diarization-calibration.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


# --- typed asset failures, never a network attempt ----------------------------


def _write_registry(project_root: Path, candidates: list[dict[str, object]]) -> None:
    path = project_root / "models" / "registry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"candidates": candidates}), encoding="utf-8")


def test_absent_candidate_is_a_typed_failure(tmp_path: Path) -> None:
    _write_registry(tmp_path, [])
    with pytest.raises(DiarizationEngineError) as error:
        resolve_diarization_candidate(tmp_path, "sherpa-onnx-pyannote-segmentation-3-0")
    assert error.value.reason == "diarization_candidate_absent"


def test_incomplete_registry_entry_is_a_typed_failure(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        [{"candidate_id": "seg", "capability": "diarization", "file_manifest": []}],
    )
    with pytest.raises(DiarizationEngineError) as error:
        load_diarization_asset(tmp_path, "seg", "model.onnx")
    assert error.value.reason == "diarization_asset_unavailable"


def test_absent_asset_tree_is_a_typed_failure(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        [
            {
                "candidate_id": "seg",
                "capability": "diarization",
                "local_path": "models/absent/",
                "file_manifest": [{"path": "model.onnx", "size": 1, "sha256": "0" * 64}],
                "asset_sha256": "0" * 64,
            }
        ],
    )
    with pytest.raises(DiarizationEngineError) as error:
        load_diarization_asset(tmp_path, "seg", "model.onnx")
    assert error.value.reason == "diarization_asset_unavailable"


def test_tampered_asset_is_a_typed_mismatch_never_a_network_attempt(tmp_path: Path) -> None:
    asset_dir = tmp_path / "models" / "seg"
    asset_dir.mkdir(parents=True)
    model = asset_dir / "model.onnx"
    model.write_bytes(b"real-bytes")
    manifest = [
        {
            "path": "model.onnx",
            "size": model.stat().st_size,
            "sha256": sha256(b"real-bytes").hexdigest(),
        }
    ]
    _write_registry(
        tmp_path,
        [
            {
                "candidate_id": "seg",
                "capability": "diarization",
                "local_path": "models/seg/",
                "file_manifest": manifest,
                # A deliberately wrong asset digest: the on-disk files are intact
                # but the pinned manifest digest does not match -> mismatch.
                "asset_sha256": "f" * 64,
            }
        ],
    )
    with pytest.raises(DiarizationEngineError) as error:
        load_diarization_asset(tmp_path, "seg", "model.onnx")
    assert error.value.reason == "diarization_asset_mismatch"


# --- audio + uncalibrated projection ------------------------------------------


def test_read_wav_rejects_non_16k_mono(tmp_path: Path) -> None:
    import wave

    path = tmp_path / "stereo.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(b"\x00\x00\x00\x00")
    with pytest.raises(DiarizationEngineError) as error:
        read_wav_samples(path)
    assert error.value.reason == "diarization_audio_invalid"


def test_uncalibrated_result_publishes_no_formal_turns() -> None:
    result = SherpaDiarizationResult(
        source_id="s",
        stream_index=0,
        part_label="part-01",
        raw_turns=(
            SpeakerTurnCandidate("speaker-0", HalfOpenInterval(ExactTime(0), ExactTime(1)), 1.0),
        ),
        partition=None,
        segmentation_asset_sha256="a" * 64,
        embedding_asset_sha256="b" * 64,
        calibrated=False,
    )
    document = result.as_json()
    assert document["speaker_turns"] == []
    assert document["diarization_vad_conflicts"] == []
