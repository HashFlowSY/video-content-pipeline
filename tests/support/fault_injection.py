"""Deterministic fault injection for the durable-write seam.

Every orchestration write that must survive a power loss goes through one of the
four :mod:`video_content_pipeline.durable_io` primitives — ``write_and_fsync``,
``durable_write``, ``atomic_replace``, ``fsync_directory``. This module wraps all
four with a single shared call counter and an optional *fail-at-Nth-call*
injector, so a test can (1) do a golden run that counts every durable write in
order, then (2) re-run the same orchestration failing the Nth write with one of
the three Fault classes and assert the run recovers from on-disk state alone.

The three Fault classes mirror how storage actually fails:

* **Process death** — the write never happens; a :class:`SimulatedProcessDeath`
  is raised and the interceptor *freezes*: every later durable write also raises
  and touches no disk. This is the crucial property a naive ``raise`` lacks —
  production ``except``/``finally`` handlers must not be able to perform disk
  work a real power loss would never have run, so recovery is forced to rely on
  what is already on disk.
* **Exhausted disk** — the Nth write raises ``OSError(errno.ENOSPC)`` before
  touching disk; the process stays alive (no freeze), exactly as a full
  filesystem surfaces to a caller that is expected to handle the error.
* **Torn write** — a strict byte-prefix of the payload lands on disk (an
  ``atomic_replace`` leaves a half-written ``.tmp`` beside the intact target),
  then the death freeze takes over. This is the interrupted-mid-``write`` case.

The kit is plain importable code (``from tests.support.fault_injection import
...``) with no conftest hook, preserving the project's zero-conftest convention.
"""

from __future__ import annotations

import enum
import errno
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from video_content_pipeline import durable_io

#: Shape of ``monkeypatch.setattr`` as :meth:`DurableIoInterceptor.install` uses
#: it — kept as an alias so the kit never has to import pytest.
_SetAttr = Callable[[ModuleType, str, object], None]

#: Temp suffix ``atomic_replace`` uses for its write-then-rename. Kept in step
#: with :mod:`video_content_pipeline.durable_io` so a torn ``atomic_replace``
#: leaves its partial bytes exactly where the real one would have.
_TMP_SUFFIX = ".tmp"

#: The durable_io names an interceptor stands in for, in the order the module
#: declares them. :meth:`DurableIoInterceptor.install` redirects each of these on
#: a caller module to the interceptor's matching bound method.
DURABLE_IO_FUNCTIONS = (
    "write_and_fsync",
    "durable_write",
    "atomic_replace",
    "fsync_directory",
)


class SimulatedProcessDeath(Exception):
    """Stands in for a power loss / SIGKILL at a durable-write boundary.

    Raised at the injection point for the process-death and torn-write classes,
    and again by every durable write attempted afterwards while the interceptor
    is frozen — so no post-death disk work can slip through.
    """


class FaultClass(enum.Enum):
    """How the injected durable write fails."""

    PROCESS_DEATH = "process_death"
    EXHAUSTED_DISK = "exhausted_disk"
    TORN_WRITE = "torn_write"


@dataclass(frozen=True)
class InjectionPlan:
    """Fail the ``fail_at``-th durable write (1-based) with ``fault``.

    ``torn_prefix_bytes`` overrides how many payload bytes a torn write lands;
    left ``None`` it defaults to half the payload, always clamped to a strict
    prefix (never the whole payload).
    """

    fail_at: int
    fault: FaultClass
    torn_prefix_bytes: int | None = None

    def __post_init__(self) -> None:
        if self.fail_at < 1:
            raise ValueError("fail_at is 1-based; the first durable write is call 1")
        if self.torn_prefix_bytes is not None and self.torn_prefix_bytes < 0:
            raise ValueError("torn_prefix_bytes cannot be negative")


class _Action(enum.Enum):
    PASS = "pass"
    INJECT = "inject"


