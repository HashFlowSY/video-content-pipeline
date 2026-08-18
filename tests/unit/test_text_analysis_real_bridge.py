"""The real text-semantics bridge: cue loading + engine composition (no model).

Covers load_part_with_cue_texts (both branches read the same candidate) and
build_text_semantics_analysis mapping the engine result onto the report contract,
with the engine monkeypatched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from video_content_pipeline import text_semantics_engine
from video_content_pipeline.text_analysis import (
    RestrictedRawOutput,
    TextAnalysisError,
    TextAnalysisReportStatus,
    build_text_semantics_analysis,
)
from video_content_pipeline.text_generation import load_part_with_cue_texts
from video_content_pipeline.text_semantics_engine import Qwen3TextSemanticsResult
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
from video_content_pipeline.transcription import publish_asr_subtitle_candidate
from video_content_pipeline.transcription_contracts import ProjectedAsrCue


def _publish(tmp_path: Path) -> Path:
    cues = (
        ProjectedAsrCue(0, HalfOpenInterval(ExactTime(0), ExactTime(2)), "hello", (), ()),
        ProjectedAsrCue(1, HalfOpenInterval(ExactTime(2), ExactTime(5)), "world", (), ()),
    )
    candidate_path = tmp_path / "source-candidate.json"
    publish_asr_subtitle_candidate(candidate_path, cues)
    return candidate_path


def test_load_part_with_cue_texts_round_trips_a_published_candidate(tmp_path: Path) -> None:
    candidate_path = _publish(tmp_path)
    part, cue_texts = load_part_with_cue_texts(candidate_path, part_id="part-a", stream_index=1)
    assert len(part.cue_ids) == 2
    assert list(cue_texts.values()) == ["hello", "world"]
    # cue_texts is keyed by the same cue identities the Part carries.
    assert set(cue_texts) == set(part.cue_ids)


def _candidate(high_bytes: int) -> dict[str, object]:
    return {
        "candidate_id": "qwen3-4b-instruct-2507-8bit",
        "eligibility_evidence": {"resource_high_bytes": high_bytes},
    }


def _canned_result(peak: int, status: str = "complete") -> Qwen3TextSemanticsResult:
    return Qwen3TextSemanticsResult(
        source_id="part-a",
        stream_index=1,
        status=status,
        segments=(),
        chapters=(),
        collection_summary=None,
        unsupported_item_count=0,
        diagnostics=(),
        restricted_raw_output=RestrictedRawOutput(Path("/raw.txt"), "a" * 64, 3),
        projection_state={"state": "projected"},
        model_asset_sha256="b" * 64,
        calibration_version="cal-v1",
        peak_memory_bytes=peak,
    )


def test_build_text_semantics_analysis_maps_result_and_records_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate_path = _publish(tmp_path)
    captured: dict[str, object] = {}

    def fake_generate(project_root, workspace_path, contracts, **kwargs):
        captured["cue_texts"] = kwargs["cue_texts"]
        captured["available"] = kwargs["available"]
        return _canned_result(peak=5_000_000_000)

    monkeypatch.setattr(text_semantics_engine, "generate_text_semantics", fake_generate)

    outcome = build_text_semantics_analysis(
        tmp_path,
        tmp_path / "ws",
        object(),  # contracts: passed through to the (patched) engine
        [("part-a", 1, candidate_path)],
        (),
        _candidate(high_bytes=5_051_028_740),
    )

    assert outcome.status == TextAnalysisReportStatus.COMPLETE
    assert list(captured["cue_texts"].values()) == ["hello", "world"]
    assert len(captured["available"]) == 1
    record = outcome.stage_execution[0]
    assert record["capability"] == "text_semantics"
    assert record["state"] == "completed"


def test_build_text_semantics_analysis_over_envelope_is_release_unverified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate_path = _publish(tmp_path)
    monkeypatch.setattr(
        text_semantics_engine,
        "generate_text_semantics",
        lambda *a, **k: _canned_result(peak=99_000_000_000),
    )
    outcome = build_text_semantics_analysis(
        tmp_path,
        tmp_path / "ws",
        object(),
        [("part-a", 1, candidate_path)],
        (),
        _candidate(high_bytes=5_051_028_740),
    )
    assert outcome.stage_execution[0]["state"] == "release_unverified"


def test_build_text_semantics_analysis_maps_invalid_output_to_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate_path = _publish(tmp_path)
    monkeypatch.setattr(
        text_semantics_engine,
        "generate_text_semantics",
        lambda *a, **k: _canned_result(peak=1, status="model_output_invalid"),
    )
    outcome = build_text_semantics_analysis(
        tmp_path,
        tmp_path / "ws",
        object(),
        [("part-a", 1, candidate_path)],
        (),
        _candidate(high_bytes=5_051_028_740),
    )
    assert outcome.status == TextAnalysisReportStatus.FAILED


def test_transcription_transcript_by_source_maps_published_candidates(tmp_path: Path) -> None:
    import json as _json

    from video_content_pipeline.text_analysis import _transcription_transcript_by_source

    report_dir = tmp_path / "work" / "transcription-reports" / "tid"
    report_dir.mkdir(parents=True)
    candidate_path = "/proj/transcript/part-a/source-candidate.json"
    (report_dir / "transcription-report.json").write_text(
        _json.dumps(
            {
                "transcript": [
                    {
                        "source_id": "part-a",
                        "stream_index": 1,
                        "source_candidate": {"path": candidate_path},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    mapping = _transcription_transcript_by_source(tmp_path, "tid")
    assert mapping == {"part-a": (1, Path(candidate_path))}
    # No bound report id => no full-ASR candidates (subtitle-priority runs).
    assert _transcription_transcript_by_source(tmp_path, None) == {}


def test_build_text_semantics_analysis_requires_a_part(tmp_path: Path) -> None:
    with pytest.raises(TextAnalysisError) as error:
        build_text_semantics_analysis(
            tmp_path, tmp_path / "ws", object(), [], (), _candidate(1)
        )
    assert error.value.reason == "text_analysis_input_invalid"
