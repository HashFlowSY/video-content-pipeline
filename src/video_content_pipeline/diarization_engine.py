"""Real sherpa-onnx diarization engine behind the Phase 5 SpeakerTurn contract.

Phase 11 ticket 07 fills the recorded *Diarization capability vacancy* with the
credential-free sherpa-onnx offline pipeline (decision D5): pyannote-segmentation
-3.0 ONNX (MIT) for overlap-aware speaker segmentation, the 3D-Speaker CAM++
zh-en advanced embedding (Apache-2.0), and agglomerative clustering. Both assets
are ONNX-scale, so the pipeline itself runs through sherpa-onnx's
``OfflineSpeakerDiarization`` over the two vendored, hash-pinned assets resolved
from the model registry. Per ADR 0055 that ran in-process; ADR 0058 wraps it in a
Model runtime subprocess for the orchestrated real run (see
:mod:`video_content_pipeline.diarization_child`) so its peak is measured honestly
in a fresh process. The engine:

* verifies each pinned asset from disk before loading it
  (:func:`load_segmentation_asset`, :func:`load_embedding_asset`) -- a missing or
  tampered asset is a typed acquisition failure, never a network attempt
  (sherpa-onnx only ever opens the local files);
* runs the real pipeline and shapes its overlap-aware speaker segments into
  anonymous cluster candidates on the authoritative source timeline;
* projects those candidates through the shared ADR 0030 / 0031 gate
  (:func:`video_content_pipeline.audio_analysis.partition_speaker_turns`) against
  the real VAD partition from ticket 06 -- only with a valid, model-matched
  *diarization calibration record* (ADR 0031) may it publish formal SpeakerTurns;
  without one every turn stays a raw anonymous candidate.

Speaker labels stay anonymous and Part-local (``part-NN:speaker-MM``); the engine
asserts no cross-Part, cross-run, or real-person identity (ADR 0030). Overlapping
speech yields independent overlapping turns -- concurrent speakers are never
merged. The real engine slots *beside* -- never replaces -- the controlled
offline adapter that Phase 5's pytest gate uses (ADR 0037); it is exercised by
the offline integration test and the maintainer-invoked prototype, not by
``analyze_audio``. The segment-shaping and gate steps are pure and model-free, so
they are unit-tested without sherpa-onnx; only asset loading and inference touch
the models.
"""

from __future__ import annotations

import json
import wave
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from video_content_pipeline.audio_analysis import (
    SpeakerTurnCandidate,
    SpeakerTurnPartition,
    VoiceActivityInterval,
    diarization_vad_conflict_as_json,
    partition_speaker_turns,
    published_speaker_turn_as_json,
)
from video_content_pipeline.audio_derivation import DerivativeTimeMapping
from video_content_pipeline.capabilities import load_registry_document
from video_content_pipeline.model_acquisition import (
    AssetVerificationError,
    verify_acquired_asset,
)
from video_content_pipeline.model_runtime import EngineRequest, run_engine_subprocess
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval, interval_as_json

CAPABILITY = "diarization"
SEGMENTATION_CANDIDATE_ID = "sherpa-onnx-pyannote-segmentation-3-0"
EMBEDDING_CANDIDATE_ID = "3dspeaker-campplus-zh-en-advanced"

#: The production diarization child argv and its per-derivative subprocess budget.
#: The whole ``analyze_derivative_diarization`` sequence runs in the child so its
#: peak is an honest fresh-process figure comparable to the device baselines.
DIARIZATION_CHILD_MODULE = "video_content_pipeline.diarization_child"
DEFAULT_DIARIZATION_TIMEOUT_SECONDS = 600.0


def default_diarization_command() -> list[str]:
    """The production child argv: this interpreter running the diarization child."""

    import sys

    return [sys.executable, "-m", DIARIZATION_CHILD_MODULE]


#: The sherpa-onnx diarization pipeline is configured for 16 kHz mono audio.
DIARIZATION_SAMPLE_RATE = 16000
#: The segmentation model file inside the pinned segmentation asset tree.
SEGMENTATION_MODEL_FILE = "model.onnx"
#: The embedding model file inside the pinned embedding asset tree.
EMBEDDING_MODEL_FILE = "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"