@dataclass
class DurableIoInterceptor:
    """Counts durable writes and, given a plan, fails one of them.

    With no ``plan`` this is a transparent pass-through that only records calls —
    the golden run. With a plan it additionally fails the ``fail_at``-th call.
    ``calls`` holds the durable_io function name of each write in order, so a
    fault-matrix test can assert the exact write sequence and detect new write
    sites (recorded-N assertion).
    """

    plan: InjectionPlan | None = None
    calls: list[str] = field(default_factory=list)
    _frozen: bool = field(default=False, init=False)

    @property
    def call_count(self) -> int:
        """How many durable writes have been observed so far."""

        return len(self.calls)

    def install(self, setattr_fn: _SetAttr, *modules: ModuleType) -> None:
        """Redirect each module's imported durable_io names to this interceptor.

        ``setattr_fn`` is pytest's ``monkeypatch.setattr`` (passed in rather than
        imported, so the kit stays independent of pytest). Only names a module
        actually imported are patched, so a module that uses just
        ``atomic_replace`` is left otherwise untouched.
        """

        for module in modules:
            for name in DURABLE_IO_FUNCTIONS:
                if hasattr(module, name):
                    setattr_fn(module, name, getattr(self, name))

    # -- wrapped durable_io surface ------------------------------------------

    def write_and_fsync(self, descriptor: int, text: str) -> None:
        if self._begin("write_and_fsync") is _Action.PASS:
            durable_io.write_and_fsync(descriptor, text)
            return
        if self._fault is FaultClass.TORN_WRITE:
            os.write(descriptor, self._prefix(text))
        self._finish()

    def durable_write(self, path: Path, text: str, *, flags: int) -> None:
        if self._begin("durable_write") is _Action.PASS:
            durable_io.durable_write(path, text, flags=flags)
            return
        if self._fault is FaultClass.TORN_WRITE:
            self._write_prefix(path, text, flags=flags)
        self._finish()

    def atomic_replace(self, path: Path, text: str) -> None:
        if self._begin("atomic_replace") is _Action.PASS:
            durable_io.atomic_replace(path, text)
            return
        if self._fault is FaultClass.TORN_WRITE:
            # Mirror a kill mid-``atomic_replace``: the fsync'd temp write is
            # interrupted, so a partial ``.tmp`` is left and the rename never
            # runs — the live document stays whole.
            tmp_path = path.with_name(path.name + _TMP_SUFFIX)
            self._write_prefix(tmp_path, text, flags=os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
        self._finish()

    def fsync_directory(self, path: Path) -> None:
        # A directory fsync carries no payload, so there is nothing to tear: a
        # torn-write injection here degrades to a plain death freeze.
        if self._begin("fsync_directory") is _Action.PASS:
            durable_io.fsync_directory(path)
            return
        self._finish()

    # -- injection machinery -------------------------------------------------

    def _begin(self, name: str) -> _Action:
        """Record the call and decide whether it passes through or is failed.

        Raises immediately when frozen (a post-death write) or when the fault is
        exhausted-disk (which never touches disk); otherwise returns whether to
        pass through or run the torn/death branch in the caller.
        """

        if self._frozen:
            raise SimulatedProcessDeath("durable write attempted after the injected process death")
        self.calls.append(name)
        if self.plan is None or len(self.calls) != self.plan.fail_at:
            return _Action.PASS
        if self._fault is FaultClass.EXHAUSTED_DISK:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC), str(name))
        return _Action.INJECT

    def _finish(self) -> None:
        """Complete a death or torn-write injection: freeze, then raise."""

        assert self.plan is not None
        self._frozen = True
        raise SimulatedProcessDeath(
            f"injected {self.plan.fault.value} on durable write {self.plan.fail_at}"
        )

    @property
    def _fault(self) -> FaultClass:
        assert self.plan is not None
        return self.plan.fault

    def _prefix(self, text: str) -> bytes:
        data = text.encode("utf-8")
        if self.plan is not None and self.plan.torn_prefix_bytes is not None:
            count = self.plan.torn_prefix_bytes
        else:
            count = len(data) // 2
        # Always a *strict* prefix so the torn file is provably incomplete.
        count = max(0, min(count, len(data) - 1))
        return data[:count]

    def _write_prefix(self, path: Path, text: str, *, flags: int) -> None:
        descriptor = os.open(path, flags, 0o644)
        try:
            os.write(descriptor, self._prefix(text))
        finally:
            os.close(descriptor)


# -- control-file corruption ------------------------------------------------


def corrupt_with_garbage(path: Path, data: bytes = b"\x00\xff not-json garbage \x00") -> None:
    """Overwrite ``path`` with non-parseable bytes (a control-file corruption).

    Models a file that survived on disk but whose contents are unreadable — the
    reader must reject it as corrupt rather than mistaking it for a valid
    request.
    """

    path.write_bytes(data)


def truncate_file(path: Path, keep_bytes: int) -> None:
    """Truncate ``path`` to its first ``keep_bytes`` bytes.

    Models a write cut short by a crash: the leading bytes are intact but the
    document is incomplete, so a strict parser must reject it.
    """

    if keep_bytes < 0:
        raise ValueError("keep_bytes cannot be negative")
    original = path.read_bytes()
    path.write_bytes(original[:keep_bytes])
