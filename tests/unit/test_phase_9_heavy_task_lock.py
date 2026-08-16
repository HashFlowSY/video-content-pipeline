from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from video_content_pipeline.heavy_task_lock import (
    LOCK_SCHEMA_VERSION,
    HeavyTaskLock,
    HeavyTaskLockError,
    HeavyTaskLockHeld,
    LockHolder,
    ProcessIdentity,
    SystemProcessProbe,
    acquire_heavy_task_lock,
    heavy_task_lock_path,
    inspect_heavy_task_lock,
)

_RUN_A = "20260816T083000Z-0123456789abcdef"
_RUN_B = "20260816T090000Z-fedcba9876543210"


class _FakeProbe:
    """A deterministic process probe: a process is live iff its exact identity
    (pid *and* start time) is in ``live``; a matching pid with a different start
    time is pid reuse and therefore not the same process."""

    def __init__(self, identity: ProcessIdentity, live: set[ProcessIdentity]) -> None:
        self._identity = identity
        self._live = set(live)

    def identify(self) -> ProcessIdentity:
        return self._identity

    def is_running(self, identity: ProcessIdentity) -> bool:
        return identity in self._live


def _identity(pid: int, start_time: str) -> ProcessIdentity:
    return ProcessIdentity(pid=pid, start_time=start_time)


def _fixed_clock() -> Callable[[], datetime]:
    return lambda: datetime(2026, 8, 16, 8, 30, 0, tzinfo=UTC)


def _probe_for(pid: int, start_time: str, *, alive: bool = True) -> _FakeProbe:
    me = _identity(pid, start_time)
    return _FakeProbe(me, {me} if alive else set())


# --- Path -------------------------------------------------------------------


def test_lock_path_is_project_scoped(tmp_path: Path) -> None:
    assert heavy_task_lock_path(tmp_path) == tmp_path / "work" / "heavy-task.lock"


# --- Acquisition ------------------------------------------------------------


def test_acquire_on_free_path_records_holder(tmp_path: Path) -> None:
    path = heavy_task_lock_path(tmp_path)
    path.parent.mkdir(parents=True)
    lock = acquire_heavy_task_lock(
        path, run_id=_RUN_A, probe=_probe_for(100, "s100"), clock=_fixed_clock()
    )
    assert lock.holder.run_id == _RUN_A
    assert lock.holder.pid == 100
    assert lock.holder.process_start_time == "s100"
    assert lock.stole_from is None
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schema_version"] == LOCK_SCHEMA_VERSION
    assert document["run_id"] == _RUN_A


def test_second_live_acquire_fails_fast(tmp_path: Path) -> None:
    path = heavy_task_lock_path(tmp_path)
    path.parent.mkdir(parents=True)
    acquire_heavy_task_lock(
        path, run_id=_RUN_A, probe=_probe_for(100, "s100"), clock=_fixed_clock()
    )
    # A different run, on a live holder, must fail fast — no waiting queue.
    with pytest.raises(HeavyTaskLockHeld) as excinfo:
        acquire_heavy_task_lock(
            path,
            run_id=_RUN_B,
            probe=_FakeProbe(_identity(200, "s200"), {_identity(100, "s100")}),
            clock=_fixed_clock(),
        )
    assert excinfo.value.reason == "heavy_task_lock_held"
    assert excinfo.value.holder.run_id == _RUN_A
    # The live holder's lock is untouched.
    assert json.loads(path.read_text(encoding="utf-8"))["run_id"] == _RUN_A


def test_acquire_steals_a_dead_holder(tmp_path: Path) -> None:
    path = heavy_task_lock_path(tmp_path)
    path.parent.mkdir(parents=True)
    acquire_heavy_task_lock(
        path, run_id=_RUN_A, probe=_probe_for(100, "s100"), clock=_fixed_clock()
    )
    # The prior holder's process is dead: not in the live set.
    lock = acquire_heavy_task_lock(
        path,
        run_id=_RUN_B,
        probe=_FakeProbe(_identity(200, "s200"), {_identity(200, "s200")}),
        clock=_fixed_clock(),
    )
    assert lock.holder.run_id == _RUN_B
    assert lock.stole_from is not None
    assert lock.stole_from.run_id == _RUN_A
    assert json.loads(path.read_text(encoding="utf-8"))["run_id"] == _RUN_B


def test_acquire_steals_on_pid_reuse(tmp_path: Path) -> None:
    path = heavy_task_lock_path(tmp_path)
    path.parent.mkdir(parents=True)
    acquire_heavy_task_lock(
        path, run_id=_RUN_A, probe=_probe_for(100, "s100"), clock=_fixed_clock()
    )
    # pid 100 is alive again but with a *different* start time: a reused pid,
    # not the original holder — so the lock is stale and stealable.
    lock = acquire_heavy_task_lock(
        path,
        run_id=_RUN_B,
        probe=_FakeProbe(
            _identity(300, "s300"), {_identity(100, "s-REUSED"), _identity(300, "s300")}
        ),
        clock=_fixed_clock(),
    )
    assert lock.holder.run_id == _RUN_B
    assert lock.stole_from is not None and lock.stole_from.run_id == _RUN_A


