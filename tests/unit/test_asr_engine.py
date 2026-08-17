"""Pure, model-free tests for the real ASR engines (Phase 11 ticket 09).

Chunk-to-cue assembly, the VAD-trimmed review-window derivation, and the typed
asset failures never touch mlx: they are exercised here with plain data. The Model
runtime subprocess seam is exercised against tiny stub executables -- so the whole
orchestration (:func:`transcribe_derivative` / :func:`review_suspicious_intervals`)
is proven end to end without loading a model, the hub-offline guards are proven to
reach the child, and the primary adapter's assembled cues are flowed through the
unchanged canonical-timeline gate to prove they round-trip the projection contract.
The Independent-model review requirement is re-proven with the real registry
identities against the unchanged ``classify_review_attempt``. Real inference lives
in the offline integration test.
"""

from __future__ import annotations

import json
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from video_content_pipeline.asr_engine import (
    ASR_SAMPLE_RATE,
    AsrEngineError,
    assemble_transcript_cues,
    load_primary_asset,
    load_review_asset,
    resolve_primary_candidate,
    resolve_review_candidate,
    review_suspicious_intervals,
    review_windows,
    transcribe_chunk,
    transcribe_derivative,
    trim_interval_to_speech,
)
from video_content_pipeline.audio_derivation import DerivativeTimeMapping
from video_content_pipeline.coverage import StreamCoverage
from video_content_pipeline.model_acquisition import build_file_manifest, manifest_asset_sha256
from video_content_pipeline.model_runtime import ModelRuntimeError
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
from video_content_pipeline.timeline import CollectionTimeline, TimelinePart
from video_content_pipeline.transcription_arbitration import classify_review_attempt
from video_content_pipeline.transcription_gates import (
    DurationToTextBounds,
    TimingGateRuleset,
    gate_projected_cues,
)
from video_content_pipeline.vad_chunking import SpeechChunk

SR = ASR_SAMPLE_RATE
REPO_ROOT = Path(__file__).resolve().parents[2]


def _mapping(seconds: int) -> DerivativeTimeMapping:
    sample_count = seconds * SR
    return DerivativeTimeMapping(
        HalfOpenInterval(ExactTime(0), ExactTime(sample_count, SR)), SR, sample_count
    )


def _interval(start: int, end: int) -> HalfOpenInterval:
    return HalfOpenInterval(ExactTime(start), ExactTime(end))


def _chunk(
    index: int, start_sample: int, end_sample: int, mapping: DerivativeTimeMapping
) -> SpeechChunk:
    return SpeechChunk(
        chunk_index=index,
        start_sample=start_sample,
        end_sample=end_sample,
        source_interval=mapping.source_interval_for_samples(start_sample, end_sample),
        speech_runs=(mapping.source_interval_for_samples(start_sample, end_sample),),
    )


# --- chunk-to-cue assembly ----------------------------------------------------


def test_assemble_produces_one_monotonic_cue_per_nonempty_chunk() -> None:
    mapping = _mapping(20)
    chunks = (
        _chunk(0, 0, 3 * SR, mapping),
        _chunk(1, 5 * SR, 9 * SR, mapping),
    )
    cues = assemble_transcript_cues([(chunks[0], "hello world"), (chunks[1], "second chunk")])

    assert [cue.ordinal for cue in cues] == [0, 1]
    assert [cue.text for cue in cues] == ["hello world", "second chunk"]
    assert cues[0].interval == chunks[0].source_interval
    assert cues[1].interval == chunks[1].source_interval
    # Strictly advancing, non-overlapping intervals on the source timeline.
    assert cues[0].interval.end <= cues[1].interval.start
    # No per-token or language-span evidence from the Qwen3-ASR path.
    assert cues[0].tokens == () and cues[0].language_spans == ()


def test_assemble_skips_chunks_with_no_visible_text() -> None:
    mapping = _mapping(20)
    chunks = (
        _chunk(0, 0, 3 * SR, mapping),
        _chunk(1, 5 * SR, 9 * SR, mapping),
        _chunk(2, 12 * SR, 15 * SR, mapping),
    )
    cues = assemble_transcript_cues([(chunks[0], "   "), (chunks[1], "real text"), (chunks[2], "")])

    # Only the middle chunk had speech; the empty ones drop out and the ordinal
    # sequence stays gap-free.
    assert [cue.ordinal for cue in cues] == [0]
    assert cues[0].text == "real text"
    assert cues[0].interval == chunks[1].source_interval


