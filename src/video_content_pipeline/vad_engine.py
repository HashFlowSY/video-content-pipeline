"""Real silero-vad engine: the first production model behind a Phase 5 contract.

Phase 11 ticket 06 wires the first real model engine. silero-vad is ONNX-scale,
so per ADR 0055 it runs *in-process* through onnxruntime (no Model runtime
subprocess) over the vendored, hash-pinned ``silero_vad.onnx`` resolved from the
model registry. The engine:

* verifies the pinned asset from disk before loading it (:func:`load_silero_asset`)
  -- a missing or tampered asset is a typed acquisition failure, never a network
  attempt (onnxruntime only ever opens the local file);
* runs windowed inference to speech probabilities and shapes them into speech
  runs under a *model-specific calibration record* (ADR 0029): without a valid,
  model-matched calibration the formal partition is entirely ``indeterminate``;
* projects those runs into the existing Complete VAD partition contract
  (:func:`video_content_pipeline.audio_analysis.derive_vad_part_evidence`) and
  the shared <=5-minute chunk derivation
  (:func:`video_content_pipeline.vad_chunking.derive_speech_chunks`).

The real engine slots *beside* -- never replaces -- the controlled offline
adapter that Phase 5's pytest gate uses (ADR 0037); it is exercised by the
offline integration test and the maintainer-invoked prototype, not by
``analyze_audio``. The probability-shaping and projection steps are pure and
model-free, so they are unit-tested without onnxruntime; only asset loading and
inference touch the model.
"""

from __future__ import annotations

import json
import sys
import wave
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from video_content_pipeline.audio_analysis import (
    VadPartEvidence,
    VoiceActivityCandidateSegment,
    VoiceActivityState,
    derive_vad_part_evidence,
)
from video_content_pipeline.audio_derivation import DerivativeTimeMapping
from video_content_pipeline.capabilities import load_registry_document
from video_content_pipeline.coverage import StreamCoverage
from video_content_pipeline.model_acquisition import (
    AssetVerificationError,
    verify_acquired_asset,
)
from video_content_pipeline.model_runtime import EngineRequest, run_engine_subprocess
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval, interval_as_json
from video_content_pipeline.vad_chunking import FIVE_MINUTES, SpeechChunk, derive_speech_chunks

CAPABILITY = "vad"
CANDIDATE_ID = "silero-vad"

#: The production VAD child argv and its per-derivative subprocess budget. The
#: whole ``analyze_derivative_vad`` sequence runs in the child so its peak is an
#: honest fresh-process figure; the ONNX session is small, so the budget is far
#: below the MLX engines' 300 s.
VAD_CHILD_MODULE = "video_content_pipeline.vad_child"
DEFAULT_VAD_TIMEOUT_SECONDS = 300.0


def default_vad_command() -> list[str]:
    """The production child argv: this interpreter running the VAD child module."""

    return [sys.executable, "-m", VAD_CHILD_MODULE]

#: silero v6 processes fixed 512-sample windows at 16 kHz; the model is loaded
#: and calibrated for exactly this configuration.
SILERO_SAMPLE_RATE = 16000
SILERO_WINDOW_SAMPLES = 512
#: silero v5/v6 prepends 64 samples of the previous window as context to every
#: 16 kHz frame (the model input is ``context + window`` = 576 samples). The model
#: accepts a bare 512-sample input without erroring but scores near-zero on real
#: speech, so the context is mandatory, not optional.
SILERO_CONTEXT_SAMPLES = 64
#: The width of silero's recurrent hidden state (the ``state`` input/output).
SILERO_STATE_DIM = 128

_CALIBRATION_PATH = Path("config") / "audio-analysis" / "silero-vad-calibration.json"


