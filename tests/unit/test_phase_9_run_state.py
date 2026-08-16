from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from video_content_pipeline.orchestration import RunLayout, initialize_run_workspace
from video_content_pipeline.run_state import (
    _ALLOWED_TRANSITIONS,
    EVENT_SCHEMA_VERSION,
    STATE_SCHEMA_VERSION,
    EventKind,
    RunEvent,
    RunState,
    RunStateError,
    RunStateWriter,
    RunStatus,
    read_journal,
    read_run_state,
)

_SOURCE_ID = "a" * 64
_RUN_ID = "20260816T083000Z-0123456789abcdef"
_PLAN_ID = "plan0123456789abcdef0123"


def _layout(tmp_path: Path) -> RunLayout:
    layout = RunLayout(project_root=tmp_path, source_id=_SOURCE_ID, run_id=_RUN_ID)
    return initialize_run_workspace(layout)


def _fixed_clock() -> Callable[[], datetime]:
    return lambda: datetime(2026, 8, 16, 8, 30, 0, tzinfo=UTC)


def _tick_clock(start: datetime | None = None) -> Callable[[], datetime]:
    base = start or datetime(2026, 8, 16, 8, 30, 0, tzinfo=UTC)

    def generator() -> Iterator[datetime]:
        step = 0
        while True:
            yield base + timedelta(seconds=step)
            step += 1

    stream = generator()
    return lambda: next(stream)


def _writer(tmp_path: Path, clock: Callable[[], datetime] | None = None) -> RunStateWriter:
    return RunStateWriter.create(_layout(tmp_path), plan_id=_PLAN_ID, clock=clock or _tick_clock())


# --- Initial creation -------------------------------------------------------


