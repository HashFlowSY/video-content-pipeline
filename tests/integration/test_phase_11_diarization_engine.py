"""Real sherpa-onnx diarization over a fixture-derived wav (Phase 11 ticket 07).

This is the one place the real offline sherpa-onnx speaker-diarization pipeline
runs against the two pinned, vendored assets resolved from the model registry --
offline, from disk, on the provisioned machine where the git-ignored ``models/``
tree lives (error, never skip, mirroring the Phase 10 identity-pinned toolchain
tests and the ticket 06 VAD engine test). It proves the real engine produces
*contract-valid, anonymous, Part-local SpeakerTurn evidence* and that its
diarization-VAD conflict evidence is graded against the real VAD partition from
ticket 06. Speaker-separation quality (zh/en) is not asserted here -- that is the
maintainer's prototype review (Phase 11 ticket 13); this test asserts the
contract, structure, and provenance.

The wav is derived at runtime from a deterministic synthetic recipe (two
alternating, briefly overlapping tones) written as a 16 kHz mono PCM-16 file, so
the test is self-contained, offline, and fast.
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest

from video_content_pipeline.audio_derivation import DerivativeTimeMapping
from video_content_pipeline.diarization_engine import (
    DIARIZATION_SAMPLE_RATE,
    analyze_derivative_diarization,
    load_embedding_asset,
    load_segmentation_asset,
)
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
from video_content_pipeline.vad_engine import analyze_derivative_vad

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_fixture_wav(path: Path, sample_count: int) -> None:
    """Write a deterministic 16 kHz mono PCM-16 wav: two briefly overlapping tones."""

    t = np.arange(sample_count) / DIARIZATION_SAMPLE_RATE
    first = 0.3 * np.sin(2 * np.pi * 180.0 * t) * ((t > 0.5) & (t < 2.5))
    second = 0.3 * np.sin(2 * np.pi * 260.0 * t) * ((t > 2.0) & (t < 4.5))
    pcm = np.clip((first + second).astype(np.float32), -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(DIARIZATION_SAMPLE_RATE)
        handle.writeframes(pcm16.tobytes())


def _mapping(sample_count: int) -> DerivativeTimeMapping:
    return DerivativeTimeMapping(
        HalfOpenInterval(ExactTime(0), ExactTime(sample_count, DIARIZATION_SAMPLE_RATE)),
        DIARIZATION_SAMPLE_RATE,
        sample_count,
    )


def _registry_asset_sha256(candidate_id: str) -> str:
    registry = json.loads((REPO_ROOT / "models" / "registry.json").read_text(encoding="utf-8"))
    (candidate,) = [c for c in registry["candidates"] if c.get("candidate_id") == candidate_id]
    return str(candidate["asset_sha256"])


def test_real_diarization_produces_contract_valid_speaker_turns(tmp_path: Path) -> None:
    sample_count = 5 * DIARIZATION_SAMPLE_RATE  # 5 seconds
    wav_path = tmp_path / "analysis-16k.wav"
    _write_fixture_wav(wav_path, sample_count)
    mapping = _mapping(sample_count)

    # The real VAD partition from ticket 06 grades the diarization-VAD conflicts,
    # proving that evidence keeps its meaning against a real upstream partition.
    vad = analyze_derivative_vad(
        REPO_ROOT, wav_path, mapping, source_id="fixture-part", stream_index=0
    )

    result = analyze_derivative_diarization(
        REPO_ROOT,
        wav_path,
        mapping,
        source_id="fixture-part",
        stream_index=0,
        part_label="part-01",
        voice_activity_intervals=vad.part_evidence.voice_activity_intervals,
    )

    # Provenance: the calibrated real pipeline ran against both pinned assets.
    assert result.calibrated is True
    assert result.segmentation_asset_sha256 == _registry_asset_sha256(
        "sherpa-onnx-pyannote-segmentation-3-0"
    )
    assert result.embedding_asset_sha256 == _registry_asset_sha256(
        "3dspeaker-campplus-zh-en-advanced"
    )
    assert result.partition is not None

    document = result.as_json()
    assert document["source_id"] == "fixture-part"
    assert isinstance(document["speaker_turns"], list)
    assert isinstance(document["diarization_vad_conflicts"], list)

    # Every anonymous cluster candidate is labelled Part-local and anonymous, and
    # every published/conflicted turn lands inside the derivative coverage. The
    # synthetic recipe need not fire the real model; whatever it finds must be
    # contract-valid (real speaker quality is the maintainer's prototype review).
    coverage = mapping.source_interval
    for turn in result.raw_turns:
        assert turn.cluster_id.startswith("speaker-")
        assert coverage.start <= turn.interval.start < turn.interval.end <= coverage.end
    for label in result.partition.labels_by_cluster.values():
        assert label.startswith("part-01:speaker-")
    for published in result.partition.published:
        assert published.speaker_label.startswith("part-01:speaker-")
    for conflict in result.partition.conflicts:
        assert conflict.vad_states  # a conflict names the VAD states it overlaps


def test_real_diarization_assets_verify_from_disk() -> None:
    segmentation_path, segmentation_sha256 = load_segmentation_asset(REPO_ROOT)
    embedding_path, embedding_sha256 = load_embedding_asset(REPO_ROOT)

    assert segmentation_path.is_file() and segmentation_path.name == "model.onnx"
    assert embedding_path.is_file() and embedding_path.suffix == ".onnx"
    assert segmentation_sha256 == _registry_asset_sha256("sherpa-onnx-pyannote-segmentation-3-0")
    assert embedding_sha256 == _registry_asset_sha256("3dspeaker-campplus-zh-en-advanced")
