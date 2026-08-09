"""Pinned, argv-only external-tool handling for Phase 3."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path


class ExternalToolError(ValueError):
    """An external-tool failure with a stable diagnostic reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class PinnedExternalTool:
    """Identity evidence for one external binary, never a project-managed tool."""

    tool_id: str
    path: Path
    version: str
    sha256: str

    def as_json(self) -> dict[str, str]:
        return {
            "tool_id": self.tool_id,
            "path": self.path.as_posix(),
            "version": self.version,
            "sha256": self.sha256,
        }


def identify_external_tool(tool_id: str, path: Path) -> PinnedExternalTool:
    """Capture binary identity using an argv-only version query."""

    resolved_path = path.resolve(strict=True)
    if not resolved_path.is_file():
        raise ExternalToolError("tool_not_regular_file", f"Tool is not a regular file: {path}")
    digest = _sha256_file(resolved_path)
    result = subprocess.run(
        [str(resolved_path), *_version_arguments(tool_id)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ExternalToolError(
            "tool_version_failed",
            f"{tool_id} rejected its version query with exit code {result.returncode}.",
        )
    version = result.stdout.splitlines()[0].strip() if result.stdout else ""
    if not version:
        raise ExternalToolError("tool_version_empty", f"{tool_id} returned no version identity.")
    return PinnedExternalTool(tool_id=tool_id, path=resolved_path, version=version, sha256=digest)


def revalidate_external_tool(expected: PinnedExternalTool) -> None:
    """Require the current binary evidence to equal the captured identity exactly."""

    current = identify_external_tool(expected.tool_id, expected.path)
    if current != expected:
        raise ExternalToolError(
            "tool_identity_changed", f"Pinned tool identity changed for {expected.tool_id}."
        )


def run_tool(
    arguments: tuple[str, ...], *, timeout_seconds: int | None = None
) -> subprocess.CompletedProcess[str]:
    """Run an already assembled argv list with no shell interpretation."""

    if not arguments:
        raise ExternalToolError(
            "tool_arguments_empty", "An external-tool argv list must not be empty."
        )
    return subprocess.run(
        list(arguments),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as binary:
        while chunk := binary.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _version_arguments(tool_id: str) -> tuple[str, ...]:
    """Use the documented version syntax for the approved external tools."""

    if tool_id in {"ffmpeg", "ffprobe"}:
        return ("-version",)
    return ("--version",)
