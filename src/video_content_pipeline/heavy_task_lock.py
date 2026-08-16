"""The Heavy-task lock that serializes heavy runs (ADR 0032, ADR 0053).

Heavy work — ASR, forced alignment, diarization, OCR, and text models — must
never load concurrently, so at most one heavy run may hold this lock at a time.
The lock is a single project-scoped file at ``work/heavy-task.lock`` recording
its holder's run id, process id, and process start time. A second heavy run
that finds the lock held by a *live* holder fails fast with
:class:`HeavyTaskLockHeld` — there is no persistent cross-process queue; the
run's ``queued`` status is only the transient moment it spends attempting to
acquire.

A crash leaves the lock behind with a holder whose process is no longer
running. The process start time is what lets us decide that safely: the
recorded holder is live only if its pid is running *and* still carries the same
start time, so a pid the operating system has recycled for a different process
(pid reuse) never masquerades as the original holder. When neither condition
holds the holder is *stale*. :func:`acquire_heavy_task_lock` steals a stale lock
(recording whom it stole from, so the caller can journal a recovery event);
:func:`inspect_heavy_task_lock` reports staleness read-only for the ``vcp
status`` stale-running diagnosis — it reports *that* the holder is not running,
not which of the two sub-causes applies. All liveness questions go through a
:class:`ProcessProbe`, so runs are fully testable offline with a deterministic
probe.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from video_content_pipeline.durable_io import to_utc_isoformat, utc_now, write_and_fsync

LOCK_SCHEMA_VERSION = 1

#: The staleness reason recorded when a holder's recorded process is no longer
#: running — either its pid is dead, or the pid is alive but is now a different
#: process (pid reuse). Deliberately does not distinguish the two sub-causes.
_HOLDER_PROCESS_NOT_RUNNING = "holder_process_not_running"
#: The inspection reason for a lock whose holder is still live.
_HOLDER_HELD = "held"


class HeavyTaskLockError(ValueError):
    """A heavy-task lock failure with an auditable machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class HeavyTaskLockHeld(HeavyTaskLockError):
    """Raised when the lock is held by a live holder: a second heavy run must
    fail fast rather than wait. Carries the current :class:`LockHolder`."""

    def __init__(self, holder: LockHolder) -> None:
        super().__init__(
            "heavy_task_lock_held",
            f"The heavy-task lock is held by run {holder.run_id} (pid {holder.pid}).",
        )
        self.holder = holder


@dataclass(frozen=True)
class ProcessIdentity:
    """A process's pid together with its start time.

    The start time is what makes the identity precise across pid reuse: the
    operating system may hand pid ``100`` to a new process after the original
    dies, but the new process has a different start time, so the two identities
    never compare equal.
    """

    pid: int
    start_time: str


class ProcessProbe(Protocol):
    """The liveness oracle the lock consults; a fake makes runs testable."""

    def identify(self) -> ProcessIdentity:
        """Return the identity of the current process."""

    def is_running(self, identity: ProcessIdentity) -> bool:
        """Return whether that exact process (pid *and* start time) is alive."""


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The process exists but is owned by another user — still alive.
        return True
    except OSError:
        return False
    return True


def _process_start_time(pid: int) -> str:
    """Best-effort opaque process start-time token, or ``""`` if unknowable.

    On Linux the value comes from ``/proc/<pid>/stat`` field 22; elsewhere (for
    example macOS) it comes from ``ps -o lstart=``. An empty string means the
    start time could not be read, in which case staleness falls back to the
    weaker pid-liveness check alone.
    """

    if sys.platform.startswith("linux"):
        try:
            raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        except OSError:
            return ""
        # The comm field is parenthesised and may contain spaces/parens, so the
        # stat fields resume after the final ')'. starttime is field 22 overall,
        # i.e. index 19 among the fields that follow comm.
        tail = raw.rpartition(")")[2].split()
        return tail[19] if len(tail) > 19 else ""
    try:
        completed = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ""
    return completed.stdout.strip()


class SystemProcessProbe:
    """The real :class:`ProcessProbe`, backed by the operating system."""

    def identify(self) -> ProcessIdentity:
        pid = os.getpid()
        return ProcessIdentity(pid=pid, start_time=_process_start_time(pid))

    def is_running(self, identity: ProcessIdentity) -> bool:
        if not _pid_alive(identity.pid):
            return False
        current = _process_start_time(identity.pid)
        if not current or not identity.start_time:
            # Start time is unavailable on one side; we cannot detect reuse, so
            # conservatively treat a live pid as the same process (never steal a
            # lock we cannot prove is stale).
            return True
        return current == identity.start_time