def test_assembled_cues_round_trip_the_unchanged_canonical_timeline_gate() -> None:
    mapping = _mapping(20)
    chunks = (_chunk(0, 0, 3 * SR, mapping), _chunk(1, 5 * SR, 9 * SR, mapping))
    cues = assemble_transcript_cues([(chunks[0], "hello there"), (chunks[1], "goodbye now")])

    result = gate_projected_cues(
        part_id="part-1",
        cues=cues,
        part_coverage=StreamCoverage(
            coverage=HalfOpenInterval(ExactTime(0), ExactTime(20)), gaps=(), diagnostics=()
        ),
        timeline=CollectionTimeline(
            parts=(
                TimelinePart(
                    part_id="part-1", coverage=HalfOpenInterval(ExactTime(0), ExactTime(20))
                ),
            )
        ),
        rules=TimingGateRuleset(
            version="test-gate-v1",
            calibration_required=True,
            duration_to_text=DurationToTextBounds(
                minimum_seconds_per_character=ExactTime(1, 100),
                maximum_seconds_per_character=ExactTime(60),
            ),
        ),
    )

    # The real adapter's cues are contract-valid: the unchanged gate admits both.
    assert [cue.ordinal for cue in result.admitted] == [0, 1]
    assert result.rejected == ()


# --- review: VAD-trimmed window derivation ------------------------------------


def test_trim_keeps_only_the_speech_within_an_interval() -> None:
    mapping = _mapping(10)
    # Speech at [1s,3s) and [6s,8s); the interval [2s,7s) overlaps the tail of the
    # first run and the head of the second, dropping the [3s,6s) silence between.
    windows = trim_interval_to_speech(_interval(2, 7), [_interval(1, 3), _interval(6, 8)], mapping)
    assert windows == ((2 * SR, 3 * SR), (6 * SR, 7 * SR))


def test_trim_is_empty_when_the_interval_is_all_silence() -> None:
    mapping = _mapping(10)
    # The interval [4s,5s) falls in the silence gap between the two speech runs.
    windows = trim_interval_to_speech(_interval(4, 5), [_interval(1, 3), _interval(6, 8)], mapping)
    assert windows == ()


def test_trim_clamps_speech_overlap_to_the_derivative() -> None:
    mapping = _mapping(10)
    windows = trim_interval_to_speech(_interval(0, 20), [_interval(0, 20)], mapping)
    assert windows == ((0, 10 * SR),)


# --- Independent-model review requirement, real identities --------------------


def _registry_asset_sha256(candidate_id: str) -> str:
    registry = json.loads((REPO_ROOT / "models" / "registry.json").read_text(encoding="utf-8"))
    (candidate,) = [c for c in registry["candidates"] if c.get("candidate_id") == candidate_id]
    return str(candidate["asset_sha256"])


def test_real_review_identity_differs_from_primary_is_independent() -> None:
    primary = _registry_asset_sha256("qwen3-asr-1-7b")
    review = _registry_asset_sha256("whisper-large-v3")
    assert primary != review

    classification = classify_review_attempt(
        primary_model_identity=primary, review_model_identity=review
    )
    assert classification.independent is True
    assert classification.kind == "independent_review"


def test_same_model_retry_with_real_identity_is_recovery_never_review() -> None:
    primary = _registry_asset_sha256("qwen3-asr-1-7b")
    classification = classify_review_attempt(
        primary_model_identity=primary, review_model_identity=primary
    )
    assert classification.independent is False
    assert classification.kind == "recovery"


# --- typed asset failures, never a network attempt ----------------------------


def _write_registry(project_root: Path, candidates: list[dict[str, object]]) -> None:
    path = project_root / "models" / "registry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"candidates": candidates}), encoding="utf-8")


_ASSET_CASES = [
    ("qwen3-asr-1-7b", "asr_primary", resolve_primary_candidate, load_primary_asset),
    ("whisper-large-v3", "asr_review", resolve_review_candidate, load_review_asset),
]


@pytest.mark.parametrize(("candidate_id", "capability", "resolve", "load"), _ASSET_CASES)
def test_absent_candidate_is_a_typed_failure(
    tmp_path: Path, candidate_id: str, capability: str, resolve, load
) -> None:
    _write_registry(tmp_path, [])
    with pytest.raises(AsrEngineError) as error:
        resolve(tmp_path)
    assert error.value.reason == "asr_candidate_absent"


