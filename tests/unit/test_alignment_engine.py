"""Pure, model-free tests for the real forced-alignment engine (Phase 11 ticket 08).

Cue-to-chunk assignment, window derivation, the item-to-proposal projection,
calibration parsing, and the typed asset failures never touch mlx-audio: they are
exercised here with plain data. The Model runtime subprocess seam is exercised
against a tiny stub executable -- so the whole orchestration
(:func:`analyze_derivative_alignment`) is proven end to end without loading a
model -- and the low-confidence non-override rule is proven by flowing the real
adapter's proposal shape through the unchanged Adopted alignment timing view. Real
inference lives in the offline integration test.
"""

from __future__ import annotations

import json
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from video_content_pipeline.alignment_engine import (
    ALIGNER_SAMPLE_RATE,
    DECIDED_ALIGNMENT_CONFIDENCE,
    AlignmentEngineError,
    Qwen3AlignerCalibration,
    align_chunk,
    analyze_derivative_alignment,
    assign_cues_to_chunks,
    cue_window_samples,
    load_aligner_asset,
    load_alignment_calibration,
    project_alignment_part,
    proposal_from_alignment_items,
    resolve_aligner_candidate,
)
from video_content_pipeline.audio_analysis import (
    AlignmentCue,
    VoiceActivityInterval,
    VoiceActivityState,
    derive_adopted_alignment_timing_view,
)
from video_content_pipeline.audio_derivation import DerivativeTimeMapping
from video_content_pipeline.model_acquisition import build_file_manifest, manifest_asset_sha256
from video_content_pipeline.model_runtime import ModelRuntimeError
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
from video_content_pipeline.vad_chunking import SpeechChunk

SR = ALIGNER_SAMPLE_RATE


def _mapping(seconds: int) -> DerivativeTimeMapping:
    sample_count = seconds * SR
    return DerivativeTimeMapping(
        HalfOpenInterval(ExactTime(0), ExactTime(sample_count, SR)), SR, sample_count
    )


def _cue(ordinal: int, start: int, end: int, text: str = "hello") -> AlignmentCue:
    return AlignmentCue(ordinal, text, HalfOpenInterval(ExactTime(start), ExactTime(end)))


