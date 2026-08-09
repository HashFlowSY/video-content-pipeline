"""Immutable local-source snapshots for the Phase 3 intake boundary."""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
import stat
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


class SourceIntakeError(ValueError):
    """A local-source failure with an auditable machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class ValidatedLocalSource:
    """A regular local path bound to the inode observed during validation."""

    path: Path
    device: int
    inode: int


@dataclass(frozen=True)
class SourceArtifact:
    """A project-owned, content-addressed copy of an authorized local source."""

    source_id: str
    sha256: str
    byte_count: int
    media_path: Path
    origin_kind: str = "local_file"

    def as_json(self) -> dict[str, object]:
        """Return persistent metadata without retaining the original local path."""

        return {
            "source_id": self.source_id,
            "sha256": self.sha256,
            "byte_count": self.byte_count,
            "media_path": self.media_path.as_posix(),
            "origin_kind": self.origin_kind,
        }


@dataclass(frozen=True)
class DiskHeadroom:
    """The deterministic disk requirement for one proposed acquisition."""

    increment_bytes: int
    reserve_bytes: int
    required_bytes: int


def calculate_disk_headroom(increment_bytes: int) -> DiskHeadroom:
    """Require planned growth plus the adopted one-GiB-or-five-percent reserve."""

    if increment_bytes < 0:
        raise SourceIntakeError(
            "disk_increment_invalid", "Planned disk growth must not be negative."
        )
    reserve_bytes = max(1024**3, (increment_bytes * 5 + 99) // 100)
    return DiskHeadroom(
        increment_bytes=increment_bytes,
        reserve_bytes=reserve_bytes,
        required_bytes=increment_bytes + reserve_bytes,
    )


def ensure_disk_headroom(target_root: Path, requirement: DiskHeadroom) -> None:
    """Fail before an acquisition when the project filesystem has insufficient space."""

    available_bytes = shutil.disk_usage(target_root).free
    if available_bytes < requirement.required_bytes:
        raise SourceIntakeError(
            "disk_headroom_insufficient",
            (
                f"Need {requirement.required_bytes} free bytes but only "
                f"{available_bytes} are available."
            ),
        )


def validate_local_source_candidate(path: Path) -> Path:
    """Accept one explicit regular file and reject all reference-like inputs."""

    return _validated_local_source(path).path


def _validated_local_source(path: Path) -> ValidatedLocalSource:
    """Validate one candidate and retain its initial filesystem identity."""

    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise SourceIntakeError("source_not_found", f"Source does not exist: {path}") from error
    except OSError as error:
        raise SourceIntakeError(
            "source_validation_failed", "The local source could not be validated."
        ) from error

    if stat.S_ISLNK(metadata.st_mode):
        raise SourceIntakeError(
            "source_symlink_rejected", "A local source must not be a symbolic link."
        )
    if not stat.S_ISREG(metadata.st_mode):
        raise SourceIntakeError(
            "source_not_regular_file",
            "A local source must be one regular file, not a directory or stream.",
        )
    try:
        resolved_path = path.resolve(strict=True)
    except OSError as error:
        raise SourceIntakeError(
            "source_validation_failed", "The local source could not be validated."
        ) from error
    try:
        resolved_metadata = resolved_path.lstat()
    except OSError as error:
        raise SourceIntakeError(
            "source_changed_during_snapshot", "The source changed during validation."
        ) from error
    if (
        not stat.S_ISREG(resolved_metadata.st_mode)
        or resolved_metadata.st_dev != metadata.st_dev
        or resolved_metadata.st_ino != metadata.st_ino
    ):
        raise SourceIntakeError(
            "source_changed_during_snapshot", "The source changed during validation."
        )
    return ValidatedLocalSource(
        path=resolved_path,
        device=metadata.st_dev,
        inode=metadata.st_ino,
    )


def sha256_file(path: Path) -> tuple[str, int]:
    """Hash one file without loading it into memory."""

    digest = hashlib.sha256()
    byte_count = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            byte_count += len(chunk)
    return digest.hexdigest(), byte_count


def snapshot_local_source(
    source_path: Path,
    input_root: Path,
    *,
    before_copy: Callable[[int], None] | None = None,
) -> SourceArtifact:
    """Create or reuse a double-hashed SourceArtifact below the project input root."""

    candidate = _validated_local_source(source_path)
    descriptor, opened_metadata = _open_regular_source(candidate)
    try:
        before_hash, before_size = _hash_open_file(descriptor)
        input_root.mkdir(parents=True, exist_ok=True)
        artifact_directory = input_root / before_hash
        media_path = artifact_directory / "media"

        existing_artifact = _existing_artifact(media_path)
        if existing_artifact is not None:
            existing_hash, existing_size = existing_artifact
            _ensure_candidate_is_unchanged(candidate.path, opened_metadata)
            after_hash, after_size = _hash_open_file(descriptor)
            if before_hash != after_hash or before_size != after_size:
                raise SourceIntakeError(
                    "source_changed_during_snapshot",
                    "The source changed while its existing artifact was being revalidated.",
                )
            if existing_hash != before_hash or existing_size != before_size:
                raise SourceIntakeError(
                    "artifact_hash_mismatch",
                    "The existing content-addressed artifact has unexpected bytes.",
                )
            return SourceArtifact(before_hash, before_hash, before_size, media_path)

        current_hash, current_size = _hash_open_file(descriptor)
        if before_hash != current_hash or before_size != current_size:
            raise SourceIntakeError(
                "source_changed_during_snapshot",
                "The source changed before its snapshot could be copied.",
            )
        pending_path = input_root / f".pending-{before_hash[:12]}-{uuid.uuid4().hex}"
        if before_copy is not None:
            before_copy(before_size)
        _copy_open_file(descriptor, pending_path, before_size)
        destination_hash, destination_size = sha256_file(pending_path)
        _ensure_candidate_is_unchanged(candidate.path, opened_metadata)
        after_hash, after_size = _hash_open_file(descriptor)
        if before_hash != after_hash or before_size != after_size:
            raise SourceIntakeError(
                "source_changed_during_snapshot",
                "The source changed while it was copied; retained pending bytes are not an "
                "artifact.",
            )
        if destination_hash != before_hash or destination_size != before_size:
            raise SourceIntakeError(
                "artifact_hash_mismatch",
                "The copied source bytes do not match the authorized source.",
            )
        try:
            artifact_directory.mkdir()
        except FileExistsError as error:
            existing_artifact = _existing_artifact(media_path)
            if existing_artifact is None:
                raise SourceIntakeError(
                    "artifact_directory_conflict",
                    "A retained artifact directory has no valid media file.",
                ) from error
            existing_hash, existing_size = existing_artifact
            if existing_hash != before_hash or existing_size != before_size:
                raise SourceIntakeError(
                    "artifact_hash_mismatch",
                    "Concurrent artifact creation produced different bytes.",
                )
        else:
            os.replace(pending_path, media_path)
            os.chmod(media_path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

        return SourceArtifact(before_hash, before_hash, before_size, media_path)
    except SourceIntakeError:
        raise
    except OSError as error:
        raise SourceIntakeError(
            "source_snapshot_failed", "The source could not be snapshotted safely."
        ) from error
    finally:
        os.close(descriptor)


def _open_regular_source(candidate: ValidatedLocalSource) -> tuple[int, os.stat_result]:
    """Open one regular source without following a path substituted as a symlink."""

    try:
        descriptor = os.open(candidate.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise SourceIntakeError(
                "source_symlink_rejected", "A local source must not be a symbolic link."
            ) from error
        raise SourceIntakeError(
            "source_changed_during_snapshot", "The source changed before its snapshot could begin."
        ) from error
    try:
        metadata = os.fstat(descriptor)
    except OSError as error:
        os.close(descriptor)
        raise SourceIntakeError(
            "source_snapshot_failed", "The source could not be snapshotted safely."
        ) from error
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise SourceIntakeError(
            "source_not_regular_file",
            "A local source must be one regular file, not a directory or stream.",
        )
    if metadata.st_dev != candidate.device or metadata.st_ino != candidate.inode:
        os.close(descriptor)
        raise SourceIntakeError(
            "source_changed_during_snapshot", "The source changed before its snapshot could begin."
        )
    return descriptor, metadata


def _hash_open_file(descriptor: int) -> tuple[str, int]:
    """Hash a duplicate descriptor so its caller retains ownership of the original."""

    digest = hashlib.sha256()
    byte_count = 0
    with os.fdopen(os.dup(descriptor), "rb") as source:
        source.seek(0)
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            byte_count += len(chunk)
    return digest.hexdigest(), byte_count


def _copy_open_file(descriptor: int, destination: Path, byte_count: int) -> None:
    """Copy from an already verified descriptor without reopening the source path."""

    with os.fdopen(os.dup(descriptor), "rb") as source, destination.open("xb") as output:
        source.seek(0)
        remaining = byte_count
        while remaining:
            chunk = source.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            output.write(chunk)
            remaining -= len(chunk)


def _existing_artifact(media_path: Path) -> tuple[str, int] | None:
    """Accept reuse only for a regular, unlinked project-owned artifact file."""

    try:
        directory_descriptor = os.open(
            media_path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except FileNotFoundError:
        return None
    except OSError as error:
        raise SourceIntakeError(
            "artifact_invalid", "The existing artifact directory cannot be validated."
        ) from error
    try:
        directory_metadata = os.fstat(directory_descriptor)
        if not stat.S_ISDIR(directory_metadata.st_mode):
            raise SourceIntakeError(
                "artifact_invalid", "The existing artifact directory must be project-owned."
            )
        try:
            media_descriptor = os.open(
                "media",
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_descriptor,
            )
        except FileNotFoundError:
            return None
        except OSError as error:
            raise SourceIntakeError(
                "artifact_invalid", "The existing artifact cannot be validated."
            ) from error
        try:
            media_metadata = os.fstat(media_descriptor)
            if not stat.S_ISREG(media_metadata.st_mode) or media_metadata.st_nlink != 1:
                raise SourceIntakeError(
                    "artifact_invalid", "The existing artifact must be one unlinked regular file."
                )
            return _hash_open_file(media_descriptor)
        finally:
            os.close(media_descriptor)
    finally:
        os.close(directory_descriptor)


def _ensure_candidate_is_unchanged(path: Path, opened_metadata: os.stat_result) -> None:
    """Reject a pathname replacement even if the original descriptor remains readable."""

    try:
        current_metadata = path.lstat()
    except OSError as error:
        raise SourceIntakeError(
            "source_changed_during_snapshot", "The source changed while it was being snapshotted."
        ) from error
    if (
        not stat.S_ISREG(current_metadata.st_mode)
        or current_metadata.st_dev != opened_metadata.st_dev
        or current_metadata.st_ino != opened_metadata.st_ino
    ):
        raise SourceIntakeError(
            "source_changed_during_snapshot", "The source changed while it was being snapshotted."
        )
