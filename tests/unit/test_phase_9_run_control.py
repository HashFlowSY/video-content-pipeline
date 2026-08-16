from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from video_content_pipeline.orchestration import RunLayout, initialize_run_workspace
from video_content_pipeline.run_control import (
    CONTROL_SCHEMA_VERSION,
    ControlDirective,
    ControlKind,
    ControlRequestError,
    apply_cancel,
    apply_pause,
    control_dir,
    observe_controls_at_boundary,
    read_pending_controls,
    request_control,
)
from video_content_pipeline.run_state import (
    EventKind,
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


def _tick_clock(start: datetime | None = None) -> Callable[[], datetime]:
    base = start or datetime(2026, 8, 16, 8, 30, 0, tzinfo=UTC)

    def generator() -> Iterator[datetime]:
        step = 0
        while True:
            yield base + timedelta(seconds=step)
            step += 1

    stream = generator()
    return lambda: next(stream)


def _running_writer(tmp_path: Path) -> RunStateWriter:
    writer = RunStateWriter.create(_layout(tmp_path), plan_id=_PLAN_ID, clock=_tick_clock())
    writer.transition_to(RunStatus.QUEUED)
    writer.transition_to(RunStatus.RUNNING)
    return writer


# --- Requesting control (command side) --------------------------------------


def test_request_control_writes_a_readable_file(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    path = request_control(
        layout, ControlKind.PAUSE, detail={"requested_by": "second_terminal"}, clock=_tick_clock()
    )
    assert path.parent == control_dir(layout)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schema_version"] == CONTROL_SCHEMA_VERSION
    assert document["kind"] == "pause"
    assert document["detail"] == {"requested_by": "second_terminal"}


def test_request_control_requires_the_run_workspace(tmp_path: Path) -> None:
    layout = RunLayout(project_root=tmp_path, source_id=_SOURCE_ID, run_id=_RUN_ID)
    with pytest.raises(ControlRequestError) as excinfo:
        request_control(layout, ControlKind.PAUSE, clock=_tick_clock())
    assert excinfo.value.reason == "run_workspace_missing"


def test_re_requesting_same_kind_overwrites(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    request_control(layout, ControlKind.PAUSE, detail={"n": 1}, clock=_tick_clock())
    request_control(layout, ControlKind.PAUSE, detail={"n": 2}, clock=_tick_clock())
    pending = read_pending_controls(layout)
    assert len(pending) == 1
    assert pending[0].detail == {"n": 2}


def test_read_pending_is_empty_without_requests(tmp_path: Path) -> None:
    assert read_pending_controls(_layout(tmp_path)) == ()


def test_read_pending_orders_cancel_before_pause(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    request_control(layout, ControlKind.PAUSE, clock=_tick_clock())
    request_control(layout, ControlKind.CANCEL, clock=_tick_clock())
    kinds = [request.kind for request in read_pending_controls(layout)]
    assert kinds == [ControlKind.CANCEL, ControlKind.PAUSE]


# --- Observing control at a stage-unit boundary (run side) ------------------


def test_observe_without_requests_continues(tmp_path: Path) -> None:
    writer = _running_writer(tmp_path)
    before = len(read_journal(_layout(tmp_path).journal_path))
    assert observe_controls_at_boundary(writer, _layout(tmp_path)) is ControlDirective.CONTINUE
    # No journal event when nothing was observed.
    assert len(read_journal(_layout(tmp_path).journal_path)) == before


def test_observe_pause_journals_and_returns_pause(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    writer = _running_writer(tmp_path)
    request_control(layout, ControlKind.PAUSE, detail={"requested_by": "t2"}, clock=_tick_clock())
    directive = observe_controls_at_boundary(writer, layout)
    assert directive is ControlDirective.PAUSE
    event = read_journal(layout.journal_path)[-1]
    assert event.kind is EventKind.CONTROL_REQUEST_OBSERVED
    # The observation event carries the request's own timestamp and detail, so
    # the request is reconstructable from the journal despite never being
    # written there directly (single-writer discipline).
    assert event.data["control"] == "pause"
    assert event.data["detail"] == {
        "requested_at": "2026-08-16T08:30:00+00:00",
        "request_detail": {"requested_by": "t2"},
    }


def test_observe_consumes_the_request(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    writer = _running_writer(tmp_path)
    request_control(layout, ControlKind.PAUSE, clock=_tick_clock())
    observe_controls_at_boundary(writer, layout)
    # The request is consumed so a later boundary does not re-observe it.
    assert read_pending_controls(layout) == ()
    assert observe_controls_at_boundary(writer, layout) is ControlDirective.CONTINUE


def test_observe_cancel_supersedes_pause(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    writer = _running_writer(tmp_path)
    request_control(layout, ControlKind.PAUSE, clock=_tick_clock())
    request_control(layout, ControlKind.CANCEL, clock=_tick_clock())
    directive = observe_controls_at_boundary(writer, layout)
    assert directive is ControlDirective.CANCEL
    event = read_journal(layout.journal_path)[-1]
    assert event.data["control"] == "cancel"
    # Both requests are consumed, so a resuming run never sees the pause again.
    assert read_pending_controls(layout) == ()


def test_observe_does_not_change_run_status(tmp_path: Path) -> None:
    # Observation only journals; the transition is a separate, explicit step.
    layout = _layout(tmp_path)
    writer = _running_writer(tmp_path)
    request_control(layout, ControlKind.PAUSE, clock=_tick_clock())
    observe_controls_at_boundary(writer, layout)
    assert writer.state.status is RunStatus.RUNNING


# --- Applying the directive (run side transitions) --------------------------


def test_apply_pause_runs_the_pausing_paused_sequence(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    writer = _running_writer(tmp_path)
    final = apply_pause(writer)
    assert final.status is RunStatus.PAUSED
    tos = [event.data.get("to") for event in read_journal(layout.journal_path)]
    assert tos[-2:] == ["pausing", "paused"]
    assert read_run_state(layout.state_path).status is RunStatus.PAUSED


def test_apply_cancel_moves_straight_to_cancelled(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    writer = _running_writer(tmp_path)
    final = apply_cancel(writer)
    assert final.status is RunStatus.CANCELLED
    assert read_run_state(layout.state_path).status is RunStatus.CANCELLED


# --- An unobserved request cannot corrupt state -----------------------------


def test_unobserved_request_leaves_state_untouched(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    writer = _running_writer(tmp_path)
    state_before = read_run_state(layout.state_path).to_document()
    journal_before = len(read_journal(layout.journal_path))
    # A request that no running process ever observes (writer never calls
    # observe) must not touch state or journal.
    request_control(layout, ControlKind.CANCEL, clock=_tick_clock())
    assert read_run_state(layout.state_path).to_document() == state_before
    assert len(read_journal(layout.journal_path)) == journal_before
    assert writer.state.status is RunStatus.RUNNING
