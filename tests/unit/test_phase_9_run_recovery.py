from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from video_content_pipeline.heavy_task_lock import (
    ProcessIdentity,
    acquire_heavy_task_lock,
    heavy_task_lock_path,
)
from video_content_pipeline.orchestration import RunLayout, initialize_run_workspace
from video_content_pipeline.planning import RunPlan, calculate_disk_headroom
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
from video_content_pipeline.run_control import ControlDirective, apply_pause
from video_content_pipeline.run_recovery import (
    ArtifactRepair,
    RecoveredFrom,
    ResumeAction,
    ResumeCase,
    RunRecoveryError,
    diagnose_run,
    record_resume_recovery,
    repair_journal_tail,
    repair_run_artifacts,
    resume_run,
    validate_decision,
)
from video_content_pipeline.run_state import (
    EventKind,
    RunStateWriter,
    RunStatus,
    read_journal,
    read_run_state,
)
from video_content_pipeline.source import SourceArtifact
from video_content_pipeline.stage_dag import (
    StageInvalidationKey,
    StageName,
    StageResult,
    StageRunDisposition,
    StageUnit,
    UnitStatus,
    compute_invalidation_keys,
    execute_stages,
    plan_stage_units,
    read_recorded_units,
    unit_record,
)

_PART_A = "a" * 64
_PART_B = "b" * 64
_PLAN_ID = "plan0123456789abcdef0123"
_CONFIG = "cfg" + "0" * 61
_SOURCE_ID = "s" * 64
_RUN_ID = "20260816T083000Z-0123456789abcdef"


def _choice(stage: str, key: str, value: str) -> RunChoice:
    return RunChoice(
        stage=stage,
        key=key,
        scope=COLLECTION_SCOPE,
        value=value,
        provenance=ChoiceProvenance.USER_CHOSEN,
    )


def _choices(mode: AsrMode = AsrMode.FULL_ASR) -> RunPlanChoices:
    return RunPlanChoices.build(
        (
            _choice(STAGE_RUN, KEY_ASR_MODE, mode.value),
            _choice(STAGE_RUN, KEY_VISUAL_TEXT_ENABLED, "false"),
        )
    )


def _artifact(content_hash: str) -> SourceArtifact:
    return SourceArtifact(
        source_id=content_hash,
        sha256=content_hash,
        byte_count=1,
        media_path=Path("input") / content_hash / "media",
    )


def _plan(*parts: str) -> RunPlan:
    return RunPlan(
        plan_id=_PLAN_ID,
        report_id="0" * 32,
        source_artifacts=tuple(_artifact(part) for part in (parts or (_PART_A,))),
        tools=(),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint=_CONFIG,
        run_choices=_choices(),
    )


def _layout(tmp_path: Path) -> RunLayout:
    return initialize_run_workspace(
        RunLayout(project_root=tmp_path, source_id=_SOURCE_ID, run_id=_RUN_ID)
    )


def _clock() -> Callable[[], datetime]:
    step = {"n": 0}

    def tick() -> datetime:
        moment = datetime(2026, 8, 16, 8, 30, step["n"] % 60, tzinfo=UTC)
        step["n"] += 1
        return moment

    return tick


class _FakeProbe:
    """A deterministic process probe: a process is live iff its exact identity
    is in ``live`` (matches the fakes used by the lock and control tests)."""

    def __init__(self, identity: ProcessIdentity, live: set[ProcessIdentity]) -> None:
        self._identity = identity
        self._live = set(live)

    def identify(self) -> ProcessIdentity:
        return self._identity

    def is_running(self, identity: ProcessIdentity) -> bool:
        return identity in self._live


_RESUMER = ProcessIdentity(pid=900, start_time="s900")
_ALIVE = _FakeProbe(_RESUMER, {_RESUMER})


def _running_writer(layout: RunLayout) -> RunStateWriter:
    writer = RunStateWriter.create(layout, plan_id=_PLAN_ID, clock=_clock())
    writer.transition_to(RunStatus.QUEUED)
    writer.transition_to(RunStatus.RUNNING)
    return writer


