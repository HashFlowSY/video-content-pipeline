"""Offline unit contract for the committed model registry (Phase 11 tickets 01, 04).

Ticket 01 completed ``models/registry.json`` to the plan §13.2 field set
(metadata only): it filled the recorded diarization candidate vacancy with the
two sherpa-onnx pipeline assets, added the ``text_semantics`` candidate,
recorded RapidOCR's 2026-08-16 license/source approval, and completed every
candidate's provenance fields.

Ticket 04 then executed the maintainer-confirmed download plans, so every
candidate is now ``acquired``: pinned by an exact ``revision`` and an
``asset_sha256`` (the SHA-256 of its canonical file manifest), with a
first-download authorization record. This contract proves those provenance
facts. Runtime *eligibility* stays a later concern -- no candidate carries a
``resource_estimate`` yet, so the shared gate still grades every acquired
candidate below ``eligible`` until its adapter/prototype lands.

These tests read the real repository registry offline. No model is downloaded,
hashed against the network, executed, or network-accessed here; on-disk
re-hashing lives in ``tests/integration/test_phase_11_acquired_assets.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from video_content_pipeline.capabilities import (
    SHA256_PATTERN,
    assess_candidate,
    parse_candidate_matrix,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# Every capability the pipeline evaluates from the registry, so the shared
# parser returns -- and this contract inspects -- every committed candidate.
_ALL_CAPABILITIES = (
    "vad",
    "forced_alignment",
    "diarization",
    "asr_primary",
    "asr_review",
    "ocr_primary",
    "text_semantics",
)


class _RegistryError(AssertionError):
    pass


def _registry() -> dict[str, Any]:
    return json.loads((REPO_ROOT / "models" / "registry.json").read_text(encoding="utf-8"))


def _grouped() -> dict[str, list[Any]]:
    return parse_candidate_matrix(
        _registry(), _ALL_CAPABILITIES, invalid_error=_RegistryError
    )


def test_registry_is_the_schema_2_candidate_matrix() -> None:
    registry = _registry()
    assert registry["schema_version"] == 2
    assert registry["automatic_downloads"] is False
    assert registry["runtime_network_access"] is False


def test_diarization_vacancy_is_filled_with_two_acquired_candidates() -> None:
    grouped = _grouped()

    assert [candidate["candidate_id"] for candidate in grouped["diarization"]] == [
        "sherpa-onnx-pyannote-segmentation-3-0",
        "3dspeaker-campplus-zh-en-advanced",
    ]
    for candidate in grouped["diarization"]:
        assert candidate["verification_status"] == "acquired"
        assert assess_candidate(candidate, "diarization", REPO_ROOT).state != "eligible"


def test_text_semantics_candidate_is_registered_and_acquired() -> None:
    grouped = _grouped()

    assert [candidate["candidate_id"] for candidate in grouped["text_semantics"]] == [
        "qwen3-4b-instruct-2507-8bit"
    ]
    candidate = grouped["text_semantics"][0]
    assert candidate["quantization"] == "8bit"
    assert candidate["verification_status"] == "acquired"
    assert assess_candidate(candidate, "text_semantics", REPO_ROOT).state != "eligible"


def test_rapidocr_is_acquired_from_the_pinned_wheel() -> None:
    grouped = _grouped()
    rapidocr = next(c for c in grouped["ocr_primary"] if c["candidate_id"] == "rapidocr")

    assert rapidocr["official_source"]["approved"] is True
    assert rapidocr["license_approved"] is True
    # Bundled models are pinned from the wheel (recorded, not downloaded); the
    # default det/cls/rec roles are recorded from the RapidOCR().config dump.
    assert rapidocr["verification_status"] == "acquired"
    assert rapidocr["local_path"] == "bundled-in-wheel"
    assert rapidocr["revision"] == "3.9.2"
    assert set(rapidocr["default_models"]) == {"det", "cls", "rec"}
    # Still below eligible until a resource estimate lands (ticket 11/13).
    assert assess_candidate(rapidocr, "ocr_primary", REPO_ROOT).state != "eligible"


def test_every_candidate_is_acquired_and_carries_provenance() -> None:
    registry = _registry()
    candidates = registry["candidates"]
    assert candidates, "the registry must carry the selected candidates"

    for candidate in candidates:
        capability = candidate["capability"]
        # Capability, source, license, and the license-approval state are present
        # on every candidate (ticket 01 acceptance criterion 2).
        assert isinstance(capability, str) and capability
        assert candidate["official_source"]["url"].startswith("https://")
        assert candidate["official_source"]["approved"] is True
        assert isinstance(candidate["license"], str) and candidate["license"]
        assert candidate["license_approved"] is True
        # Ticket 04 acquisition provenance: pinned revision + asset hash + a
        # non-empty verified manifest + a first-download authorization record.
        assert candidate["verification_status"] == "acquired"
        assert isinstance(candidate["revision"], str) and candidate["revision"]
        assert candidate["revision"] != "pending-download-plan-pin"
        assert SHA256_PATTERN.fullmatch(candidate["asset_sha256"]) is not None
        assert candidate["file_manifest"], "an acquired candidate must carry a manifest"
        auth = candidate["first_download_authorization"]
        assert auth["scope"] == "model_download"
        assert auth["separate_from_media_authorization"] is True
        # Acquisition does not, by itself, make a candidate runtime-eligible.
        assert assess_candidate(candidate, capability, REPO_ROOT).state != "eligible"


def test_no_candidate_is_credential_gated() -> None:
    # The whole selected stack is credential-free (D1-D7); a credential-gated
    # candidate would block its capability, so none may carry that flag.
    for candidate in _registry()["candidates"]:
        assert candidate["credential_required"] is False
