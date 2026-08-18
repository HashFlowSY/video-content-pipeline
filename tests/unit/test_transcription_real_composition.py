"""build_asr_transcript composes the real primary ASR into published cues + evidence.

The engine is monkeypatched (no model loaded); the focus is the composition: recover
the derivative + VAD speech runs from the audio report, re-chunk at the semantic
window, run primary ASR, publish the subtitle candidate, and record stage execution.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from video_content_pipeline import asr_engine
from video_content_pipeline.asr_engine import PrimaryTranscriptionResult
from video_content_pipeline.audio_derivation import DerivativeTimeMapping
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
from video_content_pipeline.transcription import TranscriptionError, build_asr_transcript
from video_content_pipeline.transcription_contracts import ProjectedAsrCue


def _mapping() -> DerivativeTimeMapping:
    # 4 s of 16 kHz mono audio.
    return DerivativeTimeMapping(
        source_interval=HalfOpenInterval(ExactTime(0), ExactTime(4)),
        sample_rate=16000,
        sample_count=64000,
    )


def _audio_report_document(wav_path: str) -> dict[str, object]:
    return {
        "report_id": "audio",
        "analysis_audio_derivatives": [
            {
                "source_id": "part-a",
                "stream_index": 1,
                "path": wav_path,
                "mapping": _mapping().as_json(),
            }
        ],
        "analysis_audio_streams": [{"source_id": "part-a", "stream_index": 1, "language": "en"}],
        "formal_evidence": [
            {
                "capability": "vad",
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
                            },
                            {
                                "interval": {
                                    "start": {"numerator": 2, "denominator": 1},
                                    "end": {"numerator": 4, "denominator": 1},
                                },
                                "state": "non_speech",
                            },
                        ],
                    }
                ],
            }
        ],
    }


def _candidate(high_bytes: int) -> dict[str, object]:
    return {
        "candidate_id": "qwen3-asr-1-7b",
        "eligibility_evidence": {"resource_high_bytes": high_bytes},
    }


def test_build_asr_transcript_publishes_cues_and_records_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_transcribe(project_root, wav_path, **kwargs):
        captured["wav_path"] = wav_path
        captured["kwargs"] = kwargs
        return PrimaryTranscriptionResult(
            source_id="part-a",
            stream_index=1,
            language="en",
            cues=(
                ProjectedAsrCue(0, HalfOpenInterval(ExactTime(0), ExactTime(2)), "hello", (), ()),
            ),
            model_asset_sha256="a" * 64,
            peak_memory_bytes=5_000_000_000,
            chunk_peak_memory_bytes=(5_000_000_000,),
        )

    monkeypatch.setattr(asr_engine, "transcribe_derivative", fake_transcribe)

    workspace = tmp_path / "ws"
    transcript, stage_execution = build_asr_transcript(
        tmp_path,
        _audio_report_document("/proj/stream-1.wav"),
        ["part-a"],
        _candidate(high_bytes=5_462_840_040),
        workspace,
    )

    # The engine received the pinned derivative and the chunk(s) derived from the
    # single speech-likely run (0..2 s -> samples 0..32000).
    assert captured["wav_path"] == Path("/proj/stream-1.wav")
    assert captured["kwargs"]["language"] == "en"
    chunks = captured["kwargs"]["chunks"]
    assert len(chunks) == 1
    assert (chunks[0].start_sample, chunks[0].end_sample) == (0, 32000)

    entry = transcript[0]
    assert entry["source_id"] == "part-a"
    assert entry["cue_count"] == 1
    published = Path(str(entry["source_candidate"]["path"]))
    assert published.is_file()

    record = stage_execution[0]
    assert record["capability"] == "asr_primary"
    assert record["state"] == "completed"
    assert record["resource_measurement"]["path"].endswith("resource-measurement.json")


def test_build_asr_transcript_over_envelope_is_release_unverified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_transcribe(project_root, wav_path, **kwargs):
        return PrimaryTranscriptionResult(
            source_id="part-a",
            stream_index=1,
            language="en",
            cues=(),
            model_asset_sha256="a" * 64,
            peak_memory_bytes=99_000_000_000,
            chunk_peak_memory_bytes=(99_000_000_000,),
        )

    monkeypatch.setattr(asr_engine, "transcribe_derivative", fake_transcribe)

    _, stage_execution = build_asr_transcript(
        tmp_path,
        _audio_report_document("/proj/stream-1.wav"),
        ["part-a"],
        _candidate(high_bytes=5_462_840_040),
        tmp_path / "ws",
    )
    assert stage_execution[0]["state"] == "release_unverified"


def test_build_asr_transcript_rejects_missing_part_evidence(tmp_path: Path) -> None:
    document = _audio_report_document("/proj/stream-1.wav")
    document["analysis_audio_streams"] = []  # no language for the Part
    with pytest.raises(TranscriptionError) as error:
        build_asr_transcript(tmp_path, document, ["part-a"], _candidate(1), tmp_path / "ws")
    assert error.value.reason == "transcription_audio_evidence_invalid"
