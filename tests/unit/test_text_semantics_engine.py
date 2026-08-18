"""Pure, model-free tests for the real text-semantics engine (Phase 11 ticket 10).

Calibration parsing/gating, the typed asset failures, and prompt rendering never
touch mlx-lm: they are exercised here with plain data. The Model runtime subprocess
seam is exercised against tiny stub executables -- so the whole orchestration
(:func:`generate_text_semantics`) is proven end to end without loading a model, the
hub-offline guards are proven to reach the child, a valid model output is proven to
flow through the *unchanged* Text-model output projection and adjudication into
verified segments, and a malformed model output is proven to become retained
restricted audit evidence plus a typed ``model_output_invalid`` status -- never a
crash or fabricated content. Real inference lives in the offline integration test.
"""

from __future__ import annotations

import json
import sys
from hashlib import sha256
from pathlib import Path

import pytest

import video_content_pipeline.text_semantics_engine as text_semantics_engine
from video_content_pipeline.model_acquisition import build_file_manifest, manifest_asset_sha256
from video_content_pipeline.model_runtime import ModelRuntimeError
from video_content_pipeline.text_contracts import (
    TextGenerationContracts,
    revalidate_text_generation_contracts,
)
from video_content_pipeline.text_generation import LoadedPart
from video_content_pipeline.text_semantics_engine import (
    Qwen3TextSemanticsCalibration,
    TextSemanticsEngineError,
    _build_local_id_maps,
    _decode_generation_output,
    generate_semantics,
    generate_text_semantics,
    load_text_semantics_asset,
    load_text_semantics_calibration,
    render_text_semantics_prompt,
    resolve_text_semantics_candidate,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CANDIDATE_ID = "qwen3-4b-instruct-2507-8bit"
CAPABILITY = "text_semantics"
PROMPT_VERSION = "phase-06-prompt-template-v2"
PART_ID = "part"
TRACK_ID = "stream-0"
CUE_A = f"{PART_ID}:{TRACK_ID}:0"
CUE_B = f"{PART_ID}:{TRACK_ID}:1"
CUE_TEXTS = {CUE_A: "第一条字幕文本", CUE_B: "第二条字幕文本"}


# --- calibration record -------------------------------------------------------


def _calibration_document(asset_sha256: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "calibration_version": "text-semantics-fixture-v1",
        "model_identity": {
            "model_asset_sha256": asset_sha256,
            "backend": "mlx-lm",
            "backend_version": "0.31.3",
            "precision": "8bit",
            "device_class": "apple-m1",
            "rules_fingerprint": "text-analysis-rules-v1",
        },
        "prompt_template_version": PROMPT_VERSION,
        "decoding": {"temperature": 0.0, "seed": 0, "max_tokens": 128, "max_kv_size": 512},
    }


def test_calibration_round_trips_through_json() -> None:
    document = _calibration_document("a" * 64)
    calibration = Qwen3TextSemanticsCalibration.from_json(document)
    assert calibration.temperature == 0.0
    assert calibration.max_kv_size == 512
    assert calibration.prompt_template_version == PROMPT_VERSION
    assert Qwen3TextSemanticsCalibration.from_json(calibration.as_json()) == calibration


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (lambda d: d["decoding"].update(temperature=-0.1), "temperature"),
        (lambda d: d["decoding"].update(seed=-1), "seed"),
        (lambda d: d["decoding"].update(max_tokens=0), "max_tokens"),
        (lambda d: d["decoding"].update(max_kv_size=0), "max_kv_size"),
    ],
)
def test_calibration_out_of_range_is_typed_invalid(mutate, field: str) -> None:
    document = _calibration_document("a" * 64)
    mutate(document)
    with pytest.raises(TextSemanticsEngineError) as error:
        Qwen3TextSemanticsCalibration.from_json(document)
    assert error.value.reason == "text_semantics_calibration_invalid"


def test_calibration_missing_fields_is_typed_invalid() -> None:
    with pytest.raises(TextSemanticsEngineError) as error:
        Qwen3TextSemanticsCalibration.from_json({"schema_version": 1})
    assert error.value.reason == "text_semantics_calibration_invalid"


