from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

from video_content_pipeline.coverage import StreamCoverage
from video_content_pipeline.external_tools import PinnedExternalTool
from video_content_pipeline.inspection import PlanInspectionEvidence, SubtitleTrackCandidate
from video_content_pipeline.planning import (
    DecodeThroughputProfile,
    PlanningError,
    PlanState,
    ThreePointEstimate,
    build_full_decode_command,
    confirm_run_plan,
    create_plan_report,
    estimate_full_decode,
    load_decode_measurements,
    load_decode_throughput_profile,
    load_plan_report,
    perform_full_decode_validation,
    persist_plan_report,
    record_decode_measurement,
)
from video_content_pipeline.probe import ProbeDocument
from video_content_pipeline.source import SourceArtifact, sha256_file
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval


def _artifact(tmp_path: Path) -> SourceArtifact:
    media = tmp_path / "input" / "hash" / "media"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"media")
    digest, byte_count = sha256_file(media)
    return SourceArtifact(digest, digest, byte_count, media)


def _inspection(artifact: SourceArtifact) -> PlanInspectionEvidence:
    return PlanInspectionEvidence(
        source_id=artifact.source_id,
        structural_document=ProbeDocument('{"streams": []}'),
        coverage_document=ProbeDocument('{"packets": []}'),
        coverage_by_stream=(),
        subtitle_tracks=(),
    )


def test_low_confidence_profile_estimate_has_ordered_three_points() -> None:
    estimate = estimate_full_decode(
        Fraction(120, 1),
        DecodeThroughputProfile("v1", Fraction(8), Fraction(3), Fraction(1)),
    )

    assert estimate.optimistic_seconds == 15
    assert estimate.likely_seconds == 40
    assert estimate.conservative_seconds == 120
    assert estimate.confidence == "low"


def test_matching_observed_decode_history_replaces_the_low_confidence_profile(
    tmp_path: Path,
) -> None:
    history_path = tmp_path / "plans" / "decode-throughput-history.json"
    record_decode_measurement(history_path, "source-id", 17)
    measurement = load_decode_measurements(history_path)[0]

    estimate = estimate_full_decode(
        Fraction(120, 1),
        DecodeThroughputProfile("v1", Fraction(8), Fraction(3), Fraction(1)),
        matching_measurement=measurement,
    )

    assert estimate == ThreePointEstimate(
        optimistic_seconds=17,
        likely_seconds=17,
        conservative_seconds=17,
        confidence="observed",
        basis="decode-history:source-id",
    )


