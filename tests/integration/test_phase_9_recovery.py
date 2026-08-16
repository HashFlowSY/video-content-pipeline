"""Ticket 06 acceptance: crash recovery under kill and truncation injection.

These exercises drive the real ticket 03/04/05 primitives through
:func:`~video_content_pipeline.run_recovery.resume_run` over a synthetic project
root — no model, no media, no network. A run is interrupted the way a power loss
or forced kill interrupts one (an executor that raises mid-unit; a half-written
state temp file; a torn journal tail), and resume must recover to a consistent
state losing at most the interrupted unit, revalidate the surviving checkpoints
by their invalidation keys, and journal what it discarded and why.
"""

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
from video_content_pipeline.run_control import ControlDirective
from video_content_pipeline.run_recovery import (
    ResumeAction,
    diagnose_run,
    resume_run,
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
    StageResult,
    StageRunDisposition,
    StageUnit,
    UnitStatus,
    execute_stages,
    plan_stage_units,
    read_recorded_units,
)

_PART_A = "a" * 64
_PLAN_ID = "plan0123456789abcdef0123"
_CONFIG = "cfg" + "0" * 61
_SOURCE_ID = "s" * 64
_RUN_ID = "20260816T083000Z-0123456789abcdef"

_CRASHED_HOLDER = ProcessIdentity(pid=100, start_time="s100")
_RESUMER = ProcessIdentity(pid=900, start_time="s900")


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


def _plan() -> RunPlan:
    choices = RunPlanChoices.build(
        (
            RunChoice(
                stage=STAGE_RUN,
                key=KEY_ASR_MODE,
                scope=COLLECTION_SCOPE,
                value=AsrMode.FULL_ASR.value,
                provenance=ChoiceProvenance.USER_CHOSEN,
            ),
            RunChoice(
                stage=STAGE_RUN,
                key=KEY_VISUAL_TEXT_ENABLED,
                scope=COLLECTION_SCOPE,
                value="false",
                provenance=ChoiceProvenance.USER_CHOSEN,
            ),
        )
    )
    return RunPlan(
        plan_id=_PLAN_ID,
        report_id="0" * 32,
        source_artifacts=(
            SourceArtifact(
                source_id=_PART_A,
                sha256=_PART_A,
                byte_count=1,
                media_path=Path("input") / _PART_A / "media",
            ),
        ),
        tools=(),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint=_CONFIG,
        run_choices=choices,
    )


def _layout(tmp_path: Path) -> RunLayout:
    return initialize_run_workspace(
        RunLayout(project_root=tmp_path, source_id=_SOURCE_ID, run_id=_RUN_ID)
    )