def _write_calibration(project_root: Path, document: dict[str, object]) -> None:
    path = project_root / "config" / "text-analysis" / "qwen3-text-semantics-calibration.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def test_absent_calibration_is_required(tmp_path: Path) -> None:
    with pytest.raises(TextSemanticsEngineError) as error:
        load_text_semantics_calibration(tmp_path)
    assert error.value.reason == "text_semantics_calibration_required"


def test_calibration_asset_mismatch_is_typed(tmp_path: Path) -> None:
    _write_calibration(tmp_path, _calibration_document("a" * 64))
    with pytest.raises(TextSemanticsEngineError) as error:
        load_text_semantics_calibration(tmp_path, expected_asset_sha256="b" * 64)
    assert error.value.reason == "text_semantics_calibration_model_mismatch"


def test_calibration_prompt_version_mismatch_is_typed(tmp_path: Path) -> None:
    _write_calibration(tmp_path, _calibration_document("a" * 64))
    with pytest.raises(TextSemanticsEngineError) as error:
        load_text_semantics_calibration(tmp_path, expected_prompt_version="other-prompt-v9")
    assert error.value.reason == "text_semantics_calibration_model_mismatch"


# --- asset loading ------------------------------------------------------------


def _write_registry(project_root: Path, candidates: list[dict[str, object]]) -> None:
    registry_path = project_root / "models" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps({"schema_version": 2, "candidates": candidates}), encoding="utf-8"
    )


def _install_valid_asset(project_root: Path) -> str:
    asset_dir = project_root / "models" / "text-semantics"
    asset_dir.mkdir(parents=True)
    (asset_dir / "config.json").write_text('{"model_type": "stub"}', encoding="utf-8")
    (asset_dir / "weights.safetensors").write_bytes(b"weights")
    manifest = build_file_manifest(asset_dir)
    asset_sha256 = manifest_asset_sha256(manifest)
    _write_registry(
        project_root,
        [
            {
                "candidate_id": CANDIDATE_ID,
                "capability": CAPABILITY,
                "local_path": "models/text-semantics/",
                "file_manifest": manifest,
                "asset_sha256": asset_sha256,
            }
        ],
    )
    return asset_sha256


def test_candidate_absent_is_typed(tmp_path: Path) -> None:
    _write_registry(tmp_path, [])
    with pytest.raises(TextSemanticsEngineError) as error:
        resolve_text_semantics_candidate(tmp_path)
    assert error.value.reason == "text_semantics_candidate_absent"


def test_absent_asset_tree_is_typed(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        [
            {
                "candidate_id": CANDIDATE_ID,
                "capability": CAPABILITY,
                "local_path": "models/absent/",
                "file_manifest": [{"path": "config.json", "size": 1, "sha256": "0" * 64}],
                "asset_sha256": "0" * 64,
            }
        ],
    )
    with pytest.raises(TextSemanticsEngineError) as error:
        load_text_semantics_asset(tmp_path)
    assert error.value.reason == "text_semantics_asset_unavailable"


def test_tampered_asset_is_a_typed_mismatch_never_a_network_attempt(tmp_path: Path) -> None:
    asset_dir = tmp_path / "models" / "text-semantics"
    asset_dir.mkdir(parents=True)
    (asset_dir / "config.json").write_bytes(b"real-bytes")
    manifest = [
        {
            "path": "config.json",
            "size": (asset_dir / "config.json").stat().st_size,
            "sha256": sha256(b"real-bytes").hexdigest(),
        }
    ]
    _write_registry(
        tmp_path,
        [
            {
                "candidate_id": CANDIDATE_ID,
                "capability": CAPABILITY,
                "local_path": "models/text-semantics/",
                "file_manifest": manifest,
                "asset_sha256": "f" * 64,  # files intact, pinned digest wrong
            }
        ],
    )
    with pytest.raises(TextSemanticsEngineError) as error:
        load_text_semantics_asset(tmp_path)
    assert error.value.reason == "text_semantics_asset_mismatch"


# --- pure prompt rendering ----------------------------------------------------


def _contracts() -> TextGenerationContracts:
    return revalidate_text_generation_contracts(REPO_ROOT)


def _parts() -> tuple[LoadedPart, ...]:
    return (LoadedPart(part_id=PART_ID, track_id=TRACK_ID, cue_ids=(CUE_A, CUE_B)),)


