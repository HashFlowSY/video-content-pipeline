"""Offline unit contract for the ``text_semantics`` capability (Phase 11 ticket 10).

Ticket 10 defines ``text_semantics`` as the text-analysis Context's one model
capability and evaluates it from ``models/registry.json`` through the shared Phase 5
eligibility gates -- the same gates and states as the audio ``asr_*`` and visual-text
``ocr_primary`` capabilities. These tests build registry JSON inline in a temporary
project root and assert the deterministic capability states and the
``model_acquisition_required`` result with no text-analysis evidence. They also prove
the Controlled offline text adapter can never satisfy the real-model path: it is not a
registry candidate, carries no pinned asset hash, and the real engine cannot resolve
it. No model is downloaded or executed.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from video_content_pipeline.capabilities import MAX_MODEL_RESOURCE_BYTES
from video_content_pipeline.text_analysis import (
    TextAnalysisError,
    evaluate_text_semantics_capability,
)
from video_content_pipeline.text_semantics_engine import (
    TextSemanticsEngineError,
    resolve_text_semantics_candidate,
)

CAPABILITY = "text_semantics"
CANDIDATE_ID = "qwen3-4b-instruct-2507-8bit"


def _write_registry(project_root: Path, candidates: list[dict[str, object]]) -> Path:
    registry_path = project_root / "models" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps({"schema_version": 2, "candidates": candidates}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return registry_path


def _eligible_candidate(project_root: Path, *, asset_sha256: str) -> dict[str, object]:
    dependency_plan = f"models/plans/{CANDIDATE_ID}.md"
    plan_path = project_root / dependency_plan
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text("# dependency plan\n", encoding="utf-8")
    return {
        "candidate_id": CANDIDATE_ID,
        "capability": CAPABILITY,
        "official_source": {"url": f"https://example.invalid/{CANDIDATE_ID}", "approved": True},
        "license_approved": True,
        "revision": "fixture-r1",
        "asset_sha256": asset_sha256,
        "offline_runtime": True,
        "credential_required": False,
        "telemetry": False,
        "dependency_plan": dependency_plan,
        "resource_estimate": {"high_bytes": 5 * 1024**3},
    }


def _capability(document: dict[str, object], capability: str) -> dict[str, object]:
    return next(item for item in document["capabilities"] if item["capability"] == capability)


def test_missing_registry_requires_acquisition(tmp_path: Path) -> None:
    document = evaluate_text_semantics_capability(tmp_path).as_json()

    assert document["result"] == "model_acquisition_required"
    assert document["text_analysis_evidence"] is None
    assert document["model_registry"] is None
    assert [item["capability"] for item in document["capabilities"]] == [CAPABILITY]
    assert [item["state"] for item in document["capabilities"]] == ["model_acquisition_required"]
    assert [item["candidates"] for item in document["capabilities"]] == [[]]
    assert document["guarantees"] == {
        "model_acquisition": "not_attempted",
        "model_execution": "not_attempted",
        "network_access": "not_attempted",
        "outputs_publication": "not_attempted",
    }


def test_research_candidate_without_eligibility_fields_is_ineligible(tmp_path: Path) -> None:
    _write_registry(tmp_path, [{"candidate_id": CANDIDATE_ID, "capability": CAPABILITY}])

    document = evaluate_text_semantics_capability(tmp_path).as_json()

    capability = _capability(document, CAPABILITY)
    assert capability["state"] == "model_ineligible"
    candidate = capability["candidates"][0]
    assert candidate["candidate_id"] == CANDIDATE_ID
    assert candidate["state"] == "unsupported"
    assert candidate["reason"] == "model_candidate_evidence_incomplete"
    assert candidate["model_identity"] is None


def test_eligible_candidate_requires_acquisition_and_binds_identity(tmp_path: Path) -> None:
    _write_registry(tmp_path, [_eligible_candidate(tmp_path, asset_sha256="a" * 64)])

    document = evaluate_text_semantics_capability(tmp_path).as_json()

    capability = _capability(document, CAPABILITY)
    assert capability["state"] == "model_acquisition_required"
    assert capability["candidates"][0]["state"] == "eligible"
    assert capability["candidates"][0]["model_identity"] == "a" * 64
    # No model is acquired or executed in this evaluation.
    assert document["result"] == "model_acquisition_required"
    assert document["text_analysis_evidence"] is None


def test_credential_gated_candidate_is_blocked(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        [{"candidate_id": CANDIDATE_ID, "capability": CAPABILITY, "credential_required": True}],
    )

    capability = _capability(evaluate_text_semantics_capability(tmp_path).as_json(), CAPABILITY)
    assert capability["state"] == "model_credential_gated"
    assert capability["candidates"][0]["state"] == "blocked"
    assert capability["candidates"][0]["reason"] == "model_credential_gated"


def test_resource_envelope_exceeded_candidate_is_ineligible(tmp_path: Path) -> None:
    over_envelope = _eligible_candidate(tmp_path, asset_sha256="a" * 64)
    over_envelope["resource_estimate"] = {"high_bytes": MAX_MODEL_RESOURCE_BYTES + 1}
    _write_registry(tmp_path, [over_envelope])

    capability = _capability(evaluate_text_semantics_capability(tmp_path).as_json(), CAPABILITY)
    assert capability["state"] == "model_ineligible"
    assert capability["candidates"][0]["state"] == "blocked"
    assert capability["candidates"][0]["reason"] == "resource_envelope_exceeded"


def test_registry_evidence_is_hash_pinned(tmp_path: Path) -> None:
    registry_path = _write_registry(
        tmp_path, [{"candidate_id": CANDIDATE_ID, "capability": CAPABILITY}]
    )

    document = evaluate_text_semantics_capability(tmp_path).as_json()

    assert document["model_registry"]["sha256"] == sha256(registry_path.read_bytes()).hexdigest()
    assert Path(document["model_registry"]["path"]) == registry_path


def test_legacy_schema_registry_has_no_text_semantics_candidate(tmp_path: Path) -> None:
    registry_path = tmp_path / "models" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps({"schema_version": 1, "models": [{"capability": "vad"}]}), encoding="utf-8"
    )

    document = evaluate_text_semantics_capability(tmp_path).as_json()

    assert document["result"] == "model_acquisition_required"
    assert [item["state"] for item in document["capabilities"]] == ["model_acquisition_required"]
    assert [item["candidates"] for item in document["capabilities"]] == [[]]


def test_invalid_registry_is_rejected(tmp_path: Path) -> None:
    registry_path = tmp_path / "models" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(json.dumps({"schema_version": 2, "candidates": {}}), encoding="utf-8")

    with pytest.raises(TextAnalysisError) as error:
        evaluate_text_semantics_capability(tmp_path)
    assert error.value.reason == "model_registry_invalid"


def test_duplicate_candidate_ids_are_rejected(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        [
            {"candidate_id": CANDIDATE_ID, "capability": CAPABILITY},
            {"candidate_id": CANDIDATE_ID, "capability": "vad"},
        ],
    )

    with pytest.raises(TextAnalysisError) as error:
        evaluate_text_semantics_capability(tmp_path)
    assert error.value.reason == "model_registry_invalid"


def test_foreign_capabilities_are_ignored(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        [
            {"candidate_id": "silero-vad", "capability": "vad"},
            {"candidate_id": CANDIDATE_ID, "capability": CAPABILITY},
        ],
    )

    document = evaluate_text_semantics_capability(tmp_path).as_json()

    assert [item["capability"] for item in document["capabilities"]] == [CAPABILITY]
    candidates = _capability(document, CAPABILITY)["candidates"]
    assert [candidate["candidate_id"] for candidate in candidates] == [CANDIDATE_ID]


def test_controlled_offline_adapter_can_never_satisfy_the_real_model_path(tmp_path: Path) -> None:
    # The Controlled offline text adapter exists only as a config artifact; it is not
    # a registry candidate and carries no pinned asset hash, so it can never grade as
    # an eligible real model and the real engine cannot resolve it (ADR 0037 lineage).
    adapter_dir = tmp_path / "config" / "text-analysis"
    adapter_dir.mkdir(parents=True)
    (adapter_dir / "controlled-adapter.json").write_text(
        json.dumps({"schema_version": 1, "version": "phase-06-controlled-text-adapter-v1"}),
        encoding="utf-8",
    )
    _write_registry(tmp_path, [])

    document = evaluate_text_semantics_capability(tmp_path).as_json()
    capability = _capability(document, CAPABILITY)
    # No eligible real candidate exists, and the offline adapter is not counted.
    assert capability["candidates"] == []
    assert capability["state"] == "model_acquisition_required"

    with pytest.raises(TextSemanticsEngineError) as error:
        resolve_text_semantics_candidate(tmp_path)
    assert error.value.reason == "text_semantics_candidate_absent"
