"""Real RapidOCR engine behind the Phase 8 OCR evidence-item contract.

Phase 11 ticket 11 fills the ``ocr_primary`` capability with the credential-free
RapidOCR pipeline (decision D6): the ``rapidocr==3.9.2`` wheel's bundled PP-OCRv6
small det+rec (zh+en) plus the v4 mobile cls model. Those models are ONNX-scale,
so per ADR 0055 the pipeline runs *in-process* through ``rapidocr`` /
``onnxruntime`` (no Model runtime subprocess) over the models that ship inside the
installed wheel. The engine:

* locates the bundled model files inside the installed ``rapidocr`` package and
  re-hashes each one against the model registry's pinned manifest before it is
  ever loaded (:func:`verify_bundled_models`) -- a missing runtime, a missing
  model, or a drifted hash is a typed failure, never a network download (RapidOCR
  is constructed with only local files);
* runs the real pipeline over the existing deterministic frame-sampling
  pipeline's selected frames and shapes each recognised region into a
  :class:`~video_content_pipeline.visual_text_contracts.ProjectedOcrItem` on the
  Part clock -- the unchanged input to the gate, classification, and
  classification-vs-fact-upgrade separation (ADR 0049);
* reads its two research-decided levers -- ``limit_side_len`` raised for >=1080p
  frames (small-text protection) and ``use_cls`` disabled for screen content --
  from a *versioned OCR engine configuration* (``config/visual-text/
  rapidocr-engine-config.json``) whose ``config_fingerprint`` changes whenever any
  lever changes, so a config edit invalidates dependent stage keys exactly like
  the other versioned visual-text rules (ADR 0047).

The real engine slots *beside* -- never replaces -- the Controlled offline OCR
adapter that Phase 8's pytest gate uses (ADR 0037); it is exercised by the
offline integration test and the maintainer-invoked prototype (ticket 13), not by
``visual-text`` command execution. The config, manifest, and region-shaping steps
are pure and model-free, so they are unit-tested without RapidOCR; only model
location, verification, and inference touch the wheel.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from video_content_pipeline.capabilities import load_registry_document
from video_content_pipeline.model_acquisition import file_sha256, manifest_asset_sha256
from video_content_pipeline.timecode import ExactTime
from video_content_pipeline.visual_text_contracts import ProjectedOcrItem

CAPABILITY = "ocr_primary"
CANDIDATE_ID = "rapidocr"

#: The three bundled model roles RapidOCR loads (detection, classification,
#: recognition); their exact filenames come from the registry entry's
#: ``default_models`` and are hash-verified against the pinned manifest.
_MODEL_ROLES: tuple[str, ...] = ("det", "cls", "rec")

_CONFIG_PATH = Path("config") / "visual-text" / "rapidocr-engine-config.json"


class OcrEngineError(ValueError):
    """A rejected real-OCR precondition with a machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class OcrFrame:
    """One deterministically selected frame handed to the real OCR engine.

    ``part_id``, ``visual_page_id``, and ``pts`` are the frame's identity on the
    Part clock (from the deterministic page index); ``image_path`` is the extracted
    representative frame. The engine never invents these -- they flow straight onto
    every :class:`ProjectedOcrItem` recognised from the frame.
    """

    part_id: str
    visual_page_id: str
    pts: ExactTime
    image_path: Path