def _crash_after(count: int, executed: list[StageUnit]) -> Callable[..., StageResult]:
    """An executor that completes ``count`` units then dies mid-unit (a kill)."""

    def executor(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
        if len(executed) >= count:
            raise RuntimeError("process killed mid-unit")
        executed.append(unit)
        return StageResult.completed()

    return executor


def _crash_a_running_run(layout: RunLayout, *, completed_units: int) -> list[StageUnit]:
    """Drive a fresh run to ``running``, complete some units, then kill it.

    Leaves ``run-state.json`` at ``running`` with ``completed_units`` checkpointed
    and no clean transition — exactly the on-disk picture a crash leaves behind.
    """

    writer = RunStateWriter.create(layout, plan_id=_PLAN_ID, clock=_clock())
    writer.transition_to(RunStatus.QUEUED)
    writer.transition_to(RunStatus.RUNNING)
    executed: list[StageUnit] = []
    with pytest.raises(RuntimeError):
        execute_stages(
            writer=writer,
            layout=layout,
            plan=_plan(),
            executor=_crash_after(completed_units, executed),
            on_boundary=lambda: ControlDirective.CONTINUE,
        )
    assert read_run_state(layout.state_path).status is RunStatus.RUNNING
    return executed


def _crash_a_pausing_run(layout: RunLayout) -> None:
    """Leave a run wedged at ``pausing`` — a crash inside the pause sequence.

    ``apply_pause`` drives ``running -> pausing -> paused`` as two atomic writes;
    a crash between them persists ``pausing``, which resume must still recover.
    """

    writer = RunStateWriter.create(layout, plan_id=_PLAN_ID, clock=_clock())
    writer.transition_to(RunStatus.QUEUED)
    writer.transition_to(RunStatus.RUNNING)
    writer.transition_to(RunStatus.PAUSING)
    assert read_run_state(layout.state_path).status is RunStatus.PAUSING


# --- Kill injection ---------------------------------------------------------


def test_kill_recovers_losing_at_most_the_interrupted_unit(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    plan = _plan()
    executed_before = _crash_a_running_run(layout, completed_units=2)
    assert len(executed_before) == 2

    # A crash left no lock behind; resume detects the stale-running condition.
    diagnosis = diagnose_run(
        layout, lock_path=heavy_task_lock_path(tmp_path), probe=_FakeProbe(_RESUMER, {_RESUMER})
    )
    assert diagnosis.is_stale_running is True

    resumed: list[StageUnit] = []

    def resume_executor(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
        resumed.append(unit)
        return StageResult.completed()

    outcome = resume_run(
        layout=layout,
        plan=plan,
        executor=resume_executor,
        lock_path=heavy_task_lock_path(tmp_path),
        probe=_FakeProbe(_RESUMER, {_RESUMER}),
        clock=_clock(),
        on_boundary=lambda: ControlDirective.CONTINUE,
    )
    assert outcome.action is ResumeAction.EXECUTED
    assert outcome.stage_result is not None
    assert outcome.stage_result.disposition is StageRunDisposition.COMPLETED_ALL

    all_units = plan_stage_units(plan)
    # The two completed units were revalidated and adopted, not re-run; recovery
    # re-runs everything from the interrupted unit onward — at most one unit is
    # redone, none is lost.
    assert set(resumed) == set(all_units) - set(executed_before)
    assert set(outcome.recovery.revalidated) == set(executed_before)  # type: ignore[union-attr]
    recorded = read_recorded_units(read_run_state(layout.state_path))
    assert set(recorded) == set(all_units)
    assert all(record.status is UnitStatus.COMPLETED for record in recorded.values())


def test_crash_recovery_journals_what_it_revalidated(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _crash_a_running_run(layout, completed_units=2)
    resume_run(
        layout=layout,
        plan=_plan(),
        executor=lambda unit, key: StageResult.completed(),
        lock_path=heavy_task_lock_path(tmp_path),
        probe=_FakeProbe(_RESUMER, {_RESUMER}),
        clock=_clock(),
        on_boundary=lambda: ControlDirective.CONTINUE,
    )
    recovery = [e for e in read_journal(layout.journal_path) if e.kind is EventKind.RECOVERY]
    assert len(recovery) == 1
    detail = recovery[0].data["detail"]
    assert detail["recovered_from"] == "stale_heavy_task_lock"
    assert len(detail["revalidated"]) == 2  # type: ignore[arg-type]
    assert detail["discarded"] == []


def test_crash_recovery_steals_the_stale_lock_and_records_it(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _crash_a_running_run(layout, completed_units=1)
    # The crashed run's heavy-task lock is left behind with a dead holder.
    acquire_heavy_task_lock(
        heavy_task_lock_path(tmp_path),
        run_id=_RUN_ID,
        probe=_FakeProbe(_CRASHED_HOLDER, {_CRASHED_HOLDER}),
        clock=_clock(),
    )
    outcome = resume_run(
        layout=layout,
        plan=_plan(),
        executor=lambda unit, key: StageResult.completed(),
        lock_path=heavy_task_lock_path(tmp_path),
        probe=_FakeProbe(_RESUMER, {_RESUMER}),  # crashed holder pid 100 not alive
        clock=_clock(),
        on_boundary=lambda: ControlDirective.CONTINUE,
    )
    assert outcome.recovery is not None
    assert outcome.recovery.stole_from_run == _RUN_ID


def test_resume_refuses_when_another_live_run_holds_the_lock(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _crash_a_running_run(layout, completed_units=1)
    # A different, live run now holds the single heavy-task lock.
    other = ProcessIdentity(pid=200, start_time="s200")
    acquire_heavy_task_lock(
        heavy_task_lock_path(tmp_path),
        run_id="20260816T090000Z-fedcba9876543210",
        probe=_FakeProbe(other, {other}),
        clock=_clock(),
    )
    before = layout.state_path.read_bytes()
    with pytest.raises(Exception) as excinfo:
        resume_run(
            layout=layout,
            plan=_plan(),
            executor=lambda unit, key: StageResult.completed(),
            lock_path=heavy_task_lock_path(tmp_path),
            probe=_FakeProbe(_RESUMER, {other, _RESUMER}),
            clock=_clock(),
            on_boundary=lambda: ControlDirective.CONTINUE,
        )
    # Fail-fast on the held lock; the run's state is untouched.
    assert getattr(excinfo.value, "reason", "") == "heavy_task_lock_held"
    assert layout.state_path.read_bytes() == before


def test_crash_mid_pause_recovers_via_pausing_to_paused_to_running(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _crash_a_pausing_run(layout)
    diagnosis = diagnose_run(
        layout, lock_path=heavy_task_lock_path(tmp_path), probe=_FakeProbe(_RESUMER, {_RESUMER})
    )
    assert diagnosis.is_stale_running is True  # pausing + no live lock is a crash

    outcome = resume_run(
        layout=layout,
        plan=_plan(),
        executor=lambda unit, key: StageResult.completed(),
        lock_path=heavy_task_lock_path(tmp_path),
        probe=_FakeProbe(_RESUMER, {_RESUMER}),
        clock=_clock(),
        on_boundary=lambda: ControlDirective.CONTINUE,
    )
    # A run wedged at `pausing` is driven back to running through legal edges and
    # completes — the recovery is not a dead end.
    assert outcome.action is ResumeAction.EXECUTED
    assert outcome.stage_result is not None
    assert outcome.stage_result.disposition is StageRunDisposition.COMPLETED_ALL
    assert read_run_state(layout.state_path).status is RunStatus.RUNNING
    # The legal recovery path is journaled: pausing -> paused -> running.
    tos = [
        event.data.get("to")
        for event in read_journal(layout.journal_path)
        if event.kind is EventKind.TRANSITION
    ]
    assert tos[-2:] == ["paused", "running"]


# --- Truncation injection ---------------------------------------------------


def test_torn_state_temp_file_is_cleaned_and_run_recovers(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _crash_a_running_run(layout, completed_units=2)
    # A crash between the temp write and the atomic rename left a half-written
    # run-state.json.tmp beside the last good run-state.json.
    tmp_artifact = layout.state_path.with_name(layout.state_path.name + ".tmp")
    tmp_artifact.write_text('{"schema_version":1,"status":"runni', encoding="utf-8")

    outcome = resume_run(
        layout=layout,
        plan=_plan(),
        executor=lambda unit, key: StageResult.completed(),
        lock_path=heavy_task_lock_path(tmp_path),
        probe=_FakeProbe(_RESUMER, {_RESUMER}),
        clock=_clock(),
        on_boundary=lambda: ControlDirective.CONTINUE,
    )
    assert outcome.recovery is not None
    assert outcome.recovery.repair.removed_state_tmp is True
    assert not tmp_artifact.exists()
    assert outcome.stage_result is not None
    assert outcome.stage_result.disposition is StageRunDisposition.COMPLETED_ALL


def test_truncated_journal_tail_is_repaired_and_run_recovers(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _crash_a_running_run(layout, completed_units=2)
    # A crash mid-append left a torn final line in the append-only journal.
    with layout.journal_path.open("a", encoding="utf-8") as handle:
        handle.write('{"schema_version":1,"sequence":42,"kind":"transition","at":"2026')

    outcome = resume_run(
        layout=layout,
        plan=_plan(),
        executor=lambda unit, key: StageResult.completed(),
        lock_path=heavy_task_lock_path(tmp_path),
        probe=_FakeProbe(_RESUMER, {_RESUMER}),
        clock=_clock(),
        on_boundary=lambda: ControlDirective.CONTINUE,
    )
    assert outcome.recovery is not None
    assert outcome.recovery.repair.dropped_journal_lines == 1
    assert outcome.stage_result is not None
    assert outcome.stage_result.disposition is StageRunDisposition.COMPLETED_ALL
    # The repaired journal is strictly readable and the recovery event is on it.
    events = read_journal(layout.journal_path)
    assert any(event.kind is EventKind.RECOVERY for event in events)
    # Sequence numbers are contiguous after the torn tail was dropped.
    sequences = [event.sequence for event in events]
    assert sequences == list(range(len(sequences)))


def test_status_diagnosis_on_crash_mutates_nothing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _crash_a_running_run(layout, completed_units=1)
    before_state = layout.state_path.read_bytes()
    before_journal = layout.journal_path.read_bytes()
    diagnosis = diagnose_run(
        layout, lock_path=heavy_task_lock_path(tmp_path), probe=_FakeProbe(_RESUMER, {_RESUMER})
    )
    assert diagnosis.is_stale_running is True
    assert diagnosis.status is RunStatus.RUNNING
    # `vcp status` reports the stale-running diagnosis without persisting a crash.
    assert layout.state_path.read_bytes() == before_state
    assert layout.journal_path.read_bytes() == before_journal
