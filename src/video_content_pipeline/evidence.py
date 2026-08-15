"""Shared immutable-workspace evidence records and serialization helpers.

These are context-neutral utilities used by more than one analysis phase to
record hash-pinned read-only input evidence and to write authoritative reports
once. They intentionally raise no phase-specific error type: callers pass an
error factory so each Context keeps its own diagnostic identity while the
serialization and hashing logic lives in exactly one place.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from video_content_pipeline.source import sha256_file


@dataclass(frozen=True)
class InputEvidence:
    """Hash-recorded read-only evidence for a required retained input."""

    path: Path
    sha256: str
    byte_count: int

    def as_json(self) -> dict[str, object]:
        return {
            "path": self.path.as_posix(),
            "sha256": self.sha256,
            "byte_count": self.byte_count,
        }


def input_evidence(path: Path) -> InputEvidence:
    """Hash a retained input file into immutable read-only evidence."""

    digest, byte_count = sha256_file(path)
    return InputEvidence(path, digest, byte_count)


def validated_report_id(value: str, *, invalid_error: Callable[[], Exception]) -> str:
    """Return the canonical UUID hex of a report ID or raise the caller's error."""

    try:
        return uuid.UUID(hex=value).hex
    except ValueError as error:
        raise invalid_error() from error


def write_text_once(
    path: Path, text: str, *, conflict_error: Callable[[str], Exception]
) -> None:
    """Write a text record once; reject a differing rewrite.

    An identical rewrite is a no-op so a repeated write stays idempotent; a
    differing rewrite raises the caller-supplied conflict error to keep the
    workspace immutable.
    """

    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise conflict_error(f"Immutable record differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_bytes_once(
    path: Path, data: bytes, *, conflict_error: Callable[[str], Exception]
) -> None:
    """Write a bytes record once; reject a differing rewrite (see write_text_once)."""

    if path.exists():
        if path.read_bytes() != data:
            raise conflict_error(f"Immutable record differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def write_json_once(
    path: Path, payload: object, *, conflict_error: Callable[[str], Exception]
) -> None:
    """Write a deterministic JSON record once; reject a differing rewrite."""

    encoded = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    write_text_once(path, encoded, conflict_error=conflict_error)
