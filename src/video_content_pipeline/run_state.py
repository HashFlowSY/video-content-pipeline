"""Single-writer Run state document and append-only Run events journal.

A Run's authority lives in two files under ``work/<source-id>/<run-id>/`` (the
:class:`~video_content_pipeline.orchestration.RunLayout`): ``run-state.json``,
the current snapshot, and ``events.jsonl``, the audit journal. The run process
is the sole writer of both (ADR 0053): ``run-state.json`` is always replaced
atomically (temp-then-rename on the same filesystem) so a reader never observes
a torn document, and ``events.jsonl`` is only ever appended to, so its history
is never rewritten. Command-side code paths (``pause``, ``cancel``,
diagnostics, inventory) reach these files through the read-only functions in
this module — :func:`read_run_state` and :func:`read_journal` — and have no
write API against them; the only mutations are the methods of
:class:`RunStateWriter`, which a run process owns.

The state machine is exactly plan §12: ``planned -> queued -> running ->
complete | complete_with_warnings | incomplete | failed | cancelled`` with the
user-pause loop ``running -> pausing -> paused -> running``. No other transition
is representable — :meth:`RunStateWriter.transition_to` refuses any edge not in
:data:`_ALLOWED_TRANSITIONS`, so an illegal state change cannot reach disk. The
state document carries the run's status alongside the plan §12 slots the later
orchestration tickets populate — stage units, adopted outputs, invalidation
keys, and a machine-readable required decision — and every transition, control
observation, decision pause, and recovery leaves a journal event.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from video_content_pipeline.orchestration import RunLayout

STATE_SCHEMA_VERSION = 1
EVENT_SCHEMA_VERSION = 1

_STATE_TMP_SUFFIX = ".tmp"


class RunStateError(ValueError):
    """A run-state failure with an auditable machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class RunStatus(StrEnum):
    """The plan §12 run statuses; no status outside this set is representable."""

    PLANNED = "planned"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    COMPLETE = "complete"
    COMPLETE_WITH_WARNINGS = "complete_with_warnings"
    INCOMPLETE = "incomplete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EventKind(StrEnum):
    """The audited kinds of journal event (ADR 0053)."""

    TRANSITION = "transition"
    CONTROL_REQUEST_OBSERVED = "control_request_observed"
    DECISION_PAUSE = "decision_pause"
    RECOVERY = "recovery"


#: The terminal statuses: a run that reaches one of these never transitions
#: again in this process. ``incomplete`` is where a resource-envelope or
#: model-acquisition decision pause exits; the plan §12 machine has no edge out
#: of any terminal status, so recovering from a decision pause is a later-ticket
#: ``vcp resume`` concern (a fresh run or process), never an in-process
#: transition from ``incomplete``.
_TERMINAL_STATUSES: frozenset[RunStatus] = frozenset(
    {
        RunStatus.COMPLETE,
        RunStatus.COMPLETE_WITH_WARNINGS,
        RunStatus.INCOMPLETE,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }
)