@pytest.mark.parametrize(("candidate_id", "capability", "resolve", "load"), _ASSET_CASES)
def test_incomplete_registry_entry_is_a_typed_failure(
    tmp_path: Path, candidate_id: str, capability: str, resolve, load
) -> None:
    _write_registry(
        tmp_path,
        [{"candidate_id": candidate_id, "capability": capability, "file_manifest": []}],
    )
    with pytest.raises(AsrEngineError) as error:
        load(tmp_path)
    assert error.value.reason == "asr_asset_unavailable"


@pytest.mark.parametrize(("candidate_id", "capability", "resolve", "load"), _ASSET_CASES)
def test_absent_asset_tree_is_a_typed_failure(
    tmp_path: Path, candidate_id: str, capability: str, resolve, load
) -> None:
    _write_registry(
        tmp_path,
        [
            {
                "candidate_id": candidate_id,
                "capability": capability,
                "local_path": "models/absent/",
                "file_manifest": [{"path": "model.safetensors", "size": 1, "sha256": "0" * 64}],
                "asset_sha256": "0" * 64,
            }
        ],
    )
    with pytest.raises(AsrEngineError) as error:
        load(tmp_path)
    assert error.value.reason == "asr_asset_unavailable"


@pytest.mark.parametrize(("candidate_id", "capability", "resolve", "load"), _ASSET_CASES)
def test_tampered_asset_is_a_typed_mismatch_never_a_network_attempt(
    tmp_path: Path, candidate_id: str, capability: str, resolve, load
) -> None:
    asset_dir = tmp_path / "models" / "asr"
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
                "candidate_id": candidate_id,
                "capability": capability,
                "local_path": "models/asr/",
                "file_manifest": manifest,
                # Files intact, pinned digest deliberately wrong.
                "asset_sha256": "f" * 64,
            }
        ],
    )
    with pytest.raises(AsrEngineError) as error:
        load(tmp_path)
    assert error.value.reason == "asr_asset_mismatch"


# --- Model runtime subprocess seam (stub executable, no model) ----------------

# Echoes a deterministic transcript back for the requested window(s) -- handling
# both the primary single-window task and the review multi-window task -- so the
# round-trip can be asserted without a model.
_ECHO_STUB = r"""
import sys
from video_content_pipeline.model_runtime import execute_child

def handler(request):
    task = request.task
    if "windows" in task:
        joined = ";".join(f"{int(s)}-{int(e)}" for s, e in task["windows"])
        return {"text": f"{task['language']}:{joined}"}
    return {"text": f"{task['language']}:{task['start_sample']}-{task['end_sample']}"}

sys.exit(execute_child(handler))
"""

# Reports the hub-offline guard values the child actually sees, so the parent can
# prove they were forced onto the environment.
_GUARDS_STUB = r"""
import os, sys
from video_content_pipeline.model_runtime import execute_child

def handler(request):
    keys = ["HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE",
            "HF_HUB_DISABLE_TELEMETRY", "HF_HUB_DISABLE_IMPLICIT_TOKEN"]
    return {"text": ",".join(os.environ.get(k, "MISSING") for k in keys)}

sys.exit(execute_child(handler))
"""