class VadEngineError(ValueError):
    """A rejected real-VAD precondition with a machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class SileroVadCalibration:
    """A model-specific silero calibration record (ADR 0029).

    Its ``model_asset_sha256`` binds the record to the exact acquired asset; the
    classification fields turn raw probabilities into formal speech, and the
    duration thresholds drive the partition's uncovered-speech and long-silence
    risks. Without such a record the engine cannot classify and every interval
    stays ``indeterminate``.
    """

    calibration_version: str
    model_asset_sha256: str
    sample_rate: int
    window_samples: int
    speech_probability_threshold: float
    min_speech_samples: int
    min_silence_samples: int
    speech_pad_samples: int
    uncovered_speech_duration: ExactTime
    long_silence_duration: ExactTime

    def __post_init__(self) -> None:
        # Range invariants, so a directly constructed record is as valid as a
        # parsed one; the silero 16 kHz / 512-sample config check stays in the
        # loader (this record type is otherwise dimension-agnostic).
        if self.sample_rate <= 0 or self.window_samples <= 0:
            raise VadEngineError(
                "vad_calibration_invalid", "Calibration audio dimensions must be positive."
            )
        if not 0 < self.speech_probability_threshold < 1:
            raise VadEngineError(
                "vad_calibration_invalid", "The speech probability threshold must lie in (0, 1)."
            )
        if (
            self.min_speech_samples <= 0
            or self.min_silence_samples < 0
            or self.speech_pad_samples < 0
        ):
            raise VadEngineError(
                "vad_calibration_invalid", "Calibration sample counts are out of range."
            )
        if self.uncovered_speech_duration <= ExactTime(
            0
        ) or self.long_silence_duration <= ExactTime(0):
            raise VadEngineError(
                "vad_calibration_invalid", "Calibration duration thresholds must be positive."
            )

    @classmethod
    def from_json(cls, decoded: object) -> SileroVadCalibration:
        """Parse and validate a calibration record from its JSON document."""

        if not isinstance(decoded, Mapping) or decoded.get("schema_version") != 1:
            raise VadEngineError("vad_calibration_invalid", "Calibration schema is invalid.")
        classification = decoded.get("classification")
        thresholds = decoded.get("thresholds")
        if not isinstance(classification, Mapping) or not isinstance(thresholds, Mapping):
            raise VadEngineError("vad_calibration_invalid", "Calibration fields are missing.")
        try:
            return cls(
                calibration_version=_required_str(decoded, "calibration_version"),
                model_asset_sha256=_required_str(decoded, "model_asset_sha256"),
                sample_rate=_positive_int(decoded, "sample_rate"),
                window_samples=_positive_int(decoded, "window_samples"),
                speech_probability_threshold=_probability(
                    classification, "speech_probability_threshold"
                ),
                min_speech_samples=_positive_int(classification, "min_speech_samples"),
                min_silence_samples=_non_negative_int(classification, "min_silence_samples"),
                speech_pad_samples=_non_negative_int(classification, "speech_pad_samples"),
                uncovered_speech_duration=_exact_time(thresholds.get("uncovered_speech_duration")),
                long_silence_duration=_exact_time(thresholds.get("long_silence_duration")),
            )
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, VadEngineError):
                raise
            raise VadEngineError(
                "vad_calibration_invalid", "Calibration fields are invalid."
            ) from error

    def as_json(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "calibration_version": self.calibration_version,
            "model_asset_sha256": self.model_asset_sha256,
            "sample_rate": self.sample_rate,
            "window_samples": self.window_samples,
            "classification": {
                "speech_probability_threshold": self.speech_probability_threshold,
                "min_speech_samples": self.min_speech_samples,
                "min_silence_samples": self.min_silence_samples,
                "speech_pad_samples": self.speech_pad_samples,
            },
            "thresholds": {
                "uncovered_speech_duration": _exact_time_as_json(self.uncovered_speech_duration),
                "long_silence_duration": _exact_time_as_json(self.long_silence_duration),
            },
        }


@dataclass(frozen=True)
class SileroVadResult:
    """The real engine's output: the Complete VAD partition plus derived chunks."""

    part_evidence: VadPartEvidence
    speech_runs_samples: tuple[tuple[int, int], ...]
    chunks: tuple[SpeechChunk, ...]
    model_asset_sha256: str
    calibrated: bool


# --- calibration (ADR 0029 gate) ----------------------------------------------


