"""Shared VAD-derived chunk derivation (Phase 11 ticket 06).

The chunk derivation is pure and model-free: it groups a VAD partition's
speech runs into speech-anchored windows of at most five minutes, cut only in
the non-speech gaps between runs, and carries each chunk's exact
derivative-to-source time mapping. These tests exercise it directly with
in-memory ``DerivativeTimeMapping`` fixtures -- no ONNX, no filesystem.
"""

from __future__ import annotations

import pytest

from video_content_pipeline.audio_derivation import DerivativeTimeMapping
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
from video_content_pipeline.vad_chunking import (
    FIVE_MINUTES,
    SpeechChunk,
    VadChunkingError,
    derive_speech_chunks,
)


def _mapping(sample_rate: int, sample_count: int, *, start: int = 0) -> DerivativeTimeMapping:
    """A derivative whose source clock starts at ``start`` seconds at ``sample_rate``."""

    return DerivativeTimeMapping(
        HalfOpenInterval(
            ExactTime(start * sample_rate, sample_rate),
            ExactTime(start * sample_rate + sample_count, sample_rate),
        ),
        sample_rate,
        sample_count,
    )


def test_no_speech_yields_no_chunks() -> None:
    assert derive_speech_chunks((), _mapping(16000, 16000)) == ()


def test_single_speech_run_becomes_one_chunk_mapped_to_source_time() -> None:
    mapping = _mapping(16000, 160000, start=10)  # 10 s of source, from t=10 s
    chunks = derive_speech_chunks(((32000, 96000),), mapping)

    assert len(chunks) == 1
    chunk = chunks[0]
    assert (chunk.chunk_index, chunk.start_sample, chunk.end_sample) == (0, 32000, 96000)
    # 32000/16000 = 2 s past the source start of 10 s -> [12, 16) s.
    assert chunk.source_interval == HalfOpenInterval(ExactTime(12), ExactTime(16))
    assert chunk.speech_runs == (HalfOpenInterval(ExactTime(12), ExactTime(16)),)


def test_runs_within_the_window_stay_in_one_chunk() -> None:
    mapping = _mapping(100, 1000)
    chunks = derive_speech_chunks(((0, 100), (300, 400), (900, 1000)), mapping)

    assert len(chunks) == 1
    assert (chunks[0].start_sample, chunks[0].end_sample) == (0, 1000)
    assert chunks[0].speech_runs == (
        HalfOpenInterval(ExactTime(0), ExactTime(1)),
        HalfOpenInterval(ExactTime(3), ExactTime(4)),
        HalfOpenInterval(ExactTime(9), ExactTime(10)),
    )


def test_a_new_chunk_starts_only_when_the_next_run_would_exceed_the_window() -> None:
    mapping = _mapping(100, 1000)
    # max window = 5 samples of source at rate 100 -> ExactTime(5).
    chunks = derive_speech_chunks(
        ((0, 50), (100, 150), (700, 750), (900, 950)),
        mapping,
        max_chunk_duration=ExactTime(5),
    )

    # First two runs span [0, 150) = 1.5 s <= 5 s; adding the third would span
    # [0, 750) = 7.5 s > 5 s, so a cut falls in the silence gap [150, 700).
    assert [(c.start_sample, c.end_sample) for c in chunks] == [(0, 150), (700, 950)]
    # Every cut lands in a positive-width non-speech gap between runs.
    for earlier, later in zip(chunks, chunks[1:], strict=False):
        assert earlier.end_sample < later.start_sample


def test_touching_runs_coalesce_so_cuts_never_fall_at_a_zero_width_gap() -> None:
    mapping = _mapping(100, 1000)
    chunks = derive_speech_chunks(((0, 100), (100, 200)), mapping)

    assert len(chunks) == 1
    assert (chunks[0].start_sample, chunks[0].end_sample) == (0, 200)
    # The two touching runs are reported as one coalesced speech span.
    assert chunks[0].speech_runs == (HalfOpenInterval(ExactTime(0), ExactTime(2)),)


def test_chunk_indexes_are_contiguous_from_zero() -> None:
    mapping = _mapping(100, 1000)
    chunks = derive_speech_chunks(
        ((0, 50), (700, 750), (900, 950)),
        mapping,
        max_chunk_duration=ExactTime(5),
    )
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_a_single_speech_run_longer_than_the_window_is_a_typed_failure() -> None:
    mapping = _mapping(100, 1000)
    with pytest.raises(VadChunkingError) as excinfo:
        derive_speech_chunks(((0, 600),), mapping, max_chunk_duration=ExactTime(5))
    assert excinfo.value.reason == "chunk_window_exceeded"


def test_overlapping_runs_are_rejected() -> None:
    mapping = _mapping(100, 1000)
    with pytest.raises(VadChunkingError) as excinfo:
        derive_speech_chunks(((0, 100), (50, 200)), mapping)
    assert excinfo.value.reason == "chunk_speech_runs_invalid"


def test_a_run_outside_the_derivative_is_rejected() -> None:
    mapping = _mapping(100, 1000)
    with pytest.raises(VadChunkingError) as excinfo:
        derive_speech_chunks(((900, 1001),), mapping)
    assert excinfo.value.reason == "chunk_speech_runs_invalid"


def test_a_degenerate_run_is_rejected() -> None:
    mapping = _mapping(100, 1000)
    with pytest.raises(VadChunkingError) as excinfo:
        derive_speech_chunks(((100, 100),), mapping)
    assert excinfo.value.reason == "chunk_speech_runs_invalid"


def test_boolean_sample_boundaries_are_rejected() -> None:
    mapping = _mapping(100, 1000)
    with pytest.raises(VadChunkingError) as excinfo:
        derive_speech_chunks(((False, 100),), mapping)  # type: ignore[list-item]
    assert excinfo.value.reason == "chunk_speech_runs_invalid"


def test_non_positive_window_is_rejected() -> None:
    mapping = _mapping(100, 1000)
    with pytest.raises(VadChunkingError) as excinfo:
        derive_speech_chunks(((0, 50),), mapping, max_chunk_duration=ExactTime(0))
    assert excinfo.value.reason == "chunk_duration_invalid"


def test_default_window_is_five_minutes() -> None:
    assert FIVE_MINUTES == ExactTime(300)


def test_chunk_serializes_to_source_time_json() -> None:
    mapping = _mapping(16000, 160000, start=10)
    (chunk,) = derive_speech_chunks(((32000, 96000),), mapping)
    assert chunk.as_json() == {
        "chunk_index": 0,
        "start_sample": 32000,
        "end_sample": 96000,
        "source_interval": {
            "start": {"numerator": 12, "denominator": 1},
            "end": {"numerator": 16, "denominator": 1},
        },
        "speech_runs": [
            {
                "start": {"numerator": 12, "denominator": 1},
                "end": {"numerator": 16, "denominator": 1},
            }
        ],
    }


def test_speech_chunk_is_immutable() -> None:
    mapping = _mapping(100, 1000)
    (chunk,) = derive_speech_chunks(((0, 100),), mapping)
    assert isinstance(chunk, SpeechChunk)
    with pytest.raises(AttributeError):
        chunk.chunk_index = 5  # type: ignore[misc]
