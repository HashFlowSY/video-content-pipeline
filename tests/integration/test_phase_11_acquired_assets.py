"""Every acquired model asset SHA-256-verifies against the registry, from disk.

Phase 11 ticket 04 acceptance: each acquired candidate's files are re-hashed
from disk and must match the pinned ``file_manifest`` and ``asset_sha256``.
This is an on-disk, offline check -- no network, no model load -- so it runs on
the provisioned machine where the ``models/`` tree (git-ignored) actually
lives, mirroring the Phase 10 identity-pinned toolchain tests (error, never
skip). A missing or tampered asset raises the typed verification failure.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from video_content_pipeline.capabilities import SHA256_PATTERN
from video_content_pipeline.model_acquisition import BUNDLED_IN_WHEEL, verify_acquired_asset

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]


def _acquired() -> list[dict]:
    registry = json.loads((REPO_ROOT / "models" / "registry.json").read_text(encoding="utf-8"))
    return [c for c in registry["candidates"] if c.get("verification_status") == "acquired"]


def _asset_root(candidate: dict) -> Path:
    local_path = candidate["local_path"]
    if local_path == BUNDLED_IN_WHEEL:
        spec = importlib.util.find_spec("rapidocr")
        assert spec and spec.submodule_search_locations, "rapidocr wheel is not installed"
        return Path(spec.submodule_search_locations[0]) / "models"
    return REPO_ROOT / local_path


def test_all_seven_downloads_plus_rapidocr_are_acquired() -> None:
    ids = {c["candidate_id"] for c in _acquired()}
    assert ids == {
        "qwen3-asr-1-7b",
        "whisper-large-v3",
        "qwen3-forced-aligner-0-6b",
        "qwen3-4b-instruct-2507-8bit",
        "silero-vad",
        "sherpa-onnx-pyannote-segmentation-3-0",
        "3dspeaker-campplus-zh-en-advanced",
        "rapidocr",
    }


def test_models_tree_holds_only_planned_asset_files() -> None:
    """The on-disk ``models/`` tree contains only registry-planned files.

    Acceptance checkbox 5 ("models tree contains only planned files"): every
    file under ``models/`` (except the tracked registry) must belong to some
    acquired candidate's pinned manifest at its planned ``local_path``. A rogue
    asset directory -- a download outside a confirmed plan -- fails here.
    """

    models_root = REPO_ROOT / "models"
    on_disk = {
        p.relative_to(REPO_ROOT).as_posix()
        for p in models_root.rglob("*")
        if p.is_file() and p.name != "registry.json"
    }

    planned: set[str] = set()
    for candidate in _acquired():
        if candidate["local_path"] == BUNDLED_IN_WHEEL:
            continue  # ships in the wheel, never under models/
        base = candidate["local_path"].rstrip("/")
        for entry in candidate["file_manifest"]:
            planned.add(f"{base}/{entry['path']}")

    assert on_disk == planned, {
        "unplanned_on_disk": sorted(on_disk - planned),
        "planned_but_absent": sorted(planned - on_disk),
    }


@pytest.mark.parametrize("candidate", _acquired(), ids=lambda c: c["candidate_id"])
def test_acquired_asset_verifies_from_disk(candidate: dict) -> None:
    assert SHA256_PATTERN.fullmatch(candidate["asset_sha256"]) is not None
    assert candidate["file_manifest"], "an acquired asset must carry a non-empty manifest"

    asset_root = _asset_root(candidate)
    assert asset_root.is_dir(), f"acquired asset tree is absent: {asset_root}"

    # Re-hashes every file from disk; raises AssetVerificationError on any drift.
    verify_acquired_asset(candidate["file_manifest"], candidate["asset_sha256"], asset_root)
