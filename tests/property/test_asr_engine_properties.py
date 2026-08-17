"""Property tests for the real primary ASR transcript assembly (Phase 11 ticket 09).

Over generated VAD chunk streams and arbitrary per-chunk transcripts, these prove
the ticket's assembly invariant: the assembled transcript is *monotonic and
coverage-consistent* on the authoritative source timeline -- one cue per chunk that
produced visible text, with strictly advancing ordinals, strictly ordered
non-overlapping intervals, and every interval inside the derivative coverage -- the
shape the unchanged canonical-timeline gate requires.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from tests.support import hypothesis_profiles  # noqa: F401  (registers the gate profile)
from video_content_pipeline.asr_engine import ASR_SAMPLE_RATE, assemble_transcript_cues
from video_content_pipeline.audio_derivation import DerivativeTimeMapping
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
from video_content_pipeline.vad_chunking import SpeechChunk

SR = ASR_SAMPLE_RATE


@st.composite
def _chunk_stream(draw: st.DrawFn) -> tuple[DerivativeTimeMapping, list[tuple[SpeechChunk, str]]]:
    """An ordered, silence-separated chunk stream with arbitrary per-chunk texts."""

    count = draw(st.integers(min_value=0, max_value=6))
    cursor = draw(st.integers(min_value=0, max_value=2))  # leading silence (seconds)
    chunks: list[tuple[int, int]] = []
    for _ in range(count):
        length = draw(st.integers(min_value=1, max_value=4))
        chunks.append((cursor, cursor + length))
        cursor += length + draw(st.integers(min_value=1, max_value=3))  # positive silence gap
    total = cursor + 1
    mapping = DerivativeTimeMapping(
        HalfOpenInterval(ExactTime(0), ExactTime(total * SR, SR)), SR, total * SR
    )
    text_strategy = st.one_of(
        st.just(""), st.just("   "), st.text(alphabet="abc 你好", min_size=1, max_size=8)
    )
    pairs: list[tuple[SpeechChunk, str]] = []
    for index, (start, end) in enumerate(chunks):
        chunk = SpeechChunk(
            chunk_index=index,
            start_sample=start * SR,
            end_sample=end * SR,
            source_interval=mapping.source_interval_for_samples(start * SR, end * SR),
            speech_runs=(mapping.source_interval_for_samples(start * SR, end * SR),),
        )
        pairs.append((chunk, draw(text_strategy)))
    return mapping, pairs


@given(_chunk_stream())
def test_assembly_is_monotonic_and_coverage_consistent(
    case: tuple[DerivativeTimeMapping, list[tuple[SpeechChunk, str]]],
) -> None:
    mapping, pairs = case
    cues = assemble_transcript_cues(pairs)

    expected_count = sum(1 for _, text in pairs if any(not ch.isspace() for ch in text))
    assert len(cues) == expected_count

    coverage = mapping.source_interval
    previous_end = None
    for ordinal, cue in enumerate(cues):
        # Strictly advancing ordinals with no gaps.
        assert cue.ordinal == ordinal
        # Every interval sits inside the derivative coverage.
        assert coverage.start <= cue.interval.start < cue.interval.end <= coverage.end
        # Strictly ordered, non-overlapping intervals on the source timeline.
        if previous_end is not None:
            assert previous_end <= cue.interval.start
        previous_end = cue.interval.end
        # The Qwen3-ASR path emits no per-token or language-span evidence.
        assert cue.tokens == () and cue.language_spans == ()
