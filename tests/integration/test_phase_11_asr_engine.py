"""Real primary + review ASR over a fixture-derived wav (Phase 11 ticket 09).

This is the one place the real Qwen3-ASR-1.7B-8bit (asr_primary, mlx-audio) and
whisper-large-v3-mlx (asr_review, mlx-whisper) run -- each through its own Model
runtime subprocess (ADR 0055) against the pinned, vendored asset resolved from the
model registry, offline, from disk, on the provisioned machine where the git-ignored
``models/`` tree lives (error, never skip, mirroring the ticket 06--08 engine tests).
It proves each real engine, driven through the subprocess seam, produces
contract-valid output on the authoritative timeline with real MLX peak-memory
evidence, and that the two model families are independent identities (so a review is
independent evidence, never a same-model retry). Chinese/English transcription
quality is not asserted here -- that is the maintainer's prototype review (Phase 11
ticket 13); this test asserts the contract, the timeline mapping, provenance, and
the Independent-model review requirement with the real identities.

The wav is a deterministic synthetic recipe written as a 16 kHz mono PCM-16 file, so
the real models are actually exercised (transcription quality on synthetic audio is
meaningless and deliberately not graded).
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest

from video_content_pipeline.asr_engine import (
    ASR_SAMPLE_RATE,
    load_primary_asset,
    load_review_asset,
    review_suspicious_intervals,
    transcribe_derivative,
)
from video_content_pipeline.audio_derivation import DerivativeTimeMapping
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
from video_content_pipeline.transcription_arbitration import classify_review_attempt
from video_content_pipeline.vad_chunking import SpeechChunk

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
SR = ASR_SAMPLE_RATE


def _write_fixture_wav(path: Path, sample_count: int) -> None:
    """Write a deterministic 16 kHz mono PCM-16 wav (a low-amplitude tone)."""

    t = np.arange(sample_count) / SR
    pcm = 0.1 * np.sin(2 * np.pi * 220.0 * t)
    pcm16 = np.clip(pcm.astype(np.float32), -1.0, 1.0)
    pcm16 = (pcm16 * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(pcm16.tobytes())


def _mapping(sample_count: int) -> DerivativeTimeMapping:
    return DerivativeTimeMapping(
        HalfOpenInterval(ExactTime(0), ExactTime(sample_count, SR)), SR, sample_count
    )


def _whole_chunk(mapping: DerivativeTimeMapping) -> SpeechChunk:
    return SpeechChunk(
        chunk_index=0,
        start_sample=0,
        end_sample=mapping.sample_count,
        source_interval=mapping.source_interval,
        speech_runs=(mapping.source_interval,),
    )


def _registry_asset_sha256(candidate_id: str) -> str:
    registry = json.loads((REPO_ROOT / "models" / "registry.json").read_text(encoding="utf-8"))
    (candidate,) = [c for c in registry["candidates"] if c.get("candidate_id") == candidate_id]
    return str(candidate["asset_sha256"])


def test_real_primary_transcription_is_contract_valid(tmp_path: Path) -> None:
    sample_count = 2 * SR  # 2 seconds
    wav_path = tmp_path / "analysis-16k.wav"
    _write_fixture_wav(wav_path, sample_count)
    mapping = _mapping(sample_count)

    result = transcribe_derivative(
        REPO_ROOT,
        wav_path,
        source_id="fixture-part",
        stream_index=0,
        language="en",
        chunks=(_whole_chunk(mapping),),
    )

    # Provenance: the real model ran against the pinned asset, in its own
    # subprocess, reporting a real MLX peak per chunk.
    assert result.model_asset_sha256 == _registry_asset_sha256("qwen3-asr-1-7b")
    assert result.peak_memory_bytes > 0
    assert result.chunk_peak_memory_bytes == (result.peak_memory_bytes,)

    # Any produced cue is contract-valid: monotonic ordinals, inside coverage,
    # carrying text (quality is not graded here).
    coverage = mapping.source_interval
    for ordinal, cue in enumerate(result.cues):
        assert cue.ordinal == ordinal
        assert coverage.start <= cue.interval.start < cue.interval.end <= coverage.end
        assert isinstance(cue.text, str)


def test_real_review_is_independent_evidence_with_real_peak(tmp_path: Path) -> None:
    sample_count = 2 * SR
    wav_path = tmp_path / "analysis-16k.wav"
    _write_fixture_wav(wav_path, sample_count)
    mapping = _mapping(sample_count)

    result = review_suspicious_intervals(
        REPO_ROOT,
        wav_path,
        mapping,
        source_id="fixture-part",
        stream_index=0,
        language="en",
        intervals=(HalfOpenInterval(ExactTime(0), ExactTime(2)),),
        # The whole fixture is "speech" here, so the interval trims to itself and
        # the real whisper model runs over it.
        speech_intervals=(HalfOpenInterval(ExactTime(0), ExactTime(2)),),
    )

    assert result.model_asset_sha256 == _registry_asset_sha256("whisper-large-v3")
    assert result.peak_memory_bytes > 0
    # The single in-range suspicious interval was reviewed by the model.
    (review,) = result.reviews
    assert review.interval == HalfOpenInterval(ExactTime(0), ExactTime(2))
    assert review.reviewed_with_model is True
    assert isinstance(review.text, str)

    # The Independent-model review requirement holds with the real identities: the
    # whisper review differs from the Qwen3 primary, so it is independent evidence.
    classification = classify_review_attempt(
        primary_model_identity=_registry_asset_sha256("qwen3-asr-1-7b"),
        review_model_identity=result.model_asset_sha256,
    )
    assert classification.independent is True


def test_real_asr_assets_verify_from_disk() -> None:
    primary_dir, primary_sha = load_primary_asset(REPO_ROOT)
    review_dir, review_sha = load_review_asset(REPO_ROOT)

    assert primary_dir.is_dir() and (primary_dir / "model.safetensors").is_file()
    assert review_dir.is_dir() and (review_dir / "weights.npz").is_file()
    assert primary_sha == _registry_asset_sha256("qwen3-asr-1-7b")
    assert review_sha == _registry_asset_sha256("whisper-large-v3")
    assert primary_sha != review_sha