def test_create_writes_planned_state_and_first_event(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    assert writer.state.status is RunStatus.PLANNED
    on_disk = read_run_state(_layout(tmp_path).state_path)
    assert on_disk.status is RunStatus.PLANNED
    assert on_disk.source_id == _SOURCE_ID
    assert on_disk.run_id == _RUN_ID
    assert on_disk.plan_id == _PLAN_ID
    events = read_journal(_layout(tmp_path).journal_path)
    assert len(events) == 1
    assert events[0].kind is EventKind.TRANSITION
    assert events[0].data == {"from": None, "to": "planned"}
    assert events[0].sequence == 0


def test_create_refuses_to_clobber_existing_state(tmp_path: Path) -> None:
    _writer(tmp_path)
    with pytest.raises(RunStateError) as excinfo:
        RunStateWriter.create(_layout(tmp_path), plan_id=_PLAN_ID, clock=_fixed_clock())
    assert excinfo.value.reason == "run_state_exists"


def test_create_requires_the_run_workspace(tmp_path: Path) -> None:
    layout = RunLayout(project_root=tmp_path, source_id=_SOURCE_ID, run_id=_RUN_ID)
    with pytest.raises(RunStateError) as excinfo:
        RunStateWriter.create(layout, plan_id=_PLAN_ID, clock=_fixed_clock())
    assert excinfo.value.reason == "run_workspace_missing"


# --- State machine ----------------------------------------------------------


def test_happy_path_transitions_to_complete(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.transition_to(RunStatus.QUEUED)
    writer.transition_to(RunStatus.RUNNING)
    final = writer.transition_to(RunStatus.COMPLETE)
    assert final.status is RunStatus.COMPLETE
    assert read_run_state(_layout(tmp_path).state_path).status is RunStatus.COMPLETE


def test_pause_loop_is_representable(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.transition_to(RunStatus.QUEUED)
    writer.transition_to(RunStatus.RUNNING)
    writer.transition_to(RunStatus.PAUSING)
    writer.transition_to(RunStatus.PAUSED)
    resumed = writer.transition_to(RunStatus.RUNNING)
    assert resumed.status is RunStatus.RUNNING


@pytest.mark.parametrize(
    "target",
    [
        RunStatus.COMPLETE_WITH_WARNINGS,
        RunStatus.INCOMPLETE,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    ],
)
def test_running_reaches_every_terminal_status(tmp_path: Path, target: RunStatus) -> None:
    writer = _writer(tmp_path)
    writer.transition_to(RunStatus.QUEUED)
    writer.transition_to(RunStatus.RUNNING)
    assert writer.transition_to(target).status is target


def test_illegal_transition_is_rejected_and_leaves_disk_untouched(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    with pytest.raises(RunStateError) as excinfo:
        writer.transition_to(RunStatus.RUNNING)  # planned -> running skips queued
    assert excinfo.value.reason == "illegal_transition"
    assert writer.state.status is RunStatus.PLANNED
    assert read_run_state(_layout(tmp_path).state_path).status is RunStatus.PLANNED
    # No transition event was journaled for the rejected edge.
    events = read_journal(_layout(tmp_path).journal_path)
    assert [event.data.get("to") for event in events] == ["planned"]


def test_terminal_status_has_no_outgoing_transition(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.transition_to(RunStatus.QUEUED)
    writer.transition_to(RunStatus.RUNNING)
    writer.transition_to(RunStatus.FAILED)
    with pytest.raises(RunStateError) as excinfo:
        writer.transition_to(RunStatus.RUNNING)
    assert excinfo.value.reason == "illegal_transition"


def test_transition_table_is_exactly_the_plan_machine() -> None:
    # The graph is the single source of truth for "no other transition is
    # representable"; assert it edge-for-edge against plan §12.
    expected = {
        RunStatus.PLANNED: {RunStatus.QUEUED},
        RunStatus.QUEUED: {RunStatus.RUNNING},
        RunStatus.RUNNING: {
            RunStatus.PAUSING,
            RunStatus.COMPLETE,
            RunStatus.COMPLETE_WITH_WARNINGS,
            RunStatus.INCOMPLETE,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        },
        RunStatus.PAUSING: {RunStatus.PAUSED},
        RunStatus.PAUSED: {RunStatus.RUNNING},
        RunStatus.COMPLETE: set(),
        RunStatus.COMPLETE_WITH_WARNINGS: set(),
        RunStatus.INCOMPLETE: set(),
        RunStatus.FAILED: set(),
        RunStatus.CANCELLED: set(),
    }
    assert {status: set(targets) for status, targets in _ALLOWED_TRANSITIONS.items()} == expected
    # Every status is covered — no status without an outgoing rule.
    assert set(_ALLOWED_TRANSITIONS) == set(RunStatus)


# --- Decision pause ---------------------------------------------------------


def test_decision_pause_maps_to_incomplete_with_required_decision(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.transition_to(RunStatus.QUEUED)
    writer.transition_to(RunStatus.RUNNING)
    decision = {"kind": "resource_envelope", "needed": "confirm_disk"}
    paused = writer.record_decision_pause(decision)
    assert paused.status is RunStatus.INCOMPLETE
    assert paused.required_decision == decision
    reread = read_run_state(_layout(tmp_path).state_path)
    assert reread.status is RunStatus.INCOMPLETE
    assert reread.required_decision == decision
    events = read_journal(_layout(tmp_path).journal_path)
    assert events[-1].kind is EventKind.DECISION_PAUSE
    assert events[-1].data["required_decision"] == decision


def test_decision_pause_requires_a_decision(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.transition_to(RunStatus.QUEUED)
    writer.transition_to(RunStatus.RUNNING)
    with pytest.raises(RunStateError) as excinfo:
        writer.record_decision_pause({})
    assert excinfo.value.reason == "empty_required_decision"


def test_decision_pause_rejected_outside_running(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    with pytest.raises(RunStateError) as excinfo:
        writer.record_decision_pause({"kind": "resource_envelope"})
    assert excinfo.value.reason == "illegal_transition"


# --- Control observation and recovery events --------------------------------


def test_control_observation_journals_without_state_change(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.transition_to(RunStatus.QUEUED)
    writer.transition_to(RunStatus.RUNNING)
    writer.observe_control_request("pause", detail={"requested_by": "second_terminal"})
    assert writer.state.status is RunStatus.RUNNING
    events = read_journal(_layout(tmp_path).journal_path)
    assert events[-1].kind is EventKind.CONTROL_REQUEST_OBSERVED
    assert events[-1].data == {"control": "pause", "detail": {"requested_by": "second_terminal"}}


def test_request_observation_and_transition_are_three_events(tmp_path: Path) -> None:
    writer = _writer(tmp_path, clock=_tick_clock())
    writer.transition_to(RunStatus.QUEUED)
    writer.transition_to(RunStatus.RUNNING)
    writer.observe_control_request("pause")
    writer.transition_to(RunStatus.PAUSING)
    kinds = [event.kind for event in read_journal(_layout(tmp_path).journal_path)]
    # planned, queued, running, observed, pausing
    assert kinds[-2:] == [EventKind.CONTROL_REQUEST_OBSERVED, EventKind.TRANSITION]


def test_recovery_event_is_journaled(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.transition_to(RunStatus.QUEUED)
    writer.transition_to(RunStatus.RUNNING)
    writer.record_recovery(
        {"recovered_from": "stale_running", "discarded_unit": "subtitles/part-1"}
    )
    event = read_journal(_layout(tmp_path).journal_path)[-1]
    assert event.kind is EventKind.RECOVERY
    assert event.data["detail"]["recovered_from"] == "stale_running"


# --- Progress persistence ---------------------------------------------------


def test_set_progress_persists_fields_without_journal_event(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.transition_to(RunStatus.QUEUED)
    writer.transition_to(RunStatus.RUNNING)
    before = len(read_journal(_layout(tmp_path).journal_path))
    updated = writer.set_progress(
        stage_units=[{"stage": "subtitles", "part": "part-1", "status": "complete"}],
        adopted_outputs=[{"stage": "subtitles", "part": "part-1", "hash": "b" * 64}],
        invalidation_keys={"subtitles/part-1": "c" * 64},
    )
    assert updated.stage_units[0]["status"] == "complete"
    reread = read_run_state(_layout(tmp_path).state_path)
    assert reread.stage_units == updated.stage_units
    assert reread.adopted_outputs == updated.adopted_outputs
    assert reread.invalidation_keys == {"subtitles/part-1": "c" * 64}
    # No new journal event for a within-status data update.
    assert len(read_journal(_layout(tmp_path).journal_path)) == before


def test_set_progress_only_touches_supplied_fields(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.set_progress(stage_units=[{"stage": "subtitles", "part": "part-1"}])
    writer.set_progress(invalidation_keys={"subtitles/part-1": "d" * 64})
    state = read_run_state(_layout(tmp_path).state_path)
    assert state.stage_units == ({"stage": "subtitles", "part": "part-1"},)
    assert state.invalidation_keys == {"subtitles/part-1": "d" * 64}


def test_set_progress_with_nothing_is_a_no_op(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    before = read_run_state(_layout(tmp_path).state_path)
    writer.set_progress()
    assert read_run_state(_layout(tmp_path).state_path).to_document() == before.to_document()


# --- Atomicity and single-writer discipline ---------------------------------


def test_state_write_is_atomic_no_torn_document(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.transition_to(RunStatus.QUEUED)
    writer.transition_to(RunStatus.RUNNING)
    # No temporary artifact is left behind; only the final document exists.
    work_files = sorted(p.name for p in _layout(tmp_path).work_dir.iterdir() if p.is_file())
    assert "run-state.json" in work_files
    assert "run-state.json.tmp" not in work_files
    # The document always parses cleanly (never torn).
    document = json.loads(_layout(tmp_path).state_path.read_text(encoding="utf-8"))
    assert document["status"] == "running"


def test_journal_is_append_only_across_transitions(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.transition_to(RunStatus.QUEUED)
    writer.transition_to(RunStatus.RUNNING)
    writer.transition_to(RunStatus.COMPLETE)
    events = read_journal(_layout(tmp_path).journal_path)
    assert [event.sequence for event in events] == [0, 1, 2, 3]
    assert [event.data.get("to") for event in events] == [
        "planned",
        "queued",
        "running",
        "complete",
    ]


def test_module_exposes_no_command_side_write_api() -> None:
    import video_content_pipeline.run_state as module

    # The only public write authority is the writer class; readers and the
    # module namespace expose nothing that mutates state or journal on disk.
    public = {name for name in vars(module) if not name.startswith("_")}
    writers = {name for name in public if name.startswith(("write_", "append_"))}
    assert writers == set()
    assert callable(module.read_run_state)
    assert callable(module.read_journal)


# --- Serialization determinism and schema versioning ------------------------


def test_state_document_is_deterministic_and_sorted(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.transition_to(RunStatus.QUEUED)
    raw = _layout(tmp_path).state_path.read_text(encoding="utf-8")
    document = json.loads(raw)
    assert document["schema_version"] == STATE_SCHEMA_VERSION
    # Keys are emitted in sorted order (deterministic serialization).
    assert list(document.keys()) == sorted(document.keys())


def test_journal_lines_are_sorted_compact_json(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.transition_to(RunStatus.QUEUED)
    lines = _layout(tmp_path).journal_path.read_text(encoding="utf-8").splitlines()
    for line in lines:
        record = json.loads(line)
        assert record["schema_version"] == EVENT_SCHEMA_VERSION
        assert line == json.dumps(record, sort_keys=True, separators=(",", ":"))


def test_reader_rejects_schema_mismatch(tmp_path: Path) -> None:
    _writer(tmp_path)
    state_path = _layout(tmp_path).state_path
    document = json.loads(state_path.read_text(encoding="utf-8"))
    document["schema_version"] = 999
    state_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(RunStateError) as excinfo:
        read_run_state(state_path)
    assert excinfo.value.reason == "run_state_schema_mismatch"


def test_reader_rejects_unknown_status(tmp_path: Path) -> None:
    _writer(tmp_path)
    state_path = _layout(tmp_path).state_path
    document = json.loads(state_path.read_text(encoding="utf-8"))
    document["status"] = "halfway"
    state_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(RunStateError) as excinfo:
        read_run_state(state_path)
    assert excinfo.value.reason == "run_state_invalid"


def test_journal_field_fault_reports_a_journal_reason(tmp_path: Path) -> None:
    # A malformed journal field must surface a journal reason, not a state one.
    _writer(tmp_path)
    journal_path = _layout(tmp_path).journal_path
    record = json.loads(journal_path.read_text(encoding="utf-8").splitlines()[0])
    del record["at"]
    journal_path.write_text(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    with pytest.raises(RunStateError) as excinfo:
        read_journal(journal_path)
    assert excinfo.value.reason == "journal_invalid"


def test_naive_clock_is_rejected(tmp_path: Path) -> None:
    naive_clock = lambda: datetime(2026, 8, 16, 8, 30, 0)  # noqa: E731 - test double
    with pytest.raises(RunStateError) as excinfo:
        RunStateWriter.create(_layout(tmp_path), plan_id=_PLAN_ID, clock=naive_clock)
    assert excinfo.value.reason == "naive_event_timestamp"


# --- Reopen (resume continuity) ---------------------------------------------


def test_reopen_continues_the_journal_sequence(tmp_path: Path) -> None:
    writer = _writer(tmp_path, clock=_tick_clock())
    writer.transition_to(RunStatus.QUEUED)
    writer.transition_to(RunStatus.RUNNING)
    writer.transition_to(RunStatus.PAUSING)
    writer.transition_to(RunStatus.PAUSED)

    reopened = RunStateWriter.reopen(_layout(tmp_path), clock=_tick_clock())
    assert reopened.state.status is RunStatus.PAUSED
    reopened.record_recovery({"recovered_from": "user_pause"})
    reopened.transition_to(RunStatus.RUNNING)

    events = read_journal(_layout(tmp_path).journal_path)
    # planned(0) queued(1) running(2) pausing(3) paused(4) recovery(5) running(6)
    assert [event.sequence for event in events] == [0, 1, 2, 3, 4, 5, 6]
    assert events[5].kind is EventKind.RECOVERY
    assert events[6].data == {"from": "paused", "to": "running"}


def test_reopen_preserves_carried_state(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.transition_to(RunStatus.QUEUED)
    writer.transition_to(RunStatus.RUNNING)
    writer.set_progress(stage_units=[{"stage": "subtitles", "part": "part-1"}])
    reopened = RunStateWriter.reopen(_layout(tmp_path))
    assert reopened.state.stage_units == ({"stage": "subtitles", "part": "part-1"},)


def test_read_run_state_missing_file(tmp_path: Path) -> None:
    with pytest.raises(RunStateError) as excinfo:
        read_run_state(tmp_path / "nope" / "run-state.json")
    assert excinfo.value.reason == "run_state_missing"


def test_run_event_and_run_state_round_trip_types(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    state = writer.state
    assert isinstance(state, RunState)
    event = read_journal(_layout(tmp_path).journal_path)[0]
    assert isinstance(event, RunEvent)
    assert event.schema_version == EVENT_SCHEMA_VERSION
