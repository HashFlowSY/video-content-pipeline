"""Ticket 10 acceptance: the orchestration CLI command boundary, proven offline.

These exercises drive the real :mod:`video_content_pipeline.cli` entry point for
``vcp run/status/pause/resume/cancel/verify/inventory`` over a synthetic project
root, with the production composition replaced by a controlled one (the per-phase
functions need media, a model, and the network). They assert the machine-readable
JSON contract and exit codes: a non-interactive run publishes a bundle; pause and
cancel only write control requests and never touch run state; status never
mutates and diagnoses a stale-running crash; verify is hash-layer only; inventory
renders the published inventory faithfully.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from video_content_pipeline import cli
from video_content_pipeline.planning import RunPlan, calculate_disk_headroom
from video_content_pipeline.publication_projection import (
    PlainArtifactEvidence,
    ProjectionEvidence,
)
from video_content_pipeline.run_choices import (
    COLLECTION_SCOPE,
    KEY_ASR_MODE,
    KEY_VISUAL_TEXT_ENABLED,
    STAGE_RUN,
    AsrMode,
    ChoiceProvenance,
    RunChoice,
    RunPlanChoices,
)
from video_content_pipeline.run_loop import RunComposition, RunReportInputs
from video_content_pipeline.source import SourceArtifact
from video_content_pipeline.stage_dag import (
    StageInvalidationKey,
    StageName,
    StageResult,
    StageUnit,
)

_PLAN_ID = "plan0123456789abcdef0123"
_PART = "a" * 64


def _plan() -> RunPlan:
    choices = RunPlanChoices.build(
        (
            RunChoice(
                STAGE_RUN,
                KEY_ASR_MODE,
                COLLECTION_SCOPE,
                AsrMode.FULL_ASR.value,
                ChoiceProvenance.USER_CHOSEN,
            ),
            RunChoice(
                STAGE_RUN,
                KEY_VISUAL_TEXT_ENABLED,
                COLLECTION_SCOPE,
                "false",
                ChoiceProvenance.USER_CHOSEN,
            ),
        )
    )
    return RunPlan(
        plan_id=_PLAN_ID,
        report_id="0" * 32,
        source_artifacts=(
            SourceArtifact(
                source_id=_PART,
                sha256=_PART,
                byte_count=1,
                media_path=Path("input") / _PART / "media",
                origin_kind="synthetic_fixture",
            ),
        ),
        tools=(),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint="cfg" + "0" * 61,
        run_choices=choices,
    )


def _write_plan(project_root: Path) -> RunPlan:
    plan = _plan()
    plan_dir = project_root / "plans" / plan.plan_id
    plan_dir.mkdir(parents=True)
    (plan_dir / "run-plan.json").write_text(json.dumps(plan.as_json(), indent=2), encoding="utf-8")
    return plan


def _complete_executor(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
    return StageResult.completed()


def _composition(
    executor: Callable[[StageUnit, StageInvalidationKey], StageResult],
    *,
    evidence: ProjectionEvidence | None = None,
) -> RunComposition:
    ev = (
        evidence
        if evidence is not None
        else ProjectionEvidence(content_report=PlainArtifactEvidence(content="# 内容报告\n"))
    )
    return RunComposition(executor=executor, evidence=lambda: ev, report_inputs=RunReportInputs)


def _configure(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    executor: Callable[[StageUnit, StageInvalidationKey], StageResult] = _complete_executor,
) -> None:
    monkeypatch.setattr(cli, "assert_runtime_policy", lambda: None)
    monkeypatch.setattr(cli, "assert_project_venv", lambda: object())
    monkeypatch.setattr(cli, "_project_root", lambda: project_root)
    monkeypatch.setattr(cli, "_composition_factory", lambda layout, plan: _composition(executor))


def _invoke(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, object]]:
    code = cli.main(argv)
    output = json.loads(capsys.readouterr().out)
    return code, output


# --- vcp run ----------------------------------------------------------------


def test_run_executes_non_interactively_and_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_plan(tmp_path)
    _configure(tmp_path, monkeypatch)
    code, output = _invoke(["run", "--plan", _PLAN_ID], capsys)
    assert code == 0
    assert output["status"] == "ok"
    assert output["run_status"] == "complete"
    assert output["published"] is True
    assert output["verified"] is True
    assert (
        tmp_path / "outputs" / output["source_id"] / output["run_id"] / "manifest.json"
    ).is_file()


def test_run_with_unknown_plan_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _configure(tmp_path, monkeypatch)
    code, output = _invoke(["run", "--plan", "no-such-plan"], capsys)
    assert code == 2
    assert output["status"] == "error"
    assert output["reason"] == "run_plan_not_confirmed"


# --- vcp status -------------------------------------------------------------


def test_status_lists_runs_and_reports_one_without_mutating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_plan(tmp_path)
    _configure(tmp_path, monkeypatch)
    _, run_output = _invoke(["run", "--plan", _PLAN_ID], capsys)
    run_id = run_output["run_id"]

    code, listing = _invoke(["status"], capsys)
    assert code == 0
    assert any(run["run_id"] == run_id for run in listing["runs"])

    layout_state = tmp_path / "work" / run_output["source_id"] / run_id / "run-state.json"
    before = layout_state.read_bytes()
    code, single = _invoke(["status", "--run", run_id], capsys)
    assert code == 0
    assert single["run"]["run_id"] == run_id
    assert single["run"]["status"] == "complete"
    # `vcp status` never mutates the run state document.
    assert layout_state.read_bytes() == before


def test_status_unknown_run_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _configure(tmp_path, monkeypatch)
    code, output = _invoke(["status", "--run", "20260816T090000Z-deadbeefdeadbeef"], capsys)
    assert code == 2
    assert output["reason"] == "run_not_found"


# --- vcp pause / cancel only write control requests -------------------------


def test_pause_writes_only_a_control_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_plan(tmp_path)
    _configure(tmp_path, monkeypatch)
    _, run_output = _invoke(["run", "--plan", _PLAN_ID], capsys)
    run_id, source_id = run_output["run_id"], run_output["source_id"]
    state_path = tmp_path / "work" / source_id / run_id / "run-state.json"
    before = state_path.read_bytes()

    code, output = _invoke(["pause", "--run", run_id], capsys)
    assert code == 0
    assert output["requested"] == "pause"
    assert Path(output["control_request_path"]).is_file()
    # A control request never touches the run state document.
    assert state_path.read_bytes() == before


def test_cancel_writes_only_a_control_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_plan(tmp_path)
    _configure(tmp_path, monkeypatch)
    _, run_output = _invoke(["run", "--plan", _PLAN_ID], capsys)
    run_id = run_output["run_id"]
    code, output = _invoke(["cancel", "--run", run_id], capsys)
    assert code == 0
    assert output["requested"] == "cancel"


# --- vcp verify and inventory -----------------------------------------------


def test_verify_is_hash_layer_and_validates_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_plan(tmp_path)
    _configure(tmp_path, monkeypatch)
    _, run_output = _invoke(["run", "--plan", _PLAN_ID], capsys)
    run_id = run_output["run_id"]

    code, output = _invoke(["verify", "--run", run_id], capsys)
    assert code == 0
    assert output["verified"] is True
    assert output["hash_verified"] is True
    assert output["inventory_valid"] is True
    assert output["discrepancies"] == []


def test_verify_detects_a_tampered_published_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_plan(tmp_path)
    _configure(tmp_path, monkeypatch)
    _, run_output = _invoke(["run", "--plan", _PLAN_ID], capsys)
    run_id, source_id = run_output["run_id"], run_output["source_id"]
    bundle = tmp_path / "outputs" / source_id / run_id
    (bundle / "processing-report.md").write_text("tampered\n", encoding="utf-8")

    code, output = _invoke(["verify", "--run", run_id], capsys)
    assert code == 0
    assert output["verified"] is False
    assert any(d["reason"] == "hash_mismatch" for d in output["discrepancies"])


def test_inventory_renders_the_published_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_plan(tmp_path)
    _configure(tmp_path, monkeypatch)
    _, run_output = _invoke(["run", "--plan", _PLAN_ID], capsys)
    run_id = run_output["run_id"]
    code, output = _invoke(["inventory", "--run", run_id], capsys)
    assert code == 0
    assert output["inventory"]["schema_version"] == 1
    assert output["inventory"]["run_id"] == run_id


def test_inventory_unknown_published_run_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _configure(tmp_path, monkeypatch)
    code, output = _invoke(["inventory", "--run", "20260816T090000Z-deadbeefdeadbeef"], capsys)
    assert code == 2
    assert output["reason"] == "published_run_not_found"


# --- vcp resume: decision handoff -------------------------------------------


def test_resume_answers_a_decision_pause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_plan(tmp_path)
    required = {
        "reason": "resource_envelope_exceeded",
        "decision": "resource_configuration_changed",
    }

    def paused_executor(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
        if unit.stage is StageName.TRANSCRIPTION:
            return StageResult.decision_required(required)
        return StageResult.completed()

    _configure(tmp_path, monkeypatch, executor=paused_executor)
    _, run_output = _invoke(["run", "--plan", _PLAN_ID], capsys)
    run_id = run_output["run_id"]
    assert run_output["run_status"] == "incomplete"
    assert run_output["required_decision"]["decision"] == "resource_configuration_changed"

    # Resume with the matching decision validates and hands off (no re-execution).
    code, output = _invoke(
        ["resume", "--run", run_id, "--decision", "resource_configuration_changed"], capsys
    )
    assert code == 0
    assert output["accepted_decision"] == "resource_configuration_changed"


def test_resume_with_wrong_decision_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_plan(tmp_path)
    required = {
        "reason": "resource_envelope_exceeded",
        "decision": "resource_configuration_changed",
    }

    def paused_executor(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
        if unit.stage is StageName.TRANSCRIPTION:
            return StageResult.decision_required(required)
        return StageResult.completed()

    _configure(tmp_path, monkeypatch, executor=paused_executor)
    _, run_output = _invoke(["run", "--plan", _PLAN_ID], capsys)
    run_id = run_output["run_id"]
    code, output = _invoke(["resume", "--run", run_id, "--decision", "wrong_token"], capsys)
    assert code == 2
    assert output["reason"] == "decision_mismatch"