#: A sherpa hard speaker assignment carries no soft score; a decided assignment
#: is represented at full confidence (see the calibration record's notes).
DECIDED_ASSIGNMENT_CONFIDENCE = 1.0

_CALIBRATION_PATH = Path("config") / "audio-analysis" / "sherpa-diarization-calibration.json"


class DiarizationEngineError(ValueError):
    """A rejected real-diarization precondition with a machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class DiarizationPipelineConfig:
    """The sherpa-onnx diarization *execution* configuration, without model identity.

    This is exactly what :func:`build_diarization` needs to construct the
    pipeline; keeping it separate from the calibration record means the
    uncalibrated path (ADR 0031, raw clusters only) drives inference from the
    sherpa defaults without fabricating a bound identity it does not have.
    """

    num_clusters: int
    cluster_threshold: float
    min_duration_on: float
    min_duration_off: float
    num_threads: int


#: The sherpa-onnx defaults: automatic speaker-count discovery, 0.5 cosine
#: clustering threshold, 300 ms minimum speech-on / 500 ms minimum speech-off.
SHERPA_DEFAULT_PIPELINE = DiarizationPipelineConfig(
    num_clusters=-1,
    cluster_threshold=0.5,
    min_duration_on=0.3,
    min_duration_off=0.5,
    num_threads=1,
)


@dataclass(frozen=True)
class SherpaDiarizationCalibration:
    """A model-specific sherpa-onnx diarization calibration record (ADR 0031).

    Its ``segmentation_asset_sha256`` / ``embedding_asset_sha256`` bind the record
    to the exact acquired assets; ``backend`` / ``backend_version`` / ``precision``
    / ``device_class`` / ``rules_fingerprint`` complete the bound identity; the
    pipeline block is the exact execution configuration and ``minimum_confidence``
    the formal-turn gate. Without such a record the pipeline may keep raw
    anonymous clusters but publishes no formal SpeakerTurns.
    """

    calibration_version: str
    segmentation_asset_sha256: str
    embedding_asset_sha256: str
    backend: str
    backend_version: str
    precision: str
    device_class: str
    rules_fingerprint: str
    sample_rate: int
    num_clusters: int
    cluster_threshold: float
    min_duration_on: float
    min_duration_off: float
    num_threads: int
    minimum_confidence: float

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise DiarizationEngineError(
                "diarization_calibration_invalid", "Calibration sample rate must be positive."
            )
        if not 0 <= self.minimum_confidence <= 1:
            raise DiarizationEngineError(
                "diarization_calibration_invalid", "minimum_confidence must lie in [0, 1]."
            )
        if not 0 < self.cluster_threshold:
            raise DiarizationEngineError(
                "diarization_calibration_invalid", "cluster_threshold must be positive."
            )
        if self.min_duration_on < 0 or self.min_duration_off < 0:
            raise DiarizationEngineError(
                "diarization_calibration_invalid", "Minimum durations must be non-negative."
            )
        if self.num_threads <= 0:
            raise DiarizationEngineError(
                "diarization_calibration_invalid", "num_threads must be positive."
            )

    @classmethod
    def from_json(cls, decoded: object) -> SherpaDiarizationCalibration:
        """Parse and validate a calibration record from its JSON document."""

        if not isinstance(decoded, Mapping) or decoded.get("schema_version") != 1:
            raise DiarizationEngineError(
                "diarization_calibration_invalid", "Calibration schema is invalid."
            )
        identity = decoded.get("model_identity")
        pipeline = decoded.get("pipeline")
        thresholds = decoded.get("thresholds")
        if (
            not isinstance(identity, Mapping)
            or not isinstance(pipeline, Mapping)
            or not isinstance(thresholds, Mapping)
        ):
            raise DiarizationEngineError(
                "diarization_calibration_invalid", "Calibration fields are missing."
            )
        try:
            return cls(
                calibration_version=_required_str(decoded, "calibration_version"),
                segmentation_asset_sha256=_required_str(identity, "segmentation_asset_sha256"),
                embedding_asset_sha256=_required_str(identity, "embedding_asset_sha256"),
                backend=_required_str(identity, "backend"),
                backend_version=_required_str(identity, "backend_version"),
                precision=_required_str(identity, "precision"),
                device_class=_required_str(identity, "device_class"),
                rules_fingerprint=_required_str(identity, "rules_fingerprint"),
                sample_rate=_positive_int(decoded, "sample_rate"),
                num_clusters=_num_clusters(pipeline),
                cluster_threshold=_positive_float(pipeline, "cluster_threshold"),
                min_duration_on=_non_negative_float(pipeline, "min_duration_on"),
                min_duration_off=_non_negative_float(pipeline, "min_duration_off"),
                num_threads=_positive_int(pipeline, "num_threads"),
                minimum_confidence=_unit_float(thresholds, "minimum_confidence"),
            )
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, DiarizationEngineError):
                raise
            raise DiarizationEngineError(
                "diarization_calibration_invalid", "Calibration fields are invalid."
            ) from error

    def pipeline_config(self) -> DiarizationPipelineConfig:
        """The execution configuration this record binds, for :func:`build_diarization`."""

        return DiarizationPipelineConfig(
            num_clusters=self.num_clusters,
            cluster_threshold=self.cluster_threshold,
            min_duration_on=self.min_duration_on,
            min_duration_off=self.min_duration_off,
            num_threads=self.num_threads,
        )

    def as_json(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "calibration_version": self.calibration_version,
            "model_identity": {
                "segmentation_asset_sha256": self.segmentation_asset_sha256,
                "embedding_asset_sha256": self.embedding_asset_sha256,
                "backend": self.backend,
                "backend_version": self.backend_version,
                "precision": self.precision,
                "device_class": self.device_class,
                "rules_fingerprint": self.rules_fingerprint,
            },
            "sample_rate": self.sample_rate,
            "pipeline": {
                "num_clusters": self.num_clusters,
                "cluster_threshold": self.cluster_threshold,
                "min_duration_on": self.min_duration_on,
                "min_duration_off": self.min_duration_off,
                "num_threads": self.num_threads,
            },
            "thresholds": {"minimum_confidence": self.minimum_confidence},
        }


@dataclass(frozen=True)
class SherpaDiarizationResult:
    """The real engine's output: anonymous candidates and, if calibrated, turns."""

    source_id: str
    stream_index: int
    part_label: str
    raw_turns: tuple[SpeakerTurnCandidate, ...]
    partition: SpeakerTurnPartition | None
    segmentation_asset_sha256: str
    embedding_asset_sha256: str
    calibrated: bool

    def as_json(self) -> dict[str, object]:
        """The formal SpeakerTurn Part evidence (turns + retained VAD conflicts).

        Role candidates are added downstream from subtitle/user evidence, not by
        the diarization engine; this projection carries only what diarization
        establishes. When uncalibrated (ADR 0031) there are no formal turns.
        """

        partition = self.partition
        return {
            "source_id": self.source_id,
            "audio_stream_index": self.stream_index,
            "speaker_turns": (
                [published_speaker_turn_as_json(turn) for turn in partition.published]
                if partition is not None
                else []
            ),
            "diarization_vad_conflicts": (
                [diarization_vad_conflict_as_json(conflict) for conflict in partition.conflicts]
                if partition is not None
                else []
            ),
        }


