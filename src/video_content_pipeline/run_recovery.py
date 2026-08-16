"""Resume and crash recovery for a Run: the three cases of ``vcp resume``.

A run process is the sole writer of its state and journal (ADR 0053), and it
exits at a clean stage-unit boundary for a user pause, or dies without any
transition on a crash. This module is the *next* process's side of that story:
it diagnoses what an on-disk run needs, and drives the three-case ``vcp resume``
contract over the ticket 03/04/05 primitives.

- **Paused resume**: a run that exited cleanly at ``paused`` is reopened, its
  heavy-task lock re-acquired, its adoptable checkpoints revalidated, and it
  transitions ``paused -> running`` and continues.
- **Decision-pause resume**: a run parked at ``incomplete`` with a
  machine-readable required decision. Because plan §12 gives ``incomplete`` no
  outward edge, this module owns only the *validation gate* — a supplied
  ``--decision`` must match the recorded requirement; a mismatch or an absent
  decision is an error that changes nothing. On a match it journals an
  accepted-decision recovery event and hands off; re-execution is a later
  contract (a fresh continuation run), never an in-process transition out of a
  terminal status.
- **Crash recovery**: a run whose state says ``running`` (or ``pausing``) while
  its heavy-task lock is *not* live — the detected stale-running condition that
  a crash leaves behind (ADR 0053). A crash is never a persisted state, so
  :func:`diagnose_run` reports it read-only for ``vcp status`` without touching
  anything. Recovery repairs any torn write artifacts, reopens, revalidates the
  checkpointed units by their invalidation keys, journals a recovery event
  recording what was revalidated and what was discarded and why, and continues
  from the last checkpoint — at most the interrupted unit is re-run.

A run whose own heavy-task lock is *live* is still running: resume refuses it
with a clear reason rather than racing a live writer.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from video_content_pipeline.durable_io import atomic_replace, utc_now
from video_content_pipeline.heavy_task_lock import (
    LockInspection,
    ProcessProbe,
    acquire_heavy_task_lock,
    inspect_heavy_task_lock,
)
from video_content_pipeline.orchestration import RunLayout
from video_content_pipeline.planning import RunPlan
from video_content_pipeline.run_control import ControlDirective
from video_content_pipeline.run_state import (
    EVENT_SCHEMA_VERSION,
    EventKind,
    RunState,
    RunStateWriter,
    RunStatus,
    read_run_state,
)
from video_content_pipeline.stage_dag import (
    RecordedUnit,
    StageExecutor,
    StageInvalidationKey,
    StageRunResult,
    StageUnit,
    UnitStatus,
    adoptable_units,
    compute_invalidation_keys,
    execute_stages,
    read_recorded_units,
)


class RunRecoveryError(ValueError):
    """A resume/recovery failure with an auditable machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class ResumeCase(StrEnum):
    """Which of the resume contract's cases an on-disk run falls into.

    ``PAUSED`` and ``CRASHED`` are executed; ``DECISION_PAUSE`` is validated and
    handed off; ``RUNNING_LIVE`` and ``NOT_RESUMABLE`` are refused. The last two
    are still meaningful to :func:`diagnose_run`'s ``vcp status`` callers, which
    only report the classification.
    """

    PAUSED = "paused"
    DECISION_PAUSE = "decision_pause"
    CRASHED = "crashed"
    RUNNING_LIVE = "running_live"
    NOT_RESUMABLE = "not_resumable"


class ResumeAction(StrEnum):
    """What :func:`resume_run` actually did for a resumable run."""

    #: The run re-acquired its lock, recovered, and ran the stage DAG.
    EXECUTED = "executed"
    #: A matching decision was validated and journaled; execution is handed off.
    DECISION_ACCEPTED = "decision_accepted"


