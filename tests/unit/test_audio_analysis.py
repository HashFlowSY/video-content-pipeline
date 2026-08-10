"""VAD evidence rules independent of model execution."""

from __future__ import annotations

import pytest

from video_content_pipeline.audio_analysis import (
    AlignmentCue,
    AlignmentProposal,
    AudioAnalysisError,
    VoiceActivityCandidateSegment,
    VoiceActivityInterval,
    VoiceActivityState,
    derive_adopted_alignment_timing_view,
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
        uncovered_speech_threshold=ExactTime(1),
        long_silence_threshold=ExactTime(2),
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


def test_adopted_alignment_retains_rejected_cue_original_time_and_legal_overlap() -> None:
    view = derive_adopted_alignment_timing_view(
        source_id="part-a",
        language="en",
        source_cues=(
            AlignmentCue(0, "First cue", _interval(0, 3)),
            AlignmentCue(1, "Second cue", _interval(1, 4)),
        ),
        proposals=(
            AlignmentProposal(0, "First cue", _interval(0, 2), confidence=0.9),
            AlignmentProposal(1, "Second cue", _interval(1, 4), confidence=0.9),
        ),
        usable_audio_intervals=(_interval(0, 5),),
        voice_activity_intervals=(
            VoiceActivityInterval(_interval(0, 2), VoiceActivityState.SPEECH_LIKELY),
            VoiceActivityInterval(_interval(2, 3), VoiceActivityState.NON_SPEECH),
            VoiceActivityInterval(_interval(3, 5), VoiceActivityState.SPEECH_LIKELY),
        ),
        minimum_confidence=0.8,
        duration_rules={"en": (ExactTime(1), ExactTime(6))},
    )

    assert view.state == "adopted"
    assert [cue.source_ordinal for cue in view.cues] == [0, 1]
    assert [cue.interval for cue in view.cues] == [_interval(0, 2), _interval(1, 4)]
    assert view.candidates[0].adopted is True
    assert view.candidates[1].adopted is False
    assert view.candidates[1].reason == "alignment_vad_conflict"
    assert view.cues[1].interval == _interval(1, 4)


def test_adopted_alignment_rejects_changed_text_or_cue_cardinality() -> None:
    with pytest.raises(AudioAnalysisError) as error:
        derive_adopted_alignment_timing_view(
            source_id="part-a",
            language="en",
            source_cues=(AlignmentCue(0, "Original", _interval(0, 2)),),
            proposals=(AlignmentProposal(0, "Changed", _interval(0, 2), confidence=0.9),),
            usable_audio_intervals=(_interval(0, 2),),
            voice_activity_intervals=(
                VoiceActivityInterval(_interval(0, 2), VoiceActivityState.SPEECH_LIKELY),
            ),
            minimum_confidence=0.8,
            duration_rules={"en": (ExactTime(1), ExactTime(4))},
        )

    assert error.value.reason == "alignment_text_contract_violation"


def test_adopted_alignment_rejects_entire_mixed_view_when_times_reorder_source_cues() -> None:
    view = derive_adopted_alignment_timing_view(
        source_id="part-a",
        language="en",
        source_cues=(
            AlignmentCue(0, "First", _interval(0, 2)),
            AlignmentCue(1, "Second", _interval(2, 4)),
        ),
        proposals=(
            AlignmentProposal(0, "First", _interval(2, 3), confidence=0.9),
            AlignmentProposal(1, "Second", _interval(1, 2), confidence=0.9),
        ),
        usable_audio_intervals=(_interval(0, 4),),
        voice_activity_intervals=(
            VoiceActivityInterval(_interval(0, 4), VoiceActivityState.SPEECH_LIKELY),
        ),
        minimum_confidence=0.8,
        duration_rules={"en": (ExactTime(1), ExactTime(4))},
    )

    assert view.state == "alignment_untrusted"
    assert [cue.interval for cue in view.cues] == [_interval(0, 2), _interval(2, 4)]
    assert all(candidate.adopted is False for candidate in view.candidates)
    assert all(candidate.reason == "adopted" for candidate in view.candidates)
    assert all(candidate.global_reason == "alignment_untrusted" for candidate in view.candidates)