# --- calibration (ADR 0031 gate) ----------------------------------------------


def load_diarization_calibration(
    project_root: Path,
    *,
    expected_segmentation_sha256: str | None = None,
    expected_embedding_sha256: str | None = None,
) -> SherpaDiarizationCalibration:
    """Read and gate-check the diarization calibration record (ADR 0031).

    Validates the record's schema and ranges and, when the expected asset hashes
    are given, that the record was calibrated for those exact assets. Raises
    :class:`DiarizationEngineError` (``diarization_calibration_invalid`` or
    ``diarization_calibration_model_mismatch``) otherwise; a rejected record means
    the caller may keep only raw anonymous candidates.
    """

    path = project_root / _CALIBRATION_PATH
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DiarizationEngineError(
            "diarization_calibration_invalid", "The diarization calibration record cannot be read."
        ) from error
    calibration = SherpaDiarizationCalibration.from_json(decoded)
    if calibration.sample_rate != DIARIZATION_SAMPLE_RATE:
        raise DiarizationEngineError(
            "diarization_calibration_invalid",
            "Calibration must target the 16 kHz diarization configuration.",
        )
    if (
        expected_segmentation_sha256 is not None
        and calibration.segmentation_asset_sha256 != expected_segmentation_sha256
    ) or (
        expected_embedding_sha256 is not None
        and calibration.embedding_asset_sha256 != expected_embedding_sha256
    ):
        raise DiarizationEngineError(
            "diarization_calibration_model_mismatch",
            "The calibration record was produced for different model assets.",
        )
    return calibration


