"""Offline CLI contract for Phase 6 ticket 02.

Ticket 02 adds the ``vcp analyze-text`` and explicit ``vcp resume-text-analysis``
public commands and completes input revalidation: a confirmed RunPlan and its
PlanReport, retained subtitle selection and every selected Primary track, the
versioned subtitle and text-analysis rules, and an optional Audio analysis
report binding. No Controlled offline text adapter exists yet, so a fully
revalidated attempt still retains ``controlled_adapter_unavailable`` while any
drift blocks the attempt as ``failed``. These tests drive the CLI and assert
deterministic contract properties and no-side-effect guarantees.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from video_content_pipeline import cli, text_analysis
from video_content_pipeline.inspection import PlanInspectionEvidence
from video_content_pipeline.planning import (
    PlanState,
    RunPlan,
    create_plan_report,
    inspection_evidence_fingerprints,
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


def _write_text_analysis_rules(project_root: Path) -> None:
    rules_path = project_root / "config" / "text-analysis-rules.json"
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    if rules_path.exists():
        return
    rules_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "phase-06-fixture-rules",
                "cue_rules_version": "phase-06-cue-rules-fixture",
                "prompt_template_version": "phase-06-prompt-fixture",
                "output_schema_version": "phase-06-output-schema-fixture",
                "evidence_rules_version": "phase-06-evidence-rules-fixture",
                "controlled_adapter_identity": "phase-06-controlled-text-adapter-fixture",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_text_generation_contracts(project_root)


def _write_text_generation_contracts(project_root: Path) -> None:
    """Provision the versioned prompt, schema, evidence-rule, and adapter artifacts."""

    contract_dir = project_root / "config" / "text-analysis"
    contract_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "prompt-template.json": {
            "schema_version": 1,
            "version": "phase-06-prompt-fixture",
        },
        "output-schema.json": {
            "schema_version": 1,
            "version": "phase-06-output-schema-fixture",
            "envelope": {
                "expected_schema_version": 1,
                "required_fields": [
                    "schema_version",
                    "output_schema_version",
                    "adapter_identity",
                    "result",
                ],
                "result": {
                    "required_fields": ["parts"],
                    "list_fields": ["parts"],
                    "optional_object_or_null_fields": ["collection_summary"],
                },
            },
        },
        "evidence-rules.json": {
            "schema_version": 1,
            "version": "phase-06-evidence-rules-fixture",
        },
        "controlled-adapter.json": {
            "schema_version": 1,
            "version": "phase-06-controlled-text-adapter-fixture",
            "prompt_template_version": "phase-06-prompt-fixture",
            "output_schema_version": "phase-06-output-schema-fixture",
            "evidence_rules_version": "phase-06-evidence-rules-fixture",
        },
    }
    for name, payload in artifacts.items():
        (contract_dir / name).write_text(
            json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8"
        )


def _confirmed_plan(project_root: Path) -> RunPlan:
    media_path = project_root / "input" / "source" / "synthetic-media"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"phase-6-cli-contract-fixture")
    digest, byte_count = sha256_file(media_path)
    artifact = SourceArtifact(
        digest, digest, byte_count, media_path, origin_kind="synthetic_fixture"
    )
    evidence = PlanInspectionEvidence(
        source_id=artifact.source_id,
        structural_document=ProbeDocument(
            json.dumps({"streams": [{"index": 1, "codec_type": "subtitle"}]})
        ),
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
        plan_id="confirmed-phase-6-fixture-plan",
        report_id=plan_report.report_id,
        source_artifacts=(artifact,),
        tools=(),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint=plan_report.configuration_fingerprint,
        inspection_evidence_fingerprints=inspection_evidence_fingerprints((evidence,)),
    )
    plan_path = project_root / "plans" / plan.plan_id / "run-plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(
        json.dumps(plan.as_json(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return plan


def _retained_subtitle_report(
    project_root: Path,
    plan: RunPlan,
    *,
    candidates: tuple[SubtitleCandidate, ...] | None = None,
    state: CandidateReportState = CandidateReportState.COMPLETED,
) -> SubtitleCandidateReport:
    _write_text_analysis_rules(project_root)
    report_id = "1" * 32
    report_path = (
        project_root
        / "work"
        / plan.source_artifacts[0].source_id
        / report_id
        / "candidate-report.json"
    )
    rules_path = project_root / "config" / "subtitle-rules.json"
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text(
        '{"schema_version": 1, "id": "phase-04-fixture-rules"}\n', encoding="utf-8"
    )
    if candidates is None:
        source_candidate_path = report_path.parent / "source-candidate.json"
        source_artifact_path = report_path.parent / "source.vtt"
        readable_artifact_path = report_path.parent / "readable.vtt"
        source_candidate_path.parent.mkdir(parents=True)
        source_artifact_path.write_text("WEBVTT\n\n", encoding="utf-8")
        readable_artifact_path.write_text("WEBVTT\n\n", encoding="utf-8")
        source_candidate_path.write_text(
            json.dumps({"schema_version": 1, "cues": []}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        candidates = (
            SubtitleCandidate(
                source_id=plan.source_artifacts[0].source_id,
                stream_index=1,
                state=CandidateState.VALID,
                source_candidate_path=source_candidate_path.as_posix(),
                source_candidate_sha256=sha256(source_candidate_path.read_bytes()).hexdigest(),
                source_vtt_path=source_artifact_path.as_posix(),
                readable_vtt_path=readable_artifact_path.as_posix(),
                raw_pts_cue_intervals=(),
            ),
        )
    report = SubtitleCandidateReport(
        report_id=report_id,
        plan_id=plan.plan_id,
        state=state,
        subtitle_rules_fingerprint=subtitle_rules_fingerprint(project_root),
        candidates=candidates,
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


def _run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    project_root: Path,
    argv: list[str],
) -> tuple[int, dict[str, object]]:
    _configure_cli(project_root, monkeypatch)
    code = cli.main(argv)
    return code, json.loads(capsys.readouterr().out)


def test_analyze_text_cli_retains_controlled_adapter_unavailable_without_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    plan_path = tmp_path / "plans" / plan.plan_id / "run-plan.json"
    plan_before = plan_path.read_bytes()
    subtitles_before = subtitle_report.report_path.read_bytes()
    candidate_before = Path(subtitle_report.candidates[0].source_candidate_path or "").read_bytes()

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["analyze-text", plan.plan_id, subtitle_report.report_id, "--json"],
    )

    assert code == 0
    assert response["status"] == "controlled_adapter_unavailable"
    report = response["report"]
    assert report["status"] == "controlled_adapter_unavailable"
    assert report["plan_id"] == plan.plan_id
    assert report["subtitle_report_id"] == subtitle_report.report_id
    assert report["controlled_text_adapter"]["state"] == "controlled_adapter_unavailable"
    assert report["segments"] == []
    assert report["chapters"] == []
    assert report["collection_summary"] is None
    assert report["audio_analysis"] == {"state": "not_available"}
    assert report["audio_completeness"] == "not_verified"
    assert report["input_evidence"]["audio_analysis_report"] is None
    assert report["input_evidence"]["resumed_from_report"] is None
    assert report["input_evidence"]["resumption_decision"] is None
    revalidation = report["revalidation"]
    assert revalidation["run_plan_confirmed"] is True
    assert revalidation["subtitle_rules_fingerprint"] == subtitle_rules_fingerprint(tmp_path)
    assert revalidation["text_analysis_rules_fingerprint"] == (
        text_analysis.text_analysis_rules_fingerprint(tmp_path)
    )
    assert revalidation["selected_primary_tracks"] == [
        {
            "source_id": plan.source_artifacts[0].source_id,
            "stream_index": 1,
            "sha256": subtitle_report.candidates[0].source_candidate_sha256,
        }
    ]
    assert report["diagnostics"] == [
        {
            "reason": "controlled_adapter_unavailable",
            "message": (
                "No Controlled offline text adapter is available; no semantic "
                "content was generated."
            ),
        }
    ]

    report_path = Path(report["report_path"])
    contracts = report["text_generation_contracts"]
    assert contracts["prompt_template"]["version"] == "phase-06-prompt-fixture"
    assert contracts["output_schema"]["version"] == "phase-06-output-schema-fixture"
    assert contracts["evidence_rules"]["version"] == "phase-06-evidence-rules-fixture"
    assert contracts["controlled_adapter"]["version"] == (
        "phase-06-controlled-text-adapter-fixture"
    )
    prompt_bytes = (tmp_path / "config" / "text-analysis" / "prompt-template.json").read_bytes()
    assert contracts["prompt_template"]["sha256"] == sha256(prompt_bytes).hexdigest()

    rendered = report["rendered_report"]
    assert rendered["version"] == "phase-06-text-report-renderer-v1"
    markdown_path = Path(rendered["path"])
    markdown_text = markdown_path.read_text(encoding="utf-8")
    assert rendered["sha256"] == sha256(markdown_text.encode("utf-8")).hexdigest()
    assert rendered["byte_count"] == len(markdown_text.encode("utf-8"))
    assert "not_verified" in markdown_text
    assert markdown_path.parent == report_path.parent

    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    assert plan_path.read_bytes() == plan_before
    assert subtitle_report.report_path.read_bytes() == subtitles_before
    assert Path(subtitle_report.candidates[0].source_candidate_path or "").read_bytes() == (
        candidate_before
    )
    assert not (tmp_path / "outputs").exists()


def test_analyze_text_cli_binds_an_optional_audio_analysis_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    audio_report_id = "2" * 32
    audio_report_path = (
        tmp_path
        / "work"
        / "audio-analysis-reports"
        / audio_report_id
        / "audio-analysis-report.json"
    )
    audio_report_path.parent.mkdir(parents=True)
    audio_report_path.write_text(
        json.dumps(
            {
                "report_id": audio_report_id,
                "plan_id": plan.plan_id,
                "subtitle_report_id": subtitle_report.report_id,
                "state": "complete",
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        [
            "analyze-text",
            plan.plan_id,
            subtitle_report.report_id,
            "--audio-report",
            audio_report_id,
            "--json",
        ],
    )

    assert code == 0
    report = response["report"]
    assert report["status"] == "controlled_adapter_unavailable"
    assert report["audio_analysis"] == {
        "state": "bound",
        "report_id": audio_report_id,
        "plan_id": plan.plan_id,
        "subtitle_report_id": subtitle_report.report_id,
    }
    assert report["audio_completeness"] == "not_verified"
    assert report["input_evidence"]["audio_analysis_report"]["sha256"] == (
        sha256(audio_report_path.read_bytes()).hexdigest()
    )


def test_analyze_text_cli_rejects_an_audio_report_for_another_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    audio_report_id = "2" * 32
    audio_report_path = (
        tmp_path
        / "work"
        / "audio-analysis-reports"
        / audio_report_id
        / "audio-analysis-report.json"
    )
    audio_report_path.parent.mkdir(parents=True)
    audio_report_path.write_text(
        json.dumps(
            {
                "report_id": audio_report_id,
                "plan_id": "a-different-plan",
                "subtitle_report_id": subtitle_report.report_id,
                "state": "complete",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    _, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        [
            "analyze-text",
            plan.plan_id,
            subtitle_report.report_id,
            "--audio-report",
            audio_report_id,
            "--json",
        ],
    )

    report = response["report"]
    assert report["status"] == "failed"
    assert report["diagnostics"][0]["reason"] == "audio_report_mismatch"
    assert report["audio_analysis"] == {"state": "not_available"}


def test_analyze_text_cli_blocks_when_the_confirmed_plan_report_drifts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    plan_report_path = tmp_path / "plans" / "reports" / plan.report_id / "plan-report.json"
    drifted = json.loads(plan_report_path.read_text(encoding="utf-8"))
    drifted["configuration_fingerprint"] = "changed-planning-configuration"
    plan_report_path.write_text(
        json.dumps(drifted, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )

    _, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["analyze-text", plan.plan_id, subtitle_report.report_id, "--json"],
    )

    report = response["report"]
    assert report["status"] == "failed"
    assert report["diagnostics"][0]["reason"] == "run_plan_not_confirmed"
    assert report["revalidation"]["run_plan_confirmed"] is False


def test_analyze_text_cli_blocks_on_inspection_evidence_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    plan_report_path = tmp_path / "plans" / "reports" / plan.report_id / "plan-report.json"
    drifted = json.loads(plan_report_path.read_text(encoding="utf-8"))
    drifted["inspection_evidence"][0]["structural_probe_document"]["raw_json"] = json.dumps(
        {"streams": [{"index": 9, "codec_type": "subtitle"}]}
    )
    plan_report_path.write_text(
        json.dumps(drifted, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )

    _, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["analyze-text", plan.plan_id, subtitle_report.report_id, "--json"],
    )

    report = response["report"]
    assert report["status"] == "failed"
    assert report["diagnostics"][0]["reason"] == "inspection_evidence_changed"


def test_analyze_text_cli_blocks_on_subtitle_rules_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    (tmp_path / "config" / "subtitle-rules.json").write_text(
        '{"schema_version": 1, "id": "phase-04-DRIFTED-rules"}\n', encoding="utf-8"
    )

    _, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["analyze-text", plan.plan_id, subtitle_report.report_id, "--json"],
    )

    report = response["report"]
    assert report["status"] == "failed"
    assert report["diagnostics"][0]["reason"] == "subtitle_rules_changed"


def test_analyze_text_cli_blocks_on_selected_primary_track_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    candidate_path = Path(subtitle_report.candidates[0].source_candidate_path or "")
    candidate_path.write_text(
        json.dumps({"schema_version": 1, "cues": ["tampered"]}, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    _, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["analyze-text", plan.plan_id, subtitle_report.report_id, "--json"],
    )

    report = response["report"]
    assert report["status"] == "failed"
    assert report["diagnostics"][0]["reason"] == "subtitle_track_changed"


def test_analyze_text_cli_blocks_when_text_analysis_rules_are_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    (tmp_path / "config" / "text-analysis-rules.json").unlink()

    _, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["analyze-text", plan.plan_id, subtitle_report.report_id, "--json"],
    )

    report = response["report"]
    assert report["status"] == "failed"
    assert report["diagnostics"][0]["reason"] == "text_analysis_rules_invalid"


def test_analyze_text_cli_blocks_when_a_generation_contract_drifts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    output_schema_path = tmp_path / "config" / "text-analysis" / "output-schema.json"
    drifted = json.loads(output_schema_path.read_text(encoding="utf-8"))
    drifted["version"] = "phase-06-output-schema-DRIFTED"
    output_schema_path.write_text(json.dumps(drifted, sort_keys=True) + "\n", encoding="utf-8")

    _, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["analyze-text", plan.plan_id, subtitle_report.report_id, "--json"],
    )

    report = response["report"]
    assert report["status"] == "failed"
    assert report["diagnostics"][0]["reason"] == "output_schema_invalid"
    assert report["text_generation_contracts"] is None


def test_analyze_text_cli_blocks_when_a_generation_contract_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    (tmp_path / "config" / "text-analysis" / "controlled-adapter.json").unlink()

    _, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["analyze-text", plan.plan_id, subtitle_report.report_id, "--json"],
    )

    report = response["report"]
    assert report["status"] == "failed"
    assert report["diagnostics"][0]["reason"] == "controlled_adapter_invalid"


def test_analyze_text_cli_blocks_when_subtitle_selection_is_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(
        tmp_path, plan, state=CandidateReportState.AWAITING_SUBTITLE_SELECTION
    )

    _, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["analyze-text", plan.plan_id, subtitle_report.report_id, "--json"],
    )

    report = response["report"]
    assert report["status"] == "failed"
    assert report["diagnostics"][0]["reason"] == "subtitle_selection_unresolved"


def test_analyze_text_cli_does_not_read_source_media(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    original_sha256_file = text_analysis.sha256_file
    hashed_paths: list[Path] = []

    def record_hash(path: Path) -> tuple[str, int]:
        hashed_paths.append(path)
        return original_sha256_file(path)

    monkeypatch.setattr(text_analysis, "sha256_file", record_hash)

    _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["analyze-text", plan.plan_id, subtitle_report.report_id, "--json"],
    )

    assert plan.source_artifacts[0].media_path not in hashed_paths


def test_resume_text_analysis_cli_requires_a_resumable_pause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    _, created = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["analyze-text", plan.plan_id, subtitle_report.report_id, "--json"],
    )
    report_id = created["report"]["report_id"]
    report_before = Path(created["report"]["report_path"]).read_bytes()

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["resume-text-analysis", report_id, "--decision", "model_release_verified", "--json"],
    )

    assert code == 2
    assert response["status"] == "error"
    assert response["reason"] == "text_analysis_resume_invalid"
    assert Path(created["report"]["report_path"]).read_bytes() == report_before
    assert not (tmp_path / "outputs").exists()


def test_resume_text_analysis_cli_rejects_a_missing_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["resume-text-analysis", "3" * 32, "--decision", "model_release_verified", "--json"],
    )

    assert code == 2
    assert response["status"] == "error"
    assert response["reason"] == "text_analysis_report_invalid"
