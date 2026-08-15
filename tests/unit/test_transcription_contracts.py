"""Offline unit contract for Phase 7 ticket 03.

Ticket 03 gives the Controlled offline ASR adapter and its output projection
explicit versioned identities -- mirroring the Phase 6 text adapter -- so ASR
text enters the evidence system only through one auditable entry point. These
tests build the contract artifacts and the raw model output inline in a
temporary project root, exactly as the Phase 6 controlled-adapter tests do, and
assert:

* the projection-schema and controlled-adapter identities are revalidated and
  bound to hash evidence, and drift is rejected;
* the optional bound fixture is hash-verified and confined to project-relative,
  non-escaping paths, carrying its symmetric input-manifest hash;
* a raw ASR output projects into typed cues with exact rational times, text,
  optional per-token confidence, and language spans -- while any incomplete or
  schema-invalid output becomes ``model_output_invalid`` with no partial
  projection; and
* raw output is retained as restricted local audit evidence marked
  audit-only.

No model is downloaded or executed.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from video_content_pipeline import transcription_contracts as contracts
from video_content_pipeline.timecode import ExactTime


def _write_contracts(project_root: Path) -> None:
    """Provision a self-consistent set of Phase 7 transcription contract artifacts."""

    config = project_root / "config" / "transcription"
    config.mkdir(parents=True, exist_ok=True)
    rules = {
        "schema_version": 1,
        "id": "phase-07-transcription-rules-v1",
        "projection_schema_version": "phase-07-asr-projection-schema-v1",
        "controlled_adapter_identity": "phase-07-controlled-asr-adapter-v1",
    }
    projection_schema = {
        "schema_version": 1,
        "version": "phase-07-asr-projection-schema-v1",
        "cue": {
            "required_fields": ["ordinal", "start", "end", "text"],
            "token": {"required_fields": ["text"], "confidence_range": [0, 1]},
            "language_span": {"required_fields": ["language", "start_token", "end_token"]},
        },
    }
    controlled_adapter = {
        "schema_version": 1,
        "version": "phase-07-controlled-asr-adapter-v1",
        "implementation_version": "phase-07-controlled-asr-adapter-impl-v1",
        "projection_schema_version": "phase-07-asr-projection-schema-v1",
    }
    artifacts = {
        config / "transcription-rules.json": rules,
        config / "asr-projection-schema.json": projection_schema,
        config / "controlled-adapter.json": controlled_adapter,
    }
    for path, payload in artifacts.items():
        path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _valid_output() -> dict[str, object]:
    return {
        "schema_version": 1,
        "projection_schema_version": "phase-07-asr-projection-schema-v1",
        "adapter_identity": "phase-07-controlled-asr-adapter-v1",
        "capability": "asr_primary",
        "result": {
            "cues": [
                {
                    "ordinal": 0,
                    "start": {"numerator": 0, "denominator": 1},
                    "end": {"numerator": 5, "denominator": 1},
                    "text": "你好 world",
                    "tokens": [
                        {"text": "你好", "confidence": 0.98},
                        {"text": "world"},
                    ],
                    "language_spans": [
                        {"language": "zh", "start_token": 0, "end_token": 1},
                        {"language": "en", "start_token": 1, "end_token": 2},
                    ],
                }
            ]
        },
    }


# --- Contract revalidation --------------------------------------------------


def test_revalidate_binds_every_versioned_identity(tmp_path: Path) -> None:
    _write_contracts(tmp_path)

    bound = contracts.revalidate_asr_contracts(tmp_path)

    schema_path = tmp_path / "config" / "transcription" / "asr-projection-schema.json"
    schema_bytes = schema_path.read_bytes()
    assert bound.projection_schema.version == "phase-07-asr-projection-schema-v1"
    assert bound.projection_schema.evidence.sha256 == sha256(schema_bytes).hexdigest()
    assert bound.controlled_adapter.version == "phase-07-controlled-asr-adapter-v1"
    document = bound.as_json()
    assert set(document) == {"projection_schema", "controlled_adapter"}
    assert document["controlled_adapter"]["version"] == "phase-07-controlled-asr-adapter-v1"


def test_revalidate_rejects_a_version_mismatch(tmp_path: Path) -> None:
    _write_contracts(tmp_path)
    schema_path = tmp_path / "config" / "transcription" / "asr-projection-schema.json"
    schema_path.write_text(
        json.dumps({"schema_version": 1, "version": "phase-07-asr-projection-schema-DRIFT"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(contracts.TranscriptionContractError) as excinfo:
        contracts.revalidate_asr_contracts(tmp_path)

    assert excinfo.value.reason == "asr_projection_schema_invalid"


def test_revalidate_rejects_a_missing_artifact(tmp_path: Path) -> None:
    _write_contracts(tmp_path)
    (tmp_path / "config" / "transcription" / "controlled-adapter.json").unlink()

    with pytest.raises(contracts.TranscriptionContractError) as excinfo:
        contracts.revalidate_asr_contracts(tmp_path)

    assert excinfo.value.reason == "controlled_asr_adapter_invalid"


def test_revalidate_rejects_an_inconsistent_adapter_identity(tmp_path: Path) -> None:
    _write_contracts(tmp_path)
    adapter_path = tmp_path / "config" / "transcription" / "controlled-adapter.json"
    adapter = json.loads(adapter_path.read_text(encoding="utf-8"))
    adapter["projection_schema_version"] = "phase-07-asr-projection-schema-STALE"
    adapter_path.write_text(json.dumps(adapter) + "\n", encoding="utf-8")

    with pytest.raises(contracts.TranscriptionContractError) as excinfo:
        contracts.revalidate_asr_contracts(tmp_path)

    assert excinfo.value.reason == "controlled_asr_adapter_invalid"


def test_revalidate_rejects_a_schema_without_a_cue_ruleset(tmp_path: Path) -> None:
    _write_contracts(tmp_path)
    schema_path = tmp_path / "config" / "transcription" / "asr-projection-schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema.pop("cue")
    schema_path.write_text(json.dumps(schema, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(contracts.TranscriptionContractError) as excinfo:
        contracts.revalidate_asr_contracts(tmp_path)

    assert excinfo.value.reason == "asr_projection_schema_invalid"


def test_projection_confidence_bound_is_schema_driven(tmp_path: Path) -> None:
    # Narrowing the versioned schema's confidence range governs the projection:
    # the same output projects under [0, 1] but is rejected under [0, 0.5].
    _write_contracts(tmp_path)
    raw = _valid_output()
    raw["result"]["cues"][0]["tokens"][0]["confidence"] = 0.9

    permissive = contracts.revalidate_asr_contracts(tmp_path)
    assert contracts.project_asr_output(raw, permissive).state == "projected"

    schema_path = tmp_path / "config" / "transcription" / "asr-projection-schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["cue"]["token"]["confidence_range"] = [0, 0.5]
    schema_path.write_text(json.dumps(schema, sort_keys=True) + "\n", encoding="utf-8")

    narrowed = contracts.revalidate_asr_contracts(tmp_path)
    assert contracts.project_asr_output(raw, narrowed).state == "model_output_invalid"


def test_projection_required_fields_are_schema_driven(tmp_path: Path) -> None:
    # Adding a required cue field the output omits makes an otherwise valid cue
    # invalid, without a code change -- the versioned schema governs requiredness.
    _write_contracts(tmp_path)
    schema_path = tmp_path / "config" / "transcription" / "asr-projection-schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["cue"]["required_fields"].append("speaker")
    schema_path.write_text(json.dumps(schema, sort_keys=True) + "\n", encoding="utf-8")
    bound = contracts.revalidate_asr_contracts(tmp_path)

    projection = contracts.project_asr_output(_valid_output(), bound)

    assert projection.state == "model_output_invalid"


# --- Symmetric input hashing ------------------------------------------------


def test_input_manifest_hash_is_stable_and_order_independent() -> None:
    document = contracts.asr_input_manifest_document(
        "a" * 32,
        [("src-b", 1, "b" * 64), ("src-a", 0, "a" * 64)],
    )
    reordered = contracts.asr_input_manifest_document(
        "a" * 32,
        [("src-a", 0, "a" * 64), ("src-b", 1, "b" * 64)],
    )

    assert document["source_count"] == 2
    # Sources are canonically ordered so hashing is independent of caller order.
    assert contracts.asr_input_manifest_sha256(document) == contracts.asr_input_manifest_sha256(
        reordered
    )


def test_input_manifest_hash_changes_with_inputs() -> None:
    base = contracts.asr_input_manifest_document("a" * 32, [("src-a", 0, "a" * 64)])
    drifted_audio = contracts.asr_input_manifest_document("c" * 32, [("src-a", 0, "a" * 64)])
    drifted_source = contracts.asr_input_manifest_document("a" * 32, [("src-a", 0, "d" * 64)])

    base_hash = contracts.asr_input_manifest_sha256(base)
    assert base_hash != contracts.asr_input_manifest_sha256(drifted_audio)
    assert base_hash != contracts.asr_input_manifest_sha256(drifted_source)


# --- Bound fixture ----------------------------------------------------------


def _fixture_adapter(project_root: Path, output_payload: dict[str, object]) -> dict[str, object]:
    """Author a fixture-bearing adapter document bound to ``output_payload``."""

    fixtures = project_root / "config" / "transcription" / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(output_payload, sort_keys=True).encode("utf-8")
    fixture_path = fixtures / "asr-primary-output.json"
    fixture_path.write_bytes(raw)
    return {
        "schema_version": 1,
        "version": "phase-07-controlled-asr-adapter-v1",
        "implementation_version": "phase-07-controlled-asr-adapter-impl-v1",
        "projection_schema_version": "phase-07-asr-projection-schema-v1",
        "fixture": {
            "capability": "asr_primary",
            "input_fixture_sha256": "e" * 64,
            "output_fixture_path": "config/transcription/fixtures/asr-primary-output.json",
            "output_fixture_sha256": sha256(raw).hexdigest(),
        },
    }


def test_absent_fixture_block_yields_no_fixture(tmp_path: Path) -> None:
    _write_contracts(tmp_path)
    adapter = json.loads(
        (tmp_path / "config" / "transcription" / "controlled-adapter.json").read_text(
            encoding="utf-8"
        )
    )

    assert contracts.load_controlled_asr_fixture(adapter, tmp_path) is None


def test_bound_fixture_is_hash_verified_and_carries_input_identity(tmp_path: Path) -> None:
    adapter = _fixture_adapter(tmp_path, _valid_output())

    fixture = contracts.load_controlled_asr_fixture(adapter, tmp_path)

    assert fixture is not None
    assert fixture.capability == "asr_primary"
    assert fixture.input_fixture_sha256 == "e" * 64
    assert json.loads(fixture.raw_output.decode("utf-8")) == _valid_output()


def test_bound_fixture_rejects_a_tampered_output_hash(tmp_path: Path) -> None:
    adapter = _fixture_adapter(tmp_path, _valid_output())
    adapter["fixture"]["output_fixture_sha256"] = "0" * 64

    with pytest.raises(contracts.TranscriptionContractError) as excinfo:
        contracts.load_controlled_asr_fixture(adapter, tmp_path)

    assert excinfo.value.reason == "controlled_asr_fixture_invalid"


@pytest.mark.parametrize("escaping", ["/etc/passwd", "../secret.json", "a/../../b.json"])
def test_bound_fixture_rejects_escaping_paths(tmp_path: Path, escaping: str) -> None:
    adapter = _fixture_adapter(tmp_path, _valid_output())
    adapter["fixture"]["output_fixture_path"] = escaping

    with pytest.raises(contracts.TranscriptionContractError) as excinfo:
        contracts.load_controlled_asr_fixture(adapter, tmp_path)

    assert excinfo.value.reason == "controlled_asr_fixture_invalid"


def test_bound_fixture_rejects_an_unknown_capability(tmp_path: Path) -> None:
    adapter = _fixture_adapter(tmp_path, _valid_output())
    adapter["fixture"]["capability"] = "asr_translation"

    with pytest.raises(contracts.TranscriptionContractError) as excinfo:
        contracts.load_controlled_asr_fixture(adapter, tmp_path)

    assert excinfo.value.reason == "controlled_asr_fixture_invalid"


# --- Output projection ------------------------------------------------------


def test_projection_accepts_a_complete_output(tmp_path: Path) -> None:
    _write_contracts(tmp_path)
    bound = contracts.revalidate_asr_contracts(tmp_path)

    projection = contracts.project_asr_output(_valid_output(), bound)

    assert projection.state == "projected"
    assert projection.capability == "asr_primary"
    assert projection.diagnostic is None
    assert len(projection.cues) == 1
    cue = projection.cues[0]
    assert cue.ordinal == 0
    assert cue.interval.start == ExactTime(0, 1)
    assert cue.interval.end == ExactTime(5, 1)
    assert cue.text == "你好 world"
    assert cue.tokens[0].confidence == pytest.approx(0.98)
    assert cue.tokens[1].confidence is None
    assert cue.language_spans[0].language == "zh"
    assert cue.language_spans[1].language == "en"


def test_projection_round_trips_to_json(tmp_path: Path) -> None:
    _write_contracts(tmp_path)
    bound = contracts.revalidate_asr_contracts(tmp_path)

    projection = contracts.project_asr_output(_valid_output(), bound)
    document = projection.as_json()

    assert document["state"] == "projected"
    assert document["capability"] == "asr_primary"
    assert document["projection_schema_version"] == "phase-07-asr-projection-schema-v1"
    assert document["cues"][0]["start"] == {"numerator": 0, "denominator": 1}
    assert document["cues"][0]["tokens"][1] == {"text": "world", "confidence": None}


def test_projection_accepts_a_cue_without_tokens_or_spans(tmp_path: Path) -> None:
    _write_contracts(tmp_path)
    bound = contracts.revalidate_asr_contracts(tmp_path)
    raw = _valid_output()
    raw["result"]["cues"][0].pop("tokens")
    raw["result"]["cues"][0].pop("language_spans")

    projection = contracts.project_asr_output(raw, bound)

    assert projection.state == "projected"
    assert projection.cues[0].tokens == ()
    assert projection.cues[0].language_spans == ()


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda p: p.pop("result"), id="missing_result"),
        pytest.param(lambda p: p.__setitem__("schema_version", 2), id="wrong_schema_version"),
        pytest.param(
            lambda p: p.__setitem__("projection_schema_version", "phase-07-other"),
            id="projection_schema_mismatch",
        ),
        pytest.param(
            lambda p: p.__setitem__("adapter_identity", "phase-07-other-adapter"),
            id="adapter_mismatch",
        ),
        pytest.param(lambda p: p.__setitem__("capability", "asr_translation"), id="bad_capability"),
        pytest.param(lambda p: p["result"].pop("cues"), id="missing_cues"),
        pytest.param(lambda p: p["result"].__setitem__("cues", {}), id="cues_not_a_list"),
        pytest.param(lambda p: p["result"]["cues"][0].pop("text"), id="cue_missing_text"),
        pytest.param(lambda p: p["result"]["cues"][0].pop("start"), id="cue_missing_start"),
        pytest.param(
            lambda p: p["result"]["cues"][0].__setitem__("ordinal", -1), id="cue_negative_ordinal"
        ),
        pytest.param(
            lambda p: p["result"]["cues"][0]["end"].__setitem__("numerator", 0),
            id="cue_non_positive_interval",
        ),
        pytest.param(
            lambda p: p["result"]["cues"][0]["end"].__setitem__("denominator", 0),
            id="cue_zero_denominator",
        ),
        pytest.param(
            lambda p: p["result"]["cues"][0]["tokens"][0].__setitem__("confidence", 2.0),
            id="token_confidence_out_of_range",
        ),
        pytest.param(
            lambda p: p["result"]["cues"][0]["language_spans"][0].__setitem__("end_token", 9),
            id="language_span_out_of_range",
        ),
        pytest.param(
            lambda p: p["result"]["cues"][0]["language_spans"][0].__setitem__("start_token", 1),
            id="language_span_empty",
        ),
    ],
)
def test_projection_rejects_whole_invalid_output(tmp_path: Path, mutate) -> None:  # noqa: ANN001
    _write_contracts(tmp_path)
    bound = contracts.revalidate_asr_contracts(tmp_path)
    raw = _valid_output()
    mutate(raw)

    projection = contracts.project_asr_output(raw, bound)

    assert projection.state == "model_output_invalid"
    assert projection.cues == ()
    assert projection.diagnostic is not None
    assert projection.diagnostic.reason == "model_output_invalid"


def test_projection_rejects_language_spans_without_tokens(tmp_path: Path) -> None:
    _write_contracts(tmp_path)
    bound = contracts.revalidate_asr_contracts(tmp_path)
    raw = _valid_output()
    raw["result"]["cues"][0].pop("tokens")

    projection = contracts.project_asr_output(raw, bound)

    assert projection.state == "model_output_invalid"


def test_projection_rejects_non_mapping_output(tmp_path: Path) -> None:
    _write_contracts(tmp_path)
    bound = contracts.revalidate_asr_contracts(tmp_path)

    projection = contracts.project_asr_output("not-json-object", bound)

    assert projection.state == "model_output_invalid"
    assert projection.cues == ()


# --- Restricted raw-output retention ----------------------------------------


def test_restricted_raw_output_is_retained_as_audit_only_evidence(tmp_path: Path) -> None:
    workspace = tmp_path / "work" / "transcription-reports" / "attempt"
    raw = json.dumps(_valid_output(), sort_keys=True).encode("utf-8")

    retained = contracts.retain_restricted_raw_output(
        raw, workspace, capability="asr_primary", label="primary"
    )

    written = retained.evidence.path
    assert written.read_bytes() == raw
    assert retained.evidence.sha256 == sha256(raw).hexdigest()
    document = retained.as_json()
    assert document["restricted"] is True
    assert document["audit_only"] is True
    assert document["capability"] == "asr_primary"
    # Restricted evidence lives apart from the formal report tree.
    assert "restricted" in written.as_posix()


def test_restricted_raw_output_write_is_immutable(tmp_path: Path) -> None:
    workspace = tmp_path / "work" / "transcription-reports" / "attempt"
    raw = b'{"a": 1}'
    contracts.retain_restricted_raw_output(
        raw, workspace, capability="asr_primary", label="primary"
    )

    with pytest.raises(contracts.TranscriptionContractError) as excinfo:
        contracts.retain_restricted_raw_output(
            b'{"a": 2}', workspace, capability="asr_primary", label="primary"
        )

    assert excinfo.value.reason == "transcription_raw_output_conflict"