def test_acquire_rejects_unreadable_lock(tmp_path: Path) -> None:
    path = heavy_task_lock_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(HeavyTaskLockError) as excinfo:
        acquire_heavy_task_lock(
            path, run_id=_RUN_B, probe=_probe_for(200, "s200"), clock=_fixed_clock()
        )
    assert excinfo.value.reason == "heavy_task_lock_unreadable"


# --- Inspection (read-only, for `vcp status` stale-running diagnosis) -------


def test_inspect_missing_lock_is_none(tmp_path: Path) -> None:
    assert inspect_heavy_task_lock(heavy_task_lock_path(tmp_path), probe=_probe_for(1, "s")) is None


def test_inspect_live_holder(tmp_path: Path) -> None:
    path = heavy_task_lock_path(tmp_path)
    path.parent.mkdir(parents=True)
    acquire_heavy_task_lock(
        path, run_id=_RUN_A, probe=_probe_for(100, "s100"), clock=_fixed_clock()
    )
    inspection = inspect_heavy_task_lock(path, probe=_probe_for(100, "s100"))
    assert inspection is not None
    assert inspection.is_stale is False
    assert inspection.reason == "held"
    assert inspection.holder.run_id == _RUN_A


def test_inspect_stale_holder_is_reported(tmp_path: Path) -> None:
    path = heavy_task_lock_path(tmp_path)
    path.parent.mkdir(parents=True)
    acquire_heavy_task_lock(
        path, run_id=_RUN_A, probe=_probe_for(100, "s100"), clock=_fixed_clock()
    )
    inspection = inspect_heavy_task_lock(
        path,
        probe=_FakeProbe(_identity(1, "s1"), set()),  # holder pid 100 not live
    )
    assert inspection is not None
    assert inspection.is_stale is True
    assert inspection.reason == "holder_process_not_running"
    assert inspection.holder.run_id == _RUN_A


def test_inspect_rejects_unreadable_lock(tmp_path: Path) -> None:
    path = heavy_task_lock_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(HeavyTaskLockError) as excinfo:
        inspect_heavy_task_lock(path, probe=_probe_for(1, "s"))
    assert excinfo.value.reason == "heavy_task_lock_unreadable"


# --- Release and context management -----------------------------------------


def test_release_removes_our_lock(tmp_path: Path) -> None:
    path = heavy_task_lock_path(tmp_path)
    path.parent.mkdir(parents=True)
    lock = acquire_heavy_task_lock(
        path, run_id=_RUN_A, probe=_probe_for(100, "s100"), clock=_fixed_clock()
    )
    lock.release()
    assert not path.exists()
    # After release the lock is free again.
    acquire_heavy_task_lock(
        path, run_id=_RUN_B, probe=_probe_for(200, "s200"), clock=_fixed_clock()
    )


def test_release_is_idempotent(tmp_path: Path) -> None:
    path = heavy_task_lock_path(tmp_path)
    path.parent.mkdir(parents=True)
    lock = acquire_heavy_task_lock(
        path, run_id=_RUN_A, probe=_probe_for(100, "s100"), clock=_fixed_clock()
    )
    lock.release()
    lock.release()  # no error


def test_release_does_not_delete_a_stolen_lock(tmp_path: Path) -> None:
    path = heavy_task_lock_path(tmp_path)
    path.parent.mkdir(parents=True)
    lock_a = acquire_heavy_task_lock(
        path, run_id=_RUN_A, probe=_probe_for(100, "s100"), clock=_fixed_clock()
    )
    # A stole the lock from under A (A was declared dead).
    acquire_heavy_task_lock(
        path,
        run_id=_RUN_B,
        probe=_FakeProbe(_identity(200, "s200"), {_identity(200, "s200")}),
        clock=_fixed_clock(),
    )
    lock_a.release()  # A must not delete B's lock
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["run_id"] == _RUN_B


def test_context_manager_releases(tmp_path: Path) -> None:
    path = heavy_task_lock_path(tmp_path)
    path.parent.mkdir(parents=True)
    with acquire_heavy_task_lock(
        path, run_id=_RUN_A, probe=_probe_for(100, "s100"), clock=_fixed_clock()
    ) as lock:
        assert isinstance(lock, HeavyTaskLock)
        assert path.exists()
    assert not path.exists()


def test_holder_round_trips_through_document(tmp_path: Path) -> None:
    holder = LockHolder(
        run_id=_RUN_A, pid=100, process_start_time="s100", acquired_at="2026-08-16T08:30:00+00:00"
    )
    assert LockHolder.from_document(holder.to_document()) == holder


# --- System probe smoke test (no subprocess assertions) ---------------------


def test_system_probe_sees_itself_alive() -> None:
    probe = SystemProcessProbe()
    me = probe.identify()
    assert me.pid == os.getpid()
    assert probe.is_running(me) is True


def test_system_probe_reports_dead_pid() -> None:
    probe = SystemProcessProbe()
    # A pid that is essentially certain to be dead with a bogus start time.
    assert probe.is_running(_identity(2**31 - 1, "never")) is False