def load_silero_calibration(
    project_root: Path, *, expected_asset_sha256: str | None = None
) -> SileroVadCalibration:
    """Read and gate-check the silero calibration record (ADR 0029).

    Validates the record's schema and threshold ranges and, when
    ``expected_asset_sha256`` is given, that the record was calibrated for that
    exact asset. Raises :class:`VadEngineError` (``vad_calibration_invalid`` or
    ``vad_calibration_model_mismatch``) otherwise; a rejected record means the
    caller must treat the audio as ``indeterminate``.
    """

    path = project_root / _CALIBRATION_PATH
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VadEngineError(
            "vad_calibration_invalid", "The silero calibration record cannot be read."
        ) from error
    calibration = SileroVadCalibration.from_json(decoded)
    if (
        calibration.sample_rate != SILERO_SAMPLE_RATE
        or calibration.window_samples != SILERO_WINDOW_SAMPLES
    ):
        raise VadEngineError(
            "vad_calibration_invalid",
            "Calibration must target the silero 16 kHz / 512-sample configuration.",
        )
    if (
        expected_asset_sha256 is not None
        and calibration.model_asset_sha256 != expected_asset_sha256
    ):
        raise VadEngineError(
            "vad_calibration_model_mismatch",
            "The calibration record was produced for a different model asset.",
        )
    return calibration


# --- asset loading (typed acquisition failure, never network) -----------------


def resolve_silero_candidate(project_root: Path) -> Mapping[str, object]:
    """Return the ``silero-vad`` VAD candidate from the model registry."""

    registry_path = project_root / "models" / "registry.json"
    registry = load_registry_document(
        registry_path,
        invalid_error=lambda message: VadEngineError("vad_asset_unavailable", message),
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
    raise VadEngineError(
        "vad_candidate_absent", "The registry has no acquired silero-vad candidate."
    )


def load_silero_asset(
    project_root: Path, candidate: Mapping[str, object] | None = None
) -> tuple[Path, str]:
    """Verify the pinned silero asset from disk and return ``(onnx_path, asset_sha256)``.

    Re-hashes the vendored asset against the registry manifest before it is ever
    loaded. A missing directory, a drifted file, or a mismatched manifest raises
    :class:`VadEngineError` (``vad_asset_unavailable`` / ``vad_asset_mismatch``);
    the model is never fetched from the network -- verification and loading touch
    only local files.
    """

    if candidate is None:
        candidate = resolve_silero_candidate(project_root)
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
        raise VadEngineError(
            "vad_asset_unavailable", "The silero-vad registry entry is incomplete."
        )
    asset_root = (project_root / local_path).resolve()
    if not asset_root.is_dir():
        raise VadEngineError(
            "vad_asset_unavailable", f"The silero asset tree is absent: {asset_root}"
        )
    try:
        verify_acquired_asset(manifest, asset_sha256, asset_root)
    except AssetVerificationError as error:
        raise VadEngineError("vad_asset_mismatch", str(error)) from error
    onnx_relative = manifest[0]
    if not isinstance(onnx_relative, Mapping) or not isinstance(onnx_relative.get("path"), str):
        raise VadEngineError("vad_asset_unavailable", "The silero manifest is malformed.")
    return asset_root / str(onnx_relative["path"]), asset_sha256


def load_silero_session(onnx_path: Path) -> Any:
    """Open an offline onnxruntime session over the local silero ``.onnx`` file."""

    import onnxruntime as ort

    return ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])


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
        raise VadEngineError("vad_audio_invalid", "The analysis wav cannot be read.") from error
    if channels != 1 or width != 2 or rate != SILERO_SAMPLE_RATE:
        raise VadEngineError(
            "vad_audio_invalid",
            "The real VAD engine requires a 16 kHz mono PCM-16 derivative.",
        )
    pcm = np.frombuffer(frames, dtype="<i2").astype(np.float32)
    return pcm / 32768.0


