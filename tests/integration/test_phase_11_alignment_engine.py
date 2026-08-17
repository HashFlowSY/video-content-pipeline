"""Real Qwen3-ForcedAligner over a fixture-derived wav (Phase 11 ticket 08).

This is the one place the real Qwen3-ForcedAligner-0.6B-8bit runs -- through its
Model runtime subprocess (ADR 0055) against the pinned, vendored asset resolved
from the model registry, offline, from disk, on the provisioned machine where the
git-ignored ``models/`` tree lives (error, never skip, mirroring the ticket 06 VAD
and ticket 07 diarization engine tests). It proves the real engine, driven through
the subprocess seam, produces *contract-valid per-cue AlignmentProposal evidence*
that flows through the unchanged Adopted alignment timing view, and that it reports
real MLX peak-memory evidence. Chinese/English alignment quality is not asserted
here -- that is the maintainer's prototype review (Phase 11 ticket 13); this test
asserts the contract, the timeline mapping, and provenance.

The wav is a deterministic synthetic recipe written as a 16 kHz mono PCM-16 file,
and the VAD chunk is constructed directly to cover it, so the real aligner is
actually exercised (the aligner runs on any audio; word-timing quality on
synthetic audio is meaningless and deliberately not graded).
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest

from video_content_pipeline.alignment_engine import (
    ALIGNER_SAMPLE_RATE,
    analyze_derivative_alignment,
    load_aligner_asset,
)
from video_content_pipeline.audio_analysis import AlignmentCue
from video_content_pipeline.audio_derivation import DerivativeTimeMapping
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
from video_content_pipeline.vad_chunking import SpeechChunk

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
SR = ALIGNER_SAMPLE_RATE


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


def _registry_asset_sha256() -> str:
    registry = json.loads((REPO_ROOT / "models" / "registry.json").read_text(encoding="utf-8"))
    (candidate,) = [
        c for c in registry["candidates"] if c.get("candidate_id") == "qwen3-forced-aligner-0-6b"
    ]
    return str(candidate["asset_sha256"])


def test_real_alignment_produces_contract_valid_proposals(tmp_path: Path) -> None:
    sample_count = 3 * SR  # 3 seconds
    wav_path = tmp_path / "analysis-16k.wav"
    _write_fixture_wav(wav_path, sample_count)
    mapping = _mapping(sample_count)
    cues = (AlignmentCue(1, "hello world", HalfOpenInterval(ExactTime(0), ExactTime(2))),)

    result = analyze_derivative_alignment(
        REPO_ROOT,
        wav_path,
        mapping,
        source_id="fixture-part",
        stream_index=0,
        language="en",
        source_cues=cues,
        chunks=(_whole_chunk(mapping),),
    )

    # Provenance: the calibrated real model ran against the pinned asset, in its
    # own subprocess, reporting a real MLX peak.
    assert result.calibrated is True
    assert result.model_asset_sha256 == _registry_asset_sha256()
    assert result.peak_memory_bytes > 0
    assert result.chunk_peak_memory_bytes == (result.peak_memory_bytes,)

    # One proposal per source cue, carrying the cue's own text, inside coverage.
    coverage = mapping.source_interval
    (proposal,) = result.projected.proposals
    assert proposal.source_ordinal == 1
    assert proposal.text == "hello world"
    assert coverage.start <= proposal.interval.start < proposal.interval.end <= coverage.end
    assert 0 <= proposal.confidence <= 1

    # The calibrated engine drove the unchanged Adopted alignment timing view; its
    # candidate evidence covers the source cue on the authoritative timeline.
    assert result.adopted_view is not None
    (candidate,) = result.adopted_view.candidates
    assert candidate.source_ordinal == 1
    assert coverage.start <= candidate.interval.start < candidate.interval.end <= coverage.end


def test_real_aligner_asset_verifies_from_disk() -> None:
    model_dir, asset_sha256 = load_aligner_asset(REPO_ROOT)

    assert model_dir.is_dir()
    assert (model_dir / "model.safetensors").is_file()
    assert asset_sha256 == _registry_asset_sha256()