def test_report_and_plan_are_persisted_under_separate_ids(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    report = create_plan_report(
        state=PlanState.READY_FOR_CONFIRMATION,
        source_artifacts=(artifact,),
        tools=(),
        planned_increment_bytes=artifact.byte_count,
        configuration_fingerprint="config-v1",
        inspection_evidence=(_inspection(artifact),),
    )

    report_path = persist_plan_report(report, tmp_path / "plans")
    loaded = load_plan_report(report_path)
    plan = confirm_run_plan(report, tmp_path, tmp_path / "plans")

    assert report_path == tmp_path / "plans" / "reports" / report.report_id / "plan-report.json"
    assert loaded == report
    assert (tmp_path / "plans" / plan.plan_id / "run-plan.json").is_file()
    assert plan.report_id == report.report_id


def test_report_retains_probe_documents_coverage_and_subtitle_metadata(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    inspection_evidence = PlanInspectionEvidence(
        source_id=artifact.source_id,
        structural_document=ProbeDocument('{"streams": [{"index": 0}]}'),
        coverage_document=ProbeDocument('{"packets": [{"stream_index": 0}]}'),
        coverage_by_stream=(
            (
                0,
                StreamCoverage(
                    coverage=HalfOpenInterval(ExactTime(1, 2), ExactTime(3, 2)),
                    gaps=(),
                    diagnostics=(),
                ),
            ),
        ),
        subtitle_tracks=(SubtitleTrackCandidate(1, "en", "webvtt", "embedded", True),),
    )
    report = create_plan_report(
        state=PlanState.AWAITING_DECODE_CONFIRMATION,
        source_artifacts=(artifact,),
        tools=(),
        planned_increment_bytes=artifact.byte_count,
        configuration_fingerprint="config-v1",
        inspection_evidence=(inspection_evidence,),
    )

    report_path = persist_plan_report(report, tmp_path / "plans")
    payload = report.as_json()["inspection_evidence"]

    assert load_plan_report(report_path) == report
    assert payload == [
        {
            "source_id": artifact.source_id,
            "structural_probe_document": {"raw_json": '{"streams": [{"index": 0}]}'},
            "coverage_probe_document": {"raw_json": '{"packets": [{"stream_index": 0}]}'},
            "stream_coverage": [
                {
                    "stream_index": 0,
                    "coverage": {
                        "start": {"numerator": 1, "denominator": 2},
                        "end": {"numerator": 3, "denominator": 2},
                    },
                    "gaps": [],
                    "diagnostics": [],
                }
            ],
            "subtitle_track_candidates": [
                {
                    "stream_index": 1,
                    "language": "en",
                    "container_format": "webvtt",
                    "origin": "embedded",
                    "available": True,
                }
            ],
        }
    ]


def test_non_ready_report_cannot_create_run_plan(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    report = create_plan_report(
        state=PlanState.AWAITING_DECODE_CONFIRMATION,
        source_artifacts=(artifact,),
        tools=(),
        planned_increment_bytes=artifact.byte_count,
        configuration_fingerprint="config-v1",
        inspection_evidence=(_inspection(artifact),),
    )

    with pytest.raises(PlanningError) as error:
        confirm_run_plan(report, tmp_path, tmp_path / "plans")

    assert error.value.reason == "report_not_ready"


def test_report_rejects_source_artifacts_without_one_matching_inspection(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)

    with pytest.raises(PlanningError) as error:
        create_plan_report(
            state=PlanState.BLOCKED,
            source_artifacts=(artifact,),
            tools=(),
            planned_increment_bytes=artifact.byte_count,
            configuration_fingerprint="config-v1",
        )

    assert error.value.reason == "inspection_evidence_invalid"


def test_full_decode_command_has_null_output_only(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    ffmpeg = PinnedExternalTool("ffmpeg", Path("/tool/ffmpeg"), "test", "a" * 64)

    assert build_full_decode_command(ffmpeg, artifact) == (
        "/tool/ffmpeg",
        "-v",
        "error",
        "-xerror",
        "-i",
        str(artifact.media_path),
        "-map",
        "0:v?",
        "-map",
        "0:a?",
        "-f",
        "null",
        "-",
    )


def test_decode_profile_rejects_non_positive_throughput(tmp_path: Path) -> None:
    profile_path = tmp_path / "decode-throughput-profiles.json"
    profile_path.write_text(
        """{
  \"profiles\": [{
    \"id\": \"phase-03-default-v1\",
    \"optimistic_realtime_factor\": \"8\",
    \"likely_realtime_factor\": \"0\",
    \"conservative_realtime_factor\": \"1\"
  }]
}
""",
        encoding="utf-8",
    )

    with pytest.raises(PlanningError) as error:
        load_decode_throughput_profile(profile_path)

    assert error.value.reason == "decode_profile_invalid"


def test_full_decode_start_failure_is_reported_as_a_planning_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _artifact(tmp_path)
    ffmpeg = PinnedExternalTool("ffmpeg", Path("/tool/ffmpeg"), "test", "a" * 64)

    def unavailable_tool(*_args: object) -> object:
        raise OSError("FFmpeg is unavailable")

    monkeypatch.setattr("video_content_pipeline.planning.run_tool", unavailable_tool)

    with pytest.raises(PlanningError) as error:
        perform_full_decode_validation(ffmpeg, artifact)

    assert error.value.reason == "full_decode_failed"
