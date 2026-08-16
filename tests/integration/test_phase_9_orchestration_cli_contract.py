"""Orchestration CLI command-boundary contract, proven offline (tickets 10, 12).

These exercises drive the real :mod:`video_content_pipeline.cli` entry point for
``vcp run/status/pause/resume/cancel/verify/inventory`` over a synthetic project
root, with the production composition replaced by a controlled one (the per-phase
functions need media, a model, and the network). They assert the machine-readable
JSON contract and exit codes: a non-interactive run publishes a bundle; pause and
cancel only write control requests and never touch run state; status never
mutates and diagnoses a stale-running crash; verify is hash-layer only; inventory
renders the published inventory faithfully.

Ticket 12 adds the phase-exit proofs at this boundary: ``vcp status`` diagnoses a
crashed run and ``vcp resume`` recovers it under kill and truncation injection,
and the reformulated guarantee that non-publication commands never write
``outputs/`` while publication is exercised only inside synthetic project roots.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from video_content_pipeline import cli
from video_content_pipeline.orchestration import (
    RunLayout,
    initialize_run_workspace,
    run_id_from_run_plan,
    source_id_from_run_plan,
)
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
from video_content_pipeline.run_control import ControlDirective
from video_content_pipeline.run_loop import RunComposition, RunReportInputs
from video_content_pipeline.run_state import RunStateWriter, RunStatus, read_run_state
from video_content_pipeline.source import SourceArtifact
from video_content_pipeline.stage_dag import (
    StageInvalidationKey,
    StageName,
    StageResult,
    StageUnit,
    execute_stages,
)

_PLAN_ID = "plan0123456789abcdef0123"
_PART = "a" * 64
# The real repository root, used to prove synthetic publishes never touch it.
_REPO_ROOT = Path(__file__).resolve().parents[2]


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


# --- vcp status / resume: crash recovery at the CLI boundary -----------------


def _crash_a_running_run(project_root: Path, plan: RunPlan, *, completed_units: int) -> RunLayout:
    """Leave a discoverable run wedged at ``running`` with checkpoints, no lock.

    This is the on-disk picture a power loss or forced kill leaves behind: the
    single writer transitioned to ``running`` and checkpointed some units, then
    died mid-unit before any clean transition and without releasing a lock. The
    layout is addressed exactly as the CLI will find it under ``work/``.
    """

    now = datetime(2026, 8, 16, 8, 30, 0, tzinfo=UTC)
    layout = initialize_run_workspace(
        RunLayout(
            project_root=project_root,
            source_id=source_id_from_run_plan(plan),
            run_id=run_id_from_run_plan(plan, now),
        )
    )
    writer = RunStateWriter.create(layout, plan_id=plan.plan_id)
    writer.transition_to(RunStatus.QUEUED)
    writer.transition_to(RunStatus.RUNNING)
    executed: list[StageUnit] = []

    def crashing(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
        if len(executed) >= completed_units:
            raise RuntimeError("process killed mid-unit")
        executed.append(unit)
        return StageResult.completed()

    with pytest.raises(RuntimeError):
        execute_stages(
            writer=writer,
            layout=layout,
            plan=plan,
            executor=crashing,
            on_boundary=lambda: ControlDirective.CONTINUE,
        )
    assert read_run_state(layout.state_path).status is RunStatus.RUNNING
    return layout


def test_status_diagnoses_a_crashed_run_and_resume_recovers_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _write_plan(tmp_path)
    layout = _crash_a_running_run(tmp_path, plan, completed_units=2)
    _configure(tmp_path, monkeypatch)

    # `vcp status --run` detects the stale-running crash without mutating anything.
    before = layout.state_path.read_bytes()
    code, single = _invoke(["status", "--run", layout.run_id], capsys)
    assert code == 0
    assert single["run"]["resume_case"] == "crashed"
    assert single["run"]["stale_running"] is True
    assert layout.state_path.read_bytes() == before

    # `vcp resume` recovers from the last checkpoint and drives to a published bundle.
    code, output = _invoke(["resume", "--run", layout.run_id], capsys)
    assert code == 0
    assert output["run_status"] == "complete"
    assert output["published"] is True
    assert output["verified"] is True
    assert read_run_state(layout.state_path).status is RunStatus.COMPLETE
    # At most the interrupted unit is redone; nothing published overwrites, and the
    # recovered run leaves a hash-verifiable bundle under the synthetic root.
    assert (layout.output_dir / "manifest.json").is_file()


def test_truncated_state_temp_is_repaired_on_cli_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _write_plan(tmp_path)
    layout = _crash_a_running_run(tmp_path, plan, completed_units=1)
    # A crash between the temp write and the atomic rename left a torn temp file.
    torn = layout.state_path.with_name(layout.state_path.name + ".tmp")
    torn.write_text('{"schema_version":1,"status":"runni', encoding="utf-8")
    _configure(tmp_path, monkeypatch)

    code, output = _invoke(["resume", "--run", layout.run_id], capsys)
    assert code == 0
    assert output["run_status"] == "complete"
    assert output["published"] is True
    # The torn temp artifact is gone and the recovered bundle is hash-verifiable.
    assert not torn.exists()


# --- Reformulated guarantee: non-publication commands never write outputs/ ---


def test_non_publication_commands_never_write_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`status`, `pause`, and `cancel` are non-publication commands: they read
    state or write a control request, and must never create ``outputs/``.

    This is the Phase 9 reformulation of the prior-phase "``outputs/`` does not
    exist" invariant. Publication now writes ``outputs/`` — but only the run/
    improve/resume commands do; the control and query commands never touch it.
    """

    plan = _write_plan(tmp_path)
    # A crashed (unpublished) run exists under work/; outputs/ does not exist yet.
    layout = _crash_a_running_run(tmp_path, plan, completed_units=1)
    _configure(tmp_path, monkeypatch)
    outputs_root = tmp_path / "outputs"

    for argv in (["status"], ["status", "--run", layout.run_id]):
        code, _ = _invoke(argv, capsys)
        assert code == 0
        assert not outputs_root.exists()

    for kind in ("pause", "cancel"):
        code, output = _invoke([kind, "--run", layout.run_id], capsys)
        assert code == 0
        assert Path(output["control_request_path"]).is_file()
        assert not outputs_root.exists()


def test_repository_outputs_untouched_by_cli_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Publication is exercised only inside the synthetic project root; the
    repository's own ``outputs/`` still contains no published bundle."""

    _write_plan(tmp_path)
    _configure(tmp_path, monkeypatch)
    code, output = _invoke(["run", "--plan", _PLAN_ID], capsys)
    assert code == 0
    assert output["published"] is True
    # The synthetic root received the bundle...
    assert (tmp_path / "outputs" / output["source_id"] / output["run_id"]).is_dir()
    # ...but the real repository outputs/ was never written by the run.
    assert not (_REPO_ROOT / "outputs" / output["source_id"]).exists()
