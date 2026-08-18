"""The full-ASR transcript publishes a subtitle candidate downstream can read.

A cross-module round trip: the transcription stage publishes an ASR transcript as a
subtitle source-candidate, and the enhancement stage's retained-subtitle-cue loader
reads it back unchanged. This pins the two ends of the full-ASR handoff to one
schema so a drift on either side fails a test, not run #1.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from video_content_pipeline.enhancement import load_retained_subtitle_cues
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
from video_content_pipeline.transcription import (
    AudioReportBinding,
    TranscriptionReport,
    TranscriptionReportStatus,
    TranscriptionRevalidation,
    publish_asr_subtitle_candidate,
)
from video_content_pipeline.transcription_contracts import ProjectedAsrCue


def _cue(ordinal: int, start: int, end: int, text: str) -> ProjectedAsrCue:
    return ProjectedAsrCue(
        ordinal=ordinal,
        interval=HalfOpenInterval(ExactTime(start), ExactTime(end)),
        text=text,
        tokens=(),
        language_spans=(),
    )


def test_published_asr_candidate_round_trips_through_enhancement(tmp_path: Path) -> None:
    cues = (_cue(0, 0, 2, "hello"), _cue(1, 2, 5, "world"))
    candidate_path = tmp_path / "source-candidate.json"

    evidence, count = publish_asr_subtitle_candidate(candidate_path, cues)

    assert count == 2
    assert evidence.path == candidate_path
    # The enhancement stage reads exactly this file as its cue basis.
    retained = load_retained_subtitle_cues(candidate_path, part_id="part-a", stream_index=1)
    assert [cue.source_ordinal for cue in retained] == [0, 1]
    assert [cue.text for cue in retained] == ["hello", "world"]
    assert retained[0].interval == HalfOpenInterval(ExactTime(0), ExactTime(2))


def test_publish_is_write_once(tmp_path: Path) -> None:
    candidate_path = tmp_path / "source-candidate.json"
    publish_asr_subtitle_candidate(candidate_path, (_cue(0, 0, 2, "hi"),))
    # Re-publishing identical content is idempotent; differing content is rejected.
    publish_asr_subtitle_candidate(candidate_path, (_cue(0, 0, 2, "hi"),))
    with pytest.raises(Exception):
        publish_asr_subtitle_candidate(candidate_path, (_cue(0, 0, 2, "changed"),))


def test_transcription_report_carries_transcript_and_stage_execution() -> None:
    report = TranscriptionReport(
        report_id="r",
        plan_id="p",
        subtitle_report_id="s",
        audio_report_id="a",
        status=TranscriptionReportStatus.COMPLETE,
        workspace_path=Path("/w"),
        report_path=Path("/w/report.json"),
        run_plan_evidence=None,
        subtitle_report_evidence=None,
        audio_report_evidence=None,
        model_registry_evidence=None,
        resumed_from_report=None,
        resumed_from_report_id=None,
        resumption_decision=None,
        start_precondition=None,
        revalidation=TranscriptionRevalidation(True, None, ()),
        audio_analysis=AudioReportBinding("not_available"),
        capabilities=(),
        independent_review=None,
        required_decision=None,
        diagnostics=(),
        transcript=({"source_id": "part-a", "cue_count": 2},),
        stage_execution=({"capability": "asr_primary", "state": "completed"},),
    )
    document = report.as_json()
    assert document["transcript"] == [{"source_id": "part-a", "cue_count": 2}]
    assert document["stage_execution"] == [{"capability": "asr_primary", "state": "completed"}]
    # Pre-execution states default to empty, leaving the offline document unchanged.
    assert (
        TranscriptionReport(
            report_id="r",
            plan_id="p",
            subtitle_report_id="s",
            audio_report_id="a",
            status=TranscriptionReportStatus.MODEL_ACQUISITION_REQUIRED,
            workspace_path=Path("/w"),
            report_path=Path("/w/report.json"),
            run_plan_evidence=None,
            subtitle_report_evidence=None,
            audio_report_evidence=None,
            model_registry_evidence=None,
            resumed_from_report=None,
            resumed_from_report_id=None,
            resumption_decision=None,
            start_precondition=None,
            revalidation=TranscriptionRevalidation(True, None, ()),
            audio_analysis=AudioReportBinding("not_available"),
            capabilities=(),
            independent_review=None,
            required_decision=None,
            diagnostics=(),
        ).as_json()["transcript"]
        == []
    )
