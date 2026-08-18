"""Real Qwen3-4B text-semantics engine behind the Phase 6 text contracts (ticket 10).

Phase 11 ticket 10 wires the phase's one new capability, ``text_semantics``, to its
real model. Qwen3-4B-Instruct-2507-8bit is MLX-scale, so per ADR 0055 it runs in
its *own* Model runtime subprocess (ticket 05) through mlx-lm, so unified memory is
returned to the OS when the stage exits on a 16 GiB machine. The engine:

* verifies the pinned model asset from disk before the child ever loads it
  (:func:`load_text_semantics_asset`) -- a missing or tampered asset is a typed
  acquisition failure, never a network attempt (the child forces the hub-offline
  guards and only opens local files);
* runs the model with a model-specific decoding calibration (ADR 0056): deterministic
  sampling (temperature 0, fixed seed), a bounded KV cache, and the versioned prompt
  template it was calibrated for. A missing, invalid, or asset-mismatched calibration
  is a typed precondition failure, so the real model never runs from an unpinned
  decoding configuration;
* projects the raw model output through the *unchanged* Text-model output projection
  (:func:`~video_content_pipeline.text_contracts.project_text_model_output`) and
  composes verified segments through the *unchanged* adjudication
  (:func:`~video_content_pipeline.text_generation.generate_analysis`): model-proposed
  boundaries and content are validated against the revalidated cue evidence, and every
  invalid proposal is retained as a diagnostic, never formal output. A whole invalid
  or malformed model output becomes retained restricted audit evidence plus a typed
  ``model_output_invalid`` status, never a crash or fabricated content.

The real engine slots *beside* -- never replaces -- the Controlled offline text
adapter that Phase 6's pytest gate uses (ADR 0037); it is exercised by the offline
integration test and the maintainer-invoked prototype, not by ``analyze_text``. The
Controlled offline text adapter is not a registry candidate and carries no pinned
asset hash, so it can never grade as an eligible real model (see
:func:`~video_content_pipeline.text_analysis.evaluate_text_semantics_capability`).
Prompt rendering, output decoding, and projection are pure and model-free, so they
are unit-tested without mlx-lm and the subprocess protocol against a stub executable;
only the child module touches a model.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from video_content_pipeline.capabilities import load_registry_document
from video_content_pipeline.model_acquisition import (
    AssetVerificationError,
    verify_acquired_asset,
)
from video_content_pipeline.model_runtime import EngineRequest, run_engine_subprocess
from video_content_pipeline.planning import PlanningDiagnostic
from video_content_pipeline.text_aggregation import Chapter, CollectionSummary
from video_content_pipeline.text_analysis import RestrictedRawOutput, record_restricted_raw_output
from video_content_pipeline.text_contracts import (
    TextGenerationContracts,
    project_text_model_output,
)
from video_content_pipeline.text_generation import (
    GeneratedSegment,
    LoadedPart,
    UnavailablePartInfo,
    generate_analysis,
)

#: The provider-neutral capability and its acquired candidate.
CAPABILITY = "text_semantics"
CANDIDATE_ID = "qwen3-4b-instruct-2507-8bit"

#: The child module that loads the model and runs generation in the subprocess.
TEXT_SEMANTICS_CHILD_MODULE = "video_content_pipeline.text_semantics_child"

#: A generous per-stage budget: a large MLX model load plus one bounded generation.
DEFAULT_TIMEOUT_SECONDS = 900.0

_CALIBRATION_PATH = Path("config") / "text-analysis" / "qwen3-text-semantics-calibration.json"

#: The status a whole-invalid model output concludes: no formal SemanticSegments,
#: the raw output retained only as restricted local audit evidence.
STATUS_MODEL_OUTPUT_INVALID = "model_output_invalid"


class TextSemanticsEngineError(ValueError):
    """A rejected real text-semantics precondition with a machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def default_text_semantics_command() -> list[str]:
    """The production child argv: this interpreter running the text-semantics module."""

    return [sys.executable, "-m", TEXT_SEMANTICS_CHILD_MODULE]