class RecoveredFrom(StrEnum):
    """The condition a resume recovered from, recorded in the recovery event."""

    #: A run that exited cleanly at ``paused`` and is being continued.
    USER_PAUSE = "user_pause"
    #: A stale-running crash: ``running``/``pausing`` state with a non-live lock.
    STALE_HEAVY_TASK_LOCK = "stale_heavy_task_lock"
    #: A decision pause whose matching decision was accepted (validate-and-handoff).
    DECISION_PAUSE = "decision_pause"


#: The run statuses whose live-lock question decides crash vs. live: a process
#: dies mid-``running`` or mid-``pausing`` without a clean transition, so either
#: status paired with a non-live lock is the stale-running crash condition.
_ACTIVE_STATUSES: frozenset[RunStatus] = frozenset({RunStatus.RUNNING, RunStatus.PAUSING})


@dataclass(frozen=True)
class RunDiagnosis:
    """A read-only classification of an on-disk run for status and resume.

    Carries the persisted ``status``, the resume ``case`` it maps to, the
    heavy-task lock inspection (``None`` when no lock file exists), and the
    machine-readable ``required_decision`` for a decision pause. Producing this
    never mutates state, journal, or lock — a crash is a *detected* condition,
    so ``vcp status`` can report the stale-running diagnosis without touching
    anything (ADR 0053).
    """

    status: RunStatus
    case: ResumeCase
    lock: LockInspection | None
    required_decision: Mapping[str, object] | None

    @property
    def is_stale_running(self) -> bool:
        """Whether this is the crash condition: an active status, no live lock."""

        return self.case is ResumeCase.CRASHED

    def to_document(self) -> dict[str, object]:
        """A deterministic machine-readable view for ``vcp status`` output."""

        document: dict[str, object] = {
            "status": self.status.value,
            "resume_case": self.case.value,
            "stale_running": self.is_stale_running,
        }
        if self.lock is not None:
            document["lock"] = {
                "run_id": self.lock.holder.run_id,
                "is_stale": self.lock.is_stale,
                "reason": self.lock.reason,
            }
        if self.required_decision is not None:
            document["required_decision"] = dict(self.required_decision)
        return document


@dataclass(frozen=True)
class RecoveryOutcome:
    """What a resume recovered before continuing: kept, discarded, and repairs.

    ``revalidated`` are the recorded units re-used by run-scoped adoption (their
    invalidation keys still match); ``discarded`` are the recorded units the
    resume will re-run, each with the reason it was not adoptable. ``repair``
    records any torn write artifacts cleaned up on the way in.
    """

    recovered_from: RecoveredFrom
    revalidated: tuple[StageUnit, ...]
    discarded: tuple[tuple[StageUnit, str], ...]
    repair: ArtifactRepair
    stole_from_run: str | None = None

    def to_document(self) -> dict[str, object]:
        document: dict[str, object] = {
            "recovered_from": self.recovered_from.value,
            "revalidated": [_unit_ref(unit) for unit in self.revalidated],
            "discarded": [{**_unit_ref(unit), "reason": reason} for unit, reason in self.discarded],
            "repair": self.repair.to_document(),
        }
        if self.stole_from_run is not None:
            document["stole_from_run"] = self.stole_from_run
        return document


@dataclass(frozen=True)
class ArtifactRepair:
    """Torn write artifacts cleaned up before a run's journal is reopened.

    A crash can leave a half-written ``run-state.json.tmp`` (the temp side of an
    atomic replace that never renamed) and a torn final line in the append-only
    ``events.jsonl``. The last good ``run-state.json`` is always intact — the
    rename is atomic — so only the temp artifact and the journal tail need
    repair before :meth:`RunStateWriter.reopen` reads a strict journal.
    """

    removed_state_tmp: bool
    dropped_journal_lines: int

    @property
    def repaired_anything(self) -> bool:
        return self.removed_state_tmp or self.dropped_journal_lines > 0

    def to_document(self) -> dict[str, object]:
        return {
            "removed_state_tmp": self.removed_state_tmp,
            "dropped_journal_lines": self.dropped_journal_lines,
        }


