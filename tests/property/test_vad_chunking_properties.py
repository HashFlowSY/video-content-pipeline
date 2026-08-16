"""Property tests for the shared VAD chunk derivation (Phase 11 ticket 06).

Over generated derivatives and speech-run partitions, these prove the four
chunking invariants the ticket names: every chunk is at most the window long,
every cut falls in a positive non-speech gap, all speech is covered exactly
once, and each chunk's sample bounds round-trip through the derivative mapping
to its source interval. They run under the deterministic gate profile.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from tests.support import hypothesis_profiles  # noqa: F401  (registers the gate profile)
from video_content_pipeline.audio_derivation import DerivativeTimeMapping
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
from video_content_pipeline.vad_chunking import derive_speech_chunks


@st.composite
def _partition(draw: st.DrawFn) -> tuple[DerivativeTimeMapping, list[tuple[int, int]], ExactTime]:
    """A derivative, disjoint speech runs, and a window each run fits inside.

    Keeps sample rates small so grouping is exercised cheaply; every run is
    bounded to at most the window so ``chunk_window_exceeded`` never fires.
    """

    sample_rate = draw(st.sampled_from([8, 10, 100]))
    max_seconds = draw(st.integers(min_value=1, max_value=6))
    max_chunk = ExactTime(max_seconds)
    max_run_samples = max_seconds * sample_rate

    runs: list[tuple[int, int]] = []
    cursor = draw(st.integers(min_value=0, max_value=max_run_samples))
    for _ in range(draw(st.integers(min_value=0, max_value=8))):
        gap = draw(st.integers(min_value=1, max_value=max_run_samples))
        length = draw(st.integers(min_value=1, max_value=max_run_samples))
        start = cursor + gap
        runs.append((start, start + length))
        cursor = start + length

    sample_count = cursor + draw(st.integers(min_value=1, max_value=max_run_samples))
    start_offset = draw(st.integers(min_value=0, max_value=1000))
    mapping = DerivativeTimeMapping(
        HalfOpenInterval(
            ExactTime(start_offset, sample_rate),
            ExactTime(start_offset + sample_count, sample_rate),
        ),
        sample_rate,
        sample_count,
    )
    return mapping, runs, max_chunk


@given(_partition())
def test_every_chunk_is_within_the_window(
    partition: tuple[DerivativeTimeMapping, list[tuple[int, int]], ExactTime],
) -> None:
    mapping, runs, max_chunk = partition
    chunks = derive_speech_chunks(runs, mapping, max_chunk_duration=max_chunk)
    for chunk in chunks:
        duration = chunk.source_interval.end - chunk.source_interval.start
        assert duration <= max_chunk


@given(_partition())
def test_every_cut_falls_in_a_positive_non_speech_gap(
    partition: tuple[DerivativeTimeMapping, list[tuple[int, int]], ExactTime],
) -> None:
    mapping, runs, max_chunk = partition
    chunks = derive_speech_chunks(runs, mapping, max_chunk_duration=max_chunk)
    for earlier, later in zip(chunks, chunks[1:], strict=False):
        assert earlier.end_sample < later.start_sample


@given(_partition())
def test_all_speech_is_covered_exactly_once(
    partition: tuple[DerivativeTimeMapping, list[tuple[int, int]], ExactTime],
) -> None:
    mapping, runs, max_chunk = partition
    chunks = derive_speech_chunks(runs, mapping, max_chunk_duration=max_chunk)

    # Coalesce touching input runs the same way the derivation does.
    coalesced: list[tuple[int, int]] = []
    for start, end in sorted(runs):
        if coalesced and start == coalesced[-1][1]:
            coalesced[-1] = (coalesced[-1][0], end)
        else:
            coalesced.append((start, end))

    covered = [run for chunk in chunks for run in chunk.speech_runs]
    expected = [mapping.source_interval_for_samples(start, end) for start, end in coalesced]
    assert covered == expected
    # Each speech span lies inside exactly the chunk that reports it.
    for chunk in chunks:
        for run in chunk.speech_runs:
            assert chunk.source_interval.start <= run.start
            assert run.end <= chunk.source_interval.end


@given(_partition())
def test_chunk_sample_bounds_round_trip_to_the_source_interval(
    partition: tuple[DerivativeTimeMapping, list[tuple[int, int]], ExactTime],
) -> None:
    mapping, runs, max_chunk = partition
    chunks = derive_speech_chunks(runs, mapping, max_chunk_duration=max_chunk)
    for chunk in chunks:
        assert chunk.source_interval == mapping.source_interval_for_samples(
            chunk.start_sample, chunk.end_sample
        )
        assert chunk.source_interval.start == mapping.source_time_for_sample(chunk.start_sample)
        assert chunk.source_interval.end == mapping.source_time_for_sample(chunk.end_sample)


@given(_partition())
def test_chunk_indexes_are_contiguous(
    partition: tuple[DerivativeTimeMapping, list[tuple[int, int]], ExactTime],
) -> None:
    mapping, runs, max_chunk = partition
    chunks = derive_speech_chunks(runs, mapping, max_chunk_duration=max_chunk)
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
