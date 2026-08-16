"""Ticket 10 acceptance: the non-interactive run loop, proven offline.

These exercises drive :func:`~video_content_pipeline.run_loop.execute_confirmed_run`
and :func:`~video_content_pipeline.run_loop.start_run` over synthetic project
roots with a controlled :class:`~video_content_pipeline.run_loop.RunComposition`
— no model, no media, no network. They prove the orchestration the production
composition rides on: non-interactive execution, the plan §12 terminal
classification, the guaranteed Minimal RunBundle on every ordinary failure,
cancel-still-publishes, pause-never-publishes, decision pauses distinct from user
pauses, and fail-fast on a held heavy-task lock.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from video_content_pipeline.heavy_task_lock import (
    HeavyTaskLockHeld,
    ProcessIdentity,
    acquire_heavy_task_lock,
    heavy_task_lock_path,
)
from video_content_pipeline.orchestration import (
    RunLayout,
    initialize_run_workspace,
    run_id_from_run_plan,
    source_id_from_run_plan,
)
from video_content_pipeline.planning import RunPlan, calculate_disk_headroom
from video_content_pipeline.publication import read_latest_pointer, verify_published_bundle
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
from video_content_pipeline.run_loop import (
    RunComposition,
    RunReportInputs,
    classify_completed_run,
    execute_confirmed_run,
    resume_and_finalize,
    start_run,
)
from video_content_pipeline.run_reports import (
    MINIMAL_RUN_BUNDLE_DOCUMENTS,
    GateOutcome,
    GateStatus,
    StageGateReport,
)
from video_content_pipeline.run_state import RunStatus, read_run_state
from video_content_pipeline.source import SourceArtifact
from video_content_pipeline.stage_dag import (
    StageInvalidationKey,
    StageName,
    StageResult,
    StageRunDisposition,
    StageRunResult,
    StageUnit,
    plan_stage_units,
)

_PLAN_ID = "plan0123456789abcdef0123"
_CONFIG = "cfg" + "0" * 61
_RESUMER = ProcessIdentity(pid=900, start_time="s900")
_NOW = datetime(2026, 8, 16, 8, 45, 0, tzinfo=UTC)


class _FakeProbe:
    def __init__(self, identity: ProcessIdentity, live: set[ProcessIdentity]) -> None:
        self._identity = identity
        self._live = set(live)

    def identify(self) -> ProcessIdentity:
        return self._identity

    def is_running(self, identity: ProcessIdentity) -> bool:
        return identity in self._live


def _clock() -> Callable[[], datetime]:
    step = {"n": 0}

    def tick() -> datetime:
        moment = datetime(2026, 8, 16, 8, 30, step["n"] % 60, tzinfo=UTC)
        step["n"] += 1
        return moment

    return tick


def _plan(
    part_ids: tuple[str, ...],
    *,
    mode: AsrMode = AsrMode.FULL_ASR,
    visual: bool = False,
    include_toggle: bool = True,
) -> RunPlan:
    choices = [
        RunChoice(
            stage=STAGE_RUN,
            key=KEY_ASR_MODE,
            scope=COLLECTION_SCOPE,
            value=mode.value,
            provenance=ChoiceProvenance.USER_CHOSEN,
        ),
    ]
    if include_toggle:
        choices.append(
            RunChoice(
                stage=STAGE_RUN,
                key=KEY_VISUAL_TEXT_ENABLED,
                scope=COLLECTION_SCOPE,
                value="true" if visual else "false",
                provenance=ChoiceProvenance.USER_CHOSEN,
            )
        )
    return RunPlan(
        plan_id=_PLAN_ID,
        report_id="0" * 32,
        source_artifacts=tuple(
            SourceArtifact(
                source_id=part,
                sha256=part,
                byte_count=1,
                media_path=Path("input") / part / "media",
            )
            for part in part_ids
        ),
        tools=(),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint=_CONFIG,
        run_choices=RunPlanChoices.build(tuple(choices)),
    )


def _plan_without_mode(part_ids: tuple[str, ...]) -> RunPlan:
    return RunPlan(
        plan_id=_PLAN_ID,
        report_id="0" * 32,
        source_artifacts=tuple(
            SourceArtifact(source_id=p, sha256=p, byte_count=1, media_path=Path("input") / p / "m")
            for p in part_ids
        ),
        tools=(),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint=_CONFIG,
        run_choices=RunPlanChoices(()),
    )


def _layout(tmp_path: Path, plan: RunPlan) -> RunLayout:
    source_id = source_id_from_run_plan(plan)
    run_id = run_id_from_run_plan(plan, _NOW)
    return initialize_run_workspace(RunLayout(tmp_path, source_id, run_id))


def _evidence_with_content() -> ProjectionEvidence:
    """Evidence that yields at least one VALID published content artifact."""

    return ProjectionEvidence(content_report=PlainArtifactEvidence(content="# 内容报告\n"))


def _composition(
    executor: Callable[[StageUnit, StageInvalidationKey], StageResult],
    *,
    evidence: ProjectionEvidence | None = None,
    report_inputs: RunReportInputs | None = None,
) -> RunComposition:
    ev = evidence if evidence is not None else ProjectionEvidence()
    ri = report_inputs if report_inputs is not None else RunReportInputs()
    return RunComposition(executor=executor, evidence=lambda: ev, report_inputs=lambda: ri)


def _complete_executor(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
    return StageResult.completed()


def _run(
    tmp_path: Path,
    plan: RunPlan,
    layout: RunLayout,
    composition: RunComposition,
    *,
    on_boundary: Callable[[], ControlDirective] | None = None,
):
    return execute_confirmed_run(
        layout=layout,
        plan=plan,
        composition=composition,
        lock_path=heavy_task_lock_path(tmp_path),
        probe=_FakeProbe(_RESUMER, {_RESUMER}),
        clock=_clock(),
        now=_NOW,
        on_boundary=on_boundary,
    )


# --- A clean run publishes a full bundle and advances latest ----------------


def test_clean_run_completes_publishes_and_advances_latest(tmp_path: Path) -> None:
    plan = _plan(("a" * 64,))
    layout = _layout(tmp_path, plan)
    outcome = _run(
        tmp_path, plan, layout, _composition(_complete_executor, evidence=_evidence_with_content())
    )

    assert outcome.status is RunStatus.COMPLETE
    assert read_run_state(layout.state_path).status is RunStatus.COMPLETE
    assert outcome.publication is not None
    # The six-piece Minimal RunBundle floor is present, plus the manifest.
    published = {
        p.relative_to(layout.output_dir).as_posix()
        for p in layout.output_dir.rglob("*")
        if p.is_file()
    }
    for document in MINIMAL_RUN_BUNDLE_DOCUMENTS:
        assert document in published
    assert "manifest.json" in published
    # The bundle re-hashes clean, and the latest pointer names this run.
    assert verify_published_bundle(layout.output_dir).verified is True
    assert outcome.publication.latest_advanced is True
    pointer = read_latest_pointer(layout.latest_path)
    assert pointer is not None and pointer.run_id == layout.run_id


def test_gate_warning_yields_complete_with_warnings(tmp_path: Path) -> None:
    plan = _plan(("a" * 64,))
    layout = _layout(tmp_path, plan)
    report_inputs = RunReportInputs(
        stage_reports=(
            StageGateReport(
                stage="subtitles",
                scope="a" * 64,
                outcomes=(GateOutcome(gate="coverage", status=GateStatus.WARNING),),
            ),
        )
    )
    outcome = _run(
        tmp_path,
        plan,
        layout,
        _composition(
            _complete_executor, evidence=_evidence_with_content(), report_inputs=report_inputs
        ),
    )
    assert outcome.status is RunStatus.COMPLETE_WITH_WARNINGS


# --- Partial and total failure ----------------------------------------------


def test_one_failed_part_of_two_yields_incomplete_and_publishes(tmp_path: Path) -> None:
    part_a, part_b = "a" * 64, "b" * 64
    plan = _plan((part_a, part_b))
    layout = _layout(tmp_path, plan)

    def executor(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
        if unit.stage is StageName.SUBTITLES and unit.scope == part_b:
            return StageResult.failed()
        return StageResult.completed()

    outcome = _run(
        tmp_path, plan, layout, _composition(executor, evidence=_evidence_with_content())
    )
    assert outcome.status is RunStatus.INCOMPLETE
    assert outcome.publication is not None
    assert part_b in outcome.stage_result.failed_scopes


def test_collection_stage_failure_yields_failed_and_publishes(tmp_path: Path) -> None:
    plan = _plan(("a" * 64,))
    layout = _layout(tmp_path, plan)

    def executor(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
        if unit.stage is StageName.SOURCE_REVALIDATION:
            return StageResult.failed()
        return StageResult.completed()

    outcome = _run(tmp_path, plan, layout, _composition(executor))
    assert outcome.status is RunStatus.FAILED
    assert outcome.publication is not None


def test_all_parts_failing_yields_failed(tmp_path: Path) -> None:
    plan = _plan(("a" * 64,))
    layout = _layout(tmp_path, plan)

    def executor(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
        if unit.stage is StageName.SUBTITLES:
            return StageResult.failed()
        return StageResult.completed()

    outcome = _run(tmp_path, plan, layout, _composition(executor))
    assert outcome.status is RunStatus.FAILED


# --- Decision pauses ---------------------------------------------------------


def test_stage_decision_pause_records_incomplete_and_publishes(tmp_path: Path) -> None:
    plan = _plan(("a" * 64,))
    layout = _layout(tmp_path, plan)
    required = {
        "reason": "resource_envelope_exceeded",
        "decision": "resource_configuration_changed",
    }

    def executor(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
        if unit.stage is StageName.TRANSCRIPTION:
            return StageResult.decision_required(required)
        return StageResult.completed()

    outcome = _run(
        tmp_path, plan, layout, _composition(executor, evidence=_evidence_with_content())
    )
    assert outcome.status is RunStatus.INCOMPLETE
    assert outcome.required_decision is not None
    assert outcome.required_decision["decision"] == "resource_configuration_changed"
    state = read_run_state(layout.state_path)
    assert state.status is RunStatus.INCOMPLETE
    assert state.required_decision is not None
    assert outcome.publication is not None


def test_missing_front_loaded_choice_is_a_decision_pause_not_a_prompt(tmp_path: Path) -> None:
    plan = _plan_without_mode(("a" * 64,))
    layout = _layout(tmp_path, plan)
    outcome = _run(tmp_path, plan, layout, _composition(_complete_executor))
    assert outcome.status is RunStatus.INCOMPLETE
    assert outcome.required_decision is not None
    assert outcome.required_decision["reason"] == "front_loaded_choice_missing"
    # A Minimal RunBundle still publishes, with only the audit documents.
    assert outcome.publication is not None
    assert verify_published_bundle(layout.output_dir).verified is True


# --- Pause and cancel at unit boundaries ------------------------------------


def test_cancel_stops_stages_and_still_publishes(tmp_path: Path) -> None:
    plan = _plan(("a" * 64,))
    layout = _layout(tmp_path, plan)
    outcome = _run(
        tmp_path,
        plan,
        layout,
        _composition(_complete_executor, evidence=_evidence_with_content()),
        on_boundary=lambda: ControlDirective.CANCEL,
    )
    assert outcome.status is RunStatus.CANCELLED
    assert read_run_state(layout.state_path).status is RunStatus.CANCELLED
    # Cancel still publishes a bundle of whatever exists.
    assert outcome.publication is not None
    assert verify_published_bundle(layout.output_dir).verified is True


def test_pause_exits_cleanly_without_publishing(tmp_path: Path) -> None:
    plan = _plan(("a" * 64,))
    layout = _layout(tmp_path, plan)
    outcome = _run(
        tmp_path,
        plan,
        layout,
        _composition(_complete_executor),
        on_boundary=lambda: ControlDirective.PAUSE,
    )
    assert outcome.status is RunStatus.PAUSED
    assert outcome.disposition is StageRunDisposition.PAUSED
    # A paused run has not published — a later resume continues it.
    assert outcome.publication is None
    assert not layout.output_dir.exists()


# --- Resume drives a paused run to completion and publishes ------------------


def test_resume_a_paused_run_completes_and_publishes(tmp_path: Path) -> None:
    plan = _plan(("a" * 64,))
    layout = _layout(tmp_path, plan)
    paused = _run(
        tmp_path,
        plan,
        layout,
        _composition(_complete_executor, evidence=_evidence_with_content()),
        on_boundary=lambda: ControlDirective.PAUSE,
    )
    assert paused.status is RunStatus.PAUSED
    assert not layout.output_dir.exists()

    outcome = resume_and_finalize(
        layout=layout,
        plan=plan,
        composition=_composition(_complete_executor, evidence=_evidence_with_content()),
        lock_path=heavy_task_lock_path(tmp_path),
        probe=_FakeProbe(_RESUMER, {_RESUMER}),
        clock=_clock(),
        now=_NOW,
        on_boundary=lambda: ControlDirective.CONTINUE,
    )
    assert outcome.status is RunStatus.COMPLETE
    assert outcome.publication is not None
    assert read_run_state(layout.state_path).status is RunStatus.COMPLETE
    assert verify_published_bundle(layout.output_dir).verified is True


# --- An ordinary exception still publishes a failed bundle -------------------


def test_ordinary_stage_exception_publishes_a_failed_bundle(tmp_path: Path) -> None:
    plan = _plan(("a" * 64,))
    layout = _layout(tmp_path, plan)

    def executor(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
        if unit.stage is StageName.SUBTITLES:
            raise RuntimeError("adapter blew up")
        return StageResult.completed()

    outcome = _run(tmp_path, plan, layout, _composition(executor))
    assert outcome.status is RunStatus.FAILED
    assert outcome.failure_reason == "RuntimeError"
    assert read_run_state(layout.state_path).status is RunStatus.FAILED
    assert outcome.publication is not None
    for document in MINIMAL_RUN_BUNDLE_DOCUMENTS:
        assert (layout.output_dir / document).is_file()


# --- Fail-fast on a held heavy-task lock ------------------------------------


def test_second_heavy_run_fails_fast_and_stays_queued(tmp_path: Path) -> None:
    plan = _plan(("a" * 64,))
    layout = _layout(tmp_path, plan)
    holder = ProcessIdentity(pid=100, start_time="s100")
    acquire_heavy_task_lock(
        heavy_task_lock_path(tmp_path),
        run_id="20260816T090000Z-fedcba9876543210",
        probe=_FakeProbe(holder, {holder}),
        clock=_clock(),
    )
    with pytest.raises(HeavyTaskLockHeld):
        execute_confirmed_run(
            layout=layout,
            plan=plan,
            composition=_composition(_complete_executor),
            lock_path=heavy_task_lock_path(tmp_path),
            probe=_FakeProbe(_RESUMER, {holder, _RESUMER}),
            clock=_clock(),
            now=_NOW,
        )
    # `queued` is the only state the blocked run ever reached.
    assert read_run_state(layout.state_path).status is RunStatus.QUEUED


# --- start_run loads a confirmed plan and refuses to overwrite --------------


def test_start_run_loads_plan_and_publishes(tmp_path: Path) -> None:
    plan = _plan(("a" * 64,))
    plan_dir = tmp_path / "plans" / plan.plan_id
    plan_dir.mkdir(parents=True)
    (plan_dir / "run-plan.json").write_text(
        __import__("json").dumps(plan.as_json(), indent=2), encoding="utf-8"
    )
    outcome = start_run(
        tmp_path,
        plan.plan_id,
        composition_factory=lambda layout, plan: _composition(
            _complete_executor, evidence=_evidence_with_content()
        ),
        run_start=_NOW,
        probe=_FakeProbe(_RESUMER, {_RESUMER}),
        clock=_clock(),
        now=_NOW,
    )
    assert outcome.status is RunStatus.COMPLETE
    assert outcome.publication is not None


# --- Terminal classification unit cases -------------------------------------


def test_classify_completed_run_cases() -> None:
    plan = _plan(("a" * 64, "b" * 64))
    clean = StageRunResult(StageRunDisposition.COMPLETED_ALL, frozenset())
    assert classify_completed_run(clean, plan, RunReportInputs()) is RunStatus.COMPLETE
    warned = RunReportInputs(warnings=("careful",))
    assert classify_completed_run(clean, plan, warned) is RunStatus.COMPLETE_WITH_WARNINGS
    partial = StageRunResult(StageRunDisposition.COMPLETED_ALL, frozenset({"b" * 64}))
    assert classify_completed_run(partial, plan, RunReportInputs()) is RunStatus.INCOMPLETE
    both = StageRunResult(StageRunDisposition.COMPLETED_ALL, frozenset({"a" * 64, "b" * 64}))
    assert classify_completed_run(both, plan, RunReportInputs()) is RunStatus.FAILED
    collection = StageRunResult(StageRunDisposition.COMPLETED_ALL, frozenset({COLLECTION_SCOPE}))
    assert classify_completed_run(collection, plan, RunReportInputs()) is RunStatus.FAILED


def test_plan_stage_units_smoke(tmp_path: Path) -> None:
    # Guards the test plan builder against a DAG shape regression.
    plan = _plan(("a" * 64,))
    units = plan_stage_units(plan)
    assert units[0] == StageUnit(StageName.SOURCE_REVALIDATION, COLLECTION_SCOPE)