@dataclass(frozen=True)
class ResumeOutcome:
    """The result of a :func:`resume_run` call."""

    case: ResumeCase
    action: ResumeAction
    diagnosis: RunDiagnosis
    stage_result: StageRunResult | None = None
    recovery: RecoveryOutcome | None = None
    accepted_decision: str | None = None


def _unit_ref(unit: StageUnit) -> dict[str, object]:
    return {"stage": unit.stage.value, "scope": unit.scope}


def _unit_sort_key(unit: StageUnit) -> tuple[str, str]:
    return (unit.stage.value, unit.scope)


def diagnose_run(
    layout: RunLayout,
    *,
    lock_path: Path,
    probe: ProcessProbe | None = None,
) -> RunDiagnosis:
    """Classify an on-disk run for ``vcp status`` and resume dispatch (read-only).

    Reads only ``run-state.json`` and inspects the heavy-task lock; it never
    writes, so it is safe to call against a crashed run. The crash condition is
    an active status (``running``/``pausing``) whose lock is not this run's own
    live holder — a stale, missing, or foreign lock all mean this run is no
    longer executing.
    """

    state = read_run_state(layout.state_path)
    lock = inspect_heavy_task_lock(lock_path, probe=probe)
    owned_live = lock is not None and lock.holder.run_id == layout.run_id and not lock.is_stale
    status = state.status
    if status in _ACTIVE_STATUSES:
        case = ResumeCase.RUNNING_LIVE if owned_live else ResumeCase.CRASHED
    elif status is RunStatus.PAUSED:
        case = ResumeCase.PAUSED
    elif status is RunStatus.INCOMPLETE and state.required_decision:
        case = ResumeCase.DECISION_PAUSE
    else:
        case = ResumeCase.NOT_RESUMABLE
    return RunDiagnosis(
        status=status,
        case=case,
        lock=lock,
        required_decision=state.required_decision,
    )


def validate_decision(state: RunState, decision: str | None) -> str:
    """Return the required decision token iff ``decision`` matches it.

    The gate for decision-pause resume: the run must be parked at ``incomplete``
    with a machine-readable required decision, and ``decision`` must equal its
    recorded ``decision`` token. A missing or mismatched decision raises — and
    raising before any write is what makes "an error that changes nothing" true.
    """

    required = state.required_decision
    if state.status is not RunStatus.INCOMPLETE or not required:
        raise RunRecoveryError(
            "not_a_decision_pause",
            "This run is not paused for a decision, so --decision does not apply.",
        )
    expected = required.get("decision")
    if not isinstance(expected, str) or not expected:
        raise RunRecoveryError(
            "decision_requirement_invalid",
            "The recorded required decision has no decision token to match.",
        )
    if decision is None:
        raise RunRecoveryError(
            "decision_required",
            f"This run is paused for decision {expected!r}; resume with a matching --decision.",
        )
    if decision != expected:
        raise RunRecoveryError(
            "decision_mismatch",
            f"Decision {decision!r} does not match the required decision {expected!r}.",
        )
    return expected


def _state_tmp_path(layout: RunLayout) -> Path:
    return layout.state_path.with_name(layout.state_path.name + ".tmp")


def _parses_as_event(line: str) -> bool:
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return False
    if not isinstance(record, Mapping):
        return False
    if record.get("schema_version") != EVENT_SCHEMA_VERSION:
        return False
    kind = record.get("kind")
    if not isinstance(kind, str):
        return False
    try:
        EventKind(kind)
    except ValueError:
        return False
    return isinstance(record.get("sequence"), int) and not isinstance(record.get("sequence"), bool)