#: The exact plan §12 transition graph. A status maps to the statuses it may
#: move to; terminal statuses map to the empty set. This table is the single
#: definition of "no other transition is representable".
_ALLOWED_TRANSITIONS: Mapping[RunStatus, frozenset[RunStatus]] = {
    RunStatus.PLANNED: frozenset({RunStatus.QUEUED}),
    RunStatus.QUEUED: frozenset({RunStatus.RUNNING}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.PAUSING,
            RunStatus.COMPLETE,
            RunStatus.COMPLETE_WITH_WARNINGS,
            RunStatus.INCOMPLETE,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.PAUSING: frozenset({RunStatus.PAUSED}),
    RunStatus.PAUSED: frozenset({RunStatus.RUNNING}),
    RunStatus.COMPLETE: frozenset(),
    RunStatus.COMPLETE_WITH_WARNINGS: frozenset(),
    RunStatus.INCOMPLETE: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _canonical_line(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _durable_write(path: Path, text: str, *, flags: int) -> None:
    """Write ``text`` to ``path`` under ``flags`` and fsync before returning.

    Shared by the atomic state replace (into a temp file the caller renames) and
    the append-only journal, so both the state document and every journal line
    are flushed to stable storage before the write is considered done — the
    durability the crash-recovery path (ADR 0053) relies on.
    """

    descriptor = os.open(path, flags, 0o644)
    try:
        os.write(descriptor, text.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _instant(value: datetime, *, reason: str) -> str:
    if value.tzinfo is None:
        raise RunStateError(reason, "A run-state timestamp must be timezone-aware.")
    return value.astimezone(UTC).isoformat()


@dataclass(frozen=True)
class RunState:
    """The current snapshot of a run, serialized to ``run-state.json``.

    Beyond ``status`` this carries the plan §12 slots that keep a run's picture
    consistent on disk: ``stage_units`` (the atomic ``(stage, Part)`` units and
    their progress), ``adopted_outputs`` (run-scoped adoptions), and
    ``invalidation_keys`` (per-stage keys, ADR 0052) — all stored as faithful
    JSON structures that later tickets shape and populate — plus
    ``required_decision``, the machine-readable decision a decision pause
    records. The fields default empty so the initial ``planned`` document is
    valid before any stage runs.
    """

    source_id: str
    run_id: str
    plan_id: str
    status: RunStatus
    stage_units: tuple[Mapping[str, object], ...] = ()
    adopted_outputs: tuple[Mapping[str, object], ...] = ()
    invalidation_keys: Mapping[str, object] = field(default_factory=dict)
    required_decision: Mapping[str, object] | None = None
    schema_version: int = STATE_SCHEMA_VERSION

    def to_document(self) -> dict[str, object]:
        """Return the deterministic JSON document for this snapshot."""

        return {
            "schema_version": self.schema_version,
            "source_id": self.source_id,
            "run_id": self.run_id,
            "plan_id": self.plan_id,
            "status": self.status.value,
            "stage_units": [dict(unit) for unit in self.stage_units],
            "adopted_outputs": [dict(output) for output in self.adopted_outputs],
            "invalidation_keys": dict(self.invalidation_keys),
            "required_decision": (
                dict(self.required_decision) if self.required_decision is not None else None
            ),
        }


@dataclass(frozen=True)
class RunEvent:
    """One append-only journal record read back from ``events.jsonl``."""

    sequence: int
    at: str
    kind: EventKind
    data: Mapping[str, object]
    schema_version: int = EVENT_SCHEMA_VERSION


def _parse_status(value: object, *, reason: str) -> RunStatus:
    if not isinstance(value, str):
        raise RunStateError(reason, "A run status must be a string.")
    try:
        return RunStatus(value)
    except ValueError as error:
        raise RunStateError(reason, f"Unknown run status {value!r}.") from error


def read_run_state(path: Path) -> RunState:
    """Read ``run-state.json`` as a validated :class:`RunState` (read-only)."""

    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise RunStateError("run_state_missing", f"No run state at {path}.") from error
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RunStateError("run_state_unreadable", f"Run state at {path} is not JSON.") from error
    if not isinstance(document, Mapping):
        raise RunStateError("run_state_invalid", "Run state must be a JSON object.")
    if document.get("schema_version") != STATE_SCHEMA_VERSION:
        raise RunStateError(
            "run_state_schema_mismatch",
            f"Run state schema_version must be {STATE_SCHEMA_VERSION}.",
        )
    required_decision = document.get("required_decision")
    if required_decision is not None and not isinstance(required_decision, Mapping):
        raise RunStateError("run_state_invalid", "required_decision must be an object or null.")
    return RunState(
        source_id=_required_str(document, "source_id", reason="run_state_invalid"),
        run_id=_required_str(document, "run_id", reason="run_state_invalid"),
        plan_id=_required_str(document, "plan_id", reason="run_state_invalid"),
        status=_parse_status(document.get("status"), reason="run_state_invalid"),
        stage_units=_mapping_tuple(document.get("stage_units", []), field_name="stage_units"),
        adopted_outputs=_mapping_tuple(
            document.get("adopted_outputs", []), field_name="adopted_outputs"
        ),
        invalidation_keys=_mapping(
            document.get("invalidation_keys", {}), field_name="invalidation_keys"
        ),
        required_decision=dict(required_decision) if required_decision is not None else None,
    )


def read_journal(path: Path) -> tuple[RunEvent, ...]:
    """Read ``events.jsonl`` as validated events in append order (read-only)."""

    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise RunStateError("journal_missing", f"No events journal at {path}.") from error
    events: list[RunEvent] = []
    for line_number, line in enumerate(raw.splitlines()):
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise RunStateError(
                "journal_unreadable",
                f"Events journal line {line_number + 1} is not JSON.",
            ) from error
        if not isinstance(record, Mapping):
            raise RunStateError("journal_invalid", "A journal event must be a JSON object.")
        if record.get("schema_version") != EVENT_SCHEMA_VERSION:
            raise RunStateError(
                "journal_schema_mismatch",
                f"Journal event schema_version must be {EVENT_SCHEMA_VERSION}.",
            )
        kind_value = record.get("kind")
        if not isinstance(kind_value, str):
            raise RunStateError("journal_invalid", "A journal event needs a string kind.")
        try:
            kind = EventKind(kind_value)
        except ValueError as error:
            raise RunStateError("journal_invalid", f"Unknown event kind {kind_value!r}.") from error
        events.append(
            RunEvent(
                sequence=_required_int(record, "sequence"),
                at=_required_str(record, "at", reason="journal_invalid"),
                kind=kind,
                data=_mapping(record.get("data", {}), field_name="data"),
            )
        )
    return tuple(events)


def _required_str(document: Mapping[str, object], key: str, *, reason: str) -> str:
    value = document.get(key)
    if not isinstance(value, str):
        raise RunStateError(reason, f"{key} must be a string.")
    return value


def _required_int(document: Mapping[str, object], key: str) -> int:
    value = document.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise RunStateError("journal_invalid", f"{key} must be an integer.")
    return value


def _mapping(value: object, *, field_name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise RunStateError("run_state_invalid", f"{field_name} must be an object.")
    return dict(value)


def _mapping_tuple(value: object, *, field_name: str) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise RunStateError("run_state_invalid", f"{field_name} must be a list.")
    result: list[Mapping[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise RunStateError("run_state_invalid", f"Each {field_name} entry must be an object.")
        result.append(dict(item))
    return tuple(result)


class RunStateWriter:
    """The run process's sole write authority over one run's state and journal.

    A run process obtains a writer with :meth:`create` (a fresh run, entering at
    ``planned``) or :meth:`reopen` (a resuming process, continuing the existing
    state and journal sequence). Every mutating method replaces
    ``run-state.json`` atomically and appends exactly the ADR 0053 events; there
    is deliberately no way to move to an illegal status or to rewrite journal
    history. Construct one directly only for advanced cases — prefer the
    classmethods, which own the on-disk lifecycle.
    """

    def __init__(
        self,
        *,
        state_path: Path,
        journal_path: Path,
        state: RunState,
        next_sequence: int,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._state_path = state_path
        self._journal_path = journal_path
        self._state = state
        self._next_sequence = next_sequence
        self._clock = clock

    @property
    def state(self) -> RunState:
        """The current in-memory snapshot, identical to what is on disk."""

        return self._state

    @classmethod
    def create(
        cls,
        layout: RunLayout,
        *,
        plan_id: str,
        clock: Callable[[], datetime] = _utc_now,
    ) -> RunStateWriter:
        """Begin a fresh run at ``planned``, writing the first state and event.

        Refuses to clobber an existing state document so a live run's authority
        is never silently replaced; the run workspace must already exist
        (:func:`~video_content_pipeline.orchestration.initialize_run_workspace`).
        """

        state_path = layout.state_path
        journal_path = layout.journal_path
        if state_path.exists():
            raise RunStateError(
                "run_state_exists",
                f"A run state already exists at {state_path}; reopen it instead.",
            )
        if not state_path.parent.is_dir():
            raise RunStateError(
                "run_workspace_missing",
                f"The run workspace {state_path.parent} does not exist.",
            )
        state = RunState(
            source_id=layout.source_id,
            run_id=layout.run_id,
            plan_id=plan_id,
            status=RunStatus.PLANNED,
        )
        writer = cls(
            state_path=state_path,
            journal_path=journal_path,
            state=state,
            next_sequence=0,
            clock=clock,
        )
        writer._write_state(state)
        writer._append_event(
            EventKind.TRANSITION,
            {"from": None, "to": RunStatus.PLANNED.value},
        )
        return writer

    @classmethod
    def reopen(
        cls,
        layout: RunLayout,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> RunStateWriter:
        """Reattach a new run process to an existing run's state and journal.

        The journal's next sequence continues after the last recorded event, so
        a resuming process never rewinds or duplicates the append-only history.
        The status is not changed here; the resuming process drives the machine
        (for example ``paused -> running``) and journals a recovery event.
        """

        state = read_run_state(layout.state_path)
        events = read_journal(layout.journal_path)
        next_sequence = events[-1].sequence + 1 if events else 0
        return cls(
            state_path=layout.state_path,
            journal_path=layout.journal_path,
            state=state,
            next_sequence=next_sequence,
            clock=clock,
        )

    def transition_to(
        self,
        target: RunStatus,
        *,
        detail: Mapping[str, object] | None = None,
    ) -> RunState:
        """Move to ``target`` if plan §12 permits it, journaling the transition.

        Raises :class:`RunStateError` (``illegal_transition``) for any edge
        outside :data:`_ALLOWED_TRANSITIONS`, so an unrepresented transition can
        never reach disk.
        """

        current = self._state.status
        if target not in _ALLOWED_TRANSITIONS[current]:
            raise RunStateError(
                "illegal_transition",
                f"A run may not move from {current.value} to {target.value}.",
            )
        updated = replace(self._state, status=target)
        self._commit(updated)
        event: dict[str, object] = {"from": current.value, "to": target.value}
        if detail is not None:
            event["detail"] = dict(detail)
        self._append_event(EventKind.TRANSITION, event)
        return updated

    def observe_control_request(
        self,
        control: str,
        *,
        detail: Mapping[str, object] | None = None,
    ) -> None:
        """Journal that the run observed a control request at a unit boundary.

        Records only the observation; the resulting transition (for example
        ``running -> pausing`` or ``running -> cancelled``) is a separate
        :meth:`transition_to` call, so request, observation, and transition are
        three distinct auditable events (ADR 0053).
        """

        event: dict[str, object] = {"control": control}
        if detail is not None:
            event["detail"] = dict(detail)
        self._append_event(EventKind.CONTROL_REQUEST_OBSERVED, event)

    def record_decision_pause(self, required_decision: Mapping[str, object]) -> RunState:
        """Pause the run for a required user decision (running -> incomplete).

        Maps a stage-required decision — resource-envelope, model acquisition,
        or resource confirmation — to run status ``incomplete`` with a
        machine-readable ``required_decision`` stored in the state document, and
        journals a dedicated decision-pause event. This is strictly distinct
        from a user pause (``pausing``/``paused``).
        """

        if not required_decision:
            raise RunStateError(
                "empty_required_decision",
                "A decision pause must carry a machine-readable required decision.",
            )
        current = self._state.status
        if RunStatus.INCOMPLETE not in _ALLOWED_TRANSITIONS[current]:
            raise RunStateError(
                "illegal_transition",
                f"A run may not pause for a decision from {current.value}.",
            )
        decision = dict(required_decision)
        updated = replace(self._state, status=RunStatus.INCOMPLETE, required_decision=decision)
        self._commit(updated)
        self._append_event(
            EventKind.DECISION_PAUSE,
            {
                "from": current.value,
                "to": RunStatus.INCOMPLETE.value,
                "required_decision": decision,
            },
        )
        return updated

    def record_recovery(self, detail: Mapping[str, object]) -> None:
        """Journal a crash- or resume-recovery observation (no transition).

        A resuming process calls this after :meth:`reopen` to record what it
        recovered from — a stale ``running`` state, a discarded partial unit,
        the last checkpoint it revalidated. The transition back to ``running``
        is a separate :meth:`transition_to` call.
        """

        self._append_event(EventKind.RECOVERY, {"detail": dict(detail)})

    def set_progress(
        self,
        *,
        stage_units: Sequence[Mapping[str, object]] | None = None,
        adopted_outputs: Sequence[Mapping[str, object]] | None = None,
        invalidation_keys: Mapping[str, object] | None = None,
    ) -> RunState:
        """Persist stage progress fields atomically without a status change.

        The stage-orchestration tickets use this to keep the run's stage units,
        adopted outputs, and invalidation keys consistent on disk as work
        advances within the ``running`` status. Only the supplied fields change;
        omitted fields are left untouched. No journal event is written — journal
        events are reserved for transitions, control observations, decision
        pauses, and recovery.
        """

        if stage_units is None and adopted_outputs is None and invalidation_keys is None:
            return self._state
        updated = replace(
            self._state,
            stage_units=(
                self._state.stage_units
                if stage_units is None
                else tuple(dict(unit) for unit in stage_units)
            ),
            adopted_outputs=(
                self._state.adopted_outputs
                if adopted_outputs is None
                else tuple(dict(output) for output in adopted_outputs)
            ),
            invalidation_keys=(
                self._state.invalidation_keys
                if invalidation_keys is None
                else dict(invalidation_keys)
            ),
        )
        self._commit(updated)
        return updated

    def _commit(self, state: RunState) -> None:
        self._write_state(state)
        self._state = state

    def _write_state(self, state: RunState) -> None:
        payload = json.dumps(state.to_document(), sort_keys=True, indent=2) + "\n"
        tmp_path = self._state_path.with_name(self._state_path.name + _STATE_TMP_SUFFIX)
        _durable_write(tmp_path, payload, flags=os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
        os.replace(tmp_path, self._state_path)

    def _append_event(self, kind: EventKind, data: Mapping[str, object]) -> None:
        record = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "sequence": self._next_sequence,
            "at": _instant(self._clock(), reason="naive_event_timestamp"),
            "kind": kind.value,
            "data": dict(data),
        }
        line = _canonical_line(record) + "\n"
        _durable_write(self._journal_path, line, flags=os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        self._next_sequence += 1