# --- calibration (ADR 0056 gate) ----------------------------------------------


@dataclass(frozen=True)
class Qwen3TextSemanticsCalibration:
    """A model-specific text-semantics decoding calibration profile (ADR 0056).

    ``model_asset_sha256`` binds the profile to the exact acquired asset;
    ``backend`` / ``backend_version`` / ``precision`` / ``device_class`` /
    ``rules_fingerprint`` complete the bound identity (a change to any invalidates
    the profile). ``prompt_template_version`` pins the versioned prompt template the
    profile was calibrated for. ``temperature`` / ``seed`` are the deterministic
    sampling controls (temperature 0 is decoded as greedy argmax), ``max_tokens`` the
    generation length bound, and ``max_kv_size`` the bounded KV-cache window. Without
    such a profile the real engine does not run: an LLM cannot decode from an unpinned
    configuration, so a missing profile reports ``text_semantics_calibration_required``.
    """

    calibration_version: str
    model_asset_sha256: str
    backend: str
    backend_version: str
    precision: str
    device_class: str
    rules_fingerprint: str
    prompt_template_version: str
    temperature: float
    seed: int
    max_tokens: int
    max_kv_size: int

    def __post_init__(self) -> None:
        if self.temperature < 0:
            raise TextSemanticsEngineError(
                "text_semantics_calibration_invalid", "temperature must be non-negative."
            )
        if self.seed < 0:
            raise TextSemanticsEngineError(
                "text_semantics_calibration_invalid", "seed must be non-negative."
            )
        if self.max_tokens <= 0:
            raise TextSemanticsEngineError(
                "text_semantics_calibration_invalid", "max_tokens must be positive."
            )
        if self.max_kv_size <= 0:
            raise TextSemanticsEngineError(
                "text_semantics_calibration_invalid", "max_kv_size must be positive."
            )

    @classmethod
    def from_json(cls, decoded: object) -> Qwen3TextSemanticsCalibration:
        """Parse and validate a calibration profile from its JSON document."""

        if not isinstance(decoded, Mapping) or decoded.get("schema_version") != 1:
            raise TextSemanticsEngineError(
                "text_semantics_calibration_invalid", "Calibration schema is invalid."
            )
        identity = decoded.get("model_identity")
        decoding = decoded.get("decoding")
        if not isinstance(identity, Mapping) or not isinstance(decoding, Mapping):
            raise TextSemanticsEngineError(
                "text_semantics_calibration_invalid", "Calibration fields are missing."
            )
        try:
            return cls(
                calibration_version=_required_str(decoded, "calibration_version"),
                model_asset_sha256=_required_str(identity, "model_asset_sha256"),
                backend=_required_str(identity, "backend"),
                backend_version=_required_str(identity, "backend_version"),
                precision=_required_str(identity, "precision"),
                device_class=_required_str(identity, "device_class"),
                rules_fingerprint=_required_str(identity, "rules_fingerprint"),
                prompt_template_version=_required_str(decoded, "prompt_template_version"),
                temperature=_non_negative_float(decoding, "temperature"),
                seed=_non_negative_int(decoding, "seed"),
                max_tokens=_positive_int(decoding, "max_tokens"),
                max_kv_size=_positive_int(decoding, "max_kv_size"),
            )
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, TextSemanticsEngineError):
                raise
            raise TextSemanticsEngineError(
                "text_semantics_calibration_invalid", "Calibration fields are invalid."
            ) from error

    def as_json(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "calibration_version": self.calibration_version,
            "model_identity": {
                "model_asset_sha256": self.model_asset_sha256,
                "backend": self.backend,
                "backend_version": self.backend_version,
                "precision": self.precision,
                "device_class": self.device_class,
                "rules_fingerprint": self.rules_fingerprint,
            },
            "prompt_template_version": self.prompt_template_version,
            "decoding": {
                "temperature": self.temperature,
                "seed": self.seed,
                "max_tokens": self.max_tokens,
                "max_kv_size": self.max_kv_size,
            },
        }