def repair_journal_tail(path: Path) -> int:
    """Drop a torn trailing record from ``events.jsonl``; return lines dropped.

    Every clean append writes a whole ``line + "\\n"`` and fsyncs it, so a crash
    can only corrupt the *tail*: a record missing its trailing newline, or a
    partially flushed final line. The maximal prefix of newline-terminated,
    schema-valid event lines is kept; anything after the last healthy record is
    dropped and the file is rewritten atomically. A clean journal is left
    untouched (returns ``0``), so a repair on a healthy resume is a no-op.
    """

    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return 0
    if not raw:
        return 0
    segments = raw.split("\n")
    ends_with_newline = segments[-1] == ""
    if ends_with_newline:
        segments = segments[:-1]
    kept: list[str] = []
    for index, segment in enumerate(segments):
        is_last = index == len(segments) - 1
        if is_last and not ends_with_newline:
            break  # a partial final line that never got its terminating newline
        if not _parses_as_event(segment):
            break
        kept.append(segment)
    dropped = len(segments) - len(kept)
    if dropped == 0:
        return 0
    payload = "".join(f"{line}\n" for line in kept)
    atomic_replace(path, payload)
    return dropped


def repair_run_artifacts(layout: RunLayout) -> ArtifactRepair:
    """Clean torn write artifacts so a reopen sees consistent state and journal.

    Removes a leftover ``run-state.json.tmp`` (a crash between the temp write and
    the atomic rename) and repairs a torn ``events.jsonl`` tail. The surviving
    ``run-state.json`` is the last atomically committed checkpoint, so at most
    the interrupted unit's checkpoint is lost.
    """

    tmp_path = _state_tmp_path(layout)
    removed_tmp = False
    try:
        tmp_path.unlink()
        removed_tmp = True
    except FileNotFoundError:
        pass
    dropped = repair_journal_tail(layout.journal_path)
    return ArtifactRepair(removed_state_tmp=removed_tmp, dropped_journal_lines=dropped)


def _discard_reason(record: RecordedUnit, fresh: Mapping[StageUnit, StageInvalidationKey]) -> str:
    if record.status is not UnitStatus.COMPLETED:
        # A failed or blocked unit was never a checkpoint to adopt; a resume is
        # an explicit retry of it (plan §12: no automatic retry, but resume is).
        return record.status.value
    if record.unit not in fresh:
        return "unit_no_longer_planned"
    return "invalidation_key_changed"


def record_resume_recovery(
    writer: RunStateWriter,
    plan: RunPlan,
    *,
    recovered_from: RecoveredFrom,
    repair: ArtifactRepair,
    stole_from_run: str | None = None,
) -> RecoveryOutcome:
    """Journal what a resume revalidated and discarded, then return the outcome.

    Revalidation-before-use over this run's own recorded units (ADR 0052): a
    recorded ``completed`` unit whose freshly recomputed invalidation key still
    matches is revalidated and adopted; every other recorded unit is discarded
    with its reason and will be re-run. The recovery event captures the full
    picture *before* :func:`execute_stages` prunes the state to the adoptable
    set, so the audit of what was discarded is never lost.
    """

    recorded = read_recorded_units(writer.state)
    fresh = compute_invalidation_keys(plan)
    adoptable = adoptable_units(recorded, fresh)
    revalidated = tuple(sorted(adoptable, key=_unit_sort_key))
    discarded = tuple(
        (unit, _discard_reason(recorded[unit], fresh))
        for unit in sorted(set(recorded) - adoptable, key=_unit_sort_key)
    )
    outcome = RecoveryOutcome(
        recovered_from=recovered_from,
        revalidated=revalidated,
        discarded=discarded,
        repair=repair,
        stole_from_run=stole_from_run,
    )
    writer.record_recovery(outcome.to_document())
    return outcome


