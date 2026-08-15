"""Offline unit contract for Phase 8 ticket 01.

Ticket 01 registers the provider-neutral Visual-text capability contract -- the
single OCR capability ``ocr_primary`` -- and evaluates it from
``models/registry.json`` using the shared Phase 5 eligibility gates. These tests
build registry JSON inline in a temporary project root -- exactly as the Phase
6/7 controlled-adapter and capability tests do -- and assert the deterministic
capability state and the Model-acquisition-required visual-text result with no
OCR evidence. One test reads the real repository registry to prove RapidOCR is
registered as a metadata-only research candidate. No model is downloaded or
executed, no frame is extracted, and no network is accessed.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from video_content_pipeline.visual_text import (
    VISUAL_TEXT_CAPABILITIES,
    VisualTextError,
    evaluate_ocr_capabilities,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

_OFFLINE_GUARANTEES = {
    "frame_extraction": "not_attempted",
    "model_acquisition": "not_attempted",
    "model_execution": "not_attempted",
    "network_access": "not_attempted",
    "outputs_publication": "not_attempted",
}


def _write_registry(project_root: Path, candidates: list[dict[str, object]]) -> Path:
    registry_path = project_root / "models" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps({"schema_version": 2, "candidates": candidates}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return registry_path


def _eligible_candidate(
    project_root: Path,
    *,
    candidate_id: str,
    capability: str,
    asset_sha256: str,
) -> dict[str, object]:
    dependency_plan = f"models/plans/{candidate_id}.md"
    plan_path = project_root / dependency_plan
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(f"# {candidate_id} dependency plan\n", encoding="utf-8")
    return {
        "candidate_id": candidate_id,
        "capability": capability,
        "official_source": {
            "url": f"https://example.invalid/{candidate_id}",
            "approved": True,
        },
        "license_approved": True,
        "revision": "fixture-r1",
        "asset_sha256": asset_sha256,
        "offline_runtime": True,
        "credential_required": False,
        "telemetry": False,
        "dependency_plan": dependency_plan,
        "resource_estimate": {"high_bytes": 4 * 1024**3},
    }


def _capability(report_json: dict[str, object], capability: str) -> dict[str, object]:
    return next(item for item in report_json["capabilities"] if item["capability"] == capability)


def test_contract_is_ocr_primary_only() -> None:
    # The provider-neutral contract carries exactly one model capability and no
    # general-vision slot (ADR 0047).
    assert VISUAL_TEXT_CAPABILITIES == ("ocr_primary",)


def test_missing_registry_requires_acquisition(tmp_path: Path) -> None:
    document = evaluate_ocr_capabilities(tmp_path).as_json()

    assert document["result"] == "model_acquisition_required"
    assert document["ocr_evidence"] is None
    assert document["model_registry"] is None
    assert [item["capability"] for item in document["capabilities"]] == ["ocr_primary"]
    assert [item["state"] for item in document["capabilities"]] == ["model_acquisition_required"]
    assert [item["candidates"] for item in document["capabilities"]] == [[]]
    assert document["guarantees"] == _OFFLINE_GUARANTEES


def test_repo_registry_registers_rapidocr_as_metadata_only_research_candidate() -> None:
    # Criterion 2: RapidOCR is registered in the real repository registry as a
    # research candidate -- metadata only, so it is not eligible for execution
    # and the result stays model_acquisition_required (no download, no run).
    document = evaluate_ocr_capabilities(REPO_ROOT).as_json()

    ocr_primary = _capability(document, "ocr_primary")
    candidate_ids = [candidate["candidate_id"] for candidate in ocr_primary["candidates"]]
    assert "rapidocr" in candidate_ids
    rapidocr = next(c for c in ocr_primary["candidates"] if c["candidate_id"] == "rapidocr")
    assert rapidocr["state"] != "eligible"
    assert rapidocr["model_identity"] is None
    assert document["result"] == "model_acquisition_required"
    assert document["ocr_evidence"] is None
    assert document["guarantees"] == _OFFLINE_GUARANTEES


def test_no_general_vision_model_capability_is_evaluated(tmp_path: Path) -> None:
    # Criterion 3: even if a general-vision candidate is present in the registry,
    # it is a foreign capability -- never a slot in the visual-text contract.
    _write_registry(
        tmp_path,
        [
            {"candidate_id": "rapidocr", "capability": "ocr_primary"},
            {"candidate_id": "qwen3-vl-8b", "capability": "vlm_general"},
        ],
    )

    document = evaluate_ocr_capabilities(tmp_path).as_json()

    assert [item["capability"] for item in document["capabilities"]] == ["ocr_primary"]
    ocr_primary = _capability(document, "ocr_primary")
    assert [candidate["candidate_id"] for candidate in ocr_primary["candidates"]] == ["rapidocr"]


def test_research_candidate_without_eligibility_fields_is_ineligible(tmp_path: Path) -> None:
    _write_registry(tmp_path, [{"candidate_id": "rapidocr", "capability": "ocr_primary"}])

    document = evaluate_ocr_capabilities(tmp_path).as_json()

    assert document["result"] == "model_acquisition_required"
    ocr_primary = _capability(document, "ocr_primary")
    assert ocr_primary["state"] == "model_ineligible"
    candidate = ocr_primary["candidates"][0]
    assert candidate["candidate_id"] == "rapidocr"
    assert candidate["state"] == "unsupported"
    assert candidate["reason"] == "model_candidate_evidence_incomplete"
    assert candidate["model_identity"] is None


def test_eligible_candidate_still_requires_acquisition(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        [
            _eligible_candidate(
                tmp_path,
                candidate_id="rapidocr",
                capability="ocr_primary",
                asset_sha256="a" * 64,
            )
        ],
    )

    document = evaluate_ocr_capabilities(tmp_path).as_json()

    ocr_primary = _capability(document, "ocr_primary")
    assert ocr_primary["state"] == "model_acquisition_required"
    assert ocr_primary["candidates"][0]["state"] == "eligible"
    assert ocr_primary["candidates"][0]["model_identity"] == "a" * 64
    # An eligible candidate means acquisition is the remaining step; no model is
    # acquired or executed in this phase.
    assert document["result"] == "model_acquisition_required"
    assert document["ocr_evidence"] is None
    assert document["guarantees"] == _OFFLINE_GUARANTEES


def test_credential_gated_candidate_is_blocked(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        [{"candidate_id": "rapidocr", "capability": "ocr_primary", "credential_required": True}],
    )

    document = evaluate_ocr_capabilities(tmp_path).as_json()

    ocr_primary = _capability(document, "ocr_primary")
    assert ocr_primary["state"] == "model_credential_gated"
    assert ocr_primary["candidates"][0]["state"] == "blocked"
    assert ocr_primary["candidates"][0]["reason"] == "model_credential_gated"


def test_resource_envelope_exceeded_candidate_is_ineligible(tmp_path: Path) -> None:
    over_envelope = _eligible_candidate(
        tmp_path,
        candidate_id="rapidocr",
        capability="ocr_primary",
        asset_sha256="a" * 64,
    )
    over_envelope["resource_estimate"] = {"high_bytes": 24 * 1024**3 + 1}
    _write_registry(tmp_path, [over_envelope])

    document = evaluate_ocr_capabilities(tmp_path).as_json()

    ocr_primary = _capability(document, "ocr_primary")
    assert ocr_primary["state"] == "model_ineligible"
    assert ocr_primary["candidates"][0]["state"] == "blocked"
    assert ocr_primary["candidates"][0]["reason"] == "resource_envelope_exceeded"


def test_registry_evidence_is_hash_pinned(tmp_path: Path) -> None:
    registry_path = _write_registry(
        tmp_path,
        [{"candidate_id": "rapidocr", "capability": "ocr_primary"}],
    )

    document = evaluate_ocr_capabilities(tmp_path).as_json()

    assert document["model_registry"]["sha256"] == sha256(registry_path.read_bytes()).hexdigest()
    assert Path(document["model_registry"]["path"]) == registry_path


def test_foreign_audio_capabilities_are_ignored(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        [
            {"candidate_id": "silero-vad", "capability": "vad"},
            {"candidate_id": "qwen3-asr-1-7b", "capability": "asr_primary"},
            {"candidate_id": "rapidocr", "capability": "ocr_primary"},
        ],
    )

    document = evaluate_ocr_capabilities(tmp_path).as_json()

    assert [item["capability"] for item in document["capabilities"]] == ["ocr_primary"]
    ocr_primary = _capability(document, "ocr_primary")
    assert [candidate["candidate_id"] for candidate in ocr_primary["candidates"]] == ["rapidocr"]


def test_legacy_schema_registry_has_no_ocr_candidates(tmp_path: Path) -> None:
    registry_path = tmp_path / "models" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {"schema_version": 1, "models": [{"capability": "vad", "status": "model_unavailable"}]}
        ),
        encoding="utf-8",
    )

    document = evaluate_ocr_capabilities(tmp_path).as_json()

    assert document["result"] == "model_acquisition_required"
    assert [item["state"] for item in document["capabilities"]] == ["model_acquisition_required"]
    assert [item["candidates"] for item in document["capabilities"]] == [[]]


def test_invalid_registry_is_rejected(tmp_path: Path) -> None:
    registry_path = tmp_path / "models" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(json.dumps({"schema_version": 2, "candidates": {}}), encoding="utf-8")

    with pytest.raises(VisualTextError) as error:
        evaluate_ocr_capabilities(tmp_path)
    assert error.value.reason == "model_registry_invalid"


def test_duplicate_candidate_ids_are_rejected(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        [
            {"candidate_id": "rapidocr", "capability": "ocr_primary"},
            {"candidate_id": "rapidocr", "capability": "ocr_primary"},
        ],
    )

    with pytest.raises(VisualTextError) as error:
        evaluate_ocr_capabilities(tmp_path)
    assert error.value.reason == "model_registry_invalid"
