"""Offline unit contract for the acquired-asset verification helpers.

Phase 11 ticket 04 pins each model by ``asset_sha256`` -- the SHA-256 of its
canonical file manifest -- so a later run can re-derive it from disk and prove
it is running the audited bytes. These tests exercise that helper hermetically
in temp trees: no network, no model load, no real repository asset. The
integration counterpart (``test_phase_11_acquired_assets``) applies the same
helper to the real ``models/`` tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from video_content_pipeline.model_acquisition import (
    AssetVerificationError,
    build_file_manifest,
    file_sha256,
    manifest_asset_sha256,
    verify_acquired_asset,
)

# SHA-256 of b"alpha", from `printf alpha | shasum -a 256`, so the test never
# trusts the code it checks.
_ALPHA_SHA = "8ed3f6ad685b959ead7022518e1af76cd816f8e8ec7ccdda1ed4018e8f2223f8"


def _write(root: Path, rel: str, data: bytes) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_file_sha256_matches_known_vector(tmp_path: Path) -> None:
    target = tmp_path / "a.bin"
    target.write_bytes(b"alpha")
    assert file_sha256(target) == _ALPHA_SHA


def test_manifest_is_sorted_and_digest_is_walk_order_independent(tmp_path: Path) -> None:
    # Same bytes, two trees, files created in opposite order.
    _write(tmp_path / "one", "b/z.bin", b"two")
    _write(tmp_path / "one", "a.bin", b"one")
    _write(tmp_path / "two", "a.bin", b"one")
    _write(tmp_path / "two", "b/z.bin", b"two")

    m1 = build_file_manifest(tmp_path / "one")
    m2 = build_file_manifest(tmp_path / "two")

    assert [e["path"] for e in m1] == ["a.bin", "b/z.bin"]  # POSIX, sorted
    assert manifest_asset_sha256(m1) == manifest_asset_sha256(m2)


def test_digest_ignores_extra_annotation_keys(tmp_path: Path) -> None:
    _write(tmp_path, "a.bin", b"one")
    manifest = build_file_manifest(tmp_path)
    annotated = [{**entry, "note": "ignored"} for entry in manifest]
    assert manifest_asset_sha256(annotated) == manifest_asset_sha256(manifest)


def test_verify_round_trips_a_clean_asset(tmp_path: Path) -> None:
    _write(tmp_path, "model.onnx", b"weights")
    _write(tmp_path, "config.json", b"{}")
    manifest = build_file_manifest(tmp_path)
    digest = manifest_asset_sha256(manifest)
    verify_acquired_asset(manifest, digest, tmp_path)  # no raise


def test_verify_rejects_a_missing_file(tmp_path: Path) -> None:
    _write(tmp_path, "model.onnx", b"weights")
    _write(tmp_path, "config.json", b"{}")
    manifest = build_file_manifest(tmp_path)
    digest = manifest_asset_sha256(manifest)
    (tmp_path / "config.json").unlink()
    with pytest.raises(AssetVerificationError, match="missing pinned files"):
        verify_acquired_asset(manifest, digest, tmp_path)


def test_verify_rejects_an_unpinned_extra_file(tmp_path: Path) -> None:
    _write(tmp_path, "model.onnx", b"weights")
    manifest = build_file_manifest(tmp_path)
    digest = manifest_asset_sha256(manifest)
    _write(tmp_path, "sneaky.onnx", b"extra")
    with pytest.raises(AssetVerificationError, match="unpinned files"):
        verify_acquired_asset(manifest, digest, tmp_path)


def test_verify_rejects_tampered_bytes(tmp_path: Path) -> None:
    _write(tmp_path, "model.onnx", b"weights")
    manifest = build_file_manifest(tmp_path)
    digest = manifest_asset_sha256(manifest)
    (tmp_path / "model.onnx").write_bytes(b"tampered")
    with pytest.raises(AssetVerificationError, match="does not match its pinned hash"):
        verify_acquired_asset(manifest, digest, tmp_path)


def test_verify_rejects_a_digest_mismatch(tmp_path: Path) -> None:
    _write(tmp_path, "model.onnx", b"weights")
    manifest = build_file_manifest(tmp_path)
    with pytest.raises(AssetVerificationError, match="asset_sha256"):
        verify_acquired_asset(manifest, "0" * 64, tmp_path)