_MALFORMED_STUB = r"""
import json, sys
sys.stdin.read()
json.dump({"result": {"text": 123}, "peak_memory_bytes": 1}, sys.stdout)
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


def _run_primary(command: list[str], tmp_path: Path) -> tuple[str, int]:
    return transcribe_chunk(
        Path("/models/asr"),
        tmp_path / "audio.wav",
        "en",
        (SR, 2 * SR),
        command=command,
        timeout_seconds=30,
    )


def _run_review(command: list[str], tmp_path: Path) -> tuple[str, int]:
    return review_windows(
        Path("/models/asr"),
        tmp_path / "audio.wav",
        "en",
        [(SR, 2 * SR)],
        command=command,
        timeout_seconds=30,
    )


def test_primary_chunk_round_trips_text_and_peak(tmp_path: Path) -> None:
    text, peak = _run_primary(_write_stub(tmp_path, "echo.py", _ECHO_STUB), tmp_path)
    assert text == f"en:{SR}-{2 * SR}"
    assert peak > 0


def test_review_windows_round_trip_text_and_peak(tmp_path: Path) -> None:
    command = _write_stub(tmp_path, "echo.py", _ECHO_STUB)
    text, peak = review_windows(
        Path("/models/asr"),
        tmp_path / "audio.wav",
        "en",
        [(SR, 2 * SR), (4 * SR, 5 * SR)],
        command=command,
        timeout_seconds=30,
    )
    # Both speech windows reached the child in order (silence between dropped).
    assert text == f"en:{SR}-{2 * SR};{4 * SR}-{5 * SR}"
    assert peak > 0


@pytest.mark.parametrize("run", [_run_primary, _run_review])
def test_subprocess_forces_hub_offline_guards_onto_the_child(tmp_path: Path, run) -> None:
    text, _ = run(_write_stub(tmp_path, "guards.py", _GUARDS_STUB), tmp_path)
    assert text == "1,1,1,1"


@pytest.mark.parametrize("run", [_run_primary, _run_review])
def test_subprocess_rejects_malformed_child_output(tmp_path: Path, run) -> None:
    with pytest.raises(AsrEngineError) as error:
        run(_write_stub(tmp_path, "malformed.py", _MALFORMED_STUB), tmp_path)
    assert error.value.reason == "asr_output_invalid"


@pytest.mark.parametrize("run", [_run_primary, _run_review])
def test_subprocess_child_crash_surfaces_as_a_typed_model_runtime_error(
    tmp_path: Path, run
) -> None:
    with pytest.raises(ModelRuntimeError) as error:
        run(_write_stub(tmp_path, "crash.py", _CRASH_STUB), tmp_path)
    assert error.value.reason == "engine_child_crashed"


# --- end-to-end orchestration over a stub executable --------------------------


def _install_valid_asset(project_root: Path, candidate_id: str, capability: str) -> str:
    """Write a minimal valid asset + registry entry; return its sha256."""

    asset_dir = project_root / "models" / capability
    asset_dir.mkdir(parents=True)
    (asset_dir / "config.json").write_text('{"model_type": "stub"}', encoding="utf-8")
    (asset_dir / "weights.bin").write_bytes(b"weights")
    manifest = build_file_manifest(asset_dir)
    asset_sha256 = manifest_asset_sha256(manifest)
    _write_registry(
        project_root,
        [
            {
                "candidate_id": candidate_id,
                "capability": capability,
                "local_path": f"models/{capability}/",
                "file_manifest": manifest,
                "asset_sha256": asset_sha256,
            }
        ],
    )
    return asset_sha256


def test_transcribe_derivative_assembles_cues_over_the_chunk_stream(tmp_path: Path) -> None:
    asset_sha256 = _install_valid_asset(tmp_path, "qwen3-asr-1-7b", "asr_primary")
    command = _write_stub(tmp_path, "echo.py", _ECHO_STUB)
    mapping = _mapping(20)
    chunks = (_chunk(0, 0, 3 * SR, mapping), _chunk(1, 5 * SR, 9 * SR, mapping))

    result = transcribe_derivative(
        tmp_path,
        tmp_path / "audio-16k.wav",
        source_id="part",
        stream_index=0,
        language="en",
        chunks=chunks,
        command=command,
        timeout_seconds=30,
    )

    assert result.model_asset_sha256 == asset_sha256
    assert [cue.ordinal for cue in result.cues] == [0, 1]
    assert result.cues[0].interval == chunks[0].source_interval
    assert result.peak_memory_bytes > 0
    assert len(result.chunk_peak_memory_bytes) == 2


def test_review_trims_to_speech_and_short_circuits_silence(tmp_path: Path) -> None:
    asset_sha256 = _install_valid_asset(tmp_path, "whisper-large-v3", "asr_review")
    command = _write_stub(tmp_path, "echo.py", _ECHO_STUB)
    mapping = _mapping(10)
    speech = (_interval(1, 3), _interval(6, 8))
    intervals = (
        _interval(2, 7),  # overlaps both speech runs -> reviewed with the model
        _interval(4, 5),  # entirely in silence -> empty, no model run
    )

    result = review_suspicious_intervals(
        tmp_path,
        tmp_path / "audio-16k.wav",
        mapping,
        source_id="part",
        stream_index=0,
        language="en",
        intervals=intervals,
        speech_intervals=speech,
        command=command,
        timeout_seconds=30,
    )

    assert result.model_asset_sha256 == asset_sha256
    assert [review.interval for review in result.reviews] == list(intervals)
    # The speech interval was VAD-trimmed to its two speech windows and reviewed.
    assert result.reviews[0].reviewed_with_model is True
    assert result.reviews[0].text == f"en:{2 * SR}-{3 * SR};{6 * SR}-{7 * SR}"
    # The silence-only interval is empty and never ran the model.
    assert result.reviews[1].reviewed_with_model is False
    assert result.reviews[1].text == ""
    # Only the one model-run interval recorded a peak.
    assert len(result.interval_peak_memory_bytes) == 1
    assert result.peak_memory_bytes > 0