def test_prompt_render_is_deterministic_and_carries_cue_identities() -> None:
    contracts = _contracts()
    parts = _parts()
    first = render_text_semantics_prompt(contracts, parts, CUE_TEXTS)
    second = render_text_semantics_prompt(contracts, parts, CUE_TEXTS)
    assert first == second
    assert PROMPT_VERSION in first
    # Cues render under the token-efficient Part-local alias, not the full cue id.
    assert "P0:0" in first and "P0:1" in first
    assert CUE_A not in first and CUE_B not in first


def test_prompt_render_carries_cue_text_and_output_schema() -> None:
    # The v2 rendition gives the model both the verbatim cue text to segment and the
    # exact output envelope to return -- the ticket-15 adapter-completeness fix.
    prompt = render_text_semantics_prompt(_contracts(), _parts(), CUE_TEXTS)
    assert CUE_TEXTS[CUE_A] in prompt and CUE_TEXTS[CUE_B] in prompt
    # The exact fixed identity values the Text-model output projection enforces.
    assert '"output_schema_version": "phase-06-output-schema-v1"' in prompt
    assert '"adapter_identity": "phase-06-controlled-text-adapter-v1"' in prompt
    assert "start_cue_id" in prompt and "end_cue_id" in prompt


def test_prompt_render_omits_text_for_uncovered_cue() -> None:
    # A cue with no provided text renders its identity with an empty text tail,
    # never an exception -- the caller owns cue-text completeness.
    prompt = render_text_semantics_prompt(_contracts(), _parts(), {CUE_A: "只有第一条"})
    assert "- P0:0: 只有第一条\n" in prompt
    assert "- P0:1: \n" in prompt


# --- Model runtime subprocess seam (stub executable, no model) ----------------

_ENVELOPE = {
    "schema_version": 1,
    "output_schema_version": "phase-06-output-schema-v1",
    "adapter_identity": "phase-06-controlled-text-adapter-v1",
    "result": {
        "parts": [
            {
                "part_id": PART_ID,
                "segments": [
                    {"boundary": {"start_cue_id": CUE_A, "end_cue_id": CUE_B}, "content": {}}
                ],
                "chapters": [],
            }
        ],
        "collection_summary": None,
    },
}

# Emits a valid Text-model output envelope as the raw generated text.
_VALID_STUB = (
    "import json, sys\n"
    "from video_content_pipeline.model_runtime import execute_child\n"
    f"ENVELOPE = {_ENVELOPE!r}\n"
    "def handler(request):\n"
    "    return {'text': json.dumps(ENVELOPE)}\n"
    "sys.exit(execute_child(handler))\n"
)

# Emits text that is not JSON at all -> malformed model output.
_MALFORMED_JSON_STUB = (
    "import sys\n"
    "from video_content_pipeline.model_runtime import execute_child\n"
    "def handler(request):\n"
    "    return {'text': 'not json at all'}\n"
    "sys.exit(execute_child(handler))\n"
)

# Echoes the received request payload back so the parent can prove the subprocess
# request actually carries model path, prompt version, sampling, and the KV bound.
_CAPTURE_STUB = (
    "import json, sys\n"
    "from video_content_pipeline.model_runtime import execute_child\n"
    "def handler(request):\n"
    "    return {'text': json.dumps({'model_path': request.model_path, 'task': request.task})}\n"
    "sys.exit(execute_child(handler))\n"
)

# Reports the hub-offline guard values the child actually sees.
_GUARDS_STUB = (
    "import os, sys\n"
    "from video_content_pipeline.model_runtime import execute_child\n"
    "def handler(request):\n"
    "    keys = ['HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE',\n"
    "            'HF_HUB_DISABLE_TELEMETRY', 'HF_HUB_DISABLE_IMPLICIT_TOKEN']\n"
    "    return {'text': ','.join(os.environ.get(k, 'MISSING') for k in keys)}\n"
    "sys.exit(execute_child(handler))\n"
)

# 'text' is not a string -> malformed child response.
_MALFORMED_RESPONSE_STUB = (
    "import json, sys\n"
    "sys.stdin.read()\n"
    "json.dump({'result': {'text': 123}, 'peak_memory_bytes': 1}, sys.stdout)\n"
)

_CRASH_STUB = "import os, signal, sys\nsys.stdin.read()\nos.kill(os.getpid(), signal.SIGKILL)\n"


def _write_stub(tmp_path: Path, name: str, body: str) -> list[str]:
    stub = tmp_path / name
    stub.write_text(body, encoding="utf-8")
    return [sys.executable, str(stub)]


