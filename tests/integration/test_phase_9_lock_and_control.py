"""Ticket 04 acceptance: the heavy-task lock and control requests, proven
end-to-end against a small simulated run loop that uses the real primitives —
:mod:`video_content_pipeline.heavy_task_lock` and
:mod:`video_content_pipeline.run_control` — over a synthetic project root.

The DAG/executor themselves are later tickets; here a minimal driver stands in
for them so the boundary semantics (pause takes effect at the next stage-unit
boundary; cancel stops later stages but still publishes completed work; a
second heavy run fails fast; a stale lock is stolen with a journaled recovery)
are exercised exactly as the real loop will use them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from video_content_pipeline.heavy_task_lock import (
    HeavyTaskLockHeld,
    ProcessIdentity,
    acquire_heavy_task_lock,
    heavy_task_lock_path,
)
from video_content_pipeline.orchestration import RunLayout, initialize_run_workspace
from video_content_pipeline.run_control import (
    ControlDirective,
    ControlKind,
    apply_cancel,
    apply_pause,
    observe_controls_at_boundary,
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
_RUN_A = "20260816T083000Z-0123456789abcdef"
_RUN_B = "20260816T090000Z-fedcba9876543210"
_PLAN_ID = "plan0123456789abcdef0123"

_UNITS = ("subtitles/part-1", "audio/part-1", "asr/part-1", "text/part-1")


def _tick_clock(start: datetime | None = None) -> Callable[[], datetime]:
    base = start or datetime(2026, 8, 16, 8, 30, 0, tzinfo=UTC)

    def generator() -> Iterator[datetime]:
        step = 0
        while True:
            yield base + timedelta(seconds=step)
            step += 1

    stream = generator()
    return lambda: next(stream)


class _FakeProbe:
    def __init__(self, identity: ProcessIdentity, live: set[ProcessIdentity]) -> None:
        self._identity = identity
        self._live = set(live)

    def identify(self) -> ProcessIdentity:
        return self._identity

    def is_running(self, identity: ProcessIdentity) -> bool:
        return identity in self._live


def _layout(tmp_path: Path, run_id: str = _RUN_A) -> RunLayout:
    layout = RunLayout(project_root=tmp_path, source_id=_SOURCE_ID, run_id=run_id)
    return initialize_run_workspace(layout)


def _running_writer(layout: RunLayout) -> RunStateWriter:
    writer = RunStateWriter.create(layout, plan_id=_PLAN_ID, clock=_tick_clock())
    writer.transition_to(RunStatus.QUEUED)
    writer.transition_to(RunStatus.RUNNING)
    return writer


def _publish_completed(writer: RunStateWriter) -> list[str]:
    """Stand in for the publication stage: gather the units already completed."""

    return [str(unit["unit"]) for unit in writer.state.stage_units]


def _drive(
    writer: RunStateWriter,
    layout: RunLayout,
    *,
    request_after: dict[int, ControlKind] | None = None,
) -> tuple[list[str], RunStatus]:
    """A minimal stand-in run loop: run each unit, then observe controls at the
    completed-unit boundary. ``request_after`` injects a control request that
    "arrives" from a second terminal right after the unit at that index
    completes but before the boundary is observed."""

    requests = request_after or {}
    completed: list[str] = []
    for index, unit in enumerate(_UNITS):
        completed.append(unit)
        writer.set_progress(stage_units=[{"unit": done} for done in completed])
        if index in requests:
            request_control(layout, requests[index], clock=_tick_clock())
        directive = observe_controls_at_boundary(writer, layout)
        if directive is ControlDirective.PAUSE:
            apply_pause(writer)
            return completed, RunStatus.PAUSED
        if directive is ControlDirective.CANCEL:
            apply_cancel(writer)
            return completed, RunStatus.CANCELLED
    writer.transition_to(RunStatus.COMPLETE)
    return completed, RunStatus.COMPLETE


# --- Pause at the next stage-unit boundary ----------------------------------


def test_pause_takes_effect_at_the_next_unit_boundary(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    writer = _running_writer(layout)
    # A pause arrives while the second unit is running.
    completed, status = _drive(writer, layout, request_after={1: ControlKind.PAUSE})
    # Units up to and including the one in flight complete; nothing after.
    assert completed == ["subtitles/part-1", "audio/part-1"]
    assert status is RunStatus.PAUSED
    # The run process exited cleanly in `paused`, state and journal flushed.
    assert read_run_state(layout.state_path).status is RunStatus.PAUSED
    tos = [event.data.get("to") for event in read_journal(layout.journal_path)]
    assert tos[-2:] == ["pausing", "paused"]
    # Request, observation, and transition are distinct journal events.
    kinds = [event.kind for event in read_journal(layout.journal_path)]
    assert EventKind.CONTROL_REQUEST_OBSERVED in kinds


def test_paused_run_resumes_and_completes(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    writer = _running_writer(layout)
    _drive(writer, layout, request_after={1: ControlKind.PAUSE})

    # `vcp resume` starts a fresh process: reopen, journal recovery, continue.
    resumed = RunStateWriter.reopen(layout, clock=_tick_clock())
    resumed.record_recovery({"recovered_from": "user_pause"})
    resumed.transition_to(RunStatus.RUNNING)
    # No stale pause request lingers after a resume.
    assert observe_controls_at_boundary(resumed, layout) is ControlDirective.CONTINUE
    resumed.transition_to(RunStatus.COMPLETE)
    assert read_run_state(layout.state_path).status is RunStatus.COMPLETE


# --- Cancel stops later stages but still publishes --------------------------


def test_cancel_stops_later_stages_and_publishes_completed(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    writer = _running_writer(layout)
    completed, status = _drive(writer, layout, request_after={0: ControlKind.CANCEL})
    assert status is RunStatus.CANCELLED
    assert completed == ["subtitles/part-1"]  # later stages did not run
    # Publication of already-completed results still proceeds after cancel.
    published = _publish_completed(writer)
    assert published == ["subtitles/part-1"]
    assert read_run_state(layout.state_path).status is RunStatus.CANCELLED


def test_cancel_supersedes_a_concurrent_pause(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    writer = _running_writer(layout)
    # Both a pause and a cancel are pending at the same boundary.
    request_control(layout, ControlKind.PAUSE, clock=_tick_clock())
    request_control(layout, ControlKind.CANCEL, clock=_tick_clock())
    directive = observe_controls_at_boundary(writer, layout)
    assert directive is ControlDirective.CANCEL


# --- Heavy-task lock: fail fast, queued is transient ------------------------


def test_second_heavy_run_fails_fast_while_lock_held(tmp_path: Path) -> None:
    lock_path = heavy_task_lock_path(tmp_path)
    (tmp_path / "work").mkdir(parents=True)

    # Run A: created, momentarily queued, acquires the lock, then runs.
    layout_a = _layout(tmp_path, _RUN_A)
    writer_a = RunStateWriter.create(layout_a, plan_id=_PLAN_ID, clock=_tick_clock())
    writer_a.transition_to(RunStatus.QUEUED)
    probe_a = _FakeProbe(ProcessIdentity(100, "s100"), {ProcessIdentity(100, "s100")})
    acquire_heavy_task_lock(lock_path, run_id=_RUN_A, probe=probe_a, clock=_tick_clock())
    writer_a.transition_to(RunStatus.RUNNING)

    # Run B: created and queued, but the live lock forces a fast failure.
    layout_b = _layout(tmp_path, _RUN_B)
    writer_b = RunStateWriter.create(layout_b, plan_id=_PLAN_ID, clock=_tick_clock())
    writer_b.transition_to(RunStatus.QUEUED)
    probe_b = _FakeProbe(ProcessIdentity(200, "s200"), {ProcessIdentity(100, "s100")})
    with pytest.raises(HeavyTaskLockHeld) as excinfo:
        acquire_heavy_task_lock(lock_path, run_id=_RUN_B, probe=probe_b, clock=_tick_clock())
    assert excinfo.value.holder.run_id == _RUN_A
    # `queued` is the only state B ever reached; it never advanced to running.
    assert read_run_state(layout_b.state_path).status is RunStatus.QUEUED


def test_stale_lock_is_stolen_and_recovery_is_journaled(tmp_path: Path) -> None:
    lock_path = heavy_task_lock_path(tmp_path)
    (tmp_path / "work").mkdir(parents=True)

    # Run A acquired the lock, then crashed (its process is no longer alive).
    acquire_heavy_task_lock(
        lock_path,
        run_id=_RUN_A,
        probe=_FakeProbe(ProcessIdentity(100, "s100"), {ProcessIdentity(100, "s100")}),
        clock=_tick_clock(),
    )

    # Run B finds the stale lock, steals it, and journals the recovery.
    layout_b = _layout(tmp_path, _RUN_B)
    writer_b = RunStateWriter.create(layout_b, plan_id=_PLAN_ID, clock=_tick_clock())
    writer_b.transition_to(RunStatus.QUEUED)
    lock_b = acquire_heavy_task_lock(
        lock_path,
        run_id=_RUN_B,
        probe=_FakeProbe(ProcessIdentity(200, "s200"), {ProcessIdentity(200, "s200")}),
        clock=_tick_clock(),
    )
    assert lock_b.stole_from is not None
    writer_b.record_recovery(
        {
            "recovered_from": "stale_heavy_task_lock",
            "stole_from_run": lock_b.stole_from.run_id,
        }
    )
    writer_b.transition_to(RunStatus.RUNNING)

    recovery = [
        event for event in read_journal(layout_b.journal_path) if event.kind is EventKind.RECOVERY
    ]
    assert len(recovery) == 1
    assert recovery[0].data["detail"]["stole_from_run"] == _RUN_A