def load_text_semantics_calibration(
    project_root: Path,
    *,
    expected_asset_sha256: str | None = None,
    expected_prompt_version: str | None = None,
) -> Qwen3TextSemanticsCalibration:
    """Read and gate-check the text-semantics decoding calibration profile (ADR 0056).

    Validates the profile's schema and ranges and, when the expectations are given,
    that it was calibrated for that exact asset and versioned prompt template. Raises
    :class:`TextSemanticsEngineError` (``text_semantics_calibration_required`` when the
    profile is absent, ``text_semantics_calibration_invalid`` when it is malformed, or
    ``text_semantics_calibration_model_mismatch`` when its bound identity differs).
    """

    path = project_root / _CALIBRATION_PATH
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise TextSemanticsEngineError(
            "text_semantics_calibration_required",
            "The text-semantics decoding calibration profile is absent.",
        ) from error
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as error:
        raise TextSemanticsEngineError(
            "text_semantics_calibration_invalid",
            "The text-semantics calibration profile cannot be read.",
        ) from error
    calibration = Qwen3TextSemanticsCalibration.from_json(decoded)
    if (
        expected_asset_sha256 is not None
        and calibration.model_asset_sha256 != expected_asset_sha256
    ):
        raise TextSemanticsEngineError(
            "text_semantics_calibration_model_mismatch",
            "The calibration profile was produced for a different model asset.",
        )
    if (
        expected_prompt_version is not None
        and calibration.prompt_template_version != expected_prompt_version
    ):
        raise TextSemanticsEngineError(
            "text_semantics_calibration_model_mismatch",
            "The calibration profile was produced for a different prompt-template version.",
        )
    return calibration


# --- asset loading (typed acquisition failure, never network) -----------------


def resolve_text_semantics_candidate(project_root: Path) -> Mapping[str, object]:
    """Return the ``qwen3-4b-instruct-2507-8bit`` candidate from the model registry.

    The Controlled offline text adapter is not a registry candidate, so it is never
    returned here: the real engine binds only to an acquired registry asset.
    """

    registry_path = project_root / "models" / "registry.json"
    registry = load_registry_document(
        registry_path,
        invalid_error=lambda message: TextSemanticsEngineError(
            "text_semantics_asset_unavailable", message
        ),
    )
    candidates = registry.get("candidates")
    if isinstance(candidates, list):
        for candidate in candidates:
            if (
                isinstance(candidate, Mapping)
                and candidate.get("candidate_id") == CANDIDATE_ID
                and candidate.get("capability") == CAPABILITY
            ):
                return candidate
    raise TextSemanticsEngineError(
        "text_semantics_candidate_absent",
        "The registry has no acquired text_semantics candidate.",
    )


def load_text_semantics_asset(
    project_root: Path, candidate: Mapping[str, object] | None = None
) -> tuple[Path, str]:
    """Verify the pinned text-semantics asset from disk and return ``(model_dir, sha)``.

    Re-hashes the whole vendored model tree against the registry manifest before the
    child ever loads it. A missing directory, a drifted file, or a mismatched manifest
    raises :class:`TextSemanticsEngineError` (``text_semantics_asset_unavailable`` /
    ``text_semantics_asset_mismatch``); the model is never fetched -- verification
    touches only local files, and the child that loads ``model_dir`` runs under the
    hub-offline guards.
    """

    if candidate is None:
        candidate = resolve_text_semantics_candidate(project_root)
    local_path = candidate.get("local_path")
    manifest = candidate.get("file_manifest")
    asset_sha256 = candidate.get("asset_sha256")
    if (
        not isinstance(local_path, str)
        or not local_path
        or not isinstance(manifest, list)
        or not manifest
        or not isinstance(asset_sha256, str)
    ):
        raise TextSemanticsEngineError(
            "text_semantics_asset_unavailable", "The text_semantics registry entry is incomplete."
        )
    asset_root = (project_root / local_path).resolve()
    if not asset_root.is_dir():
        raise TextSemanticsEngineError(
            "text_semantics_asset_unavailable",
            f"The text_semantics asset tree is absent: {asset_root}",
        )
    try:
        verify_acquired_asset(manifest, asset_sha256, asset_root)
    except AssetVerificationError as error:
        raise TextSemanticsEngineError("text_semantics_asset_mismatch", str(error)) from error
    return asset_root, asset_sha256