def _calibration_for(asset_sha256: str) -> Qwen3TextSemanticsCalibration:
    return Qwen3TextSemanticsCalibration.from_json(_calibration_document(asset_sha256))


def test_generate_semantics_round_trips_text_and_peak(tmp_path: Path) -> None:
    command = _write_stub(tmp_path, "valid.py", _VALID_STUB)
    text, peak = generate_semantics(
        Path("/models/text-semantics"),
        "prompt",
        _calibration_for("a" * 64),
        PROMPT_VERSION,
        command=command,
        timeout_seconds=30,
    )
    assert json.loads(text) == _ENVELOPE
    assert peak > 0


def test_request_carries_model_path_prompt_version_sampling_and_kv_bound(tmp_path: Path) -> None:
    command = _write_stub(tmp_path, "capture.py", _CAPTURE_STUB)
    calibration = _calibration_for("a" * 64)
    text, _ = generate_semantics(
        Path("/models/text-semantics"),
        "the rendered prompt",
        calibration,
        PROMPT_VERSION,
        command=command,
        timeout_seconds=30,
    )
    payload = json.loads(text)
    assert payload["model_path"] == "/models/text-semantics"
    task = payload["task"]
    assert task["prompt"] == "the rendered prompt"
    assert task["prompt_version"] == PROMPT_VERSION
    assert task["sampling"] == {
        "temperature": calibration.temperature,
        "seed": calibration.seed,
        "max_tokens": calibration.max_tokens,
    }
    assert task["max_kv_size"] == calibration.max_kv_size


def test_generate_semantics_forces_hub_offline_guards(tmp_path: Path) -> None:
    command = _write_stub(tmp_path, "guards.py", _GUARDS_STUB)
    text, _ = generate_semantics(
        Path("/models/text-semantics"),
        "prompt",
        _calibration_for("a" * 64),
        PROMPT_VERSION,
        command=command,
        timeout_seconds=30,
    )
    assert text == "1,1,1,1"


def test_generate_semantics_rejects_malformed_child_response(tmp_path: Path) -> None:
    command = _write_stub(tmp_path, "malformed.py", _MALFORMED_RESPONSE_STUB)
    with pytest.raises(TextSemanticsEngineError) as error:
        generate_semantics(
            Path("/models/text-semantics"),
            "prompt",
            _calibration_for("a" * 64),
            PROMPT_VERSION,
            command=command,
            timeout_seconds=30,
        )
    assert error.value.reason == "text_semantics_output_invalid"


def test_generate_semantics_child_crash_surfaces_as_model_runtime_error(tmp_path: Path) -> None:
    command = _write_stub(tmp_path, "crash.py", _CRASH_STUB)
    with pytest.raises(ModelRuntimeError):
        generate_semantics(
            Path("/models/text-semantics"),
            "prompt",
            _calibration_for("a" * 64),
            PROMPT_VERSION,
            command=command,
            timeout_seconds=30,
        )


# --- end-to-end orchestration over a stub executable --------------------------


def test_valid_output_composes_verified_segments(tmp_path: Path) -> None:
    asset_sha256 = _install_valid_asset(tmp_path)
    _write_calibration(tmp_path, _calibration_document(asset_sha256))
    command = _write_stub(tmp_path, "valid.py", _VALID_STUB)

    result = generate_text_semantics(
        tmp_path,
        tmp_path / "work",
        _contracts(),
        source_id=PART_ID,
        stream_index=0,
        available=_parts(),
        cue_texts=CUE_TEXTS,
        command=command,
        timeout_seconds=30,
    )

    assert result.status == "complete"
    assert len(result.segments) == 1
    assert result.segments[0].cue_ids == (CUE_A, CUE_B)
    assert result.projection_state == {
        "state": "projected",
        "output_schema_version": "phase-06-output-schema-v1",
    }
    # Provenance: the pinned asset, the calibration, and a real child peak.
    assert result.model_asset_sha256 == asset_sha256
    assert result.calibration_version == "text-semantics-fixture-v1"
    assert result.peak_memory_bytes > 0
    # The raw model text is retained only as restricted local audit evidence.
    assert result.restricted_raw_output.as_json()["restriction"] == "local_audit_only"
    assert result.restricted_raw_output.path.is_file()


