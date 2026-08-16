"""Offline unit contract for Phase 11 ticket 01 -- the committed model registry.

Ticket 01 completes ``models/registry.json`` to the plan §13.2 field set
(metadata only, no downloads): it fills the recorded diarization candidate
vacancy with the two sherpa-onnx pipeline assets, adds the ``text_semantics``
candidate, records RapidOCR's 2026-08-16 license/source approval, and completes
every candidate's provenance fields. Until a separately authorized acquisition
pins each asset's SHA-256, no candidate is eligible.

These tests read the real repository registry and prove those facts through the
shared Phase 5 eligibility gates. No model is downloaded, hashed, executed, or
network-accessed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from video_content_pipeline.capabilities import assess_candidate, parse_candidate_matrix

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


def test_diarization_vacancy_is_filled_with_two_not_yet_acquired_candidates() -> None:
    grouped = _grouped()

    assert [candidate["candidate_id"] for candidate in grouped["diarization"]] == [
        "sherpa-onnx-pyannote-segmentation-3-0",
        "3dspeaker-campplus-zh-en-advanced",
    ]
    for candidate in grouped["diarization"]:
        assert assess_candidate(candidate, "diarization", REPO_ROOT).state != "eligible"


def test_text_semantics_candidate_is_registered_and_not_yet_acquired() -> None:
    grouped = _grouped()

    assert [candidate["candidate_id"] for candidate in grouped["text_semantics"]] == [
        "qwen3-4b-instruct-2507-8bit"
    ]
    candidate = grouped["text_semantics"][0]
    assert candidate["quantization"] == "8bit"
    assert assess_candidate(candidate, "text_semantics", REPO_ROOT).state != "eligible"


def test_rapidocr_license_and_source_approval_is_recorded() -> None:
    grouped = _grouped()
    rapidocr = next(c for c in grouped["ocr_primary"] if c["candidate_id"] == "rapidocr")

    assert rapidocr["official_source"]["approved"] is True
    assert rapidocr["license_approved"] is True
    # Approval recorded, but the wheel-bundled model bytes are not yet pinned, so
    # the capability stays not-eligible until an authorized acquisition.
    assert assess_candidate(rapidocr, "ocr_primary", REPO_ROOT).state != "eligible"


def test_every_candidate_carries_the_required_field_set_and_stays_unacquired() -> None:
    registry = _registry()
    candidates = registry["candidates"]
    assert candidates, "the registry must carry the selected candidates"

    for candidate in candidates:
        capability = candidate["capability"]
        # Capability, source, license, and the license-approval state are present
        # on every candidate (acceptance criterion 2).
        assert isinstance(capability, str) and capability
        assert candidate["official_source"]["url"].startswith("https://")
        assert isinstance(candidate["official_source"]["approved"], bool)
        assert isinstance(candidate["license"], str) and candidate["license"]
        assert isinstance(candidate["license_approved"], bool)
        # The fields that keep an unacquired asset ineligible until acquisition:
        # no pinned asset hash yet, and the verification state records that.
        assert candidate.get("asset_sha256") is None
        assert candidate["verification_status"] == "unacquired"
        # Proven through the real eligibility gate, not just by inspection.
        assert assess_candidate(candidate, capability, REPO_ROOT).state != "eligible"


def test_no_candidate_is_credential_gated() -> None:
    # The whole selected stack is credential-free (D1-D7); a credential-gated
    # candidate would block its capability, so none may carry that flag.
    for candidate in _registry()["candidates"]:
        assert candidate["credential_required"] is False