# --- pure prompt rendering ----------------------------------------------------


def render_text_semantics_prompt(
    contracts: TextGenerationContracts,
    available: Sequence[LoadedPart],
    cue_texts: Mapping[str, str],
) -> str:
    """Deterministically render the versioned prompt for the available Parts.

    The rendition concatenates the versioned prompt-template sections, a concrete
    output-contract section derived from the bound output-schema and controlled-adapter
    identities (the exact JSON envelope the model must return), and, for each available
    Part, its authoritative ordered cues -- each rendered as its NormalizedCue identity
    followed by its exact recognized ``cue_texts`` text (verbatim; ADR-0037 offline
    adapter parity), the only boundaries the model may propose over. Giving the model
    both the cue text to segment and the exact output shape to return is what prompt
    template v2 fixed over the ticket-10 v1 rendition, which carried only cue identities
    (Phase 11 ticket 15). Cue identities are rendered in a token-efficient Part-local
    alias form -- each available Part is aliased ``P{index}`` and each of its cues is
    ``P{index}:{position}`` (0-based position within the Part's ``cue_ids``) -- so the
    64-hex source id is not repeated on every cue line; the engine remaps these aliases
    back to the full canonical cue ids before adjudication (see ``_build_local_id_maps``
    / ``_remap_local_ids``). It is a stable function of the bound contract versions, the
    revalidated cue inventory, and the provided cue text, so the same inputs always
    render the same prompt (the subprocess request carries this exact text). Pure and
    deterministic; touches no model.
    """

    lines = [f"# prompt-template {contracts.prompt_template.version}"]
    sections = contracts.prompt_template.document.get("sections")
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, Mapping):
                continue
            role = section.get("role")
            section_id = section.get("id")
            text = section.get("text")
            lines.append(f"[{role}:{section_id}] {text}")
    lines.extend(_output_contract_lines(contracts, available))
    lines.append("# authoritative-cues")
    for part_index, part in enumerate(available):
        part_alias = _local_part_alias(part_index)
        lines.append(f"## part {part_alias} track {part.track_id}")
        for position, cue_identity in enumerate(part.cue_ids):
            local_cue = _local_cue_alias(part_index, position)
            lines.append(f"- {local_cue}: {cue_texts.get(cue_identity, '')}")
    return "\n".join(lines) + "\n"