def _chunk(index: int, start_sample: int, end_sample: int) -> SpeechChunk:
    mapping = _mapping(end_sample // SR + 1)
    return SpeechChunk(
        chunk_index=index,
        start_sample=start_sample,
        end_sample=end_sample,
        source_interval=mapping.source_interval_for_samples(start_sample, end_sample),
        speech_runs=(),
    )


# --- cue-to-chunk assignment --------------------------------------------------


def test_cues_assign_to_the_first_containing_chunk() -> None:
    chunks = (_chunk(0, 0, 3 * SR), _chunk(1, 4 * SR, 8 * SR))
    cues = (_cue(1, 1, 2), _cue(2, 5, 6), _cue(3, 10, 11))

    by_chunk = assign_cues_to_chunks(cues, chunks)

    assert [cue.source_ordinal for cue in by_chunk[0]] == [1]
    assert [cue.source_ordinal for cue in by_chunk[1]] == [2]
    # Cue 3 lies past every chunk (subtitle over silence) -> absent.
    assert 3 not in {cue.source_ordinal for cues in by_chunk.values() for cue in cues}


def test_straddling_cue_is_left_unassigned() -> None:
    # A cue whose span crosses the silence cut between two chunks is contained by
    # neither, so it is absent from the assignment and keeps its original time.
    chunks = (_chunk(0, 0, 3 * SR), _chunk(1, 4 * SR, 8 * SR))
    cues = (_cue(9, 2, 5),)

    by_chunk = assign_cues_to_chunks(cues, chunks)

    assert by_chunk == {}


def test_cues_without_chunks_are_all_unassigned() -> None:
    cues = (_cue(1, 1, 2), _cue(2, 3, 4))
    assert assign_cues_to_chunks(cues, ()) == {}


# --- window derivation --------------------------------------------------------


def test_window_pads_and_clamps_to_the_chunk() -> None:
    mapping = _mapping(10)
    chunk = _chunk(0, 2 * SR, 8 * SR)
    cue = _cue(1, 4, 5)

    window = cue_window_samples(cue, chunk, mapping, pad_samples=SR // 2)

    # The cue maps to [4s, 5s) = [64000, 80000); padded by 0.5 s each side and
    # clamped to the chunk bounds [32000, 128000).
    assert window == (4 * SR - SR // 2, 5 * SR + SR // 2)


def test_window_clamps_to_chunk_edges() -> None:
    mapping = _mapping(10)
    chunk = _chunk(0, 3 * SR, 6 * SR)
    cue = _cue(1, 2, 7)  # extends past both chunk edges

    window = cue_window_samples(cue, chunk, mapping, pad_samples=SR)

    assert window == (3 * SR, 6 * SR)


def test_empty_window_is_none() -> None:
    mapping = _mapping(10)
    # A degenerate chunk whose sample span is empty after clamping.
    chunk = _chunk(0, 5 * SR, 5 * SR + 1)
    cue = _cue(1, 0, 1)
    assert cue_window_samples(cue, chunk, mapping, pad_samples=0) is None


# --- item-to-proposal projection ----------------------------------------------


def test_proposal_maps_window_local_items_onto_the_source_timeline() -> None:
    mapping = _mapping(20)
    cue = _cue(7, 4, 5, text="the cue text")
    window_start = 4 * SR  # window begins at 4 s

    proposal = proposal_from_alignment_items(
        cue,
        window_start,
        [{"text": "the", "start": 0.5, "end": 1.0}, {"text": "cue", "start": 1.0, "end": 1.5}],
        mapping,
        DECIDED_ALIGNMENT_CONFIDENCE,
    )

    assert proposal is not None
    # earliest start 0.5 s and latest end 1.5 s, offset by the 4 s window start.
    assert proposal.interval == HalfOpenInterval(
        ExactTime(4 * SR + SR // 2, SR), ExactTime(4 * SR + 3 * SR // 2, SR)
    )
    # The proposal carries the cue's own text and the decided confidence.
    assert proposal.text == "the cue text"
    assert proposal.source_ordinal == 7
    assert proposal.confidence == DECIDED_ALIGNMENT_CONFIDENCE


def test_proposal_skips_invalid_items_and_returns_none_when_empty() -> None:
    mapping = _mapping(10)
    cue = _cue(1, 1, 2)

    # All items are invalid (negative start, non-positive span, wrong types).
    proposal = proposal_from_alignment_items(
        cue,
        SR,
        [{"start": -1.0, "end": 1.0}, {"start": 1.0, "end": 1.0}, {"start": "x", "end": 2.0}],
        mapping,
        1.0,
    )
    assert proposal is None


def test_proposal_returns_none_when_span_snaps_to_zero() -> None:
    mapping = _mapping(10)
    cue = _cue(1, 1, 2)
    # A sub-sample span at 16 kHz rounds to the same boundary -> degenerate.
    proposal = proposal_from_alignment_items(
        cue, SR, [{"start": 0.0, "end": 0.00001}], mapping, 1.0
    )
    assert proposal is None


# --- part projection: one proposal per cue ------------------------------------


def test_project_alignment_part_covers_every_cue_placed_or_not() -> None:
    cues = (_cue(1, 1, 2), _cue(2, 3, 4))
    placed = {
        1: proposal_from_alignment_items(
            cues[0], SR, [{"start": 0.0, "end": 0.5}], _mapping(10), 1.0
        )
    }
    assert placed[1] is not None

    part = project_alignment_part("en", cues, {1: placed[1]})

    assert part.language == "en"
    assert [proposal.source_ordinal for proposal in part.proposals] == [1, 2]
    # Cue 2 was not placed -> original-time, zero-confidence proposal (non-override).
    unplaced = part.proposals[1]
    assert unplaced.confidence == 0.0
    assert unplaced.interval == cues[1].interval
    assert unplaced.text == "hello"


# --- low-confidence non-override through the unchanged adoption gate -----------


def test_low_confidence_real_adapter_proposal_never_overrides_original_time() -> None:
    cues = (_cue(1, 1, 3, text="a"), _cue(2, 4, 6, text="b"))
    mapping = _mapping(10)
    # Real-adapter output shape: cue 1 placed at full confidence, cue 2 unplaced
    # (zero confidence, original time) -- exactly what the engine emits.
    placed = proposal_from_alignment_items(
        cues[0], SR, [{"start": 0.0, "end": 2.0}], mapping, DECIDED_ALIGNMENT_CONFIDENCE
    )
    assert placed is not None
    part = project_alignment_part("en", cues, {1: placed})

    view = derive_adopted_alignment_timing_view(
        source_id="s",
        language="en",
        source_cues=cues,
        proposals=part.proposals,
        usable_audio_intervals=(HalfOpenInterval(ExactTime(0), ExactTime(10)),),
        voice_activity_intervals=(),
        minimum_confidence=0.5,
        duration_rules={"en": (ExactTime(1), ExactTime(6))},
    )

    adopted = {candidate.source_ordinal: candidate for candidate in view.candidates}
    assert adopted[1].adopted is True
    # The low-confidence (unplaced) cue keeps its original interval.
    assert adopted[2].adopted is False
    assert adopted[2].reason == "alignment_low_confidence"
    assert adopted[2].interval == cues[1].interval


# --- calibration parsing / gate (ADR 0027) ------------------------------------


def _calibration_document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": 1,
        "calibration_version": "test-v1",
        "model_identity": {
            "model_asset_sha256": "a" * 64,
            "backend": "mlx-audio",
            "backend_version": "0.4.8",
            "precision": "8bit",
            "device_class": "test-cpu",
            "rules_fingerprint": "alignment-rules-v1",
        },
        "sample_rate": SR,
        "window": {"window_pad_samples": 8000},
        "thresholds": {
            "minimum_confidence": 0.5,
            "duration_rules": {
                "en": {
                    "minimum_duration": {"numerator": 3, "denominator": 10},
                    "maximum_duration": {"numerator": 10, "denominator": 1},
                }
            },
        },
    }
    document.update(overrides)
    return document


def test_calibration_round_trips_through_json() -> None:
    calibration = Qwen3AlignerCalibration.from_json(_calibration_document())
    assert calibration.model_asset_sha256 == "a" * 64
    assert calibration.window_pad_samples == 8000
    assert calibration.duration_rules["en"] == (ExactTime(3, 10), ExactTime(10))
    reparsed = Qwen3AlignerCalibration.from_json(calibration.as_json())
    assert reparsed == calibration


@pytest.mark.parametrize(
    "document",
    [
        {"schema_version": 2},
        _calibration_document(thresholds={"minimum_confidence": 1.5, "duration_rules": {}}),
        _calibration_document(window={"window_pad_samples": -1}),
        _calibration_document(
            thresholds={
                "minimum_confidence": 0.5,
                "duration_rules": {
                    "en": {
                        "minimum_duration": {"numerator": 10, "denominator": 1},
                        "maximum_duration": {"numerator": 1, "denominator": 1},
                    }
                },
            }
        ),
    ],
)
def test_calibration_rejects_invalid_documents(document: dict[str, object]) -> None:
    with pytest.raises(AlignmentEngineError) as error:
        Qwen3AlignerCalibration.from_json(document)
    assert error.value.reason == "alignment_calibration_invalid"


def test_calibration_loader_rejects_mismatched_asset(tmp_path: Path) -> None:
    _write_calibration(tmp_path, _calibration_document())
    with pytest.raises(AlignmentEngineError) as error:
        load_alignment_calibration(tmp_path, expected_asset_sha256="different")
    assert error.value.reason == "alignment_calibration_model_mismatch"


def test_calibration_loader_rejects_wrong_sample_rate(tmp_path: Path) -> None:
    _write_calibration(tmp_path, _calibration_document(sample_rate=8000))
    with pytest.raises(AlignmentEngineError) as error:
        load_alignment_calibration(tmp_path)
    assert error.value.reason == "alignment_calibration_invalid"


def _write_calibration(project_root: Path, document: dict[str, object]) -> None:
    path = project_root / "config" / "audio-analysis" / "qwen3-aligner-calibration.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


# --- typed asset failures, never a network attempt ----------------------------


def _write_registry(project_root: Path, candidates: list[dict[str, object]]) -> None:
    path = project_root / "models" / "registry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"candidates": candidates}), encoding="utf-8")


def test_absent_candidate_is_a_typed_failure(tmp_path: Path) -> None:
    _write_registry(tmp_path, [])
    with pytest.raises(AlignmentEngineError) as error:
        resolve_aligner_candidate(tmp_path)
    assert error.value.reason == "alignment_candidate_absent"


def test_incomplete_registry_entry_is_a_typed_failure(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        [
            {
                "candidate_id": "qwen3-forced-aligner-0-6b",
                "capability": "forced_alignment",
                "file_manifest": [],
            }
        ],
    )
    with pytest.raises(AlignmentEngineError) as error:
        load_aligner_asset(tmp_path)
    assert error.value.reason == "alignment_asset_unavailable"


def test_absent_asset_tree_is_a_typed_failure(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        [
            {
                "candidate_id": "qwen3-forced-aligner-0-6b",
                "capability": "forced_alignment",
                "local_path": "models/absent/",
                "file_manifest": [{"path": "model.safetensors", "size": 1, "sha256": "0" * 64}],
                "asset_sha256": "0" * 64,
            }
        ],
    )
    with pytest.raises(AlignmentEngineError) as error:
        load_aligner_asset(tmp_path)
    assert error.value.reason == "alignment_asset_unavailable"


def test_tampered_asset_is_a_typed_mismatch_never_a_network_attempt(tmp_path: Path) -> None:
    asset_dir = tmp_path / "models" / "aligner"
    asset_dir.mkdir(parents=True)
    (asset_dir / "model.safetensors").write_bytes(b"real-bytes")
    manifest = [
        {
            "path": "model.safetensors",
            "size": (asset_dir / "model.safetensors").stat().st_size,
            "sha256": sha256(b"real-bytes").hexdigest(),
        }
    ]
    _write_registry(
        tmp_path,
        [
            {
                "candidate_id": "qwen3-forced-aligner-0-6b",
                "capability": "forced_alignment",
                "local_path": "models/aligner/",
                "file_manifest": manifest,
                # A deliberately wrong asset digest: files intact, pin does not match.
                "asset_sha256": "f" * 64,
            }
        ],
    )
    with pytest.raises(AlignmentEngineError) as error:
        load_aligner_asset(tmp_path)
    assert error.value.reason == "alignment_asset_mismatch"


# --- Model runtime subprocess seam (stub executable, no model) ----------------

# Echoes each requested cue back as one aligned item spanning its whole window,
# so the projection can be asserted deterministically without a model.
_ECHO_ALIGNER_STUB = r"""
import json, sys
from video_content_pipeline.model_runtime import execute_child

def handler(request):
    cues = request.task["cues"]
    out = []
    for cue in cues:
        span = (cue["end_sample"] - cue["start_sample"]) / 16000.0
        out.append({"source_ordinal": cue["source_ordinal"], "items": [
            {"text": cue["text"], "start": 0.0, "end": span}
        ]})
    return {"cues": out}

sys.exit(execute_child(handler))
"""

_MALFORMED_STUB = r"""
import json, sys
sys.stdin.read()
json.dump({"result": {"cues": "not-a-list"}, "peak_memory_bytes": 1}, sys.stdout)
"""

_CRASH_STUB = r"""
import os, signal, sys
sys.stdin.read()
os.kill(os.getpid(), signal.SIGKILL)
"""


def _write_stub(tmp_path: Path, name: str, body: str) -> list[str]:
    stub = tmp_path / name
    stub.write_text(body, encoding="utf-8")
    return [sys.executable, str(stub)]


def test_align_chunk_round_trips_the_projection_contract(tmp_path: Path) -> None:
    command = _write_stub(tmp_path, "echo.py", _ECHO_ALIGNER_STUB)
    cue = _cue(1, 1, 2)

    items_by_ordinal, peak = align_chunk(
        Path("/models/aligner"),
        tmp_path / "audio-16k.wav",
        "en",
        [(cue, (SR, 2 * SR))],
        command=command,
        timeout_seconds=30,
    )

    assert set(items_by_ordinal) == {1}
    assert items_by_ordinal[1][0]["text"] == "hello"
    assert peak > 0


def test_align_chunk_rejects_malformed_child_output(tmp_path: Path) -> None:
    command = _write_stub(tmp_path, "malformed.py", _MALFORMED_STUB)
    with pytest.raises(AlignmentEngineError) as error:
        align_chunk(
            Path("/models/aligner"),
            tmp_path / "audio.wav",
            "en",
            [(_cue(1, 1, 2), (SR, 2 * SR))],
            command=command,
            timeout_seconds=30,
        )
    assert error.value.reason == "alignment_output_invalid"


def test_align_chunk_child_crash_surfaces_as_a_typed_model_runtime_error(tmp_path: Path) -> None:
    command = _write_stub(tmp_path, "crash.py", _CRASH_STUB)
    with pytest.raises(ModelRuntimeError) as error:
        align_chunk(
            Path("/models/aligner"),
            tmp_path / "audio.wav",
            "en",
            [(_cue(1, 1, 2), (SR, 2 * SR))],
            command=command,
            timeout_seconds=30,
        )
    assert error.value.reason == "engine_child_crashed"


# --- end-to-end orchestration over a stub executable --------------------------


def _install_valid_asset(project_root: Path) -> str:
    """Write a minimal valid aligner asset + registry entry; return its sha256."""

    asset_dir = project_root / "models" / "aligner"
    asset_dir.mkdir(parents=True)
    (asset_dir / "config.json").write_text('{"model_type": "stub"}', encoding="utf-8")
    (asset_dir / "model.safetensors").write_bytes(b"weights")
    manifest = build_file_manifest(asset_dir)
    asset_sha256 = manifest_asset_sha256(manifest)
    _write_registry(
        project_root,
        [
            {
                "candidate_id": "qwen3-forced-aligner-0-6b",
                "capability": "forced_alignment",
                "local_path": "models/aligner/",
                "file_manifest": manifest,
                "asset_sha256": asset_sha256,
            }
        ],
    )
    return asset_sha256


def test_analyze_uncalibrated_produces_proposals_but_no_adopted_view(tmp_path: Path) -> None:
    asset_sha256 = _install_valid_asset(tmp_path)
    command = _write_stub(tmp_path, "echo.py", _ECHO_ALIGNER_STUB)
    mapping = _mapping(10)
    cues = (_cue(1, 1, 2), _cue(2, 8, 9))
    chunks = (_chunk(0, 0, 5 * SR),)  # only cue 1 overlaps

    result = analyze_derivative_alignment(
        tmp_path,
        tmp_path / "audio-16k.wav",
        mapping,
        source_id="part",
        stream_index=0,
        language="en",
        source_cues=cues,
        chunks=chunks,
        command=command,
        timeout_seconds=30,
    )

    assert result.calibrated is False
    assert result.adopted_view is None
    assert result.model_asset_sha256 == asset_sha256
    assert result.peak_memory_bytes > 0
    assert len(result.chunk_peak_memory_bytes) == 1
    # Cue 1 placed (in the chunk), cue 2 unplaced (in silence) -> original time.
    proposals = {p.source_ordinal: p for p in result.projected.proposals}
    assert proposals[1].confidence == DECIDED_ALIGNMENT_CONFIDENCE
    assert proposals[2].confidence == 0.0
    assert proposals[2].interval == cues[1].interval


def test_analyze_calibrated_drives_the_adopted_view(tmp_path: Path) -> None:
    asset_sha256 = _install_valid_asset(tmp_path)
    _write_calibration(
        tmp_path,
        _calibration_document(
            model_identity={
                "model_asset_sha256": asset_sha256,
                "backend": "mlx-audio",
                "backend_version": "0.4.8",
                "precision": "8bit",
                "device_class": "test-cpu",
                "rules_fingerprint": "alignment-rules-v1",
            },
            thresholds={
                "minimum_confidence": 0.5,
                "duration_rules": {
                    "en": {
                        "minimum_duration": {"numerator": 1, "denominator": 2},
                        "maximum_duration": {"numerator": 6, "denominator": 1},
                    }
                },
            },
        ),
    )
    command = _write_stub(tmp_path, "echo.py", _ECHO_ALIGNER_STUB)
    mapping = _mapping(10)
    cues = (_cue(1, 1, 3),)
    chunks = (_chunk(0, 0, 5 * SR),)

    result = analyze_derivative_alignment(
        tmp_path,
        tmp_path / "audio-16k.wav",
        mapping,
        source_id="part",
        stream_index=0,
        language="en",
        source_cues=cues,
        chunks=chunks,
        voice_activity_intervals=(
            VoiceActivityInterval(
                HalfOpenInterval(ExactTime(0), ExactTime(5)), VoiceActivityState.SPEECH_LIKELY
            ),
        ),
        command=command,
        timeout_seconds=30,
    )

    assert result.calibrated is True
    assert result.adopted_view is not None
    assert result.adopted_view.state == "adopted"
    # The stub spans the whole [0, 5s) chunk window, an implausibly long cue under
    # the 6 s max, so it is adopted; the projection flowed through the real gate.
    (candidate,) = result.adopted_view.candidates
    assert candidate.source_ordinal == 1


def test_analyze_calibrated_without_a_language_rule_degrades_to_candidates(tmp_path: Path) -> None:
    # The profile carries a duration rule only for 'en'; a calibrated 'zh' run has
    # no rule, so no adopted view is produced (ADR 0027) -- a graceful degrade to
    # retained candidate proposals rather than a hard duration-rule-missing error.
    asset_sha256 = _install_valid_asset(tmp_path)
    _write_calibration(
        tmp_path,
        _calibration_document(
            model_identity={
                "model_asset_sha256": asset_sha256,
                "backend": "mlx-audio",
                "backend_version": "0.4.8",
                "precision": "8bit",
                "device_class": "test-cpu",
                "rules_fingerprint": "alignment-rules-v1",
            }
        ),
    )
    command = _write_stub(tmp_path, "echo.py", _ECHO_ALIGNER_STUB)
    mapping = _mapping(10)
    cues = (_cue(1, 1, 3, text="你好"),)
    chunks = (_chunk(0, 0, 5 * SR),)

    result = analyze_derivative_alignment(
        tmp_path,
        tmp_path / "audio-16k.wav",
        mapping,
        source_id="part",
        stream_index=0,
        language="zh",
        source_cues=cues,
        chunks=chunks,
        command=command,
        timeout_seconds=30,
    )

    assert result.calibrated is True
    assert result.adopted_view is None
    # The candidate proposal is still produced (retained candidate, placed).
    (proposal,) = result.projected.proposals
    assert proposal.confidence == DECIDED_ALIGNMENT_CONFIDENCE
