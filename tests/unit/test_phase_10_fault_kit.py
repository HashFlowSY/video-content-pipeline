"""Ticket 02: unit tests for the shared fault-injection kit itself.

The fault matrix (ticket 07) trusts this kit to make each Fault class fail a
specific durable write in exactly the way that class names. These tests pin that
contract down: the golden counter is stable, the death injector freezes all
later writes, the torn-write injector lands a strict prefix and nothing more, and
the exhausted-disk injector raises ``errno.ENOSPC`` at exactly the Nth call. The
control-file corruption helpers get direct coverage too.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.support.fault_injection import (
    DURABLE_IO_FUNCTIONS,
    DurableIoInterceptor,
    FaultClass,
    InjectionPlan,
    SimulatedProcessDeath,
    corrupt_with_garbage,
    truncate_file,
)
from video_content_pipeline import durable_io

_APPEND = os.O_WRONLY | os.O_CREAT | os.O_APPEND


def _drive(interceptor: DurableIoInterceptor, tmp_path: Path) -> None:
    """Run a fixed three-write sequence through the interceptor's four wrappers.

    One call to each payload-bearing primitive plus a directory fsync, so the
    shared counter sees writes 1..4 in a stable order.
    """

    path = tmp_path / "doc.json"
    interceptor.atomic_replace(path, "alpha")
    interceptor.durable_write(tmp_path / "journal", "beta\n", flags=_APPEND)
    descriptor = os.open(tmp_path / "lock", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        interceptor.write_and_fsync(descriptor, "gamma")
    finally:
        os.close(descriptor)
    interceptor.fsync_directory(tmp_path)


# -- golden-run counter ------------------------------------------------------


def test_golden_run_passes_through_and_records_every_write(tmp_path: Path) -> None:
    interceptor = DurableIoInterceptor()
    _drive(interceptor, tmp_path)
    # Every write reached disk unchanged...
    assert (tmp_path / "doc.json").read_text(encoding="utf-8") == "alpha"
    assert (tmp_path / "journal").read_text(encoding="utf-8") == "beta\n"
    assert (tmp_path / "lock").read_text(encoding="utf-8") == "gamma"
    # ...and each was recorded in call order.
    assert interceptor.calls == [
        "atomic_replace",
        "durable_write",
        "write_and_fsync",
        "fsync_directory",
    ]
    assert interceptor.call_count == 4


def test_golden_run_count_is_stable_across_two_identical_runs(tmp_path: Path) -> None:
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.mkdir()
    two.mkdir()
    first = DurableIoInterceptor()
    _drive(first, one)
    second = DurableIoInterceptor()
    _drive(second, two)
    assert first.calls == second.calls
    assert first.call_count == second.call_count


# -- process death -----------------------------------------------------------


def test_death_injector_raises_at_the_target_call(tmp_path: Path) -> None:
    interceptor = DurableIoInterceptor(InjectionPlan(fail_at=1, fault=FaultClass.PROCESS_DEATH))
    with pytest.raises(SimulatedProcessDeath):
        interceptor.atomic_replace(tmp_path / "doc.json", "alpha")
    # The failed write never landed.
    assert not (tmp_path / "doc.json").exists()


def test_no_durable_write_succeeds_after_the_death_point(tmp_path: Path) -> None:
    # Fail the 2nd write; the 1st lands, the 2nd dies, everything after freezes.
    interceptor = DurableIoInterceptor(InjectionPlan(fail_at=2, fault=FaultClass.PROCESS_DEATH))
    interceptor.atomic_replace(tmp_path / "doc.json", "alpha")
    with pytest.raises(SimulatedProcessDeath):
        interceptor.durable_write(tmp_path / "journal", "beta\n", flags=_APPEND)
    # A post-death write is refused and touches no disk.
    with pytest.raises(SimulatedProcessDeath):
        interceptor.atomic_replace(tmp_path / "doc.json", "OVERWRITE")
    assert (tmp_path / "doc.json").read_text(encoding="utf-8") == "alpha"
    assert not (tmp_path / "journal").exists()


# -- torn write --------------------------------------------------------------


def test_torn_write_lands_a_strict_prefix_and_nothing_after(tmp_path: Path) -> None:
    interceptor = DurableIoInterceptor(InjectionPlan(fail_at=1, fault=FaultClass.TORN_WRITE))
    with pytest.raises(SimulatedProcessDeath):
        interceptor.durable_write(tmp_path / "journal", "abcdef", flags=_APPEND)
    # Half the bytes landed; the write is provably incomplete.
    assert (tmp_path / "journal").read_bytes() == b"abc"


def test_torn_atomic_replace_leaves_partial_tmp_and_intact_target(tmp_path: Path) -> None:
    path = tmp_path / "doc.json"
    path.write_text("GOOD", encoding="utf-8")
    interceptor = DurableIoInterceptor(InjectionPlan(fail_at=1, fault=FaultClass.TORN_WRITE))
    with pytest.raises(SimulatedProcessDeath):
        interceptor.atomic_replace(path, "abcdef")
    # The live document is untouched; a half-written temp sits beside it, never
    # renamed into place — exactly a kill mid-atomic-replace.
    assert path.read_text(encoding="utf-8") == "GOOD"
    assert (tmp_path / "doc.json.tmp").read_bytes() == b"abc"


def test_torn_prefix_bytes_override_is_honoured(tmp_path: Path) -> None:
    interceptor = DurableIoInterceptor(
        InjectionPlan(fail_at=1, fault=FaultClass.TORN_WRITE, torn_prefix_bytes=2)
    )
    with pytest.raises(SimulatedProcessDeath):
        interceptor.durable_write(tmp_path / "journal", "abcdef", flags=_APPEND)
    assert (tmp_path / "journal").read_bytes() == b"ab"


def test_torn_write_freezes_later_writes(tmp_path: Path) -> None:
    interceptor = DurableIoInterceptor(InjectionPlan(fail_at=1, fault=FaultClass.TORN_WRITE))
    with pytest.raises(SimulatedProcessDeath):
        interceptor.durable_write(tmp_path / "journal", "abcdef", flags=_APPEND)
    with pytest.raises(SimulatedProcessDeath):
        interceptor.atomic_replace(tmp_path / "doc.json", "later")
    assert not (tmp_path / "doc.json").exists()


# -- exhausted disk ----------------------------------------------------------


def test_enospc_injector_raises_at_exactly_call_n(tmp_path: Path) -> None:
    interceptor = DurableIoInterceptor(InjectionPlan(fail_at=2, fault=FaultClass.EXHAUSTED_DISK))
    # Call 1 passes through.
    interceptor.atomic_replace(tmp_path / "doc.json", "alpha")
    # Call 2 fails with ENOSPC and touches no disk.
    with pytest.raises(OSError) as excinfo:
        interceptor.durable_write(tmp_path / "journal", "beta\n", flags=_APPEND)
    assert excinfo.value.errno == errno.ENOSPC
    assert not (tmp_path / "journal").exists()


def test_enospc_does_not_freeze_the_process(tmp_path: Path) -> None:
    interceptor = DurableIoInterceptor(InjectionPlan(fail_at=1, fault=FaultClass.EXHAUSTED_DISK))
    with pytest.raises(OSError):
        interceptor.durable_write(tmp_path / "journal", "beta\n", flags=_APPEND)
    # A full disk does not kill the process: a later write still succeeds.
    interceptor.atomic_replace(tmp_path / "doc.json", "alpha")
    assert (tmp_path / "doc.json").read_text(encoding="utf-8") == "alpha"


# -- installation seam -------------------------------------------------------


def test_install_redirects_only_the_names_a_module_imported() -> None:
    recorded: list[tuple[object, str, object]] = []

    def fake_setattr(target: object, name: str, value: object) -> None:
        recorded.append((target, name, value))
        setattr(target, name, value)

    # A module that imported just two of the four durable_io names.
    module = SimpleNamespace(
        atomic_replace=durable_io.atomic_replace,
        durable_write=durable_io.durable_write,
    )
    interceptor = DurableIoInterceptor()
    interceptor.install(fake_setattr, module)

    patched = {name for _, name, _ in recorded}
    assert patched == {"atomic_replace", "durable_write"}
    assert module.atomic_replace == interceptor.atomic_replace
    # Names the module never imported are left alone.
    assert not hasattr(module, "fsync_directory")


def test_durable_io_function_names_match_the_real_module() -> None:
    for name in DURABLE_IO_FUNCTIONS:
        assert callable(getattr(durable_io, name))


# -- plan validation ---------------------------------------------------------


def test_injection_plan_rejects_non_positive_fail_at() -> None:
    with pytest.raises(ValueError):
        InjectionPlan(fail_at=0, fault=FaultClass.PROCESS_DEATH)


# -- control-file corruption -------------------------------------------------


def test_corrupt_with_garbage_replaces_readable_content(tmp_path: Path) -> None:
    path = tmp_path / "control" / "pause.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"kind": "pause"}', encoding="utf-8")
    corrupt_with_garbage(path)
    with pytest.raises(UnicodeDecodeError):
        path.read_text(encoding="utf-8")


def test_truncate_file_keeps_a_leading_slice(tmp_path: Path) -> None:
    path = tmp_path / "control" / "cancel.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"kind": "cancel"}', encoding="utf-8")
    truncate_file(path, keep_bytes=5)
    assert path.read_bytes() == b'{"kin'