# --- asset loading (typed acquisition failure, never network) -----------------


def resolve_diarization_candidate(project_root: Path, candidate_id: str) -> Mapping[str, object]:
    """Return the named diarization candidate from the model registry."""

    registry_path = project_root / "models" / "registry.json"
    registry = load_registry_document(
        registry_path,
        invalid_error=lambda message: DiarizationEngineError(
            "diarization_asset_unavailable", message
        ),
    )
    candidates = registry.get("candidates")
    if isinstance(candidates, list):
        for candidate in candidates:
            if (
                isinstance(candidate, Mapping)
                and candidate.get("candidate_id") == candidate_id
                and candidate.get("capability") == CAPABILITY
            ):
                return candidate
    raise DiarizationEngineError(
        "diarization_candidate_absent",
        f"The registry has no acquired diarization candidate '{candidate_id}'.",
    )


def load_diarization_asset(
    project_root: Path, candidate_id: str, model_file: str
) -> tuple[Path, str]:
    """Verify one pinned diarization asset from disk and return ``(onnx_path, sha)``.

    Re-hashes the whole vendored asset tree against the registry manifest before
    it is ever loaded, then resolves ``model_file`` inside it. A missing
    directory, a drifted file, a mismatched manifest, or an absent model file
    raises :class:`DiarizationEngineError`
    (``diarization_asset_unavailable`` / ``diarization_asset_mismatch``); the
    models are never fetched -- verification and loading touch only local files.
    """

    candidate = resolve_diarization_candidate(project_root, candidate_id)
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
        raise DiarizationEngineError(
            "diarization_asset_unavailable",
            f"The '{candidate_id}' registry entry is incomplete.",
        )
    asset_root = (project_root / local_path).resolve()
    if not asset_root.is_dir():
        raise DiarizationEngineError(
            "diarization_asset_unavailable", f"The diarization asset tree is absent: {asset_root}"
        )
    try:
        verify_acquired_asset(manifest, asset_sha256, asset_root)
    except AssetVerificationError as error:
        raise DiarizationEngineError("diarization_asset_mismatch", str(error)) from error
    model_path = asset_root / model_file
    if not model_path.is_file():
        raise DiarizationEngineError(
            "diarization_asset_unavailable",
            f"The pinned model file is absent: {model_path}",
        )
    return model_path, asset_sha256


def load_segmentation_asset(project_root: Path) -> tuple[Path, str]:
    """Verify the pinned pyannote-segmentation asset and return ``(onnx_path, sha)``."""

    return load_diarization_asset(project_root, SEGMENTATION_CANDIDATE_ID, SEGMENTATION_MODEL_FILE)


def load_embedding_asset(project_root: Path) -> tuple[Path, str]:
    """Verify the pinned CAM++ embedding asset and return ``(onnx_path, sha)``."""

    return load_diarization_asset(project_root, EMBEDDING_CANDIDATE_ID, EMBEDDING_MODEL_FILE)


def build_diarization(
    segmentation_path: Path,
    embedding_path: Path,
    pipeline: DiarizationPipelineConfig,
) -> Any:
    """Build an offline sherpa-onnx speaker-diarization pipeline over local ONNX files."""

    import sherpa_onnx

    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(segmentation_path)
            ),
            num_threads=pipeline.num_threads,
            provider="cpu",
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(embedding_path),
            num_threads=pipeline.num_threads,
            provider="cpu",
        ),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=pipeline.num_clusters,
            threshold=pipeline.cluster_threshold,
        ),
        min_duration_on=pipeline.min_duration_on,
        min_duration_off=pipeline.min_duration_off,
    )
    if not config.validate():
        raise DiarizationEngineError(
            "diarization_pipeline_invalid",
            "The sherpa-onnx diarization configuration failed validation.",
        )
    return sherpa_onnx.OfflineSpeakerDiarization(config)


