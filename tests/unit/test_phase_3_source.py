from __future__ import annotations

from pathlib import Path

import pytest

from video_content_pipeline import source
from video_content_pipeline.source import (
    SourceIntakeError,
    calculate_disk_headroom,
    snapshot_local_source,
    validate_local_source_candidate,
)


def test_regular_source_is_snapshotted_and_reused_without_modifying_original(
    tmp_path: Path,
) -> None:
    original = tmp_path / "original.mp4"
    original.write_bytes(b"phase-3-source")
    input_root = tmp_path / "input"

    first = snapshot_local_source(original, input_root)
    second = snapshot_local_source(original, input_root)

    assert original.read_bytes() == b"phase-3-source"
    assert first == second
    assert first.media_path.read_bytes() == b"phase-3-source"
    assert first.media_path.parent.name == first.sha256
    assert not list(input_root.glob(".pending-*"))


def test_non_regular_local_sources_are_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(SourceIntakeError, match="regular file") as error:
        validate_local_source_candidate(directory)

    assert error.value.reason == "source_not_regular_file"


def test_symlink_source_is_rejected(tmp_path: Path) -> None:
    original = tmp_path / "original.mp4"
    original.write_bytes(b"media")
    alias = tmp_path / "alias.mp4"
    alias.symlink_to(original)

    with pytest.raises(SourceIntakeError) as error:
        validate_local_source_candidate(alias)

    assert error.value.reason == "source_symlink_rejected"


def test_snapshot_rejects_a_symlink_swapped_in_after_initial_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = tmp_path / "original.mp4"
    original.write_bytes(b"authorized")
    replacement = tmp_path / "replacement.mp4"
    replacement.write_bytes(b"unrelated")
    real_open = source.os.open

    def swap_then_open(path: str, *args: object, **kwargs: object) -> int:
        if Path(path) == original:
            original.unlink()
            original.symlink_to(replacement)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(source.os, "open", swap_then_open)

    with pytest.raises(SourceIntakeError) as error:
        snapshot_local_source(original, tmp_path / "input")

    assert error.value.reason == "source_symlink_rejected"


def test_snapshot_rejects_a_parent_directory_swap_after_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_directory = tmp_path / "source"
    source_directory.mkdir()
    original = source_directory / "original.mp4"
    original.write_bytes(b"authorized")
    replacement_directory = tmp_path / "replacement"
    replacement_directory.mkdir()
    (replacement_directory / "original.mp4").write_bytes(b"unrelated")
    real_open = source.os.open

    def swap_parent_then_open(path: str, *args: object, **kwargs: object) -> int:
        if Path(path) == original:
            source_directory.rename(tmp_path / "moved-source")
            source_directory.symlink_to(replacement_directory, target_is_directory=True)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(source.os, "open", swap_parent_then_open)

    with pytest.raises(SourceIntakeError) as error:
        snapshot_local_source(original, tmp_path / "input")

    assert error.value.reason == "source_changed_during_snapshot"


def test_snapshot_rejects_a_parent_directory_swap_during_path_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_directory = tmp_path / "source"
    source_directory.mkdir()
    original = source_directory / "original.mp4"
    original.write_bytes(b"authorized")
    replacement_directory = tmp_path / "replacement"
    replacement_directory.mkdir()
    (replacement_directory / "original.mp4").write_bytes(b"unrelated")
    real_resolve = Path.resolve

    def swap_parent_then_resolve(path: Path, *args: object, **kwargs: object) -> Path:
        if path == original:
            source_directory.rename(tmp_path / "moved-source")
            source_directory.symlink_to(replacement_directory, target_is_directory=True)
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", swap_parent_then_resolve)

    with pytest.raises(SourceIntakeError) as error:
        snapshot_local_source(original, tmp_path / "input")

    assert error.value.reason == "source_changed_during_snapshot"


def test_duplicate_artifact_reuse_does_not_require_precopy_headroom(tmp_path: Path) -> None:
    original = tmp_path / "original.mp4"
    original.write_bytes(b"phase-3-source")
    input_root = tmp_path / "input"
    first = snapshot_local_source(original, input_root)

    def fail_if_called(_byte_count: int) -> None:
        raise AssertionError("duplicate reuse must not reserve a new source copy")

    second = snapshot_local_source(original, input_root, before_copy=fail_if_called)

    assert second == first


@pytest.mark.parametrize("replacement", ["symlink", "hardlink"])
def test_duplicate_reuse_rejects_linked_artifacts(tmp_path: Path, replacement: str) -> None:
    original = tmp_path / "original.mp4"
    original.write_bytes(b"phase-3-source")
    input_root = tmp_path / "input"
    artifact = snapshot_local_source(original, input_root)
    artifact.media_path.unlink()
    if replacement == "symlink":
        artifact.media_path.symlink_to(original)
    else:
        artifact.media_path.hardlink_to(original)

    with pytest.raises(SourceIntakeError) as error:
        snapshot_local_source(original, input_root)

    assert error.value.reason == "artifact_invalid"


def test_snapshot_caps_pending_copy_to_the_reserved_source_size(tmp_path: Path) -> None:
    original = tmp_path / "original.mp4"
    original.write_bytes(b"source")

    def grow_source(_byte_count: int) -> None:
        original.write_bytes(b"source-that-grew-after-headroom-check")

    with pytest.raises(SourceIntakeError) as error:
        snapshot_local_source(original, tmp_path / "input", before_copy=grow_source)

    assert error.value.reason == "source_changed_during_snapshot"
    pending = next((tmp_path / "input").glob(".pending-*"))
    assert pending.stat().st_size == len(b"source")


def test_disk_headroom_uses_the_greater_of_one_gib_and_five_percent() -> None:
    small = calculate_disk_headroom(100)
    large = calculate_disk_headroom(100 * 1024**3)

    assert small.reserve_bytes == 1024**3
    assert large.reserve_bytes == 5 * 1024**3
    assert large.required_bytes == 105 * 1024**3
