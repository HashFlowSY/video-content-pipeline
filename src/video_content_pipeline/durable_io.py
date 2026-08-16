"""Durable-write and timestamp primitives shared by the orchestration modules.

The Run state document, the Run events journal, the Heavy-task lock, and
Control-request files are all small JSON documents that must reach stable
storage intact — a reader must never observe a torn file, and the crash-recovery
path (ADR 0053) relies on a completed write actually being on disk. This module
owns the two mechanics those modules share: an fsync-ing write
(:func:`durable_write`) and the atomic temp-then-rename replace built on it
(:func:`atomic_replace`), plus the timezone-aware clock helpers.

Each caller keeps its own machine-readable error type, so
:func:`to_utc_isoformat` raises a caller-supplied exception on a naive timestamp
rather than coupling every module to one error class.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

_TMP_SUFFIX = ".tmp"


def write_and_fsync(descriptor: int, text: str) -> None:
    """Write ``text`` to an already-open ``descriptor`` and fsync it.

    Callers that must open the file with special flags themselves — the
    Heavy-task lock uses ``O_CREAT | O_EXCL`` to claim the lock atomically — own
    the descriptor and hand it here for the write-then-flush.
    """

    os.write(descriptor, text.encode("utf-8"))
    os.fsync(descriptor)


def durable_write(path: Path, text: str, *, flags: int) -> None:
    """Open ``path`` under ``flags``, write ``text``, fsync, and close.

    Used directly for append-only writes (the events journal) and as the engine
    of :func:`atomic_replace` for whole-document replacements.
    """

    descriptor = os.open(path, flags, 0o644)
    try:
        write_and_fsync(descriptor, text)
    finally:
        os.close(descriptor)


def atomic_replace(path: Path, text: str) -> None:
    """Replace ``path`` with ``text`` atomically via a fsync'd temp then rename.

    A reader of ``path`` only ever sees the previous whole document or the new
    whole document, never a partial write.
    """

    tmp_path = path.with_name(path.name + _TMP_SUFFIX)
    durable_write(tmp_path, text, flags=os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    os.replace(tmp_path, path)


def utc_now() -> datetime:
    """The current time as a timezone-aware UTC instant."""

    return datetime.now(UTC)


def to_utc_isoformat(value: datetime, *, on_naive: Callable[[], Exception]) -> str:
    """Render ``value`` as a UTC ISO-8601 string, rejecting naive datetimes.

    A naive (timezone-less) ``value`` raises whatever ``on_naive`` returns, so
    each caller surfaces its own machine-readable reason without this helper
    depending on any one error type.
    """

    if value.tzinfo is None:
        raise on_naive()
    return value.astimezone(UTC).isoformat()