def _output_contract_lines(
    contracts: TextGenerationContracts, available: Sequence[LoadedPart]
) -> list[str]:
    """Render the exact JSON envelope the model must return as prompt instructions.

    The required top-level constants (``schema_version``, ``output_schema_version``,
    ``adapter_identity``) are taken from the bound output-schema and controlled-adapter
    identities so the rendered instructions and the envelope the Text-model output
    projection enforces can never drift. A compact skeleton over the first available
    Part's own cue identities shows the per-segment ``boundary`` and cited-``content``
    shape, rendered in the same token-efficient Part-local alias form the authoritative
    cues use (``P0`` for the first Part and ``P0:{position}`` for its cues). Pure and
    deterministic.
    """

    envelope = contracts.output_schema.document.get("envelope")
    expected_schema_version = (
        envelope.get("expected_schema_version") if isinstance(envelope, Mapping) else None
    )
    example = available[0] if available else None
    example_cues = example.cue_ids if example is not None else ()
    example_part = _local_part_alias(0)
    start_cue = _local_cue_alias(0, 0)
    end_cue = _local_cue_alias(0, len(example_cues) - 1) if example_cues else start_cue
    skeleton = {
        "schema_version": expected_schema_version,
        "output_schema_version": contracts.output_schema.version,
        "adapter_identity": contracts.controlled_adapter.version,
        "result": {
            "parts": [
                {
                    "part_id": example_part,
                    "segments": [
                        {
                            "boundary": {"start_cue_id": start_cue, "end_cue_id": end_cue},
                            "content": {
                                "title": {"text": "<zh title>", "cue_ids": [start_cue]},
                                "details": [{"text": "<zh detail>", "cue_ids": [start_cue]}],
                            },
                        }
                    ],
                    "chapters": [],
                }
            ],
            "collection_summary": None,
        },
    }
    return [
        "# output-contract",
        "Return exactly one JSON object with this shape and these fixed identity values:",
        json.dumps(skeleton, ensure_ascii=False, indent=2, sort_keys=True),
        "Rules: use the exact cue ids shown below in the authoritative-cues block (the "
        "token-efficient Part-local form P{index}:{position}, e.g. P0:0); a segment "
        "boundary names an existing cue in the same Part as start_cue_id and end_cue_id; "
        "part_id is that Part's alias (e.g. P0); every title and detail must cite one or "
        "more of the cue_ids inside its own segment; emit no field you cannot cite from "
        "the provided cues.",
    ]


# --- subprocess round-trip ----------------------------------------------------


def generate_semantics(
    model_path: Path,
    prompt: str,
    calibration: Qwen3TextSemanticsCalibration,
    prompt_version: str,
    *,
    command: Sequence[str],
    timeout_seconds: float,
) -> tuple[str, int]:
    """Run one text-semantics generation through the child; return ``(raw_text, peak)``.

    Serializes the model path, the rendered prompt and its versioned identity, the
    deterministic sampling controls, and the bounded KV-cache window to the Model
    runtime subprocess, which loads Qwen3-4B once, decodes greedily under a fixed seed,
    and returns the raw generated text plus peak-memory evidence. A malformed child
    response is a typed ``text_semantics_output_invalid`` failure; subprocess
    crashes/timeouts surface as
    :class:`~video_content_pipeline.model_runtime.ModelRuntimeError`.
    """

    request = EngineRequest(
        model_path=str(model_path),
        task={
            "prompt": prompt,
            "prompt_version": prompt_version,
            "sampling": {
                "temperature": calibration.temperature,
                "seed": calibration.seed,
                "max_tokens": calibration.max_tokens,
            },
            "max_kv_size": calibration.max_kv_size,
        },
    )
    result = run_engine_subprocess(command, request, timeout_seconds=timeout_seconds)
    return _parse_text_result(result.result), result.peak_memory_bytes


def _parse_text_result(result: Mapping[str, object]) -> str:
    text = result.get("text")
    if not isinstance(text, str):
        raise TextSemanticsEngineError(
            "text_semantics_output_invalid",
            "The text-semantics child response is missing a 'text' string.",
        )
    return text


# --- top-level real analysis --------------------------------------------------


@dataclass(frozen=True)
class Qwen3TextSemanticsResult:
    """The real engine's output: composed analysis (or invalid) plus provenance.

    ``status`` is ``complete`` / ``partial`` / ``failed`` from the unchanged
    adjudication when the model output projected, or ``model_output_invalid`` when the
    whole output failed the Text-model output projection. ``segments`` / ``chapters``
    / ``collection_summary`` carry only formally verified content (empty on an invalid
    output); ``diagnostics`` retains every boundary rejection, unsupported content
    item, and whole-output rejection. ``restricted_raw_output`` is the raw model text
    kept only as restricted local audit evidence (never formal content). The provenance
    fields bind the exact pinned asset and decoding calibration and report real peak
    memory.
    """

    source_id: str
    stream_index: int
    status: str
    segments: tuple[GeneratedSegment, ...]
    chapters: tuple[Chapter, ...]
    collection_summary: CollectionSummary | None
    unsupported_item_count: int
    diagnostics: tuple[PlanningDiagnostic, ...]
    restricted_raw_output: RestrictedRawOutput
    projection_state: dict[str, object]
    model_asset_sha256: str
    calibration_version: str
    peak_memory_bytes: int


