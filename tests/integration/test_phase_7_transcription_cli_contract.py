"""Offline CLI contract for Phase 7 ticket 02.

Ticket 02 adds the ``vcp transcribe`` and explicit ``vcp resume-transcription``
public commands and establishes the immutable transcription workspace. Before an
attempt proceeds it exactly revalidates the confirmed RunPlan and its PlanReport
(and, through them, the SourceArtifact hashes), the retained subtitle candidate
report and its rules, and the *required* Audio analysis report; any drift blocks
the attempt as ``failed``. The full-ASR start precondition is either a retained
``subtitle_unavailable_requires_asr_plan`` handoff or an explicit
whole-selection upgrade -- a subtitle-priority run never triggers ASR
automatically. A subtitle-unavailable source pauses at the Full-ASR resource
confirmation before any execution, and a conservative resource estimate over the
24 GiB envelope pauses instead of silently changing model or quantization. No
model is downloaded or executed; the terminal happy-path outcome is
``model_acquisition_required``.

These tests drive the CLI and assert deterministic contract properties, the
recorded pauses and their resumes, workspace immutability, and the no-side-effect
guarantees block.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from video_content_pipeline import cli, evidence
from video_content_pipeline.inspection import PlanInspectionEvidence
from video_content_pipeline.planning import (
    PlanningDiagnostic,
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
    SubtitleCandidateReport,
    SubtitlePartReport,
    SubtitlePartState,
    subtitle_rules_fingerprint,
)

_CONFIRM_DECISION = "full_asr_resource_plan_confirmed"
_RESOURCE_DECISION = "resource_configuration_changed"
_GUARANTEES = {
    "model_acquisition": "not_attempted",
    "model_execution": "not_attempted",
    "network_access": "not_attempted",
    "outputs_publication": "not_attempted",
}


def _confirmed_plan(project_root: Path) -> RunPlan:
    media_path = project_root / "input" / "source" / "synthetic-media"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"phase-7-cli-contract-fixture")
    digest, byte_count = sha256_file(media_path)
    artifact = SourceArtifact(
        digest, digest, byte_count, media_path, origin_kind="synthetic_fixture"
    )
    evidence_record = PlanInspectionEvidence(
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
        inspection_evidence=(evidence_record,),
    )
    persist_plan_report(plan_report, project_root / "plans")
    plan = RunPlan(
        plan_id="confirmed-phase-7-fixture-plan",
        report_id=plan_report.report_id,
        source_artifacts=(artifact,),
        tools=(),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint=plan_report.configuration_fingerprint,
        inspection_evidence_fingerprints=inspection_evidence_fingerprints((evidence_record,)),
    )
    plan_path = project_root / "plans" / plan.plan_id / "run-plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(
        json.dumps(plan.as_json(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return plan


def _write_subtitle_rules(project_root: Path) -> None:
    rules_path = project_root / "config" / "subtitle-rules.json"
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text(
        '{"schema_version": 1, "id": "phase-04-fixture-rules"}\n', encoding="utf-8"
    )


def _subtitle_report(
    project_root: Path,
    plan: RunPlan,
    *,
    unavailable: bool,
    report_id: str = "1" * 32,
    plan_id: str | None = None,
) -> SubtitleCandidateReport:
    """Retain a subtitle report either flagged for ASR or resolved from subtitles."""

    _write_subtitle_rules(project_root)
    source_id = plan.source_artifacts[0].source_id
    report_path = project_root / "work" / source_id / report_id / "candidate-report.json"
    if unavailable:
        part = SubtitlePartReport(
            source_id,
            SubtitlePartState.SUBTITLE_UNAVAILABLE_REQUIRES_ASR_PLAN,
            None,
            None,
            None,
            (),
            PlanningDiagnostic(
                "subtitle_unavailable_requires_asr_plan",
                "No valid embedded subtitle track remains for this Part.",
            ),
        )
    else:
        part = SubtitlePartReport(source_id, SubtitlePartState.COMPLETED, 1, None, None, (), None)
    report = SubtitleCandidateReport(
        report_id=report_id,
        plan_id=plan_id or plan.plan_id,
        state=CandidateReportState.COMPLETED,
        subtitle_rules_fingerprint=subtitle_rules_fingerprint(project_root),
        candidates=(),
        diagnostics=(),
        report_path=report_path,
        part_reports=(part,),
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report.as_json(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return report


def _audio_report(
    project_root: Path,
    plan: RunPlan,
    subtitle_report_id: str,
    *,
    report_id: str = "2" * 32,
    plan_id: str | None = None,
    bound_subtitle_report_id: str | None = None,
) -> tuple[str, Path]:
    report_path = (
        project_root / "work" / "audio-analysis-reports" / report_id / "audio-analysis-report.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "report_id": report_id,
                "plan_id": plan_id or plan.plan_id,
                "subtitle_report_id": bound_subtitle_report_id or subtitle_report_id,
                "state": "complete",
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return report_id, report_path


def _write_registry(project_root: Path, candidates: list[dict[str, object]]) -> Path:
    registry_path = project_root / "models" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps({"schema_version": 2, "candidates": candidates}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return registry_path


def _bare_asr_candidates() -> list[dict[str, object]]:
    return [
        {"candidate_id": "qwen3-asr-1-7b", "capability": "asr_primary"},
        {"candidate_id": "whisper-large-v3", "capability": "asr_review"},
    ]


def _over_envelope_primary(project_root: Path) -> dict[str, object]:
    dependency_plan = "models/plans/qwen3-asr-1-7b.md"
    plan_path = project_root / dependency_plan
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text("# qwen3-asr-1-7b dependency plan\n", encoding="utf-8")
    return {
        "candidate_id": "qwen3-asr-1-7b",
        "capability": "asr_primary",
        "official_source": {"url": "https://example.invalid/qwen3-asr", "approved": True},
        "license_approved": True,
        "revision": "fixture-r1",
        "asset_sha256": "a" * 64,
        "offline_runtime": True,
        "credential_required": False,
        "telemetry": False,
        "dependency_plan": dependency_plan,
        "resource_estimate": {"high_bytes": 24 * 1024**3 + 1},
    }


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


# --- Full-ASR resource confirmation pause + resume -------------------------


def test_transcribe_subtitle_unavailable_pauses_at_full_asr_resource_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _subtitle_report(tmp_path, plan, unavailable=True)
    audio_id, audio_path = _audio_report(tmp_path, plan, subtitle_report.report_id)
    _write_registry(tmp_path, _bare_asr_candidates())
    plan_path = tmp_path / "plans" / plan.plan_id / "run-plan.json"
    plan_before = plan_path.read_bytes()
    subtitle_before = subtitle_report.report_path.read_bytes()
    audio_before = audio_path.read_bytes()

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["transcribe", plan.plan_id, subtitle_report.report_id, audio_id, "--json"],
    )

    assert code == 0
    assert response["status"] == "awaiting_full_asr_resource_confirmation"
    report = response["report"]
    assert report["status"] == "awaiting_full_asr_resource_confirmation"
    assert report["plan_id"] == plan.plan_id
    assert report["subtitle_report_id"] == subtitle_report.report_id
    assert report["audio_report_id"] == audio_id
    assert report["start_precondition"] == {
        "basis": "subtitle_unavailable_requires_asr_plan",
        "source_ids": [plan.source_artifacts[0].source_id],
    }
    assert report["required_decision"] == {
        "reason": "full_asr_resource_confirmation",
        "decision": _CONFIRM_DECISION,
    }
    assert report["revalidation"]["run_plan_confirmed"] is True
    assert report["revalidation"]["source_artifacts"] == [
        {
            "source_id": plan.source_artifacts[0].source_id,
            "sha256": plan.source_artifacts[0].sha256,
            "byte_count": plan.source_artifacts[0].byte_count,
        }
    ]
    assert report["audio_analysis"] == {
        "state": "bound",
        "report_id": audio_id,
        "plan_id": plan.plan_id,
        "subtitle_report_id": subtitle_report.report_id,
    }
    assert report["audio_completeness"] == "not_verified"
    assert [item["capability"] for item in report["capabilities"]] == [
        "asr_primary",
        "asr_review",
    ]
    assert report["guarantees"] == _GUARANTEES

    report_path = Path(report["report_path"])
    assert report_path.parent == Path(report["workspace_path"])
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    # No prior evidence was mutated and nothing was published.
    assert plan_path.read_bytes() == plan_before
    assert subtitle_report.report_path.read_bytes() == subtitle_before
    assert audio_path.read_bytes() == audio_before
    assert not (tmp_path / "outputs").exists()


def test_resume_transcription_records_confirmation_and_requires_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _subtitle_report(tmp_path, plan, unavailable=True)
    audio_id, _ = _audio_report(tmp_path, plan, subtitle_report.report_id)
    _write_registry(tmp_path, _bare_asr_candidates())
    _, paused = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["transcribe", plan.plan_id, subtitle_report.report_id, audio_id, "--json"],
    )
    paused_report_id = paused["report"]["report_id"]
    paused_path = Path(paused["report"]["report_path"])
    paused_bytes = paused_path.read_bytes()

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["resume-transcription", paused_report_id, "--decision", _CONFIRM_DECISION, "--json"],
    )

    assert code == 0
    assert response["status"] == "model_acquisition_required"
    report = response["report"]
    assert report["status"] == "model_acquisition_required"
    assert report["required_decision"] is None
    # A fresh attempt is minted; the paused report is never overwritten.
    assert report["report_id"] != paused_report_id
    assert report["input_evidence"]["resumption_decision"] == _CONFIRM_DECISION
    assert report["input_evidence"]["resumed_from_report_id"] == paused_report_id
    assert report["input_evidence"]["resumed_from_report"]["sha256"] == (
        sha256(paused_bytes).hexdigest()
    )
    assert report["start_precondition"]["basis"] == "subtitle_unavailable_requires_asr_plan"
    assert report["plan_id"] == plan.plan_id
    assert report["audio_report_id"] == audio_id
    assert paused_path.read_bytes() == paused_bytes


# --- Start preconditions ---------------------------------------------------


def test_transcribe_without_handoff_or_upgrade_is_precondition_unmet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _subtitle_report(tmp_path, plan, unavailable=False)
    audio_id, _ = _audio_report(tmp_path, plan, subtitle_report.report_id)
    _write_registry(tmp_path, _bare_asr_candidates())

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["transcribe", plan.plan_id, subtitle_report.report_id, audio_id, "--json"],
    )

    assert code == 0
    assert response["status"] == "failed"
    report = response["report"]
    assert report["start_precondition"] is None
    assert report["required_decision"] is None
    assert report["diagnostics"] == [
        {
            "reason": "transcription_precondition_unmet",
            "message": (
                "A subtitle-priority run never triggers ASR automatically; transcribe requires "
                "a retained subtitle_unavailable_requires_asr_plan handoff or --upgrade-all."
            ),
        }
    ]
    assert not (tmp_path / "outputs").exists()


def test_transcribe_explicit_upgrade_requires_acquisition_without_confirmation_pause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _subtitle_report(tmp_path, plan, unavailable=False)
    audio_id, _ = _audio_report(tmp_path, plan, subtitle_report.report_id)
    _write_registry(tmp_path, _bare_asr_candidates())

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        [
            "transcribe",
            plan.plan_id,
            subtitle_report.report_id,
            audio_id,
            "--upgrade-all",
            "--json",
        ],
    )

    assert code == 0
    assert response["status"] == "model_acquisition_required"
    report = response["report"]
    assert report["required_decision"] is None
    assert report["start_precondition"] == {
        "basis": "explicit_whole_selection_upgrade",
        "source_ids": [plan.source_artifacts[0].source_id],
    }


# --- Transcription resource-envelope pause ---------------------------------


def test_transcribe_pauses_when_conservative_estimate_exceeds_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _subtitle_report(tmp_path, plan, unavailable=True)
    audio_id, _ = _audio_report(tmp_path, plan, subtitle_report.report_id)
    _write_registry(tmp_path, [_over_envelope_primary(tmp_path)])

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["transcribe", plan.plan_id, subtitle_report.report_id, audio_id, "--json"],
    )

    assert code == 0
    assert response["status"] == "resource_envelope_exceeded"
    report = response["report"]
    assert report["required_decision"] == {
        "reason": "resource_envelope_exceeded",
        "decision": _RESOURCE_DECISION,
    }
    primary = next(item for item in report["capabilities"] if item["capability"] == "asr_primary")
    assert primary["candidates"][0]["reason"] == "resource_envelope_exceeded"
    assert report["diagnostics"][0]["reason"] == "resource_envelope_exceeded"


def test_resume_resource_envelope_rejects_wrong_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _subtitle_report(tmp_path, plan, unavailable=True)
    audio_id, _ = _audio_report(tmp_path, plan, subtitle_report.report_id)
    _write_registry(tmp_path, [_over_envelope_primary(tmp_path)])
    _, paused = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["transcribe", plan.plan_id, subtitle_report.report_id, audio_id, "--json"],
    )
    paused_report_id = paused["report"]["report_id"]

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["resume-transcription", paused_report_id, "--decision", _CONFIRM_DECISION, "--json"],
    )

    assert code == 2
    assert response["status"] == "error"
    assert response["reason"] == "transcription_resume_invalid"


# --- Revalidation drift blocks the attempt ---------------------------------


def test_transcribe_blocks_on_unconfirmed_run_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _subtitle_report(tmp_path, plan, unavailable=True)
    audio_id, _ = _audio_report(tmp_path, plan, subtitle_report.report_id)
    _write_registry(tmp_path, _bare_asr_candidates())
    # Drift the confirmed PlanReport so the RunPlan no longer matches it.
    report_json_path = tmp_path / "plans" / "reports" / plan.report_id / "plan-report.json"
    drifted = json.loads(report_json_path.read_text(encoding="utf-8"))
    drifted["configuration_fingerprint"] = "drifted-fingerprint"
    report_json_path.write_text(
        json.dumps(drifted, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["transcribe", plan.plan_id, subtitle_report.report_id, audio_id, "--json"],
    )

    assert code == 0
    assert response["status"] == "failed"
    assert response["report"]["diagnostics"][0]["reason"] == "run_plan_not_confirmed"
    assert response["report"]["start_precondition"] is None


def test_transcribe_blocks_on_subtitle_report_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _subtitle_report(tmp_path, plan, unavailable=True, plan_id="a-different-plan")
    audio_id, _ = _audio_report(tmp_path, plan, subtitle_report.report_id)
    _write_registry(tmp_path, _bare_asr_candidates())

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["transcribe", plan.plan_id, subtitle_report.report_id, audio_id, "--json"],
    )

    assert code == 0
    assert response["status"] == "failed"
    assert response["report"]["diagnostics"][0]["reason"] == "subtitle_report_mismatch"


def test_transcribe_blocks_on_subtitle_rules_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _subtitle_report(tmp_path, plan, unavailable=True)
    audio_id, _ = _audio_report(tmp_path, plan, subtitle_report.report_id)
    _write_registry(tmp_path, _bare_asr_candidates())
    (tmp_path / "config" / "subtitle-rules.json").write_text(
        '{"schema_version": 1, "id": "DRIFTED"}\n', encoding="utf-8"
    )

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["transcribe", plan.plan_id, subtitle_report.report_id, audio_id, "--json"],
    )

    assert code == 0
    assert response["status"] == "failed"
    assert response["report"]["diagnostics"][0]["reason"] == "subtitle_rules_changed"


def test_transcribe_blocks_on_audio_report_for_another_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _subtitle_report(tmp_path, plan, unavailable=True)
    audio_id, _ = _audio_report(
        tmp_path, plan, subtitle_report.report_id, plan_id="a-different-plan"
    )
    _write_registry(tmp_path, _bare_asr_candidates())

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["transcribe", plan.plan_id, subtitle_report.report_id, audio_id, "--json"],
    )

    assert code == 0
    assert response["status"] == "failed"
    assert response["report"]["diagnostics"][0]["reason"] == "audio_report_mismatch"


def test_transcribe_blocks_on_missing_audio_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _subtitle_report(tmp_path, plan, unavailable=True)
    _write_registry(tmp_path, _bare_asr_candidates())

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["transcribe", plan.plan_id, subtitle_report.report_id, "2" * 32, "--json"],
    )

    assert code == 0
    assert response["status"] == "failed"
    assert response["report"]["diagnostics"][0]["reason"] == "audio_report_invalid"


def test_transcribe_reads_no_source_media(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _subtitle_report(tmp_path, plan, unavailable=True)
    audio_id, _ = _audio_report(tmp_path, plan, subtitle_report.report_id)
    _write_registry(tmp_path, _bare_asr_candidates())
    hashed: list[Path] = []
    real_sha256_file = evidence.sha256_file

    def _spy(path: Path) -> tuple[str, int]:
        hashed.append(Path(path))
        return real_sha256_file(path)

    monkeypatch.setattr(evidence, "sha256_file", _spy)

    code, _response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["transcribe", plan.plan_id, subtitle_report.report_id, audio_id, "--json"],
    )

    assert code == 0
    assert plan.source_artifacts[0].media_path not in hashed


# --- resume-transcription guards -------------------------------------------


def test_resume_transcription_rejects_unknown_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["resume-transcription", "3" * 32, "--decision", _CONFIRM_DECISION, "--json"],
    )

    assert code == 2
    assert response["status"] == "error"
    assert response["reason"] == "transcription_report_invalid"


def test_resume_transcription_requires_explicit_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _subtitle_report(tmp_path, plan, unavailable=True)
    audio_id, _ = _audio_report(tmp_path, plan, subtitle_report.report_id)
    _write_registry(tmp_path, _bare_asr_candidates())
    _, paused = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["transcribe", plan.plan_id, subtitle_report.report_id, audio_id, "--json"],
    )
    paused_report_id = paused["report"]["report_id"]

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["resume-transcription", paused_report_id, "--json"],
    )

    assert code == 2
    assert response["status"] == "error"
    assert response["reason"] == "transcription_resume_invalid"


def test_resume_transcription_rejects_non_paused_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _subtitle_report(tmp_path, plan, unavailable=False)
    audio_id, _ = _audio_report(tmp_path, plan, subtitle_report.report_id)
    _write_registry(tmp_path, _bare_asr_candidates())
    # A subtitle-priority run with no upgrade is a retained ``failed`` report,
    # not a decision pause, so it must not be resumable.
    _, failed = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["transcribe", plan.plan_id, subtitle_report.report_id, audio_id, "--json"],
    )
    failed_report_id = failed["report"]["report_id"]

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["resume-transcription", failed_report_id, "--decision", _CONFIRM_DECISION, "--json"],
    )

    assert code == 2
    assert response["status"] == "error"
    assert response["reason"] == "transcription_resume_invalid"