def silero_frame_probabilities(session: Any, samples: NDArray[np.float32]) -> list[float]:
    """Run windowed silero inference, returning one speech probability per window.

    Each 512-sample window (the last zero-padded) is prepended with the previous
    window's trailing 64 samples of context and scored with the recurrent state
    carried forward, exactly as silero v5/v6 is designed to be driven at 16 kHz.
    The context starts as silence and is refreshed from each raw window; omitting
    it makes the model score real speech near zero (it accepts the shorter input
    without complaint), so it is mandatory.
    """

    window = SILERO_WINDOW_SAMPLES
    context_size = SILERO_CONTEXT_SAMPLES
    state = np.zeros((2, 1, SILERO_STATE_DIM), dtype=np.float32)
    context = np.zeros(context_size, dtype=np.float32)
    sr = np.array(SILERO_SAMPLE_RATE, dtype=np.int64)
    probabilities: list[float] = []
    for start in range(0, len(samples), window):
        frame = samples[start : start + window]
        if len(frame) < window:
            frame = np.pad(frame, (0, window - len(frame)))
        model_input = np.concatenate((context, frame)).reshape(1, context_size + window)
        output, state = session.run(None, {"input": model_input, "state": state, "sr": sr})
        context = frame[-context_size:]
        probabilities.append(float(output[0, 0]))
    return probabilities


# --- pure probability shaping and projection ----------------------------------


def speech_runs_from_probabilities(
    probabilities: Sequence[float],
    sample_count: int,
    calibration: SileroVadCalibration,
) -> tuple[tuple[int, int], ...]:
    """Shape per-window probabilities into calibrated speech sample runs.

    A window is speech when its probability meets the calibrated threshold;
    consecutive speech windows form a run clamped to ``sample_count``. Runs
    separated by less than ``min_silence_samples`` merge, runs shorter than
    ``min_speech_samples`` drop, and survivors are padded by
    ``speech_pad_samples`` and re-merged where the padding overlaps.
    """

    window = calibration.window_samples
    raw: list[list[int]] = []
    for index, probability in enumerate(probabilities):
        if probability < calibration.speech_probability_threshold:
            continue
        start = index * window
        end = min(start + window, sample_count)
        if end <= start:
            continue
        if raw and raw[-1][1] == start:
            raw[-1][1] = end
        else:
            raw.append([start, end])

    merged = _merge_within(raw, calibration.min_silence_samples)
    long_enough = [run for run in merged if run[1] - run[0] >= calibration.min_speech_samples]
    pad = calibration.speech_pad_samples
    padded = [[max(0, run[0] - pad), min(sample_count, run[1] + pad)] for run in long_enough]
    return tuple((run[0], run[1]) for run in _merge_within(padded, 0))


def _merge_within(runs: Sequence[Sequence[int]], gap: int) -> list[list[int]]:
    merged: list[list[int]] = []
    for start, end in runs:
        if merged and start - merged[-1][1] <= gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def candidate_segments_from_speech_runs(
    speech_runs_samples: Sequence[tuple[int, int]],
    mapping: DerivativeTimeMapping,
) -> tuple[VoiceActivityCandidateSegment, ...]:
    """Tile the whole derivative with calibrated speech/non-speech segments.

    Every speech run becomes a ``speech_likely`` segment and each intervening
    (or surrounding) gap a calibrated ``non_speech`` segment, so the projection
    covers the entire coverage with no unclassified span. All boundaries are
    mapped to source time through ``mapping``.
    """

    segments: list[VoiceActivityCandidateSegment] = []
    cursor = 0
    for start, end in speech_runs_samples:
        if cursor < start:
            segments.append(_segment(mapping, cursor, start, VoiceActivityState.NON_SPEECH))
        segments.append(_segment(mapping, start, end, VoiceActivityState.SPEECH_LIKELY))
        cursor = end
    if cursor < mapping.sample_count:
        segments.append(
            _segment(mapping, cursor, mapping.sample_count, VoiceActivityState.NON_SPEECH)
        )
    return tuple(segments)


def indeterminate_segments(
    mapping: DerivativeTimeMapping,
) -> tuple[VoiceActivityCandidateSegment, ...]:
    """The uncalibrated projection (ADR 0029): the whole coverage is indeterminate."""

    return (_segment(mapping, 0, mapping.sample_count, VoiceActivityState.INDETERMINATE),)


def _segment(
    mapping: DerivativeTimeMapping, start: int, end: int, state: VoiceActivityState
) -> VoiceActivityCandidateSegment:
    return VoiceActivityCandidateSegment(mapping.source_interval_for_samples(start, end), state)


# --- top-level real analysis --------------------------------------------------