def _local_part_alias(part_index: int) -> str:
    """The token-efficient Part alias shown in the prompt (``P0``, ``P1``, ...)."""

    return f"P{part_index}"


def _local_cue_alias(part_index: int, position: int) -> str:
    """The token-efficient cue id shown in the prompt (``P{index}:{position}``)."""

    return f"{_local_part_alias(part_index)}:{position}"


def _build_local_id_maps(
    available: Sequence[LoadedPart],
) -> tuple[dict[str, str], dict[str, str]]:
    """Map the prompt's Part-local aliases back to the full canonical identities.

    Returns ``(cue_map, part_map)``: ``cue_map`` sends each ``P{i}:{j}`` cue alias to
    the full ``available[i].cue_ids[j]`` identity (by position, never assuming the
    ordinal equals the position), and ``part_map`` sends each ``P{i}`` alias to that
    Part's real ``part_id``. The engine applies these to the model's output before
    adjudication so a short alias never reaches the segments or the report.
    """

    cue_map: dict[str, str] = {}
    part_map: dict[str, str] = {}
    for part_index, part in enumerate(available):
        part_map[_local_part_alias(part_index)] = part.part_id
        for position, cue_identity in enumerate(part.cue_ids):
            cue_map[_local_cue_alias(part_index, position)] = cue_identity
    return cue_map, part_map


#: Result keys whose value is a single cue-id string, a list of cue-id strings, or a
#: Part identity -- the only places the alias->full remap rewrites, so free text
#: (titles, details, any generated prose) is never touched.
_CUE_ID_SCALAR_KEYS = frozenset({"start_cue_id", "end_cue_id"})
_CUE_ID_LIST_KEYS = frozenset({"cue_ids"})
_PART_ID_KEYS = frozenset({"part_id"})


def _remap_local_ids(
    value: object, cue_map: Mapping[str, str], part_map: Mapping[str, str]
) -> object:
    """Recursively rewrite Part-local aliases in a model result to full identities.

    Only values under the known cue-id / part-id keys are rewritten; an unknown alias
    is passed through unchanged so a malformed reference still fails adjudication
    rather than being silently corrected, and every other string -- titles, details,
    free text -- is left exactly as the model wrote it.
    """

    if isinstance(value, Mapping):
        remapped: dict[str, object] = {}
        for key, item in value.items():
            if key in _CUE_ID_SCALAR_KEYS and isinstance(item, str):
                remapped[key] = cue_map.get(item, item)
            elif key in _PART_ID_KEYS and isinstance(item, str):
                remapped[key] = part_map.get(item, item)
            elif key in _CUE_ID_LIST_KEYS and isinstance(item, list):
                remapped[key] = [
                    cue_map.get(entry, entry)
                    if isinstance(entry, str)
                    else _remap_local_ids(entry, cue_map, part_map)
                    for entry in item
                ]
            else:
                remapped[key] = _remap_local_ids(item, cue_map, part_map)
        return remapped
    if isinstance(value, list):
        return [_remap_local_ids(entry, cue_map, part_map) for entry in value]
    return value


def _count_prompt_tokens(model_path: Path, prompt: str) -> int | None:
    """Best-effort real token count of ``prompt`` via the model's own tokenizer.

    Returns ``None`` when the tokenizer cannot be loaded -- e.g. a stub asset in a
    unit test that carries no ``tokenizer.json`` -- so the pre-flight budget check is
    simply skipped and the model's own runtime ``max_kv_size`` stays the backstop; the
    real pinned asset always ships ``tokenizer.json``.
    """

    tokenizer_path = model_path / "tokenizer.json"
    if not tokenizer_path.is_file():
        return None
    try:
        from tokenizers import Tokenizer

        return len(Tokenizer.from_file(str(tokenizer_path)).encode(prompt).ids)
    except Exception:  # noqa: BLE001 - a tokenizer we cannot read just skips the pre-check
        return None