# --- real inference -----------------------------------------------------------


def read_wav_samples(wav_path: Path) -> NDArray[np.float32]:
    """Read a 16 kHz mono PCM-16 wav into a normalized float32 sample array."""

    try:
        with wave.open(str(wav_path), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            frames = handle.readframes(handle.getnframes())
    except (OSError, wave.Error) as error:
        raise DiarizationEngineError(
            "diarization_audio_invalid", "The analysis wav cannot be read."
        ) from error
    if channels != 1 or width != 2 or rate != DIARIZATION_SAMPLE_RATE:
        raise DiarizationEngineError(
            "diarization_audio_invalid",
            "The real diarization engine requires a 16 kHz mono PCM-16 derivative.",
        )
    pcm = np.frombuffer(frames, dtype="<i2").astype(np.float32)
    return pcm / 32768.0


def raw_speaker_segments(
    diarization: Any, samples: NDArray[np.float32]
) -> tuple[tuple[int, float, float], ...]:
    """Run the pipeline and return ``(speaker, start_seconds, end_seconds)`` segments.

    Segments are sorted by start time; overlap-aware segmentation may return
    concurrent segments for different speakers over the same interval.
    """

    result = diarization.process(samples)
    return tuple(
        (int(segment.speaker), float(segment.start), float(segment.end))
        for segment in result.sort_by_start_time()
    )


# --- pure segment shaping -----------------------------------------------------


def speaker_turn_candidates_from_segments(
    segments: Sequence[tuple[int, float, float]],
    mapping: DerivativeTimeMapping,
) -> tuple[SpeakerTurnCandidate, ...]:
    """Shape sherpa speaker segments into anonymous cluster candidates on the timeline.

    Each segment's second bounds are snapped to derivative sample boundaries and
    mapped to source time; every speaker becomes an anonymous ``speaker-N``
    cluster. Overlapping segments stay independent candidates -- concurrent
    speakers are never merged. A degenerate segment that snaps to zero length is
    dropped rather than manufactured into a turn.
    """

    candidates: list[SpeakerTurnCandidate] = []
    for speaker, start_seconds, end_seconds in segments:
        start_sample = _clamp_sample(round(start_seconds * mapping.sample_rate), mapping)
        end_sample = _clamp_sample(round(end_seconds * mapping.sample_rate), mapping)
        if end_sample <= start_sample:
            continue
        interval = mapping.source_interval_for_samples(start_sample, end_sample)
        candidates.append(
            SpeakerTurnCandidate(f"speaker-{speaker}", interval, DECIDED_ASSIGNMENT_CONFIDENCE)
        )
    return tuple(candidates)


def _clamp_sample(sample: int, mapping: DerivativeTimeMapping) -> int:
    return max(0, min(sample, mapping.sample_count))


# --- top-level real analysis --------------------------------------------------


def analyze_derivative_diarization(
    project_root: Path,
    wav_path: Path,
    mapping: DerivativeTimeMapping,
    *,
    source_id: str,
    stream_index: int,
    part_label: str,
    voice_activity_intervals: tuple[VoiceActivityInterval, ...] = (),
) -> SherpaDiarizationResult:
    """Run the real sherpa-onnx diarization engine over one analysis-audio derivative.

    Verifies and loads both pinned assets, runs the pipeline, and shapes its
    overlap-aware segments into anonymous cluster candidates. If a model-matched
    calibration record exists (ADR 0031), projects them through the shared ADR
    0030 gate against ``voice_activity_intervals`` (the real VAD partition from
    ticket 06) into formal, anonymous, Part-local SpeakerTurns and retained
    diarization-VAD conflicts; without one the candidates stay raw and no formal
    turn is published. The wav must be the 16 kHz mono derivative ``mapping``
    describes.
    """

    segmentation_path, segmentation_sha256 = load_segmentation_asset(project_root)
    embedding_path, embedding_sha256 = load_embedding_asset(project_root)
    samples = read_wav_samples(wav_path)
    if len(samples) != mapping.sample_count:
        raise DiarizationEngineError(
            "diarization_audio_invalid", "The wav length does not match the derivative mapping."
        )

    # An absent record is the legitimate uncalibrated state (ADR 0031); a record
    # present but invalid or bound to different assets is a typed failure.
    calibration: SherpaDiarizationCalibration | None = None
    if (project_root / _CALIBRATION_PATH).is_file():
        calibration = load_diarization_calibration(
            project_root,
            expected_segmentation_sha256=segmentation_sha256,
            expected_embedding_sha256=embedding_sha256,
        )

    # ADR 0031: an uncalibrated model may keep raw clusters, so inference still
    # runs -- from the sherpa defaults -- but the gate below stays closed until a
    # model-matched calibration record binds the identity.
    pipeline = calibration.pipeline_config() if calibration is not None else SHERPA_DEFAULT_PIPELINE
    diarization = build_diarization(segmentation_path, embedding_path, pipeline)
    segments = raw_speaker_segments(diarization, samples)
    raw_turns = speaker_turn_candidates_from_segments(segments, mapping)

    partition: SpeakerTurnPartition | None = None
    if calibration is not None:
        partition = partition_speaker_turns(
            raw_turns, part_label, voice_activity_intervals, calibration.minimum_confidence
        )

    return SherpaDiarizationResult(
        source_id=source_id,
        stream_index=stream_index,
        part_label=part_label,
        raw_turns=raw_turns,
        partition=partition,
        segmentation_asset_sha256=segmentation_sha256,
        embedding_asset_sha256=embedding_sha256,
        calibrated=calibration is not None,
    )


# --- small validators ---------------------------------------------------------


def _required_str(value: Mapping[str, object], key: str) -> str:
    item = value[key]
    if not isinstance(item, str) or not item:
        raise DiarizationEngineError(
            "diarization_calibration_invalid", f"'{key}' must be a non-empty string."
        )
    return item


def _positive_int(value: Mapping[str, object], key: str) -> int:
    item = value[key]
    if not isinstance(item, int) or isinstance(item, bool) or item <= 0:
        raise DiarizationEngineError(
            "diarization_calibration_invalid", f"'{key}' must be a positive integer."
        )
    return item


def _num_clusters(value: Mapping[str, object]) -> int:
    item = value["num_clusters"]
    # -1 requests automatic speaker-count discovery; any positive count is a fixed
    # request. Zero is meaningless for clustering.
    if not isinstance(item, int) or isinstance(item, bool) or item == 0 or item < -1:
        raise DiarizationEngineError(
            "diarization_calibration_invalid", "'num_clusters' must be -1 or a positive integer."
        )
    return item


def _positive_float(value: Mapping[str, object], key: str) -> float:
    item = value[key]
    if isinstance(item, bool) or not isinstance(item, int | float) or item <= 0:
        raise DiarizationEngineError(
            "diarization_calibration_invalid", f"'{key}' must be a positive number."
        )
    return float(item)


def _non_negative_float(value: Mapping[str, object], key: str) -> float:
    item = value[key]
    if isinstance(item, bool) or not isinstance(item, int | float) or item < 0:
        raise DiarizationEngineError(
            "diarization_calibration_invalid", f"'{key}' must be a non-negative number."
        )
    return float(item)


def _unit_float(value: Mapping[str, object], key: str) -> float:
    item = value[key]
    if isinstance(item, bool) or not isinstance(item, int | float) or not 0 <= item <= 1:
        raise DiarizationEngineError(
            "diarization_calibration_invalid", f"'{key}' must lie in [0, 1]."
        )
    return float(item)


# --- isolated (subprocess) analysis -------------------------------------------


def _speaker_turn_candidate_as_json(candidate: SpeakerTurnCandidate) -> dict[str, object]:
    return {
        "cluster_id": candidate.cluster_id,
        "interval": interval_as_json(candidate.interval),
        "confidence": candidate.confidence,
    }


def _speaker_turn_candidate_from_json(payload: object) -> SpeakerTurnCandidate:
    if not isinstance(payload, Mapping):
        raise DiarizationEngineError(
            "diarization_output_invalid", "A diarization turn candidate must be an object."
        )
    cluster_id = payload.get("cluster_id")
    interval = payload.get("interval")
    confidence = payload.get("confidence")
    if (
        not isinstance(cluster_id, str)
        or not cluster_id
        or not isinstance(interval, Mapping)
        or isinstance(confidence, bool)
        or not isinstance(confidence, int | float)
    ):
        raise DiarizationEngineError(
            "diarization_output_invalid", "A diarization turn candidate is malformed."
        )
    start = interval.get("start")
    end = interval.get("end")
    if not isinstance(start, Mapping) or not isinstance(end, Mapping):
        raise DiarizationEngineError(
            "diarization_output_invalid", "A diarization turn interval is malformed."
        )
    try:
        bounds = HalfOpenInterval(
            ExactTime(int(start["numerator"]), int(start["denominator"])),
            ExactTime(int(end["numerator"]), int(end["denominator"])),
        )
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
        raise DiarizationEngineError(
            "diarization_output_invalid", "A diarization turn interval is malformed."
        ) from error
    return SpeakerTurnCandidate(cluster_id, bounds, float(confidence))


@dataclass(frozen=True)
class IsolatedDiarizationResult:
    """One derivative's real anonymous diarization candidates, measured in a child.

    ``raw_turns`` are the anonymous cluster candidates before the ADR 0030/0031
    gate; the parent applies that gate in-process (no model) against the real VAD
    partition and the run's role evidence. ``peak_memory_bytes`` is the child's
    fresh-process high-water mark, comparable to the device baselines.
    """

    raw_turns: tuple[SpeakerTurnCandidate, ...]
    segmentation_asset_sha256: str
    embedding_asset_sha256: str
    calibrated: bool
    peak_memory_bytes: int


def run_isolated_diarization(
    project_root: Path,
    wav_path: Path,
    mapping: DerivativeTimeMapping,
    *,
    source_id: str,
    stream_index: int,
    part_label: str,
    command: Sequence[str] | None = None,
    timeout_seconds: float = DEFAULT_DIARIZATION_TIMEOUT_SECONDS,
) -> IsolatedDiarizationResult:
    """Run the real sherpa-onnx diarization in its own child and return candidates.

    The whole ``analyze_derivative_diarization`` sequence runs in the child
    (:mod:`video_content_pipeline.diarization_child`) so both pinned assets load in
    a fresh process whose peak is honest. Only the anonymous ``raw_turns`` are
    returned; the parent re-applies the shared ADR 0030 gate against the real VAD
    partition. A malformed child response is a typed ``diarization_output_invalid``
    failure; subprocess crashes/timeouts surface as ``ModelRuntimeError``.
    """

    request = EngineRequest(
        model_path=str(project_root),
        task={
            "wav_path": str(wav_path),
            "mapping": mapping.as_json(),
            "source_id": source_id,
            "stream_index": stream_index,
            "part_label": part_label,
        },
    )
    result = run_engine_subprocess(
        list(command) if command is not None else default_diarization_command(),
        request,
        timeout_seconds=timeout_seconds,
    )
    return _parse_isolated_diarization_result(result.result, result.peak_memory_bytes)


def _parse_isolated_diarization_result(
    result: Mapping[str, object], peak_memory_bytes: int
) -> IsolatedDiarizationResult:
    raw_turns = result.get("raw_turns")
    segmentation_sha256 = result.get("segmentation_asset_sha256")
    embedding_sha256 = result.get("embedding_asset_sha256")
    calibrated = result.get("calibrated")
    if (
        not isinstance(raw_turns, list)
        or not isinstance(segmentation_sha256, str)
        or not isinstance(embedding_sha256, str)
        or not isinstance(calibrated, bool)
    ):
        raise DiarizationEngineError(
            "diarization_output_invalid", "The diarization child response is missing fields."
        )
    return IsolatedDiarizationResult(
        raw_turns=tuple(_speaker_turn_candidate_from_json(turn) for turn in raw_turns),
        segmentation_asset_sha256=segmentation_sha256,
        embedding_asset_sha256=embedding_sha256,
        calibrated=calibrated,
        peak_memory_bytes=peak_memory_bytes,
    )
