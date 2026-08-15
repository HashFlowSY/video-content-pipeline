"""Offline unit contract for Phase 7 ticket 01.

Ticket 01 registers provider-neutral ``asr_primary`` and ``asr_review``
capability contracts and evaluates them from ``models/registry.json`` using the
shared Phase 5 eligibility gates. These tests build registry JSON inline in a
temporary project root -- exactly as the Phase 6 controlled-adapter tests do --
and assert the deterministic capability states, the Independent-model review
requirement, and the ``model_acquisition_required`` result with no transcription
evidence. No model is downloaded or executed.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from video_content_pipeline.transcription import (
    TranscriptionError,
    evaluate_asr_capabilities,
)


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


def test_missing_registry_requires_acquisition_for_both_capabilities(tmp_path: Path) -> None:
    report = evaluate_asr_capabilities(tmp_path)
    document = report.as_json()

    assert document["result"] == "model_acquisition_required"
    assert document["transcription_evidence"] is None
    assert document["model_registry"] is None
    assert [item["capability"] for item in document["capabilities"]] == [
        "asr_primary",
        "asr_review",
    ]
    assert [item["state"] for item in document["capabilities"]] == [
        "model_acquisition_required",
        "model_acquisition_required",
    ]
    assert [item["candidates"] for item in document["capabilities"]] == [[], []]
    assert document["independent_review"]["state"] == "no_eligible_primary"
    assert document["guarantees"] == {
        "model_acquisition": "not_attempted",
        "model_execution": "not_attempted",
        "network_access": "not_attempted",
        "outputs_publication": "not_attempted",
    }


def test_research_candidates_without_eligibility_fields_are_ineligible(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        [
            {"candidate_id": "qwen3-asr-1-7b", "capability": "asr_primary"},
            {"candidate_id": "whisper-large-v3", "capability": "asr_review"},
        ],
    )

    document = evaluate_asr_capabilities(tmp_path).as_json()

    assert document["result"] == "model_acquisition_required"
    assert _capability(document, "asr_primary")["state"] == "model_ineligible"
    assert _capability(document, "asr_review")["state"] == "model_ineligible"
    primary_candidate = _capability(document, "asr_primary")["candidates"][0]
    assert primary_candidate["candidate_id"] == "qwen3-asr-1-7b"
    assert primary_candidate["state"] == "unsupported"
    assert primary_candidate["reason"] == "model_candidate_evidence_incomplete"
    assert primary_candidate["model_identity"] is None
    assert document["independent_review"]["state"] == "no_eligible_primary"


def test_eligible_independent_pair_resolves_independent_review(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        [
            _eligible_candidate(
                tmp_path,
                candidate_id="qwen3-asr-1-7b",
                capability="asr_primary",
                asset_sha256="a" * 64,
            ),
            _eligible_candidate(
                tmp_path,
                candidate_id="whisper-large-v3",
                capability="asr_review",
                asset_sha256="b" * 64,
            ),
        ],
    )

    document = evaluate_asr_capabilities(tmp_path).as_json()

    assert _capability(document, "asr_primary")["state"] == "model_acquisition_required"
    assert _capability(document, "asr_review")["state"] == "model_acquisition_required"
    assert _capability(document, "asr_primary")["candidates"][0]["state"] == "eligible"
    assert _capability(document, "asr_primary")["candidates"][0]["model_identity"] == "a" * 64
    assert document["independent_review"] == {
        "state": "available",
        "primary_model_identity": "a" * 64,
        "review_model_identity": "b" * 64,
    }
    # No model is acquired or executed in this phase.
    assert document["result"] == "model_acquisition_required"
    assert document["transcription_evidence"] is None


def test_same_model_review_is_not_independent(tmp_path: Path) -> None:
    shared_asset = "c" * 64
    _write_registry(
        tmp_path,
        [
            _eligible_candidate(
                tmp_path,
                candidate_id="qwen3-asr-1-7b",
                capability="asr_primary",
                asset_sha256=shared_asset,
            ),
            _eligible_candidate(
                tmp_path,
                candidate_id="qwen3-asr-1-7b-review",
                capability="asr_review",
                asset_sha256=shared_asset,
            ),
        ],
    )

    document = evaluate_asr_capabilities(tmp_path).as_json()

    assert document["independent_review"]["state"] == "review_same_model_as_primary"
    assert document["independent_review"]["primary_model_identity"] == shared_asset
    assert document["independent_review"]["review_model_identity"] == shared_asset


def test_credential_gated_review_capability_is_blocked(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        [
            _eligible_candidate(
                tmp_path,
                candidate_id="qwen3-asr-1-7b",
                capability="asr_primary",
                asset_sha256="a" * 64,
            ),
            {
                "candidate_id": "whisper-large-v3",
                "capability": "asr_review",
                "credential_required": True,
            },
        ],
    )

    document = evaluate_asr_capabilities(tmp_path).as_json()

    review = _capability(document, "asr_review")
    assert review["state"] == "model_credential_gated"
    assert review["candidates"][0]["state"] == "blocked"
    assert review["candidates"][0]["reason"] == "model_credential_gated"
    assert document["independent_review"]["state"] == "no_eligible_review"


def test_resource_envelope_exceeded_primary_is_ineligible(tmp_path: Path) -> None:
    over_envelope = _eligible_candidate(
        tmp_path,
        candidate_id="qwen3-asr-1-7b",
        capability="asr_primary",
        asset_sha256="a" * 64,
    )
    over_envelope["resource_estimate"] = {"high_bytes": 24 * 1024**3 + 1}
    _write_registry(tmp_path, [over_envelope])

    document = evaluate_asr_capabilities(tmp_path).as_json()

    primary = _capability(document, "asr_primary")
    assert primary["state"] == "model_ineligible"
    assert primary["candidates"][0]["state"] == "blocked"
    assert primary["candidates"][0]["reason"] == "resource_envelope_exceeded"


def test_registry_evidence_is_hash_pinned(tmp_path: Path) -> None:
    registry_path = _write_registry(
        tmp_path,
        [{"candidate_id": "qwen3-asr-1-7b", "capability": "asr_primary"}],
    )

    document = evaluate_asr_capabilities(tmp_path).as_json()

    assert document["model_registry"]["sha256"] == sha256(registry_path.read_bytes()).hexdigest()
    assert Path(document["model_registry"]["path"]) == registry_path


def test_legacy_schema_registry_has_no_asr_candidates(tmp_path: Path) -> None:
    # A legacy schema-1 registry (a models list, no candidate matrix) is a valid
    # registry that simply carries no ASR entries; it must not be rejected.
    registry_path = tmp_path / "models" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "models": [{"capability": "vad", "status": "model_unavailable"}],
            }
        ),
        encoding="utf-8",
    )

    document = evaluate_asr_capabilities(tmp_path).as_json()

    assert document["result"] == "model_acquisition_required"
    assert [item["state"] for item in document["capabilities"]] == [
        "model_acquisition_required",
        "model_acquisition_required",
    ]
    assert [item["candidates"] for item in document["capabilities"]] == [[], []]


def test_invalid_registry_is_rejected(tmp_path: Path) -> None:
    registry_path = tmp_path / "models" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(json.dumps({"schema_version": 2, "candidates": {}}), encoding="utf-8")

    with pytest.raises(TranscriptionError) as error:
        evaluate_asr_capabilities(tmp_path)
    assert error.value.reason == "model_registry_invalid"


def test_duplicate_candidate_ids_are_rejected(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        [
            {"candidate_id": "qwen3-asr-1-7b", "capability": "asr_primary"},
            {"candidate_id": "qwen3-asr-1-7b", "capability": "asr_review"},
        ],
    )

    with pytest.raises(TranscriptionError) as error:
        evaluate_asr_capabilities(tmp_path)
    assert error.value.reason == "model_registry_invalid"


def test_foreign_audio_capabilities_are_ignored(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        [
            {"candidate_id": "silero-vad", "capability": "vad"},
            {"candidate_id": "qwen3-asr-1-7b", "capability": "asr_primary"},
        ],
    )

    document = evaluate_asr_capabilities(tmp_path).as_json()

    assert [item["capability"] for item in document["capabilities"]] == [
        "asr_primary",
        "asr_review",
    ]
    primary = _capability(document, "asr_primary")
    assert [candidate["candidate_id"] for candidate in primary["candidates"]] == ["qwen3-asr-1-7b"]
