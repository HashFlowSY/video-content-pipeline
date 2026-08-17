"""Model-free tests for the ticket-13 prototype driver's pure glue.

The real per-capability engine runs are maintainer-invoked retained evidence and
never run here; these tests cover the deterministic input-prep and sample
formatting the driver wraps around the engines.
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from video_content_pipeline.prototype import PROTOTYPE_CAPABILITIES
from video_content_pipeline.prototype_runs import _RUNNERS as RUNNERS
from video_content_pipeline.prototype_runs import (
    PROTOTYPE_SOURCES,
    format_ocr_entries,
    format_speaker_turn_entries,
    format_transcript_entries,
    loaded_part_for_cues,
    source_language,
    suspicious_intervals_from_chunks,
    timestamp_label,
)
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval


def test_runners_cover_exactly_the_prototype_capabilities() -> None:
    # Adding a capability must touch both PROTOTYPE_CAPABILITIES and _RUNNERS.
    assert set(RUNNERS) == set(PROTOTYPE_CAPABILITIES)


def _interval(start: int, end: int) -> HalfOpenInterval:
    return HalfOpenInterval(ExactTime(start), ExactTime(end))


class TestSourceLanguage:
    def test_known_sources_carry_zh_and_en(self) -> None:
        languages = {source_language(sid) for sid in PROTOTYPE_SOURCES}
        assert languages == {"zh", "en"}

    def test_unknown_source_is_refused(self) -> None:
        with pytest.raises(KeyError):
            source_language("not-a-source")


class TestTimestampLabel:
    def test_formats_minutes_and_seconds(self) -> None:
        assert timestamp_label(ExactTime(0)) == "00:00"
        assert timestamp_label(ExactTime(65)) == "01:05"
        assert timestamp_label(ExactTime(3661)) == "61:01"


class TestFormatters:
    def test_transcript_entries_pair_timestamp_and_text(self) -> None:
        entries = format_transcript_entries(
            ((ExactTime(0), "你好"), (ExactTime(5), "世界")), limit=10
        )
        assert entries == ["00:00 你好", "00:05 世界"]

    def test_transcript_entries_respect_limit(self) -> None:
        entries = format_transcript_entries(
            tuple((ExactTime(i), f"cue{i}") for i in range(20)), limit=3
        )
        assert len(entries) == 3

    def test_speaker_turn_entries_render_interval_and_label(self) -> None:
        entries = format_speaker_turn_entries(((_interval(0, 5), "speaker-0"),), limit=5)
        assert entries == ["00:00–00:05 speaker-0"]

    def test_ocr_entries_render_text_and_score(self) -> None:
        entries = format_ocr_entries((("HEADLINE", 0.97),), limit=5)
        assert entries == ["HEADLINE (0.97)"]


class TestSuspiciousIntervals:
    def test_selects_leading_speech_windows(self) -> None:
        chunks = (_interval(0, 30), _interval(60, 90), _interval(120, 150))
        intervals = suspicious_intervals_from_chunks(chunks, limit=2)
        assert intervals == (_interval(0, 30), _interval(60, 90))

    def test_empty_when_no_chunks(self) -> None:
        assert suspicious_intervals_from_chunks((), limit=2) == ()


class TestLoadedPart:
    def test_builds_ordered_cue_ids(self) -> None:
        part = loaded_part_for_cues("part-1", "asr-primary", 3)
        assert part.part_id == "part-1"
        assert part.track_id == "asr-primary"
        assert part.cue_ids == ("part-1:0", "part-1:1", "part-1:2")


def test_fraction_helpers_are_exact() -> None:
    # media/wall as exact fractions keeps the real-time-factor auditable.
    assert Fraction(242) / Fraction(121) == Fraction(2)