def test_malformed_model_output_is_retained_diagnostics_not_a_crash(tmp_path: Path) -> None:
    asset_sha256 = _install_valid_asset(tmp_path)
    _write_calibration(tmp_path, _calibration_document(asset_sha256))
    command = _write_stub(tmp_path, "malformed.py", _MALFORMED_JSON_STUB)

    result = generate_text_semantics(
        tmp_path,
        tmp_path / "work",
        _contracts(),
        source_id=PART_ID,
        stream_index=0,
        available=_parts(),
        cue_texts=CUE_TEXTS,
        command=command,
        timeout_seconds=30,
    )

    assert result.status == "model_output_invalid"
    assert result.segments == ()
    assert result.chapters == ()
    assert result.collection_summary is None
    assert result.projection_state == {"state": "model_output_invalid"}
    # The invalid output is retained as a diagnostic and restricted audit evidence,
    # never fabricated content.
    assert [diagnostic.reason for diagnostic in result.diagnostics] == ["model_output_invalid"]
    assert Path(result.restricted_raw_output.path).is_file()
    assert result.peak_memory_bytes > 0


def test_generate_requires_calibration(tmp_path: Path) -> None:
    _install_valid_asset(tmp_path)  # asset present, calibration absent
    command = _write_stub(tmp_path, "valid.py", _VALID_STUB)
    with pytest.raises(TextSemanticsEngineError) as error:
        generate_text_semantics(
            tmp_path,
            tmp_path / "work",
            _contracts(),
            source_id=PART_ID,
            stream_index=0,
            available=_parts(),
            cue_texts=CUE_TEXTS,
            command=command,
            timeout_seconds=30,
        )
    assert error.value.reason == "text_semantics_calibration_required"


# --- token-efficient cue ids + context-budget gate (Phase 12 ticket 08) -------


def _valid_envelope(segments: list[dict[str, object]]) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "output_schema_version": "phase-06-output-schema-v1",
            "adapter_identity": "phase-06-controlled-text-adapter-v1",
            "result": {
                "parts": [{"part_id": "P0", "segments": segments, "chapters": []}],
                "collection_summary": None,
            },
        }
    )


def test_build_local_id_maps_aliases_parts_and_cues_by_position() -> None:
    parts = (LoadedPart(part_id=PART_ID, track_id=TRACK_ID, cue_ids=(CUE_A, CUE_B)),)
    cue_map, part_map = _build_local_id_maps(parts)
    assert part_map == {"P0": PART_ID}
    assert cue_map == {"P0:0": CUE_A, "P0:1": CUE_B}


def test_prompt_renders_token_efficient_aliases_not_the_full_source_id() -> None:
    # The 64-hex source id must not be repeated on every cue line: cues render under
    # the Part-local P{index}:{position} alias while the verbatim text is unchanged.
    source_id = "s" * 64
    cue = f"{source_id}:{TRACK_ID}:0"
    part = (LoadedPart(part_id=source_id, track_id=TRACK_ID, cue_ids=(cue,)),)
    prompt = render_text_semantics_prompt(_contracts(), part, {cue: "第一条字幕文本"})
    assert "- P0:0: 第一条字幕文本" in prompt
    assert source_id not in prompt  # the long source id never appears in the prompt


def test_real_run_remaps_short_aliases_back_to_full_cue_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The model cites the short aliases it sees; the engine must remap them back to
    # the full canonical cue ids before adjudication, so no alias leaks into segments.
    asset_sha256 = _install_valid_asset(tmp_path)
    _write_calibration(tmp_path, _calibration_document(asset_sha256))
    part = (LoadedPart(part_id=PART_ID, track_id=TRACK_ID, cue_ids=(CUE_A, CUE_B)),)

    def fake_generate_semantics(model_path, prompt, calibration, prompt_version, **kwargs):
        assert "- P0:0:" in prompt and "- P0:1:" in prompt  # prompt carries the aliases
        return (
            _valid_envelope(
                [
                    {
                        "boundary": {"start_cue_id": "P0:0", "end_cue_id": "P0:1"},
                        "content": {
                            "title": {"text": "标题", "cue_ids": ["P0:0"]},
                            "details": [{"text": "细节", "cue_ids": ["P0:1"]}],
                        },
                    }
                ]
            ),
            4242,
        )

    monkeypatch.setattr(text_semantics_engine, "generate_semantics", fake_generate_semantics)
    result = generate_text_semantics(
        tmp_path,
        tmp_path / "work",
        _contracts(),
        source_id=PART_ID,
        stream_index=0,
        available=part,
        cue_texts=CUE_TEXTS,
        command=["unused"],
        timeout_seconds=30,
    )
    assert result.status in {"complete", "partial"}
    assert len(result.segments) == 1
    # The verified segment carries the FULL canonical cue ids, never a P0:* alias.
    assert result.segments[0].cue_ids == (CUE_A, CUE_B)
    owned = [cid for seg in result.segments for cid in seg.cue_ids]
    assert not any(cid.startswith("P0") for cid in owned)