def _enforce_context_budget(
    model_path: Path, prompt: str, calibration: Qwen3TextSemanticsCalibration
) -> None:
    """Reject a prompt that would not fit the calibrated window before the model loads.

    The whole transcript is sent in one call, so the model must hold the prompt *and*
    its generation within ``max_kv_size``. Counting the prompt's real tokens with the
    model's own tokenizer (no model load) and reserving ``max_tokens`` for output, a
    prompt that would overflow raises ``text_semantics_context_budget_exceeded`` with
    the numbers -- so a maintainer decides whether to raise the window (confirming peak
    memory stays within the 12 GiB envelope) rather than the run silently truncating.
    """

    prompt_tokens = _count_prompt_tokens(model_path, prompt)
    if prompt_tokens is None:
        return
    required = prompt_tokens + calibration.max_tokens
    if required > calibration.max_kv_size:
        raise TextSemanticsEngineError(
            "text_semantics_context_budget_exceeded",
            f"The rendered prompt is {prompt_tokens} tokens; reserving "
            f"{calibration.max_tokens} tokens for output needs {required}, over the "
            f"{calibration.max_kv_size}-token context window (max_kv_size). Raise "
            "max_kv_size (and confirm the resulting peak memory stays within the 12 GiB "
            "envelope) or shorten the source before re-running.",
        )


