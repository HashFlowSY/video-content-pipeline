"""VAD evidence rules independent of model execution."""

from __future__ import annotations

import pytest

from video_content_pipeline.audio_analysis import (
    AudioAnalysisError,
    VoiceActivityCandidateSegment,
    VoiceActivityState,
    derive_vad_part_evidence,
)
from video_content_pipeline.coverage import StreamCoverage
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval


def _interval(start: int, end: int) -> HalfOpenInterval:
    return HalfOpenInterval(ExactTime(start), ExactTime(end))


def test_vad_part_evidence_partitions_known_audio_and_keeps_caption_risks() -> None:
    evidence = derive_vad_part_evidence(
        source_id="part-a",
        stream_index=2,
        audio_coverage=StreamCoverage(
            coverage=_interval(0, 10),
            gaps=(_interval(4, 5),),
            diagnostics=(),
        ),
        candidate_segments=(
            VoiceActivityCandidateSegment(_interval(0, 3), VoiceActivityState.SPEECH_LIKELY),
            VoiceActivityCandidateSegment(_interval(5, 8), VoiceActivityState.NON_SPEECH),
        ),
        caption_intervals=(_interval(0, 1),),
        uncovered_speech_threshold=ExactTime(2),
        long_silence_threshold=ExactTime(3),
    )

    assert [(item.interval, item.state) for item in evidence.voice_activity_intervals] == [
        (_interval(0, 3), VoiceActivityState.SPEECH_LIKELY),
        (_interval(3, 4), VoiceActivityState.INDETERMINATE),
        (_interval(5, 8), VoiceActivityState.NON_SPEECH),
        (_interval(8, 10), VoiceActivityState.INDETERMINATE),
    ]
    assert [(risk.interval, risk.elevated) for risk in evidence.uncovered_speech_risks] == [
        (_interval(1, 3), True),
    ]
    assert [risk.interval for risk in evidence.audio_state_indeterminate] == [
        _interval(3, 4),
        _interval(4, 5),
        _interval(8, 10),
    ]
    assert [silence.interval for silence in evidence.long_silences] == [_interval(5, 8)]


def test_vad_part_evidence_rejects_a_segment_that_crosses_an_audio_gap() -> None:
    with pytest.raises(AudioAnalysisError, match="known usable audio") as error:
        derive_vad_part_evidence(
            source_id="part-a",
            stream_index=2,
            audio_coverage=StreamCoverage(
                coverage=_interval(0, 10),
                gaps=(_interval(4, 5),),
                diagnostics=(),
            ),
            candidate_segments=(
                VoiceActivityCandidateSegment(_interval(3, 6), VoiceActivityState.SPEECH_LIKELY),
            ),
            caption_intervals=(),
            uncovered_speech_threshold=ExactTime(2),
            long_silence_threshold=ExactTime(3),
        )

    assert error.value.reason == "model_output_invalid"


def test_vad_without_a_primary_subtitle_track_keeps_short_speech_risk() -> None:
    evidence = derive_vad_part_evidence(
        source_id="part-without-subtitles",
        stream_index=2,
        audio_coverage=StreamCoverage(coverage=_interval(0, 1), gaps=(), diagnostics=()),
        candidate_segments=(
            VoiceActivityCandidateSegment(_interval(0, 1), VoiceActivityState.SPEECH_LIKELY),
        ),
        caption_intervals=(),
        uncovered_speech_threshold=ExactTime(2),
        long_silence_threshold=ExactTime(3),
    )

    assert [(risk.interval, risk.elevated) for risk in evidence.uncovered_speech_risks] == [
        (_interval(0, 1), False),
    ]
    assert evidence.audio_state_indeterminate == ()
