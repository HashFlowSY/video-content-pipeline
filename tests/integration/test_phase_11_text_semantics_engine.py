"""Real Qwen3-4B text-semantics engine offline (Phase 11 ticket 10).

This is the one place the real Qwen3-4B-Instruct-2507-8bit runs -- through its Model
runtime subprocess (ADR 0055) against the pinned, vendored asset resolved from the
model registry, offline, from disk, on the provisioned machine where the git-ignored
``models/`` tree lives (error, never skip, mirroring the ticket 06-09 engine tests).
It proves the real engine, driven through the subprocess seam under the committed
decoding calibration (ADR 0056), returns output that flows through the *unchanged*
Text-model output projection and adjudication, and that it reports real MLX
peak-memory evidence. Chinese/English semantic quality is not asserted here -- that is
the maintainer's prototype review (Phase 11 ticket 13); this test asserts the
contract, the cue-evidence discipline, and provenance.

A general instruction model is not expected to emit the strict Phase 6 output
envelope, so the real run legitimately concludes either ``model_output_invalid`` (the
whole output is retained as restricted audit evidence and a diagnostic, never
fabricated content) or one of the composed statuses; either way it never crashes and
never invents formal segments from unprojected output.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_content_pipeline.text_contracts import revalidate_text_generation_contracts
from video_content_pipeline.text_generation import LoadedPart
from video_content_pipeline.text_semantics_engine import (
    generate_text_semantics,
    load_text_semantics_asset,
)

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
PART_ID = "fixture-part"
TRACK_ID = "stream-0"


def _registry_asset_sha256() -> str:
    registry = json.loads((REPO_ROOT / "models" / "registry.json").read_text(encoding="utf-8"))
    (candidate,) = [
        c for c in registry["candidates"] if c.get("candidate_id") == "qwen3-4b-instruct-2507-8bit"
    ]
    return str(candidate["asset_sha256"])


def test_real_text_semantics_asset_verifies_from_disk() -> None:
    model_dir, asset_sha256 = load_text_semantics_asset(REPO_ROOT)

    assert model_dir.is_dir()
    assert (model_dir / "config.json").is_file()
    assert asset_sha256 == _registry_asset_sha256()


def test_real_text_semantics_run_is_contract_valid_and_reports_peak(tmp_path: Path) -> None:
    contracts = revalidate_text_generation_contracts(REPO_ROOT)
    parts = (
        LoadedPart(
            part_id=PART_ID,
            track_id=TRACK_ID,
            cue_ids=(f"{PART_ID}:{TRACK_ID}:0", f"{PART_ID}:{TRACK_ID}:1"),
        ),
    )

    result = generate_text_semantics(
        REPO_ROOT,
        tmp_path / "work",
        contracts,
        source_id=PART_ID,
        stream_index=0,
        available=parts,
    )

    # Provenance: the pinned asset ran in its own subprocess under the committed
    # calibration, reporting a real MLX peak.
    assert result.model_asset_sha256 == _registry_asset_sha256()
    assert result.calibration_version
    assert result.peak_memory_bytes > 0

    # The raw model text is always retained only as restricted local audit evidence.
    assert result.restricted_raw_output.as_json()["restriction"] == "local_audit_only"
    assert result.restricted_raw_output.path.is_file()

    # Contract: the output either projected and composed, or was rejected whole as
    # model_output_invalid -- never a crash, and never formal segments from an
    # unprojected output.
    assert result.status in {"complete", "partial", "failed", "model_output_invalid"}
    if result.status == "model_output_invalid":
        assert result.segments == ()
        assert [d.reason for d in result.diagnostics] == ["model_output_invalid"]