def generate_text_semantics(
    project_root: Path,
    workspace_path: Path,
    contracts: TextGenerationContracts,
    *,
    source_id: str,
    stream_index: int,
    available: Sequence[LoadedPart],
    cue_texts: Mapping[str, str],
    unavailable: Sequence[UnavailablePartInfo] = (),
    command: Sequence[str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Qwen3TextSemanticsResult:
    """Run the real Qwen3-4B text-semantics engine over revalidated cue inventories.

    Verifies and loads the pinned asset, gate-checks the model-specific decoding
    calibration (ADR 0056) against that asset and the bound prompt-template version,
    renders the versioned prompt over the authoritative cues -- each cue rendered under
    a token-efficient Part-local alias (``P{index}:{position}``) plus its verbatim
    ``cue_texts`` text and the exact output envelope -- and, once a pre-flight token
    budget confirms the whole transcript fits the calibrated window, runs one Model
    runtime subprocess (ADR 0055) to generate the semantic analysis. The model's local
    aliases are remapped back to the full canonical cue/Part ids before adjudication,
    so the raw output is retained as restricted local audit evidence, projected through
    the unchanged Text-model output projection, and composed through the unchanged
    adjudication: model-proposed boundaries and content are validated against the
    revalidated cue evidence and every invalid proposal is retained as a diagnostic. A
    whole invalid or malformed model output concludes ``model_output_invalid`` with no
    formal segments -- never a crash or fabricated content.
    """

    model_path, asset_sha256 = load_text_semantics_asset(project_root)
    calibration = load_text_semantics_calibration(
        project_root,
        expected_asset_sha256=asset_sha256,
        expected_prompt_version=contracts.prompt_template.version,
    )
    child_command = list(command) if command is not None else default_text_semantics_command()

    prompt = render_text_semantics_prompt(contracts, available, cue_texts)
    _enforce_context_budget(model_path, prompt, calibration)

    raw_text, peak = generate_semantics(
        model_path,
        prompt,
        calibration,
        contracts.prompt_template.version,
        command=child_command,
        timeout_seconds=timeout_seconds,
    )
    raw_pointer = record_restricted_raw_output(
        workspace_path, "text-semantics-generation", raw_text.encode("utf-8")
    )

    projection = project_text_model_output(_decode_generation_output(raw_text), contracts)
    if projection.projection is None:
        diagnostic = projection.diagnostic or PlanningDiagnostic(
            "model_output_invalid", "The text-semantics model output is invalid."
        )
        return Qwen3TextSemanticsResult(
            source_id=source_id,
            stream_index=stream_index,
            status=STATUS_MODEL_OUTPUT_INVALID,
            segments=(),
            chapters=(),
            collection_summary=None,
            unsupported_item_count=0,
            diagnostics=(diagnostic,),
            restricted_raw_output=raw_pointer,
            projection_state={"state": projection.state},
            model_asset_sha256=asset_sha256,
            calibration_version=calibration.calibration_version,
            peak_memory_bytes=peak,
        )

    # Remap the model's token-efficient local aliases back to the full canonical cue
    # and Part identities before adjudication, so no short alias ever reaches the
    # verified segments or the published report.
    cue_map, part_map = _build_local_id_maps(available)
    raw_result = projection.projection.get("result")
    result_container = (
        _remap_local_ids(raw_result, cue_map, part_map) if isinstance(raw_result, Mapping) else {}
    )
    analysis = generate_analysis(
        available,
        unavailable,
        result_container if isinstance(result_container, Mapping) else {},
    )
    return Qwen3TextSemanticsResult(
        source_id=source_id,
        stream_index=stream_index,
        status=analysis.status,
        segments=analysis.segments,
        chapters=analysis.chapters,
        collection_summary=analysis.collection_summary,
        unsupported_item_count=analysis.unsupported_item_count,
        diagnostics=analysis.diagnostics,
        restricted_raw_output=raw_pointer,
        projection_state={
            "state": "projected",
            "output_schema_version": contracts.output_schema.version,
        },
        model_asset_sha256=asset_sha256,
        calibration_version=calibration.calibration_version,
        peak_memory_bytes=peak,
    )


def _decode_generation_output(raw_text: str) -> object:
    """Decode raw model text for projection, or a rejecting sentinel on malformed JSON.

    The instruction-tuned model reliably emits the JSON envelope but frequently
    wraps it in a Markdown code fence (```json ... ```); the fence is stripped
    before parsing so a fenced-but-otherwise-valid envelope projects instead of
    being rejected as ``model_output_invalid``. Text that is not JSON after the
    fence is removed still rejects.
    """

    try:
        return json.loads(_strip_code_fence(raw_text))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _strip_code_fence(raw_text: str) -> str:
    """Remove a leading/trailing Markdown code fence, leaving other text unchanged.

    Handles the common ```json ... ``` and ``` ... ``` wrappers the model emits;
    returns the input unchanged when it does not open with a fence, so bare JSON
    and genuine non-JSON prose are both left for the parser to accept or reject.
    """

    stripped = raw_text.strip()
    if not stripped.startswith("```"):
        return raw_text
    lines = stripped.splitlines()[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


# --- small validators ---------------------------------------------------------


def _required_str(value: Mapping[str, object], key: str) -> str:
    item = value[key]
    if not isinstance(item, str) or not item:
        raise TextSemanticsEngineError(
            "text_semantics_calibration_invalid", f"'{key}' must be a non-empty string."
        )
    return item


def _positive_int(value: Mapping[str, object], key: str) -> int:
    item = value[key]
    if not isinstance(item, int) or isinstance(item, bool) or item <= 0:
        raise TextSemanticsEngineError(
            "text_semantics_calibration_invalid", f"'{key}' must be a positive integer."
        )
    return item


def _non_negative_int(value: Mapping[str, object], key: str) -> int:
    item = value[key]
    if not isinstance(item, int) or isinstance(item, bool) or item < 0:
        raise TextSemanticsEngineError(
            "text_semantics_calibration_invalid", f"'{key}' must be a non-negative integer."
        )
    return item


def _non_negative_float(value: Mapping[str, object], key: str) -> float:
    item = value[key]
    if isinstance(item, bool) or not isinstance(item, int | float) or item < 0:
        raise TextSemanticsEngineError(
            "text_semantics_calibration_invalid", f"'{key}' must be a non-negative number."
        )
    return float(item)
