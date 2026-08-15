"""Unit coverage for Phase 6 ticket 03 generation and rendering contracts.

Ticket 03 gives the versioned prompt template, Text-model output projection
schema, evidence-rule record, and Controlled offline text adapter identity
explicit immutable identities, rejects whole-invalid projections as
``model_output_invalid`` while retaining raw output, and deterministically
renders Markdown from a verified JSON report while keeping JSON authoritative.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from video_content_pipeline import text_contracts


def _write_contracts(project_root: Path) -> None:
    """Provision a self-consistent set of Phase 6 contract artifacts."""

    config = project_root / "config"
    text_config = config / "text-analysis"
    text_config.mkdir(parents=True, exist_ok=True)
    rules = {
        "schema_version": 1,
        "id": "phase-06-text-analysis-rules-v1",
        "cue_rules_version": "phase-06-cue-rules-v1",
        "prompt_template_version": "phase-06-prompt-template-v1",
        "output_schema_version": "phase-06-output-schema-v1",
        "evidence_rules_version": "phase-06-evidence-rules-v1",
        "controlled_adapter_identity": "phase-06-controlled-text-adapter-v1",
    }
    prompt_template = {"schema_version": 1, "version": "phase-06-prompt-template-v1"}
    output_schema = {
        "schema_version": 1,
        "version": "phase-06-output-schema-v1",
        "envelope": {
            "expected_schema_version": 1,
            "required_fields": [
                "schema_version",
                "output_schema_version",
                "adapter_identity",
                "result",
            ],
            "result": {
                "required_fields": ["parts"],
                "list_fields": ["parts"],
                "optional_object_or_null_fields": ["collection_summary"],
            },
        },
    }
    evidence_rules = {"schema_version": 1, "version": "phase-06-evidence-rules-v1"}
    controlled_adapter = {
        "schema_version": 1,
        "version": "phase-06-controlled-text-adapter-v1",
        "implementation_version": "phase-06-controlled-text-adapter-impl-v1",
        "prompt_template_version": "phase-06-prompt-template-v1",
        "output_schema_version": "phase-06-output-schema-v1",
        "evidence_rules_version": "phase-06-evidence-rules-v1",
    }
    artifacts = {
        config / "text-analysis-rules.json": rules,
        text_config / "prompt-template.json": prompt_template,
        text_config / "output-schema.json": output_schema,
        text_config / "evidence-rules.json": evidence_rules,
        text_config / "controlled-adapter.json": controlled_adapter,
    }
    for path, payload in artifacts.items():
        path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _valid_projection() -> dict[str, object]:
    return {
        "schema_version": 1,
        "output_schema_version": "phase-06-output-schema-v1",
        "adapter_identity": "phase-06-controlled-text-adapter-v1",
        "result": {"parts": [], "collection_summary": None},
    }


def test_revalidate_contracts_binds_every_versioned_identity(tmp_path: Path) -> None:
    _write_contracts(tmp_path)

    contracts = text_contracts.revalidate_text_generation_contracts(tmp_path)

    prompt_bytes = (tmp_path / "config" / "text-analysis" / "prompt-template.json").read_bytes()
    assert contracts.prompt_template.version == "phase-06-prompt-template-v1"
    assert contracts.prompt_template.evidence.sha256 == sha256(prompt_bytes).hexdigest()
    assert contracts.output_schema.version == "phase-06-output-schema-v1"
    assert contracts.evidence_rules.version == "phase-06-evidence-rules-v1"
    assert contracts.controlled_adapter.version == "phase-06-controlled-text-adapter-v1"
    document = contracts.as_json()
    assert set(document) == {
        "prompt_template",
        "output_schema",
        "evidence_rules",
        "controlled_adapter",
    }
    assert document["prompt_template"]["version"] == "phase-06-prompt-template-v1"


def test_revalidate_contracts_rejects_a_version_mismatch(tmp_path: Path) -> None:
    _write_contracts(tmp_path)
    prompt_path = tmp_path / "config" / "text-analysis" / "prompt-template.json"
    prompt_path.write_text(
        json.dumps({"schema_version": 1, "version": "phase-06-prompt-template-DRIFT"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(text_contracts.TextContractError) as excinfo:
        text_contracts.revalidate_text_generation_contracts(tmp_path)

    assert excinfo.value.reason == "prompt_template_invalid"


def test_revalidate_contracts_rejects_a_missing_artifact(tmp_path: Path) -> None:
    _write_contracts(tmp_path)
    (tmp_path / "config" / "text-analysis" / "output-schema.json").unlink()

    with pytest.raises(text_contracts.TextContractError) as excinfo:
        text_contracts.revalidate_text_generation_contracts(tmp_path)

    assert excinfo.value.reason == "output_schema_invalid"


def test_revalidate_contracts_rejects_an_inconsistent_adapter_identity(tmp_path: Path) -> None:
    _write_contracts(tmp_path)
    adapter_path = tmp_path / "config" / "text-analysis" / "controlled-adapter.json"
    adapter = json.loads(adapter_path.read_text(encoding="utf-8"))
    adapter["output_schema_version"] = "phase-06-output-schema-STALE"
    adapter_path.write_text(json.dumps(adapter) + "\n", encoding="utf-8")

    with pytest.raises(text_contracts.TextContractError) as excinfo:
        text_contracts.revalidate_text_generation_contracts(tmp_path)

    assert excinfo.value.reason == "controlled_adapter_invalid"


def test_projection_accepts_a_complete_envelope(tmp_path: Path) -> None:
    _write_contracts(tmp_path)
    contracts = text_contracts.revalidate_text_generation_contracts(tmp_path)

    outcome = text_contracts.project_text_model_output(_valid_projection(), contracts)

    assert outcome.state == "projected"
    assert outcome.projection == _valid_projection()
    assert outcome.diagnostic is None


def test_projection_never_injects_defaults(tmp_path: Path) -> None:
    _write_contracts(tmp_path)
    contracts = text_contracts.revalidate_text_generation_contracts(tmp_path)
    raw = _valid_projection()

    outcome = text_contracts.project_text_model_output(raw, contracts)

    assert outcome.projection is not None
    assert set(outcome.projection) == set(raw)
    assert set(outcome.projection["result"]) == {"parts", "collection_summary"}


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda p: p.pop("result"), id="missing_result"),
        pytest.param(lambda p: p.__setitem__("schema_version", 2), id="wrong_schema_version"),
        pytest.param(
            lambda p: p.__setitem__("output_schema_version", "phase-06-output-schema-OTHER"),
            id="schema_version_mismatch",
        ),
        pytest.param(
            lambda p: p.__setitem__("adapter_identity", "phase-06-other-adapter"),
            id="adapter_mismatch",
        ),
        pytest.param(lambda p: p["result"].pop("parts"), id="missing_parts"),
        pytest.param(lambda p: p["result"].__setitem__("parts", {}), id="parts_not_a_list"),
        pytest.param(lambda p: p.__setitem__("result", []), id="result_not_a_mapping"),
    ],
)
def test_projection_rejects_whole_invalid_output(tmp_path: Path, mutate) -> None:  # noqa: ANN001
    _write_contracts(tmp_path)
    contracts = text_contracts.revalidate_text_generation_contracts(tmp_path)
    raw = _valid_projection()
    mutate(raw)

    outcome = text_contracts.project_text_model_output(raw, contracts)

    assert outcome.state == "model_output_invalid"
    assert outcome.projection is None
    assert outcome.diagnostic is not None
    assert outcome.diagnostic.reason == "model_output_invalid"


def test_projection_rejects_non_mapping_output(tmp_path: Path) -> None:
    _write_contracts(tmp_path)
    contracts = text_contracts.revalidate_text_generation_contracts(tmp_path)

    outcome = text_contracts.project_text_model_output("not-json-object", contracts)

    assert outcome.state == "model_output_invalid"
    assert outcome.projection is None


def test_render_markdown_is_deterministic_and_hash_pinned() -> None:
    report = {
        "status": "controlled_adapter_unavailable",
        "plan_id": "plan-a",
        "subtitle_report_id": "1" * 32,
        "audio_completeness": "not_verified",
        "segments": [],
        "chapters": [],
        "collection_summary": None,
        "diagnostics": [{"reason": "controlled_adapter_unavailable", "message": "none"}],
    }

    first = text_contracts.render_text_analysis_markdown(report)
    second = text_contracts.render_text_analysis_markdown(dict(report))

    assert first.version == text_contracts.TEXT_REPORT_RENDERER_VERSION
    assert first.text == second.text
    assert first.sha256 == second.sha256 == sha256(first.text.encode("utf-8")).hexdigest()
    assert first.byte_count == len(first.text.encode("utf-8"))
    assert "not_verified" in first.text
    assert first.as_json() == {
        "version": text_contracts.TEXT_REPORT_RENDERER_VERSION,
        "sha256": first.sha256,
        "byte_count": first.byte_count,
    }


def test_render_markdown_excludes_raw_generated_output() -> None:
    report = {
        "status": "failed",
        "plan_id": "plan-a",
        "subtitle_report_id": "1" * 32,
        "audio_completeness": "not_verified",
        "segments": [],
        "chapters": [],
        "collection_summary": None,
        "restricted_raw_output": [{"path": "work/raw.txt", "sha256": "deadbeef"}],
        "diagnostics": [],
    }

    rendered = text_contracts.render_text_analysis_markdown(report)

    assert "deadbeef" not in rendered.text
    assert "work/raw.txt" not in rendered.text