def analyze_derivative_vad(
    project_root: Path,
    wav_path: Path,
    mapping: DerivativeTimeMapping,
    *,
    source_id: str,
    stream_index: int,
    caption_intervals: tuple[HalfOpenInterval, ...] = (),
    max_chunk_duration: ExactTime = FIVE_MINUTES,
) -> SileroVadResult:
    """Run the real silero engine end to end over one analysis-audio derivative.

    Verifies and loads the pinned asset, runs inference, and -- if a
    model-matched calibration record exists -- projects calibrated speech into
    the Complete VAD partition and the shared chunk derivation. Without a valid
    calibration the partition is entirely ``indeterminate`` (ADR 0029) and no
    chunks are produced. The wav must be the 16 kHz mono derivative ``mapping``
    describes.
    """

    onnx_path, asset_sha256 = load_silero_asset(project_root)
    samples = read_wav_samples(wav_path)
    if len(samples) != mapping.sample_count:
        raise VadEngineError(
            "vad_audio_invalid", "The wav length does not match the derivative mapping."
        )
    session = load_silero_session(onnx_path)
    probabilities = silero_frame_probabilities(session, samples)

    # An absent record is the legitimate uncalibrated state (ADR 0029). A record
    # that is present but invalid or bound to a different model is a typed failure
    # that must surface, not silently degrade to indeterminate.
    calibration: SileroVadCalibration | None = None
    if (project_root / _CALIBRATION_PATH).is_file():
        calibration = load_silero_calibration(project_root, expected_asset_sha256=asset_sha256)

    if calibration is None:
        # ADR 0029: with no model-matched calibration the model may not classify,
        # so the whole coverage is indeterminate and no speech is chunked. The
        # duration thresholds are unused here (there are no speech/non-speech
        # intervals to grade) but must stay positive for the partition contract.
        segments = indeterminate_segments(mapping)
        speech_runs: tuple[tuple[int, int], ...] = ()
        chunks: tuple[SpeechChunk, ...] = ()
        uncovered = ExactTime(1)
        long_silence = ExactTime(1)
    else:
        speech_runs = speech_runs_from_probabilities(
            probabilities, mapping.sample_count, calibration
        )
        segments = candidate_segments_from_speech_runs(speech_runs, mapping)
        chunks = derive_speech_chunks(speech_runs, mapping, max_chunk_duration=max_chunk_duration)
        uncovered = calibration.uncovered_speech_duration
        long_silence = calibration.long_silence_duration

    coverage = StreamCoverage(coverage=mapping.source_interval, gaps=(), diagnostics=())
    part_evidence = derive_vad_part_evidence(
        source_id=source_id,
        stream_index=stream_index,
        audio_coverage=coverage,
        candidate_segments=segments,
        caption_intervals=caption_intervals,
        uncovered_speech_threshold=uncovered,
        long_silence_threshold=long_silence,
    )
    return SileroVadResult(
        part_evidence=part_evidence,
        speech_runs_samples=speech_runs,
        chunks=chunks,
        model_asset_sha256=asset_sha256,
        calibrated=calibration is not None,
    )


# --- small validators ---------------------------------------------------------


def _required_str(value: Mapping[str, object], key: str) -> str:
    item = value[key]
    if not isinstance(item, str) or not item:
        raise VadEngineError("vad_calibration_invalid", f"'{key}' must be a non-empty string.")
    return item


def _positive_int(value: Mapping[str, object], key: str) -> int:
    item = value[key]
    if not isinstance(item, int) or isinstance(item, bool) or item <= 0:
        raise VadEngineError("vad_calibration_invalid", f"'{key}' must be a positive integer.")
    return item


def _non_negative_int(value: Mapping[str, object], key: str) -> int:
    item = value[key]
    if not isinstance(item, int) or isinstance(item, bool) or item < 0:
        raise VadEngineError("vad_calibration_invalid", f"'{key}' must be a non-negative integer.")
    return item


def _probability(value: Mapping[str, object], key: str) -> float:
    item = value[key]
    if isinstance(item, bool) or not isinstance(item, int | float) or not 0 < item < 1:
        raise VadEngineError("vad_calibration_invalid", f"'{key}' must be a probability in (0, 1).")
    return float(item)