@dataclass(frozen=True)
class LockHolder:
    """Who holds (or last held) the lock: a run, its process, and when."""

    run_id: str
    pid: int
    process_start_time: str
    acquired_at: str

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": LOCK_SCHEMA_VERSION,
            "run_id": self.run_id,
            "pid": self.pid,
            "process_start_time": self.process_start_time,
            "acquired_at": self.acquired_at,
        }

    @classmethod
    def from_document(cls, document: object) -> LockHolder:
        if not isinstance(document, dict):
            raise HeavyTaskLockError(
                "heavy_task_lock_unreadable", "Lock file must be a JSON object."
            )
        if document.get("schema_version") != LOCK_SCHEMA_VERSION:
            raise HeavyTaskLockError(
                "heavy_task_lock_unreadable",
                f"Lock file schema_version must be {LOCK_SCHEMA_VERSION}.",
            )
        try:
            pid = document["pid"]
            return cls(
                run_id=_require_str(document, "run_id"),
                pid=_require_int(pid),
                process_start_time=_require_str(document, "process_start_time"),
                acquired_at=_require_str(document, "acquired_at"),
            )
        except KeyError as error:
            raise HeavyTaskLockError(
                "heavy_task_lock_unreadable", f"Lock file is missing {error}."
            ) from error

    @property
    def identity(self) -> ProcessIdentity:
        return ProcessIdentity(pid=self.pid, start_time=self.process_start_time)


def _require_str(document: dict[str, object], key: str) -> str:
    value = document[key]
    if not isinstance(value, str):
        raise HeavyTaskLockError(
            "heavy_task_lock_unreadable", f"Lock field {key} must be a string."
        )
    return value


def _require_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise HeavyTaskLockError("heavy_task_lock_unreadable", "Lock field pid must be an integer.")
    return value


@dataclass(frozen=True)
class LockInspection:
    """A read-only staleness diagnosis of the lock (for ``vcp status``)."""

    holder: LockHolder
    is_stale: bool
    reason: str


def heavy_task_lock_path(project_root: Path) -> Path:
    """The single project-scoped lock file address under ``work/``."""

    return project_root / "work" / "heavy-task.lock"


def _instant(value: datetime) -> str:
    return to_utc_isoformat(
        value,
        on_naive=lambda: HeavyTaskLockError(
            "naive_lock_timestamp", "A lock timestamp must be timezone-aware."
        ),
    )


def _read_holder(lock_path: Path) -> LockHolder:
    try:
        raw = lock_path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise HeavyTaskLockError(
            "heavy_task_lock_missing", f"No heavy-task lock at {lock_path}."
        ) from error
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as error:
        raise HeavyTaskLockError(
            "heavy_task_lock_unreadable", f"Heavy-task lock at {lock_path} is not JSON."
        ) from error
    return LockHolder.from_document(document)


@dataclass
class HeavyTaskLock:
    """A held heavy-task lock; release it (directly or as a context manager).

    ``stole_from`` names the stale holder this acquisition displaced, if any, so
    the run process can journal a recovery event. :meth:`release` only removes
    the file while *this* holder still owns it, so a run that was itself
    declared stale and stolen from never deletes the new owner's lock.
    """

    lock_path: Path
    holder: LockHolder
    stole_from: LockHolder | None = None
    _released: bool = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            current = _read_holder(self.lock_path)
        except HeavyTaskLockError:
            return
        if current != self.holder:
            # We were stolen from (or the lock was replaced): not ours to remove.
            return
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            return

    def __enter__(self) -> HeavyTaskLock:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def acquire_heavy_task_lock(
    lock_path: Path,
    *,
    run_id: str,
    probe: ProcessProbe | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> HeavyTaskLock:
    """Acquire the heavy-task lock for ``run_id``, or fail fast if held live.

    Creates the lock file atomically when free. When a lock already exists it
    consults ``probe``: a live holder raises :class:`HeavyTaskLockHeld` (the
    second heavy run fails fast); a stale holder is stolen — the stale file is
    removed and re-created for this run, and the displaced holder is returned as
    ``stole_from``. An unreadable lock is never silently stolen: it raises
    :class:`HeavyTaskLockError` so a human decides.
    """

    active_probe = probe if probe is not None else SystemProcessProbe()
    identity = active_probe.identify()
    holder = LockHolder(
        run_id=run_id,
        pid=identity.pid,
        process_start_time=identity.start_time,
        acquired_at=_instant(clock()),
    )
    payload = json.dumps(holder.to_document(), sort_keys=True, indent=2) + "\n"

    stole_from: LockHolder | None = None
    while True:
        try:
            descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            existing = _read_holder(lock_path)
            if active_probe.is_running(existing.identity):
                raise HeavyTaskLockHeld(existing) from None
            # Stale holder: steal it. Remove then retry the exclusive create so
            # the create still guards against a concurrent acquirer.
            stole_from = existing
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
            continue
        try:
            write_and_fsync(descriptor, payload)
        finally:
            os.close(descriptor)
        return HeavyTaskLock(lock_path=lock_path, holder=holder, stole_from=stole_from)


def inspect_heavy_task_lock(
    lock_path: Path,
    *,
    probe: ProcessProbe | None = None,
) -> LockInspection | None:
    """Report the lock's holder and staleness, or ``None`` if no lock exists.

    Read-only: it never removes or rewrites the lock. Used by ``vcp status`` to
    diagnose a run whose state says ``running`` while its lock is stale.
    """

    if not lock_path.exists():
        return None
    active_probe = probe if probe is not None else SystemProcessProbe()
    holder = _read_holder(lock_path)
    is_stale = not active_probe.is_running(holder.identity)
    return LockInspection(
        holder=holder,
        is_stale=is_stale,
        reason=_HOLDER_PROCESS_NOT_RUNNING if is_stale else _HOLDER_HELD,
    )
