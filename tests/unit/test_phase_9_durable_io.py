from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from video_content_pipeline.durable_io import (
    atomic_replace,
    durable_write,
    to_utc_isoformat,
    utc_now,
    write_and_fsync,
)


def test_durable_write_creates_and_appends(tmp_path: Path) -> None:
    path = tmp_path / "journal"
    durable_write(path, "one\n", flags=os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    durable_write(path, "two\n", flags=os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    assert path.read_text(encoding="utf-8") == "one\ntwo\n"


def test_atomic_replace_leaves_no_temp_and_overwrites(tmp_path: Path) -> None:
    path = tmp_path / "doc.json"
    atomic_replace(path, "first")
    atomic_replace(path, "second")
    assert path.read_text(encoding="utf-8") == "second"
    # The temp file is renamed away — never left behind.
    assert list(p.name for p in tmp_path.iterdir()) == ["doc.json"]


def test_write_and_fsync_writes_to_open_descriptor(tmp_path: Path) -> None:
    path = tmp_path / "claimed"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        write_and_fsync(descriptor, "payload")
    finally:
        os.close(descriptor)
    assert path.read_text(encoding="utf-8") == "payload"


def test_utc_now_is_timezone_aware() -> None:
    assert utc_now().tzinfo is not None


def test_to_utc_isoformat_normalizes_to_utc() -> None:
    aware = datetime(2026, 8, 16, 8, 30, 0, tzinfo=UTC)
    assert to_utc_isoformat(aware, on_naive=lambda: AssertionError("unreachable")) == (
        "2026-08-16T08:30:00+00:00"
    )


def test_to_utc_isoformat_rejects_naive_via_factory() -> None:
    naive = datetime(2026, 8, 16, 8, 30, 0)

    class _Marker(ValueError):
        pass

    with pytest.raises(_Marker):
        to_utc_isoformat(naive, on_naive=lambda: _Marker("naive"))
