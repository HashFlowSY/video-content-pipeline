"""Offline CLI contract for the first Phase 5 audio-analysis slice."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_content_pipeline import audio_analysis, cli
from video_content_pipeline.inspection import PlanInspectionEvidence
from video_content_pipeline.planning import (
    PlanState,
    RunPlan,
    create_plan_report,
    persist_plan_report,
)
from video_content_pipeline.probe import ProbeDocument
from video_content_pipeline.source import (
    SourceArtifact,
    calculate_disk_headroom,
    sha256_file,
)
from video_content_pipeline.subtitle_pipeline import (
    CandidateReportState,
    CandidateState,
    SubtitleCandidate,
    SubtitleCandidateReport,
    subtitle_rules_fingerprint,
)


def _confirmed_plan(project_root: Path) -> RunPlan:
    media_path = project_root / "input" / "source" / "synthetic-media"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"phase-5-cli-contract-fixture")
    digest, byte_count = sha256_file(media_path)
    artifact = SourceArtifact(digest, digest, byte_count, media_path)
    evidence = PlanInspectionEvidence(
        source_id=artifact.source_id,
        structural_document=ProbeDocument('{"streams": []}'),
        coverage_document=ProbeDocument('{"packets": []}'),
        coverage_by_stream=(),
        subtitle_tracks=(),
    )
    plan_report = create_plan_report(
        state=PlanState.READY_FOR_CONFIRMATION,
        source_artifacts=(artifact,),
        tools=(),
        planned_increment_bytes=0,
        configuration_fingerprint="phase-03-fixture",
        inspection_evidence=(evidence,),
    )
    persist_plan_report(plan_report, project_root / "plans")
    plan = RunPlan(
        plan_id="confirmed-phase-5-fixture-plan",
        report_id=plan_report.report_id,
        source_artifacts=(artifact,),
        tools=(),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint=plan_report.configuration_fingerprint,
    )
    plan_path = project_root / "plans" / plan.plan_id / "run-plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(
        json.dumps(plan.as_json(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return plan


def _retained_subtitle_report(project_root: Path, plan: RunPlan) -> SubtitleCandidateReport:
    report_id = "1" * 32
    report_path = project_root / "work" / plan.source_artifacts[0].source_id / report_id
    report_path = report_path / "candidate-report.json"
    rules_path = project_root / "config" / "subtitle-rules.json"
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text(
        '{"schema_version": 1, "id": "phase-04-fixture-rules"}\n', encoding="utf-8"
    )
    source_artifact_path = report_path.parent / "source.vtt"
    readable_artifact_path = report_path.parent / "readable.vtt"
    source_artifact_path.parent.mkdir(parents=True)
    source_artifact_path.write_text("WEBVTT\n\n", encoding="utf-8")
    readable_artifact_path.write_text("WEBVTT\n\n", encoding="utf-8")
    report = SubtitleCandidateReport(
        report_id=report_id,
        plan_id=plan.plan_id,
        state=CandidateReportState.COMPLETED,
        subtitle_rules_fingerprint=subtitle_rules_fingerprint(project_root),
        candidates=(
            SubtitleCandidate(
                source_id=plan.source_artifacts[0].source_id,
                stream_index=1,
                state=CandidateState.VALID,
                source_vtt_path=source_artifact_path.as_posix(),
                readable_vtt_path=readable_artifact_path.as_posix(),
            ),
        ),
        diagnostics=(),
        report_path=report_path,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report.as_json(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return report


def _configure_cli(project_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "assert_runtime_policy", lambda: None)
    monkeypatch.setattr(cli, "assert_project_venv", lambda: object())
    monkeypatch.setattr(cli, "_project_root", lambda: project_root)


def test_analyze_audio_retains_a_model_acquisition_required_report_without_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    _configure_cli(tmp_path, monkeypatch)
    plan_path = tmp_path / "plans" / plan.plan_id / "run-plan.json"
    plan_before = plan_path.read_bytes()
    subtitles_before = subtitle_report.report_path.read_bytes()
    phase_4_artifacts_before = {
        path: path.read_bytes()
        for path in (
            Path(subtitle_report.candidates[0].source_vtt_path or ""),
            Path(subtitle_report.candidates[0].readable_vtt_path or ""),
        )
    }

    assert cli.main(["analyze-audio", plan.plan_id, subtitle_report.report_id, "--json"]) == 0
    response = json.loads(capsys.readouterr().out)

    assert response["status"] == "blocked"
    report = response["report"]
    assert report["state"] == "blocked"
    assert report["processing_authorization"]["state"] == "not_started"
    assert report["plan_id"] == plan.plan_id
    assert report["subtitle_report_id"] == subtitle_report.report_id
    assert [capability["state"] for capability in report["capabilities"]] == [
        "model_acquisition_required",
        "model_acquisition_required",
        "model_acquisition_required",
    ]
    assert [capability["capability"] for capability in report["capabilities"]] == [
        "vad",
        "forced_alignment",
        "diarization",
    ]
    assert report["guarantees"] == {
        "asr": "not_attempted",
        "model_acquisition": "not_attempted",
        "model_execution": "not_attempted",
        "network_access": "not_attempted",
        "outputs_publication": "not_attempted",
        "phase_4_artifact_mutation": "not_attempted",
        "run_plan_mutation": "not_attempted",
    }
    report_path = Path(report["report_path"])
    assert report_path.parent == Path(report["workspace_path"])
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    assert plan_path.read_bytes() == plan_before
    assert subtitle_report.report_path.read_bytes() == subtitles_before
    assert {
        path: path.read_bytes() for path in phase_4_artifacts_before
    } == phase_4_artifacts_before
    assert not (tmp_path / "outputs").exists()


def test_analyze_audio_rejects_a_retained_subtitle_report_for_another_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    invalid_report = subtitle_report.as_json()
    invalid_report["plan_id"] = "another-confirmed-plan"
    subtitle_report.report_path.write_text(
        json.dumps(invalid_report, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    _configure_cli(tmp_path, monkeypatch)

    assert cli.main(["analyze-audio", plan.plan_id, subtitle_report.report_id, "--json"]) == 0
    response = json.loads(capsys.readouterr().out)

    assert response["status"] == "blocked"
    assert response["report"]["capabilities"] == []
    assert response["report"]["diagnostics"] == [
        {
            "reason": "subtitle_report_mismatch",
            "message": "Subtitle candidate report does not belong to this RunPlan.",
        }
    ]


def test_analyze_audio_does_not_read_source_artifact_before_model_availability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    original_sha256_file = audio_analysis.sha256_file
    hashed_paths: list[Path] = []

    def record_hash(path: Path) -> tuple[str, int]:
        hashed_paths.append(path)
        return original_sha256_file(path)

    monkeypatch.setattr(audio_analysis, "sha256_file", record_hash)
    _configure_cli(tmp_path, monkeypatch)

    assert cli.main(["analyze-audio", plan.plan_id, subtitle_report.report_id, "--json"]) == 0
    response = json.loads(capsys.readouterr().out)

    assert response["report"]["processing_authorization"]["state"] == "not_started"
    assert plan.source_artifacts[0].media_path not in hashed_paths


def test_analyze_audio_rejects_a_run_plan_with_changed_confirmation_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    plan_path = tmp_path / "plans" / plan.plan_id / "run-plan.json"
    invalid_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    invalid_plan["configuration_fingerprint"] = "changed-planning-configuration"
    plan_path.write_text(json.dumps(invalid_plan), encoding="utf-8")
    _configure_cli(tmp_path, monkeypatch)

    assert cli.main(["analyze-audio", plan.plan_id, subtitle_report.report_id, "--json"]) == 0
    response = json.loads(capsys.readouterr().out)

    assert response["report"]["capabilities"] == []
    assert response["report"]["diagnostics"][0]["reason"] == "run_plan_not_confirmed"


def test_analyze_audio_reports_non_acquiring_registered_capability_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    registry_path = tmp_path / "models" / "registry.json"
    registry_path.parent.mkdir()
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "models": [
                    {"capability": "vad", "status": "model_credential_gated"},
                    {"capability": "forced_alignment", "status": "model_unavailable"},
                    {"capability": "diarization", "status": "model_ineligible"},
                ],
            }
        ),
        encoding="utf-8",
    )
    _configure_cli(tmp_path, monkeypatch)

    assert cli.main(["analyze-audio", plan.plan_id, subtitle_report.report_id, "--json"]) == 0
    response = json.loads(capsys.readouterr().out)

    assert [capability["state"] for capability in response["report"]["capabilities"]] == [
        "model_credential_gated",
        "model_unavailable",
        "model_ineligible",
    ]
    assert response["report"]["guarantees"]["model_acquisition"] == "not_attempted"
