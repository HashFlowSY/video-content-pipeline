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
    contracts: TextGenerationContracts, available: Sequence[LoadedPart]
) -> str:
    """Deterministically render the versioned prompt for the available Parts.

    The rendition concatenates the versioned prompt-template sections and, for each
    available Part, its authoritative ordered cue identities -- the only boundaries
    the model may propose over. It is a stable function of the bound prompt-template
    version and the revalidated cue inventory, so the same inputs always render the
    same prompt (the subprocess request carries this exact text). Pure and
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
    lines.append("# authoritative-cues")
    for part in available:
        lines.append(f"## part {part.part_id} track {part.track_id}")
        for cue_identity in part.cue_ids:
            lines.append(f"- {cue_identity}")
    return "\n".join(lines) + "\n"


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


def generate_text_semantics(
    project_root: Path,
    workspace_path: Path,
    contracts: TextGenerationContracts,
    *,
    source_id: str,
    stream_index: int,
    available: Sequence[LoadedPart],
    unavailable: Sequence[UnavailablePartInfo] = (),
    command: Sequence[str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Qwen3TextSemanticsResult:
    """Run the real Qwen3-4B text-semantics engine over revalidated cue inventories.

    Verifies and loads the pinned asset, gate-checks the model-specific decoding
    calibration (ADR 0056) against that asset and the bound prompt-template version,
    renders the versioned prompt over the authoritative cue identities, and runs one
    Model runtime subprocess (ADR 0055) to generate the semantic analysis. The raw
    output is retained as restricted local audit evidence, then projected through the
    unchanged Text-model output projection and composed through the unchanged
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

    prompt = render_text_semantics_prompt(contracts, available)
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

    result_container = projection.projection.get("result")
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
    """Decode raw model text for projection, or a rejecting sentinel on malformed JSON."""

    try:
        return json.loads(raw_text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


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