def _exact_time(value: object) -> ExactTime:
    if not isinstance(value, Mapping):
        raise VadEngineError("vad_calibration_invalid", "A duration threshold must be an object.")
    numerator = value.get("numerator")
    denominator = value.get("denominator")
    if (
        not isinstance(numerator, int)
        or isinstance(numerator, bool)
        or not isinstance(denominator, int)
        or isinstance(denominator, bool)
        or denominator == 0
    ):
        raise VadEngineError(
            "vad_calibration_invalid", "A duration threshold needs integer numerator/denominator."
        )
    return ExactTime(numerator, denominator)


def _exact_time_as_json(value: ExactTime) -> dict[str, int]:
    return {"numerator": value.numerator, "denominator": value.denominator}


# --- isolated (subprocess) analysis -------------------------------------------


@dataclass(frozen=True)
class IsolatedVadResult:
    """One derivative's real VAD evidence measured in its own child process.

    ``part_evidence`` is the report-shaped Complete VAD partition (identical to
    the offline path's per-part evidence); ``speech_runs_samples`` lets the parent
    re-derive the shared speech chunks with the pure :func:`derive_speech_chunks`
    (no model, no cross-process dataclass rebuild); ``peak_memory_bytes`` is the
    child's fresh-process high-water mark, comparable to the device baselines.
    """

    part_evidence: Mapping[str, object]
    speech_runs_samples: tuple[tuple[int, int], ...]
    model_asset_sha256: str
    calibrated: bool
    peak_memory_bytes: int


def run_isolated_vad(
    project_root: Path,
    wav_path: Path,
    mapping: DerivativeTimeMapping,
    *,
    source_id: str,
    stream_index: int,
    caption_intervals: Sequence[HalfOpenInterval] = (),
    command: Sequence[str] | None = None,
    timeout_seconds: float = DEFAULT_VAD_TIMEOUT_SECONDS,
) -> IsolatedVadResult:
    """Run the real silero VAD in its own child and return report evidence + peak.

    The whole ``analyze_derivative_vad`` sequence runs in the child
    (:mod:`video_content_pipeline.vad_child`) so the pinned asset is loaded in a
    fresh process whose peak is honest; a malformed child response is a typed
    ``vad_output_invalid`` failure, and subprocess crashes/timeouts surface as
    :class:`~video_content_pipeline.model_runtime.ModelRuntimeError`.
    """

    request = EngineRequest(
        model_path=str(project_root),
        task={
            "wav_path": str(wav_path),
            "mapping": mapping.as_json(),
            "source_id": source_id,
            "stream_index": stream_index,
            "caption_intervals": [interval_as_json(interval) for interval in caption_intervals],
        },
    )
    result = run_engine_subprocess(
        list(command) if command is not None else default_vad_command(),
        request,
        timeout_seconds=timeout_seconds,
    )
    return _parse_isolated_vad_result(result.result, result.peak_memory_bytes)


def _parse_isolated_vad_result(
    result: Mapping[str, object], peak_memory_bytes: int
) -> IsolatedVadResult:
    part_evidence = result.get("part_evidence")
    speech_runs = result.get("speech_runs_samples")
    model_asset_sha256 = result.get("model_asset_sha256")
    calibrated = result.get("calibrated")
    if (
        not isinstance(part_evidence, Mapping)
        or not isinstance(speech_runs, list)
        or not isinstance(model_asset_sha256, str)
        or not isinstance(calibrated, bool)
    ):
        raise VadEngineError(
            "vad_output_invalid", "The VAD child response is missing required fields."
        )
    parsed_runs: list[tuple[int, int]] = []
    for run in speech_runs:
        if (
            not isinstance(run, list)
            or len(run) != 2
            or not all(isinstance(bound, int) and not isinstance(bound, bool) for bound in run)
        ):
            raise VadEngineError(
                "vad_output_invalid", "A VAD child speech-run span is malformed."
            )
        parsed_runs.append((run[0], run[1]))
    return IsolatedVadResult(
        part_evidence=part_evidence,
        speech_runs_samples=tuple(parsed_runs),
        model_asset_sha256=model_asset_sha256,
        calibrated=calibrated,
        peak_memory_bytes=peak_memory_bytes,
    )