def _completed_executor(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
    return StageResult.completed()


# --- Diagnosis (read-only; powers `vcp status`) -----------------------------


def test_diagnose_paused_run(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    writer = _running_writer(layout)
    apply_pause(writer)
    diagnosis = diagnose_run(layout, lock_path=heavy_task_lock_path(tmp_path), probe=_ALIVE)
    assert diagnosis.case is ResumeCase.PAUSED
    assert diagnosis.is_stale_running is False


def test_diagnose_decision_pause(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    writer = _running_writer(layout)
    writer.record_decision_pause({"reason": "resource_envelope_exceeded", "decision": "changed"})
    diagnosis = diagnose_run(layout, lock_path=heavy_task_lock_path(tmp_path), probe=_ALIVE)
    assert diagnosis.case is ResumeCase.DECISION_PAUSE
    assert diagnosis.required_decision is not None
    assert diagnosis.required_decision["decision"] == "changed"


def test_diagnose_running_with_live_own_lock_is_running_live(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _running_writer(layout)
    holder = ProcessIdentity(pid=100, start_time="s100")
    acquire_heavy_task_lock(
        heavy_task_lock_path(tmp_path),
        run_id=_RUN_ID,
        probe=_FakeProbe(holder, {holder}),
        clock=_clock(),
    )
    diagnosis = diagnose_run(
        layout,
        lock_path=heavy_task_lock_path(tmp_path),
        probe=_FakeProbe(_RESUMER, {holder, _RESUMER}),  # holder still alive
    )
    assert diagnosis.case is ResumeCase.RUNNING_LIVE
    assert diagnosis.is_stale_running is False


def test_diagnose_running_with_stale_lock_is_crash(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _running_writer(layout)
    holder = ProcessIdentity(pid=100, start_time="s100")
    acquire_heavy_task_lock(
        heavy_task_lock_path(tmp_path),
        run_id=_RUN_ID,
        probe=_FakeProbe(holder, {holder}),
        clock=_clock(),
    )
    diagnosis = diagnose_run(
        layout,
        lock_path=heavy_task_lock_path(tmp_path),
        probe=_FakeProbe(_RESUMER, {_RESUMER}),  # holder pid 100 not alive
    )
    assert diagnosis.case is ResumeCase.CRASHED
    assert diagnosis.is_stale_running is True


def test_diagnose_running_with_no_lock_is_crash(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _running_writer(layout)
    diagnosis = diagnose_run(layout, lock_path=heavy_task_lock_path(tmp_path), probe=_ALIVE)
    assert diagnosis.case is ResumeCase.CRASHED


def test_diagnose_running_with_foreign_live_lock_is_crash(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _running_writer(layout)
    foreign = ProcessIdentity(pid=100, start_time="s100")
    acquire_heavy_task_lock(
        heavy_task_lock_path(tmp_path),
        run_id="20260816T090000Z-fedcba9876543210",  # a different run holds it
        probe=_FakeProbe(foreign, {foreign}),
        clock=_clock(),
    )
    diagnosis = diagnose_run(
        layout,
        lock_path=heavy_task_lock_path(tmp_path),
        probe=_FakeProbe(_RESUMER, {foreign, _RESUMER}),
    )
    # This run is not the lock owner: it is no longer executing → crash.
    assert diagnosis.case is ResumeCase.CRASHED


def test_diagnose_terminal_statuses_are_not_resumable(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    writer = _running_writer(layout)
    writer.transition_to(RunStatus.COMPLETE)
    diagnosis = diagnose_run(layout, lock_path=heavy_task_lock_path(tmp_path), probe=_ALIVE)
    assert diagnosis.case is ResumeCase.NOT_RESUMABLE


def test_diagnose_incomplete_without_decision_is_not_resumable(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    writer = _running_writer(layout)
    writer.transition_to(RunStatus.INCOMPLETE)
    diagnosis = diagnose_run(layout, lock_path=heavy_task_lock_path(tmp_path), probe=_ALIVE)
    assert diagnosis.case is ResumeCase.NOT_RESUMABLE


def test_diagnose_never_mutates_state_or_journal(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _running_writer(layout)  # left at `running` — the crash condition
    before_state = layout.state_path.read_bytes()
    before_journal = layout.journal_path.read_bytes()
    diagnose_run(layout, lock_path=heavy_task_lock_path(tmp_path), probe=_ALIVE)
    assert layout.state_path.read_bytes() == before_state
    assert layout.journal_path.read_bytes() == before_journal


# --- Decision validation gate -----------------------------------------------


def test_validate_decision_matches(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    writer = _running_writer(layout)
    writer.record_decision_pause({"reason": "r", "decision": "resource_configuration_changed"})
    assert (
        validate_decision(writer.state, "resource_configuration_changed")
        == "resource_configuration_changed"
    )


def test_validate_decision_absent_is_error(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    writer = _running_writer(layout)
    writer.record_decision_pause({"reason": "r", "decision": "changed"})
    with pytest.raises(RunRecoveryError) as excinfo:
        validate_decision(writer.state, None)
    assert excinfo.value.reason == "decision_required"


def test_validate_decision_mismatch_is_error(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    writer = _running_writer(layout)
    writer.record_decision_pause({"reason": "r", "decision": "changed"})
    with pytest.raises(RunRecoveryError) as excinfo:
        validate_decision(writer.state, "something_else")
    assert excinfo.value.reason == "decision_mismatch"


def test_validate_decision_on_non_decision_run_is_error(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    writer = _running_writer(layout)
    with pytest.raises(RunRecoveryError) as excinfo:
        validate_decision(writer.state, "anything")
    assert excinfo.value.reason == "not_a_decision_pause"


# --- Torn-artifact repair ---------------------------------------------------


def test_repair_journal_tail_leaves_clean_journal_untouched(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _running_writer(layout)
    before = layout.journal_path.read_bytes()
    assert repair_journal_tail(layout.journal_path) == 0
    assert layout.journal_path.read_bytes() == before


def test_repair_journal_tail_drops_partial_final_line(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _running_writer(layout)
    healthy = read_journal(layout.journal_path)
    with layout.journal_path.open("a", encoding="utf-8") as handle:
        handle.write('{"schema_version":1,"sequence":99,"kind":"transi')  # torn, no newline
    assert repair_journal_tail(layout.journal_path) == 1
    assert read_journal(layout.journal_path) == healthy


def test_repair_journal_tail_drops_garbage_terminated_line(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _running_writer(layout)
    healthy = read_journal(layout.journal_path)
    with layout.journal_path.open("a", encoding="utf-8") as handle:
        handle.write("not json at all\n")
    assert repair_journal_tail(layout.journal_path) == 1
    assert read_journal(layout.journal_path) == healthy


def test_repair_run_artifacts_removes_state_temp(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _running_writer(layout)
    tmp = layout.state_path.with_name(layout.state_path.name + ".tmp")
    tmp.write_text("half-written{", encoding="utf-8")
    repair = repair_run_artifacts(layout)
    assert repair.removed_state_tmp is True
    assert not tmp.exists()
    # The last atomically committed state is still readable and authoritative.
    assert read_run_state(layout.state_path).status is RunStatus.RUNNING


# --- Recovery journaling ----------------------------------------------------


def test_record_resume_recovery_partitions_and_journals(tmp_path: Path) -> None:
    plan = _plan(_PART_A)
    keys = compute_invalidation_keys(plan)
    layout = _layout(tmp_path)
    writer = _running_writer(layout)
    completed = StageUnit(StageName.SOURCE_REVALIDATION, COLLECTION_SCOPE)
    failed = StageUnit(StageName.SUBTITLES, _PART_A)
    writer.set_progress(
        stage_units=[
            unit_record(completed, UnitStatus.COMPLETED, keys[completed]),
            unit_record(failed, UnitStatus.FAILED, keys[failed]),
        ]
    )
    outcome = record_resume_recovery(
        writer, plan, recovered_from=RecoveredFrom.STALE_HEAVY_TASK_LOCK, repair=_clean_repair()
    )
    assert outcome.revalidated == (completed,)
    assert outcome.discarded == ((failed, "failed"),)
    events = read_journal(layout.journal_path)
    recovery = [event for event in events if event.kind is EventKind.RECOVERY]
    assert len(recovery) == 1
    assert recovery[0].data["detail"]["recovered_from"] == "stale_heavy_task_lock"


def _clean_repair() -> ArtifactRepair:
    return ArtifactRepair(removed_state_tmp=False, dropped_journal_lines=0)


# --- resume_run coordinator: refusals ---------------------------------------


def test_resume_refuses_a_live_run(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _running_writer(layout)
    holder = ProcessIdentity(pid=100, start_time="s100")
    acquire_heavy_task_lock(
        heavy_task_lock_path(tmp_path),
        run_id=_RUN_ID,
        probe=_FakeProbe(holder, {holder}),
        clock=_clock(),
    )
    before = layout.state_path.read_bytes()
    with pytest.raises(RunRecoveryError) as excinfo:
        resume_run(
            layout=layout,
            plan=_plan(_PART_A),
            executor=_completed_executor,
            lock_path=heavy_task_lock_path(tmp_path),
            probe=_FakeProbe(_RESUMER, {holder, _RESUMER}),
            clock=_clock(),
        )
    assert excinfo.value.reason == "run_is_live"
    assert layout.state_path.read_bytes() == before  # nothing changed


def test_resume_refuses_a_terminal_run(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    writer = _running_writer(layout)
    writer.transition_to(RunStatus.FAILED)
    with pytest.raises(RunRecoveryError) as excinfo:
        resume_run(
            layout=layout,
            plan=_plan(_PART_A),
            executor=_completed_executor,
            lock_path=heavy_task_lock_path(tmp_path),
            probe=_ALIVE,
            clock=_clock(),
        )
    assert excinfo.value.reason == "not_resumable"


# --- resume_run coordinator: decision pause (validate-and-handoff) ----------


def test_resume_decision_match_journals_and_hands_off(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    writer = _running_writer(layout)
    writer.record_decision_pause({"reason": "r", "decision": "resource_configuration_changed"})
    outcome = resume_run(
        layout=layout,
        plan=_plan(_PART_A),
        executor=_completed_executor,
        lock_path=heavy_task_lock_path(tmp_path),
        decision="resource_configuration_changed",
        probe=_ALIVE,
        clock=_clock(),
    )
    assert outcome.action is ResumeAction.DECISION_ACCEPTED
    assert outcome.accepted_decision == "resource_configuration_changed"
    # Handoff: the run is not executed and stays incomplete; a recovery event
    # records the accepted decision.
    assert read_run_state(layout.state_path).status is RunStatus.INCOMPLETE
    recovery = [e for e in read_journal(layout.journal_path) if e.kind is EventKind.RECOVERY]
    assert recovery[-1].data["detail"]["accepted_decision"] == "resource_configuration_changed"


def test_resume_decision_mismatch_changes_nothing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    writer = _running_writer(layout)
    writer.record_decision_pause({"reason": "r", "decision": "changed"})
    before_state = layout.state_path.read_bytes()
    before_journal = layout.journal_path.read_bytes()
    with pytest.raises(RunRecoveryError) as excinfo:
        resume_run(
            layout=layout,
            plan=_plan(_PART_A),
            executor=_completed_executor,
            lock_path=heavy_task_lock_path(tmp_path),
            decision="wrong",
            probe=_ALIVE,
            clock=_clock(),
        )
    assert excinfo.value.reason == "decision_mismatch"
    assert layout.state_path.read_bytes() == before_state
    assert layout.journal_path.read_bytes() == before_journal


# --- resume_run coordinator: paused resume ----------------------------------


def test_resume_paused_adopts_completed_and_finishes(tmp_path: Path) -> None:
    plan = _plan(_PART_A)
    layout = _layout(tmp_path)
    writer = _running_writer(layout)
    first: list[StageUnit] = []

    def stop_after_two(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
        first.append(unit)
        return StageResult.completed()

    def pause_after_two() -> ControlDirective:
        return ControlDirective.PAUSE if len(first) >= 2 else ControlDirective.CONTINUE

    execute_stages(
        writer=writer,
        layout=layout,
        plan=plan,
        executor=stop_after_two,
        on_boundary=pause_after_two,
    )
    assert read_run_state(layout.state_path).status is RunStatus.PAUSED

    second: list[StageUnit] = []

    def resume_executor(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
        second.append(unit)
        return StageResult.completed()

    outcome = resume_run(
        layout=layout,
        plan=plan,
        executor=resume_executor,
        lock_path=heavy_task_lock_path(tmp_path),
        probe=_ALIVE,
        clock=_clock(),
        on_boundary=lambda: ControlDirective.CONTINUE,
    )
    assert outcome.action is ResumeAction.EXECUTED
    assert outcome.stage_result is not None
    assert outcome.stage_result.disposition is StageRunDisposition.COMPLETED_ALL
    # Completed units were adopted, not re-run.
    assert not set(second) & set(first)
    recorded = read_recorded_units(read_run_state(layout.state_path))
    assert set(recorded) == set(plan_stage_units(plan))
    assert all(record.status is UnitStatus.COMPLETED for record in recorded.values())
    assert outcome.recovery is not None
    assert outcome.recovery.recovered_from is RecoveredFrom.USER_PAUSE
