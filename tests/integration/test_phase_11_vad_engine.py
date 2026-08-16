"""Real silero-vad inference over a fixture-derived wav (Phase 11 ticket 06).

This is the one place real onnxruntime inference runs against the pinned,
vendored ``silero_vad.onnx`` resolved from the model registry -- offline, from
disk, on the provisioned machine where the git-ignored ``models/`` tree lives
(error, never skip, mirroring the Phase 10 identity-pinned toolchain tests). It
proves the first real engine produces a *valid Complete VAD partition* and that
its output flows through the shared chunk derivation. Model quality (Chinese/
English accuracy) is not asserted here -- that is the maintainer's prototype
review; this test asserts the contract, structure, and provenance.

The wav is derived at runtime from a deterministic synthetic recipe (silence /
broadband burst / silence) written as a 16 kHz mono PCM-16 file, so the test is
self-contained, offline, and fast.
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest

from video_content_pipeline.audio_derivation import DerivativeTimeMapping
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
from video_content_pipeline.vad_engine import (
    SILERO_SAMPLE_RATE,
    VoiceActivityState,
    analyze_derivative_vad,
    load_silero_asset,
    load_silero_session,
    silero_frame_probabilities,
)

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_fixture_wav(path: Path, sample_count: int) -> None:
    """Write a deterministic 16 kHz mono PCM-16 wav: silence, burst, silence."""

    rng = np.random.default_rng(20260816)
    samples = np.zeros(sample_count, dtype=np.float32)
    burst = slice(sample_count // 3, 2 * sample_count // 3)
    samples[burst] = rng.standard_normal(len(range(*burst.indices(sample_count)))) * 0.4
    pcm = np.clip(samples, -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SILERO_SAMPLE_RATE)
        handle.writeframes(pcm16.tobytes())


def _mapping(sample_count: int) -> DerivativeTimeMapping:
    return DerivativeTimeMapping(
        HalfOpenInterval(ExactTime(0), ExactTime(sample_count, SILERO_SAMPLE_RATE)),
        SILERO_SAMPLE_RATE,
        sample_count,
    )


def _registry_asset_sha256() -> str:
    registry = json.loads((REPO_ROOT / "models" / "registry.json").read_text(encoding="utf-8"))
    (candidate,) = [c for c in registry["candidates"] if c.get("candidate_id") == "silero-vad"]
    return str(candidate["asset_sha256"])


def test_real_silero_produces_a_valid_complete_vad_partition(tmp_path: Path) -> None:
    sample_count = 3 * SILERO_SAMPLE_RATE  # 3 seconds
    wav_path = tmp_path / "analysis-16k.wav"
    _write_fixture_wav(wav_path, sample_count)
    mapping = _mapping(sample_count)

    result = analyze_derivative_vad(
        REPO_ROOT,
        wav_path,
        mapping,
        source_id="fixture-part",
        stream_index=0,
    )

    # Provenance: the calibrated real model ran against the pinned registry asset.
    assert result.calibrated is True
    assert result.model_asset_sha256 == _registry_asset_sha256()

    intervals = result.part_evidence.voice_activity_intervals
    assert intervals, "the partition must cover the derivative"

    # Complete VAD partition invariants: contiguous, gap-free coverage of the
    # whole usable audio, no overlaps, boundaries inside the coverage.
    coverage = mapping.source_interval
    assert intervals[0].interval.start == coverage.start
    assert intervals[-1].interval.end == coverage.end
    for earlier, later in zip(intervals, intervals[1:], strict=False):
        assert earlier.interval.end == later.interval.start
    # A calibrated model fully classifies -- nothing is left indeterminate.
    assert all(
        item.state in {VoiceActivityState.SPEECH_LIKELY, VoiceActivityState.NON_SPEECH}
        for item in intervals
    )

    # silero (correctly) does not fire on synthetic audio, so this real-inference
    # test proves the partition is a valid, fully-classified tiling of the
    # coverage; the speech_likely -> partition -> chunk projection is proven
    # deterministically by the unit tests, and real speech quality is the
    # maintainer's prototype review (Phase 11 ticket 13), never a pytest assertion.
    # Whatever speech the real model found is covered by chunks that round-trip.
    if result.speech_runs_samples:
        assert result.chunks
        for chunk in result.chunks:
            assert chunk.source_interval == mapping.source_interval_for_samples(
                chunk.start_sample, chunk.end_sample
            )
    else:
        assert result.chunks == ()


def test_real_silero_inference_scores_every_window(tmp_path: Path) -> None:
    sample_count = SILERO_SAMPLE_RATE  # 1 second -> ceil(16000/512) = 32 windows
    wav_path = tmp_path / "analysis-16k.wav"
    _write_fixture_wav(wav_path, sample_count)

    onnx_path, asset_sha256 = load_silero_asset(REPO_ROOT)
    assert asset_sha256 == _registry_asset_sha256()
    session = load_silero_session(onnx_path)

    with wave.open(str(wav_path), "rb") as handle:
        pcm = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")
    samples = pcm.astype(np.float32) / 32768.0

    probabilities = silero_frame_probabilities(session, samples)
    assert len(probabilities) == -(-sample_count // 512)
    assert all(0.0 <= p <= 1.0 for p in probabilities)