def test_context_budget_gate_stops_before_loading_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A prompt that would not fit the calibrated window stops with a typed reason
    # BEFORE the model is invoked, so a maintainer decides whether to raise the window.
    asset_sha256 = _install_valid_asset(tmp_path)
    _write_calibration(tmp_path, _calibration_document(asset_sha256))
    part = (LoadedPart(part_id=PART_ID, track_id=TRACK_ID, cue_ids=(CUE_A, CUE_B)),)

    calls = {"n": 0}

    def fake_generate_semantics(*args, **kwargs):
        calls["n"] += 1
        return "{}", 1

    monkeypatch.setattr(text_semantics_engine, "generate_semantics", fake_generate_semantics)
    monkeypatch.setattr(text_semantics_engine, "_count_prompt_tokens", lambda *a: 10_000_000)

    with pytest.raises(TextSemanticsEngineError) as error:
        generate_text_semantics(
            tmp_path,
            tmp_path / "work",
            _contracts(),
            source_id=PART_ID,
            stream_index=0,
            available=part,
            cue_texts=CUE_TEXTS,
            command=["unused"],
            timeout_seconds=30,
        )
    assert error.value.reason == "text_semantics_context_budget_exceeded"
    assert calls["n"] == 0  # the model was never invoked


def test_context_budget_gate_proceeds_when_the_prompt_fits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asset_sha256 = _install_valid_asset(tmp_path)
    _write_calibration(tmp_path, _calibration_document(asset_sha256))
    part = (LoadedPart(part_id=PART_ID, track_id=TRACK_ID, cue_ids=(CUE_A, CUE_B)),)
    monkeypatch.setattr(text_semantics_engine, "_count_prompt_tokens", lambda *a: 10)

    calls = {"n": 0}

    def fake_generate_semantics(model_path, prompt, calibration, prompt_version, **kwargs):
        calls["n"] += 1
        return (
            _valid_envelope(
                [
                    {
                        "boundary": {"start_cue_id": "P0:0", "end_cue_id": "P0:1"},
                        "content": {
                            "title": {"text": "标题", "cue_ids": ["P0:0"]},
                            "details": [{"text": "细节", "cue_ids": ["P0:0"]}],
                        },
                    }
                ]
            ),
            5,
        )

    monkeypatch.setattr(text_semantics_engine, "generate_semantics", fake_generate_semantics)
    result = generate_text_semantics(
        tmp_path,
        tmp_path / "work",
        _contracts(),
        source_id=PART_ID,
        stream_index=0,
        available=part,
        cue_texts=CUE_TEXTS,
        command=["unused"],
        timeout_seconds=30,
    )
    assert calls["n"] == 1
    assert result.status in {"complete", "partial"}


def test_decode_generation_output_strips_markdown_code_fence() -> None:
    # The instruction-tuned model reliably emits the JSON envelope but often wraps
    # it in a Markdown code fence; the decoder strips the fence so a fenced-but-valid
    # envelope projects instead of being rejected (observed on real run #1 blocks).
    envelope = {"schema_version": 1, "result": {"parts": []}}
    fenced_json = "```json\n" + json.dumps(envelope) + "\n```"
    fenced_bare = "```\n" + json.dumps(envelope) + "\n```"
    assert _decode_generation_output(fenced_json) == envelope
    assert _decode_generation_output(fenced_bare) == envelope
    # Bare JSON (no fence) still parses; genuine prose still rejects to the sentinel.
    assert _decode_generation_output(json.dumps(envelope)) == envelope
    assert _decode_generation_output("这是一段散文，不是 JSON。") is None
    # A truncated (unterminated) fenced envelope rejects rather than half-parsing.
    assert _decode_generation_output('```json\n{"result": {"parts": [') is None