def _drive_to_running(writer: RunStateWriter) -> None:
    """Move a reopened run to ``running`` through legal §12 edges, if needed.

    A plain ``running`` crash needs no transition; a ``paused`` run takes
    ``paused -> running``; a run that crashed mid-pause (``pausing``) travels the
    only legal path back, ``pausing -> paused -> running``. Every hop is a real
    journaled transition, so no edge outside :data:`_ALLOWED_TRANSITIONS` is ever
    taken.
    """

    if writer.state.status is RunStatus.PAUSING:
        writer.transition_to(RunStatus.PAUSED)
    if writer.state.status is RunStatus.PAUSED:
        writer.transition_to(RunStatus.RUNNING)


def resume_run(
    *,
    layout: RunLayout,
    plan: RunPlan,
    executor: StageExecutor,
    lock_path: Path,
    decision: str | None = None,
    probe: ProcessProbe | None = None,
    clock: Callable[[], datetime] = utc_now,
    on_boundary: Callable[[], ControlDirective] | None = None,
) -> ResumeOutcome:
    """Resume an on-disk run: continue a pause, answer a decision, or recover.

    Diagnoses the run (read-only), then:

    - ``RUNNING_LIVE`` / ``NOT_RESUMABLE``: refuse with a clear reason, changing
      nothing.
    - ``DECISION_PAUSE``: validate ``decision`` against the recorded requirement
      — mismatch or absence raises before any write — then journal an
      accepted-decision recovery event and hand off (no execution).
    - ``PAUSED`` / ``CRASHED``: acquire the heavy-task lock (fail fast if another
      live run holds it), repair torn artifacts, reopen, journal the recovery,
      normalize the reopened status to ``running`` through legal §12 edges
      (``paused -> running``; a run that crashed mid-pause travels
      ``pausing -> paused -> running``; a plain ``running`` crash needs none),
      and run the stage DAG from the last checkpoint.
    """

    diagnosis = diagnose_run(layout, lock_path=lock_path, probe=probe)
    case = diagnosis.case
    if case is ResumeCase.RUNNING_LIVE:
        raise RunRecoveryError(
            "run_is_live",
            "The run is still running: its heavy-task lock is held by a live process.",
        )
    if case is ResumeCase.NOT_RESUMABLE:
        raise RunRecoveryError(
            "not_resumable",
            f"A run in status {diagnosis.status.value} cannot be resumed.",
        )
    if case is ResumeCase.DECISION_PAUSE:
        state = read_run_state(layout.state_path)
        accepted = validate_decision(state, decision)
        writer = RunStateWriter.reopen(layout, clock=clock)
        writer.record_recovery(
            {
                "recovered_from": RecoveredFrom.DECISION_PAUSE.value,
                "accepted_decision": accepted,
                "required_decision": dict(state.required_decision or {}),
            }
        )
        return ResumeOutcome(
            case=case,
            action=ResumeAction.DECISION_ACCEPTED,
            diagnosis=diagnosis,
            accepted_decision=accepted,
        )

    # PAUSED or CRASHED: acquire first so a held lock fails fast before any write.
    with acquire_heavy_task_lock(lock_path, run_id=layout.run_id, probe=probe, clock=clock) as lock:
        repair = repair_run_artifacts(layout)
        writer = RunStateWriter.reopen(layout, clock=clock)
        recovered_from = (
            RecoveredFrom.USER_PAUSE
            if case is ResumeCase.PAUSED
            else RecoveredFrom.STALE_HEAVY_TASK_LOCK
        )
        stole_from = lock.stole_from.run_id if lock.stole_from is not None else None
        recovery = record_resume_recovery(
            writer,
            plan,
            recovered_from=recovered_from,
            repair=repair,
            stole_from_run=stole_from,
        )
        _drive_to_running(writer)
        result = execute_stages(
            writer=writer,
            layout=layout,
            plan=plan,
            executor=executor,
            on_boundary=on_boundary,
        )
    return ResumeOutcome(
        case=case,
        action=ResumeAction.EXECUTED,
        diagnosis=diagnosis,
        stage_result=result,
        recovery=recovery,
    )
