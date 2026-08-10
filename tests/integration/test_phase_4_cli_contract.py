"""Offline CLI contract for the first Phase 4 subtitle-processing slice."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from video_content_pipeline import cli, subtitle_pipeline
from video_content_pipeline.coverage import StreamCoverage
from video_content_pipeline.external_tools import PinnedExternalTool
from video_content_pipeline.inspection import PlanInspectionEvidence, SubtitleTrackCandidate
from video_content_pipeline.planning import (
    PlanState,
    RunPlan,
    create_plan_report,
    persist_plan_report,
)
from video_content_pipeline.probe import ProbeDocument
from video_content_pipeline.source import SourceArtifact, calculate_disk_headroom, sha256_file
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval


def _confirmed_plan(
    project_root: Path, *, subtitle_codecs: tuple[str, ...] = ("subrip",)
) -> RunPlan:
    media_path = project_root / "input" / "source" / "media"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"synthetic-embedded-subtitle-source")
    digest, byte_count = sha256_file(media_path)
    artifact = SourceArtifact(digest, digest, byte_count, media_path)
    ffmpeg = PinnedExternalTool("ffmpeg", project_root / "controlled-ffmpeg", "fixture", "f" * 64)
    subtitle_streams = [
        {"index": stream_index, "codec_type": "subtitle", "codec_name": codec}
        for stream_index, codec in enumerate(subtitle_codecs, start=1)
    ]
    inspection = PlanInspectionEvidence(
        source_id=artifact.source_id,
        structural_document=ProbeDocument(json.dumps({"streams": subtitle_streams})),
        coverage_document=ProbeDocument('{"packets": []}'),
        coverage_by_stream=(
            (
                0,
                StreamCoverage(
                    coverage=HalfOpenInterval(ExactTime(-1), ExactTime(2)),
                    gaps=(),
                    diagnostics=(),
                ),
            ),
        ),
        subtitle_tracks=tuple(
            SubtitleTrackCandidate(index, "zh", "matroska", "embedded", True)
            for index in range(1, len(subtitle_codecs) + 1)
        ),
    )
    report = create_plan_report(
        state=PlanState.READY_FOR_CONFIRMATION,
        source_artifacts=(artifact,),
        tools=(ffmpeg,),
        planned_increment_bytes=0,
        configuration_fingerprint="phase-03-fixture",
        inspection_evidence=(inspection,),
    )
    persist_plan_report(report, project_root / "plans")
    plan = RunPlan(
        plan_id="confirmed-fixture-plan",
        report_id=report.report_id,
        source_artifacts=(artifact,),
        tools=(ffmpeg,),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint=report.configuration_fingerprint,
    )
    plan_path = project_root / "plans" / plan.plan_id / "run-plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(
        json.dumps(plan.as_json(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return plan


def _configure_cli(project_root: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    (project_root / "config").mkdir()
    (project_root / "config" / "subtitle-rules.json").write_text(
        '{"schema_version": 1, "id": "phase-04-subtitle-rules-v1"}\n', encoding="utf-8"
    )
    extraction_calls: list[tuple[str, ...]] = []

    def controlled_extraction(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        extraction_calls.append(arguments)
        destination = Path(arguments[-1])
        destination.write_bytes(
            b"1\n00:00:00,000 --> 00:00:02,000\n\xe4\xbd\xa0\xe5\xa5\xbd, subtitle.\n"
        )
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(cli, "assert_runtime_policy", lambda: None)
    monkeypatch.setattr(cli, "assert_project_venv", lambda: object())
    monkeypatch.setattr(cli, "_project_root", lambda: project_root)
    monkeypatch.setattr(subtitle_pipeline, "run_tool", controlled_extraction)
    monkeypatch.setattr(subtitle_pipeline, "revalidate_external_tool", lambda _tool: None)
    return extraction_calls


def test_subtitles_rejects_an_unconfirmed_plan_without_reading_subtitle_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    extraction_calls = _configure_cli(tmp_path, monkeypatch)

    assert cli.main(["subtitles", "not-confirmed", "--json"]) == 0
    response = json.loads(capsys.readouterr().out)

    assert response["status"] == "blocked"
    assert response["report"]["diagnostics"][0]["reason"] == "run_plan_not_confirmed"
    assert extraction_calls == []
    report_path = (
        tmp_path / "work" / "subtitle-reports" / response["report"]["report_id"] / "report.json"
    )
    assert json.loads(report_path.read_text(encoding="utf-8")) == response["report"]


def test_subtitles_extracts_and_validates_one_utf8_srt_track_into_a_retained_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    extraction_calls = _configure_cli(tmp_path, monkeypatch)

    assert cli.main(["subtitles", plan.plan_id, "--json"]) == 0
    response = json.loads(capsys.readouterr().out)

    assert response["status"] == "completed"
    report = response["report"]
    assert report["plan_id"] == plan.plan_id
    assert report["state"] == "completed"
    assert report["subtitle_rules_fingerprint"]
    assert len(report["candidates"]) == 1
    candidate = report["candidates"][0]
    assert candidate["state"] == "valid"
    assert candidate["source_format"] == "srt"
    raw_payload = Path(candidate["raw_payload_path"])
    assert raw_payload.read_text(encoding="utf-8") == (
        "1\n00:00:00,000 --> 00:00:02,000\n你好, subtitle.\n"
    )
    assert sha256_file(raw_payload) == (
        candidate["raw_payload_sha256"],
        candidate["raw_payload_bytes"],
    )
    source_candidate = Path(candidate["source_candidate_path"])
    source_candidate_evidence = json.loads(source_candidate.read_text(encoding="utf-8"))
    assert source_candidate_evidence["cues"] == [
        {
            "raw_pts_interval": {
                "end": {"denominator": 1, "numerator": 1},
                "start": {"denominator": 1, "numerator": -1},
            },
            "source_ordinal": 0,
            "text": "你好, subtitle.",
        }
    ]
    assert extraction_calls == [
        (
            str(plan.tools[0].path),
            "-v",
            "error",
            "-nostdin",
            "-n",
            "-i",
            str(plan.source_artifacts[0].media_path),
            "-map",
            "0:1",
            "-c:s",
            "copy",
            "-f",
            "srt",
            str(raw_payload),
        )
    ]
    report_path = Path(report["report_path"])
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    assert not (tmp_path / "outputs").exists()


def test_subtitles_rejects_a_drifted_plan_without_reading_subtitle_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    extraction_calls = _configure_cli(tmp_path, monkeypatch)
    plan.source_artifacts[0].media_path.write_bytes(b"changed-after-confirmation")

    assert cli.main(["subtitles", plan.plan_id, "--json"]) == 0
    response = json.loads(capsys.readouterr().out)

    assert response["status"] == "blocked"
    assert response["report"]["diagnostics"] == [
        {"reason": "source_artifact_changed", "message": "A SourceArtifact hash no longer matches."}
    ]
    assert extraction_calls == []


def test_subtitles_retains_a_partial_failed_extraction_as_an_incomplete_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _configure_cli(tmp_path, monkeypatch)

    def failed_extraction(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        Path(arguments[-1]).write_bytes(b"partial subtitle bytes")
        return subprocess.CompletedProcess(arguments, 1, "", "controlled extraction failure")

    monkeypatch.setattr(subtitle_pipeline, "run_tool", failed_extraction)

    assert cli.main(["subtitles", plan.plan_id, "--json"]) == 0
    response = json.loads(capsys.readouterr().out)

    assert response["status"] == "blocked"
    candidate = response["report"]["candidates"][0]
    assert candidate["state"] == "incomplete"
    raw_payload = Path(candidate["raw_payload_path"])
    assert raw_payload.read_bytes() == b"partial subtitle bytes"
    assert sha256_file(raw_payload) == (
        candidate["raw_payload_sha256"],
        candidate["raw_payload_bytes"],
    )


def test_subtitles_keeps_an_unsupported_second_track_unavailable_without_extracting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path, subtitle_codecs=("subrip", "hdmv_pgs_subtitle"))
    extraction_calls = _configure_cli(tmp_path, monkeypatch)

    assert cli.main(["subtitles", plan.plan_id, "--json"]) == 0
    response = json.loads(capsys.readouterr().out)

    assert response["status"] == "completed"
    assert [candidate["state"] for candidate in response["report"]["candidates"]] == [
        "valid",
        "unavailable",
    ]
    assert len(extraction_calls) == 1
