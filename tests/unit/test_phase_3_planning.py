from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

from video_content_pipeline.external_tools import PinnedExternalTool
from video_content_pipeline.planning import (
    DecodeThroughputProfile,
    PlanningError,
    PlanState,
    build_full_decode_command,
    confirm_run_plan,
    create_plan_report,
    estimate_full_decode,
    load_plan_report,
    persist_plan_report,
)
from video_content_pipeline.source import SourceArtifact, sha256_file


def _artifact(tmp_path: Path) -> SourceArtifact:
    media = tmp_path / "input" / "hash" / "media"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"media")
    digest, byte_count = sha256_file(media)
    return SourceArtifact(digest, digest, byte_count, media)


def test_low_confidence_profile_estimate_has_ordered_three_points() -> None:
    estimate = estimate_full_decode(
        Fraction(120, 1),
        DecodeThroughputProfile("v1", Fraction(8), Fraction(3), Fraction(1)),
    )

    assert estimate.optimistic_seconds == 15
    assert estimate.likely_seconds == 40
    assert estimate.conservative_seconds == 120
    assert estimate.confidence == "low"


def test_report_and_plan_are_persisted_under_separate_ids(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    report = create_plan_report(
        state=PlanState.READY_FOR_CONFIRMATION,
        source_artifacts=(artifact,),
        tools=(),
        planned_increment_bytes=artifact.byte_count,
        configuration_fingerprint="config-v1",
    )

    report_path = persist_plan_report(report, tmp_path / "plans")
    loaded = load_plan_report(report_path)
    plan = confirm_run_plan(report, tmp_path, tmp_path / "plans")

    assert report_path == tmp_path / "plans" / "reports" / report.report_id / "plan-report.json"
    assert loaded == report
    assert (tmp_path / "plans" / plan.plan_id / "run-plan.json").is_file()
    assert plan.report_id == report.report_id


def test_non_ready_report_cannot_create_run_plan(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    report = create_plan_report(
        state=PlanState.AWAITING_DECODE_CONFIRMATION,
        source_artifacts=(artifact,),
        tools=(),
        planned_increment_bytes=artifact.byte_count,
        configuration_fingerprint="config-v1",
    )

    with pytest.raises(PlanningError) as error:
        confirm_run_plan(report, tmp_path, tmp_path / "plans")

    assert error.value.reason == "report_not_ready"


def test_full_decode_command_has_null_output_only(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    ffmpeg = PinnedExternalTool("ffmpeg", Path("/tool/ffmpeg"), "test", "a" * 64)

    assert build_full_decode_command(ffmpeg, artifact)[-3:] == ("-f", "null", "-")