@dataclass(frozen=True)
class RapidOcrEngineConfig:
    """A versioned RapidOCR engine configuration (ADR 0047 versioned-rules lineage).

    ``asset_sha256`` binds the config to the exact acquired model set; ``backend`` /
    ``backend_version`` / ``runtime_extra`` / ``device_class`` complete the recorded
    identity. The detection block carries the ``limit_side_len`` policy (a baseline
    plus a raised value applied when a frame's shorter side reaches
    ``high_resolution_min_side``) and ``use_cls`` the screen-content classifier
    switch -- the two research-decided levers (§6). The derived
    :attr:`config_fingerprint` changes whenever any versioned lever changes, so it
    is the stage-key input a change invalidates, in the versioned-rules spirit of
    ADR 0047 (recorded in the engine's result provenance, like the sibling engines'
    calibration identities).
    """

    config_version: str
    asset_sha256: str
    backend: str
    backend_version: str
    runtime_extra: str
    device_class: str
    limit_side_len: int
    high_resolution_limit_side_len: int
    high_resolution_min_side: int
    use_cls: bool
    qualification_scope: str

    def __post_init__(self) -> None:
        if self.limit_side_len <= 0 or self.high_resolution_limit_side_len <= 0:
            raise OcrEngineError("ocr_config_invalid", "limit_side_len values must be positive.")
        if self.high_resolution_limit_side_len < self.limit_side_len:
            raise OcrEngineError(
                "ocr_config_invalid",
                "The high-resolution limit_side_len must not be smaller than the baseline.",
            )
        if self.high_resolution_min_side <= 0:
            raise OcrEngineError("ocr_config_invalid", "high_resolution_min_side must be positive.")

    @classmethod
    def from_json(cls, decoded: object) -> RapidOcrEngineConfig:
        """Parse and validate an OCR engine configuration from its JSON document."""

        if not isinstance(decoded, Mapping) or decoded.get("schema_version") != 1:
            raise OcrEngineError("ocr_config_invalid", "OCR engine config schema is invalid.")
        identity = decoded.get("model_identity")
        detection = decoded.get("detection")
        classification = decoded.get("classification")
        if (
            not isinstance(identity, Mapping)
            or not isinstance(detection, Mapping)
            or not isinstance(classification, Mapping)
        ):
            raise OcrEngineError("ocr_config_invalid", "OCR engine config fields are missing.")
        try:
            return cls(
                config_version=_required_str(decoded, "config_version"),
                asset_sha256=_required_str(identity, "asset_sha256"),
                backend=_required_str(identity, "backend"),
                backend_version=_required_str(identity, "backend_version"),
                runtime_extra=_required_str(identity, "runtime_extra"),
                device_class=_required_str(identity, "device_class"),
                limit_side_len=_positive_int(detection, "limit_side_len"),
                high_resolution_limit_side_len=_positive_int(
                    detection, "high_resolution_limit_side_len"
                ),
                high_resolution_min_side=_positive_int(detection, "high_resolution_min_side"),
                use_cls=_required_bool(classification, "use_cls"),
                qualification_scope=_required_str(decoded, "qualification_scope"),
            )
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, OcrEngineError):
                raise
            raise OcrEngineError(
                "ocr_config_invalid", "OCR engine config fields are invalid."
            ) from error

    @property
    def config_fingerprint(self) -> str:
        """A stable digest of every versioned lever; a change here invalidates stage keys."""

        payload = json.dumps(
            {
                "config_version": self.config_version,
                "asset_sha256": self.asset_sha256,
                "backend": self.backend,
                "backend_version": self.backend_version,
                "runtime_extra": self.runtime_extra,
                "device_class": self.device_class,
                "limit_side_len": self.limit_side_len,
                "high_resolution_limit_side_len": self.high_resolution_limit_side_len,
                "high_resolution_min_side": self.high_resolution_min_side,
                "use_cls": self.use_cls,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def limit_side_len_for_frame(self, width: int, height: int) -> int:
        """Return the ``limit_side_len`` for a frame: raised once its shorter side is high-res.

        A frame whose shorter side reaches ``high_resolution_min_side`` (>=1080p in
        practice) uses the raised value so small text is not lost to detection
        downsampling; every other frame uses the baseline.
        """

        return (
            self.high_resolution_limit_side_len
            if min(width, height) >= self.high_resolution_min_side
            else self.limit_side_len
        )

    def as_json(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "config_version": self.config_version,
            "config_fingerprint": self.config_fingerprint,
            "model_identity": {
                "asset_sha256": self.asset_sha256,
                "backend": self.backend,
                "backend_version": self.backend_version,
                "runtime_extra": self.runtime_extra,
                "device_class": self.device_class,
            },
            "detection": {
                "limit_side_len": self.limit_side_len,
                "high_resolution_limit_side_len": self.high_resolution_limit_side_len,
                "high_resolution_min_side": self.high_resolution_min_side,
            },
            "classification": {"use_cls": self.use_cls},
            "qualification_scope": self.qualification_scope,
        }


@dataclass(frozen=True)
class OcrEngineResult:
    """The real engine's output: projected OCR items plus the bound provenance.

    ``items`` are contract-valid :class:`ProjectedOcrItem` on the Part clock, ready
    for the unchanged gate/classification chain. The provenance names the exact
    model set (``asset_sha256``) and the versioned config that produced them.
    """

    items: tuple[ProjectedOcrItem, ...]
    asset_sha256: str
    config_version: str
    config_fingerprint: str
    frames_processed: int

    def as_json(self) -> dict[str, object]:
        return {
            "capability": CAPABILITY,
            "items": [item.as_json() for item in self.items],
            "asset_sha256": self.asset_sha256,
            "config_version": self.config_version,
            "config_fingerprint": self.config_fingerprint,
            "frames_processed": self.frames_processed,
        }


# --- versioned config (ADR 0047 versioned-rules lineage) ----------------------


def load_rapidocr_engine_config(
    project_root: Path, *, expected_asset_sha256: str | None = None
) -> RapidOcrEngineConfig:
    """Read and validate the versioned OCR engine config, binding it to the models.

    Validates the record's schema and ranges and, when ``expected_asset_sha256`` is
    given, that the config was written for that exact model set. Raises
    :class:`OcrEngineError` (``ocr_config_invalid`` or ``ocr_config_model_mismatch``)
    otherwise. The config is required: RapidOCR's detection/classification levers
    are deliberate research decisions, not defaults to be silently assumed.
    """

    path = project_root / _CONFIG_PATH
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OcrEngineError(
            "ocr_config_invalid", "The RapidOCR engine config cannot be read."
        ) from error
    config = RapidOcrEngineConfig.from_json(decoded)
    if expected_asset_sha256 is not None and config.asset_sha256 != expected_asset_sha256:
        raise OcrEngineError(
            "ocr_config_model_mismatch",
            "The RapidOCR engine config was written for a different model set.",
        )
    return config


# --- bundled-model location and verification (typed failure, never network) ---


def resolve_ocr_candidate(project_root: Path) -> Mapping[str, object]:
    """Return the acquired ``ocr_primary`` RapidOCR candidate from the model registry."""

    registry_path = project_root / "models" / "registry.json"
    registry = load_registry_document(
        registry_path,
        invalid_error=lambda message: OcrEngineError("ocr_asset_unavailable", message),
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
    raise OcrEngineError(
        "ocr_candidate_absent",
        f"The registry has no acquired OCR candidate '{CANDIDATE_ID}'.",
    )


def locate_bundled_models_dir() -> Path:
    """Return the installed ``rapidocr`` package's bundled ``models`` directory.

    A missing ``rapidocr`` or ``onnxruntime`` runtime is a typed failure
    (``ocr_runtime_unavailable``), never a download attempt: the bundled models are
    only ever the ones that shipped inside the pinned wheel.
    """

    for module in ("rapidocr", "onnxruntime"):
        if importlib.util.find_spec(module) is None:
            raise OcrEngineError(
                "ocr_runtime_unavailable",
                f"The '{module}' runtime is not installed; RapidOCR cannot run offline.",
            )
    spec = importlib.util.find_spec("rapidocr")
    origin = None if spec is None else spec.origin
    if origin is None:
        raise OcrEngineError(
            "ocr_runtime_unavailable", "The installed 'rapidocr' package has no locatable path."
        )
    models_dir = Path(origin).resolve().parent / "models"
    if not models_dir.is_dir():
        raise OcrEngineError(
            "ocr_asset_unavailable",
            f"The bundled RapidOCR models directory is absent: {models_dir}",
        )
    return models_dir


def verify_bundled_models(project_root: Path) -> tuple[Path, str, dict[str, Path]]:
    """Verify the installed bundled models against the pinned registry manifest.

    Re-hashes each of the three default det/cls/rec model files that shipped inside
    the installed ``rapidocr`` wheel and asserts its size and SHA-256 match the
    registry entry's pinned manifest before any model is loaded. Returns the models
    directory, the registry's recorded ``asset_sha256`` identity (established at
    acquisition over the whole vendored tree), and the resolved per-role paths. A
    missing file or a drifted hash raises :class:`OcrEngineError`
    (``ocr_asset_unavailable`` / ``ocr_asset_mismatch``); nothing is ever fetched.

    The recorded ``asset_sha256`` is re-derived from the registry manifest and must
    match before it is returned, so the identity this result binds is verified, not
    trusted -- the bundled model bytes are then checked against that same manifest.
    """

    candidate = resolve_ocr_candidate(project_root)
    manifest = candidate.get("file_manifest")
    asset_sha256 = candidate.get("asset_sha256")
    default_models = candidate.get("default_models")
    if (
        not isinstance(manifest, list)
        or not manifest
        or not isinstance(asset_sha256, str)
        or not asset_sha256
        or not isinstance(default_models, Mapping)
    ):
        raise OcrEngineError(
            "ocr_asset_unavailable", f"The '{CANDIDATE_ID}' registry entry is incomplete."
        )
    if manifest_asset_sha256(manifest) != asset_sha256:
        raise OcrEngineError(
            "ocr_asset_mismatch",
            "The registry manifest does not digest to its recorded asset_sha256.",
        )
    manifest_by_path = {
        str(entry["path"]): entry
        for entry in manifest
        if isinstance(entry, Mapping) and "path" in entry
    }
    models_dir = locate_bundled_models_dir()

    role_paths: dict[str, Path] = {}
    for role in _MODEL_ROLES:
        filename = default_models.get(role)
        if not isinstance(filename, str) or not filename:
            raise OcrEngineError(
                "ocr_asset_unavailable",
                f"The registry entry omits the bundled '{role}' model filename.",
            )
        expected = manifest_by_path.get(filename)
        if not isinstance(expected, Mapping):
            raise OcrEngineError(
                "ocr_asset_unavailable",
                f"The registry manifest omits the bundled model file '{filename}'.",
            )
        installed = models_dir / filename
        if not installed.is_file():
            raise OcrEngineError(
                "ocr_asset_unavailable", f"The bundled model file is absent: {installed}"
            )
        size_ok = installed.stat().st_size == expected.get("size")
        if not size_ok or file_sha256(installed) != expected.get("sha256"):
            raise OcrEngineError(
                "ocr_asset_mismatch",
                f"The installed bundled model does not match its pinned hash: {filename}",
            )
        role_paths[role] = installed
    return models_dir, asset_sha256, role_paths


# --- engine construction ------------------------------------------------------


def build_engine(models_dir: Path, config: RapidOcrEngineConfig, limit_side_len: int) -> Any:
    """Build a RapidOCR pipeline over the local bundled models with the given levers.

    ``use_cls`` comes from the versioned config; ``limit_side_len`` is chosen per
    frame resolution. ``Global.model_root_dir`` is pinned to the verified
    ``models_dir`` so the pipeline loads exactly the hashed files.
    """

    from rapidocr import RapidOCR

    return RapidOCR(
        params={
            "Global.model_root_dir": str(models_dir),
            "Global.use_cls": config.use_cls,
            "Det.limit_side_len": limit_side_len,
        }
    )


# --- pure region shaping ------------------------------------------------------


def recognized_regions(output: Any) -> tuple[tuple[str, float], ...]:
    """Pair one RapidOCR output's recognised texts with their confidence scores.

    RapidOCR exposes its results as two parallel ``txts`` / ``scores`` sequences
    (either ``None`` when nothing is recognised); this pairs them into
    ``(text, score)`` tuples so the rest of the engine never touches the raw output
    shape. A frame that recognises nothing yields ``()``; mismatched text and score
    counts are a rejected output (``ocr_output_invalid``), never a silent truncation.
    """

    texts = output.txts
    scores = output.scores
    if texts is None or scores is None:
        return ()
    if len(texts) != len(scores):
        raise OcrEngineError(
            "ocr_output_invalid", "RapidOCR returned mismatched text and score counts."
        )
    return tuple((str(text), float(score)) for text, score in zip(texts, scores, strict=True))


def projected_items_from_regions(
    frame: OcrFrame, regions: Sequence[tuple[str, float]]
) -> tuple[ProjectedOcrItem, ...]:
    """Shape one frame's recognised ``(text, score)`` regions into ProjectedOcrItems.

    Each region becomes one item carrying the frame's Part, page, and exact PTS.
    Text is kept verbatim and no language attribution is invented
    (``language_spans`` stays empty), so mixed zh/en text is never rewritten. A
    score outside ``[0, 1]`` is a rejected output, not a silently clamped one.
    """

    items: list[ProjectedOcrItem] = []
    for text, score in regions:
        if not 0 <= score <= 1:
            raise OcrEngineError(
                "ocr_output_invalid", "A RapidOCR region confidence is outside [0, 1]."
            )
        items.append(
            ProjectedOcrItem(
                part_id=frame.part_id,
                visual_page_id=frame.visual_page_id,
                pts=frame.pts,
                text=text,
                confidence=score,
                language_spans=(),
            )
        )
    return tuple(items)


# --- top-level real analysis --------------------------------------------------


def analyze_frames_ocr(
    project_root: Path,
    frames: Sequence[OcrFrame],
    *,
    config: RapidOcrEngineConfig | None = None,
) -> OcrEngineResult:
    """Run the real RapidOCR engine over the deterministically selected frames.

    Verifies the installed bundled models against the pinned manifest, loads the
    model-matched versioned config, and runs RapidOCR in-process over each frame --
    choosing ``limit_side_len`` from the frame's resolution -- shaping the
    recognised regions into contract-valid :class:`ProjectedOcrItem` for the
    unchanged gate/classification chain. Model verification and loading touch only
    local files; nothing is fetched.
    """

    import cv2

    models_dir, asset_sha256, _ = verify_bundled_models(project_root)
    if config is None:
        config = load_rapidocr_engine_config(project_root, expected_asset_sha256=asset_sha256)
    elif config.asset_sha256 != asset_sha256:
        raise OcrEngineError(
            "ocr_config_model_mismatch",
            "The supplied RapidOCR engine config was written for a different model set.",
        )

    engines: dict[int, Any] = {}
    items: list[ProjectedOcrItem] = []
    for frame in frames:
        image = cv2.imread(str(frame.image_path))
        if image is None:
            raise OcrEngineError(
                "ocr_frame_unreadable", f"The selected frame cannot be read: {frame.image_path}"
            )
        height, width = int(image.shape[0]), int(image.shape[1])
        limit_side_len = config.limit_side_len_for_frame(width, height)
        engine = engines.get(limit_side_len)
        if engine is None:
            engine = build_engine(models_dir, config, limit_side_len)
            engines[limit_side_len] = engine
        regions = recognized_regions(engine(image))
        items.extend(projected_items_from_regions(frame, regions))

    return OcrEngineResult(
        items=tuple(items),
        asset_sha256=asset_sha256,
        config_version=config.config_version,
        config_fingerprint=config.config_fingerprint,
        frames_processed=len(frames),
    )


# --- small validators ---------------------------------------------------------


def _required_str(value: Mapping[str, object], key: str) -> str:
    item = value[key]
    if not isinstance(item, str) or not item:
        raise OcrEngineError("ocr_config_invalid", f"'{key}' must be a non-empty string.")
    return item


def _required_bool(value: Mapping[str, object], key: str) -> bool:
    item = value[key]
    if not isinstance(item, bool):
        raise OcrEngineError("ocr_config_invalid", f"'{key}' must be a boolean.")
    return item


def _positive_int(value: Mapping[str, object], key: str) -> int:
    item = value[key]
    if not isinstance(item, int) or isinstance(item, bool) or item <= 0:
        raise OcrEngineError("ocr_config_invalid", f"'{key}' must be a positive integer.")
    return item


__all__ = [
    "CANDIDATE_ID",
    "CAPABILITY",
    "OcrEngineError",
    "OcrEngineResult",
    "OcrFrame",
    "RapidOcrEngineConfig",
    "analyze_frames_ocr",
    "build_engine",
    "load_rapidocr_engine_config",
    "locate_bundled_models_dir",
    "projected_items_from_regions",
    "recognized_regions",
    "resolve_ocr_candidate",
    "verify_bundled_models",
]
