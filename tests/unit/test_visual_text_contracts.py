"""Offline unit contract for Phase 8 ticket 05: the OCR adapter and projection.

Ticket 05 routes OCR output through exactly one auditable entry point -- the
versioned OCR output projection -- behind the Controlled offline OCR adapter.
These tests exercise the pure contract functions directly: the two versioned
identities are revalidated and bound, the optional bound fixture is hash-verified
and carries its input identity, the input manifest is order-independent, and the
projection turns a well-formed output into typed items (source language preserved,
including mixed Chinese/English) while rejecting any malformed output whole as
``model_output_invalid``. No model runs and no frame is extracted.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from video_content_pipeline import visual_text_contracts as contracts
from video_content_pipeline.timecode import ExactTime

_SCHEMA_VERSION = "phase-08-ocr-projection-schema-v1"
_ADAPTER_VERSION = "phase-08-controlled-ocr-adapter-v1"


def _install_contracts(project_root: Path) -> None:
    """Copy the shipped rules and OCR contract artifacts into a fixture project root."""

    source = Path(__file__).resolve().parents[2] / "config" / "visual-text"
    destination = project_root / "config" / "visual-text"
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("rules.json", "ocr-projection-schema.json", "controlled-ocr-adapter.json"):
        (destination / name).write_text(
            (source / name).read_text(encoding="utf-8"), encoding="utf-8"
        )


def _valid_output() -> dict[str, object]:
    return {
        "schema_version": 1,
        "projection_schema_version": _SCHEMA_VERSION,
        "adapter_identity": _ADAPTER_VERSION,
        "capability": "ocr_primary",
        "result": {
            "items": [
                {
                    "part_id": "part-1",
                    "visual_page_id": "page-01",
                    "pts": {"numerator": 0, "denominator": 1},
                    "text": "登录 Login",
                    "confidence": 0.9,
                    "language_spans": [
                        {"language": "zh", "start_char": 0, "end_char": 2},
                        {"language": "en", "start_char": 3, "end_char": 8},
                    ],
                }
            ]
        },
    }


# --- Versioned identity revalidation ----------------------------------------


def test_revalidate_binds_the_two_versioned_identities(tmp_path: Path) -> None:
    _install_contracts(tmp_path)
    bound = contracts.revalidate_ocr_contracts(tmp_path)
    assert bound.projection_schema.version == _SCHEMA_VERSION
    assert bound.controlled_adapter.version == _ADAPTER_VERSION
    assert bound.implementation_version == "phase-08-controlled-ocr-adapter-impl-v1"


def test_revalidate_rejects_a_missing_adapter(tmp_path: Path) -> None:
    _install_contracts(tmp_path)
    (tmp_path / "config" / "visual-text" / "controlled-ocr-adapter.json").unlink()
    with pytest.raises(contracts.VisualTextContractError) as excinfo:
        contracts.revalidate_ocr_contracts(tmp_path)
    assert excinfo.value.reason == "controlled_ocr_adapter_invalid"


def test_revalidate_requires_an_implementation_version(tmp_path: Path) -> None:
    _install_contracts(tmp_path)
    adapter_path = tmp_path / "config" / "visual-text" / "controlled-ocr-adapter.json"
    document = json.loads(adapter_path.read_text(encoding="utf-8"))
    del document["implementation_version"]
    adapter_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(contracts.VisualTextContractError) as excinfo:
        contracts.revalidate_ocr_contracts(tmp_path)
    assert excinfo.value.reason == "controlled_ocr_adapter_invalid"


def test_revalidate_rejects_a_stale_projection_schema_reference(tmp_path: Path) -> None:
    _install_contracts(tmp_path)
    adapter_path = tmp_path / "config" / "visual-text" / "controlled-ocr-adapter.json"
    document = json.loads(adapter_path.read_text(encoding="utf-8"))
    document["projection_schema_version"] = "phase-08-ocr-projection-schema-v0"
    adapter_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(contracts.VisualTextContractError) as excinfo:
        contracts.revalidate_ocr_contracts(tmp_path)
    assert excinfo.value.reason == "controlled_ocr_adapter_invalid"


# --- Input manifest ---------------------------------------------------------


def test_input_manifest_is_order_independent(tmp_path: Path) -> None:
    a = ("part-1", "page-01", ExactTime(0), "aaa")
    b = ("part-2", "page-01", ExactTime(5), "bbb")
    forward = contracts.ocr_input_manifest_document("plan", [a, b])
    reverse = contracts.ocr_input_manifest_document("plan", [b, a])
    assert forward == reverse
    assert contracts.ocr_input_manifest_sha256(forward) == contracts.ocr_input_manifest_sha256(
        reverse
    )


# --- Bound fixture ----------------------------------------------------------


def _fixture_adapter(project_root: Path, output_payload: dict[str, object]) -> None:
    """Rewrite the installed adapter with a bound fixture pointing at ``output_payload``."""

    fixtures = project_root / "config" / "visual-text" / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(output_payload).encode("utf-8")
    (fixtures / "ocr-primary-output.json").write_bytes(raw)
    adapter_path = project_root / "config" / "visual-text" / "controlled-ocr-adapter.json"
    document = json.loads(adapter_path.read_text(encoding="utf-8"))
    document["fixture"] = {
        "capability": "ocr_primary",
        "input_fixture_sha256": "e" * 64,
        "output_fixture_path": "config/visual-text/fixtures/ocr-primary-output.json",
        "output_fixture_sha256": sha256(raw).hexdigest(),
    }
    adapter_path.write_text(json.dumps(document), encoding="utf-8")


def test_absent_fixture_block_yields_no_fixture(tmp_path: Path) -> None:
    _install_contracts(tmp_path)
    bound = contracts.revalidate_ocr_contracts(tmp_path)
    assert contracts.load_controlled_ocr_fixture(bound, tmp_path) is None


def test_bound_fixture_is_hash_verified_and_carries_input_identity(tmp_path: Path) -> None:
    _install_contracts(tmp_path)
    _fixture_adapter(tmp_path, _valid_output())
    bound = contracts.revalidate_ocr_contracts(tmp_path)
    fixture = contracts.load_controlled_ocr_fixture(bound, tmp_path)
    assert fixture is not None
    assert fixture.capability == "ocr_primary"
    assert fixture.input_fixture_sha256 == "e" * 64
    assert fixture.implementation_version == "phase-08-controlled-ocr-adapter-impl-v1"
    assert json.loads(fixture.raw_output.decode("utf-8")) == _valid_output()


def test_bound_fixture_rejects_a_tampered_output_hash(tmp_path: Path) -> None:
    _install_contracts(tmp_path)
    _fixture_adapter(tmp_path, _valid_output())
    adapter_path = tmp_path / "config" / "visual-text" / "controlled-ocr-adapter.json"
    document = json.loads(adapter_path.read_text(encoding="utf-8"))
    document["fixture"]["output_fixture_sha256"] = "0" * 64
    adapter_path.write_text(json.dumps(document), encoding="utf-8")
    bound = contracts.revalidate_ocr_contracts(tmp_path)
    with pytest.raises(contracts.VisualTextContractError) as excinfo:
        contracts.load_controlled_ocr_fixture(bound, tmp_path)
    assert excinfo.value.reason == "controlled_ocr_fixture_invalid"


# --- Versioned output projection --------------------------------------------


def _bound(tmp_path: Path) -> contracts.OcrGenerationContracts:
    _install_contracts(tmp_path)
    return contracts.revalidate_ocr_contracts(tmp_path)


def test_projection_projects_valid_items_and_preserves_mixed_language(tmp_path: Path) -> None:
    projection = contracts.project_ocr_output(_valid_output(), _bound(tmp_path))
    assert projection.state == "projected"
    assert projection.adapter_version == _ADAPTER_VERSION
    (item,) = projection.items
    # AC#4: text is kept verbatim in its source language, mixed Chinese/English intact.
    assert item.text == "登录 Login"
    assert [(span.language, span.start_char, span.end_char) for span in item.language_spans] == [
        ("zh", 0, 2),
        ("en", 3, 8),
    ]
    assert item.confidence == 0.9
    assert item.visual_page_id == "page-01"
    assert item.pts == ExactTime(0)


def test_projection_rejects_an_unknown_capability(tmp_path: Path) -> None:
    output = _valid_output()
    output["capability"] = "ocr_secondary"
    projection = contracts.project_ocr_output(output, _bound(tmp_path))
    assert projection.state == "model_output_invalid"
    assert projection.items == ()
    assert projection.diagnostic is not None


def test_projection_rejects_a_missing_required_field(tmp_path: Path) -> None:
    output = _valid_output()
    del output["result"]["items"][0]["confidence"]  # type: ignore[index]
    projection = contracts.project_ocr_output(output, _bound(tmp_path))
    assert projection.state == "model_output_invalid"


def test_projection_rejects_confidence_outside_the_schema_range(tmp_path: Path) -> None:
    output = _valid_output()
    output["result"]["items"][0]["confidence"] = 1.5  # type: ignore[index]
    projection = contracts.project_ocr_output(output, _bound(tmp_path))
    assert projection.state == "model_output_invalid"


def test_projection_rejects_a_language_span_beyond_the_text(tmp_path: Path) -> None:
    output = _valid_output()
    output["result"]["items"][0]["language_spans"] = [  # type: ignore[index]
        {"language": "en", "start_char": 0, "end_char": 999}
    ]
    projection = contracts.project_ocr_output(output, _bound(tmp_path))
    assert projection.state == "model_output_invalid"


def test_projection_rejects_a_non_object_output(tmp_path: Path) -> None:
    projection = contracts.project_ocr_output(None, _bound(tmp_path))
    assert projection.state == "model_output_invalid"


# --- Restricted raw-output retention ----------------------------------------


def test_retain_restricted_raw_output_is_audit_only_and_immutable(tmp_path: Path) -> None:
    workspace = tmp_path / "work" / "attempt"
    pointer = contracts.retain_restricted_ocr_output(
        b"{}", workspace, capability="ocr_primary", label="visual-text"
    )
    record = pointer.as_json()
    assert record["restricted"] is True and record["audit_only"] is True
    written = (
        workspace / "restricted" / "ocr" / "ocr_primary" / "visual-text-raw-native-output.json"
    )
    assert written.exists()
    # An identical rewrite is idempotent; a differing rewrite is a conflict.
    contracts.retain_restricted_ocr_output(
        b"{}", workspace, capability="ocr_primary", label="visual-text"
    )
    with pytest.raises(contracts.VisualTextContractError) as excinfo:
        contracts.retain_restricted_ocr_output(
            b'{"x":1}', workspace, capability="ocr_primary", label="visual-text"
        )
    assert excinfo.value.reason == "visual_text_raw_output_conflict"
