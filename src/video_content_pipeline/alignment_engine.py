"""Real Qwen3-ForcedAligner engine behind the Phase 5 alignment contracts.

Phase 11 ticket 08 wires the forced_alignment capability to its real model.
Qwen3-ForcedAligner-0.6B-8bit is MLX-scale, so per ADR 0055 it runs in its *own*
Model runtime subprocess (ticket 05) through mlx-audio -- one subprocess per
VAD-derived <=5-minute chunk (ticket 06), so unified memory is returned to the OS
between chunks on a 16 GiB machine. The engine:

* verifies the pinned model asset from disk before the child ever loads it
  (:func:`load_aligner_asset`) -- a missing or tampered asset is a typed
  acquisition failure, never a network attempt (the child forces the hub-offline
  guards and only opens local files);
* refines each Primary subtitle cue's timing by aligning the cue text against a
  padded audio window around its original interval, projecting the aligner's
  word/char ``{text, start, end}`` output into the existing per-cue
  :class:`~video_content_pipeline.audio_analysis.AlignmentProposal` contract on
  the authoritative source timeline;
* with a valid, model-matched *alignment calibration profile* (ADR 0027), drives
  the unchanged Adopted alignment timing view
  (:func:`~video_content_pipeline.audio_analysis.derive_adopted_alignment_timing_view`);
  without a profile it produces retained candidate proposals but no adopted view.

The aligner performs a single non-autoregressive forward pass emitting decided
word/char timestamps with no soft score, so a placed cue carries confidence
``1.0`` and the operative adoption gate is the profile's language-aware
duration-plausibility rule plus the usable-audio and VAD-conflict checks (the
low-confidence non-override rule still keeps the original cue time for any cue the
engine could not place). The real engine slots *beside* -- never replaces -- the
controlled offline adapter that Phase 5's pytest gate uses (ADR 0037); it is
exercised by the offline integration test and the maintainer-invoked prototype,
not by ``analyze_audio``. Cue-to-chunk assignment, window derivation, and the
item-to-proposal projection are pure and model-free, so they are unit-tested
without mlx-audio and the subprocess protocol against a stub executable; only the
child touches the model.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from video_content_pipeline.audio_analysis import (
    AdoptedAlignmentTimingView,
    AlignmentCue,
    AlignmentProposal,
    ProjectedAlignmentPart,
    VoiceActivityInterval,
    derive_adopted_alignment_timing_view,
)
from video_content_pipeline.audio_derivation import DerivativeTimeMapping
from video_content_pipeline.capabilities import load_registry_document
from video_content_pipeline.model_acquisition import (
    AssetVerificationError,
    verify_acquired_asset,
)
from video_content_pipeline.model_runtime import EngineRequest, run_engine_subprocess
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
from video_content_pipeline.vad_chunking import SpeechChunk

CAPABILITY = "forced_alignment"
CANDIDATE_ID = "qwen3-forced-aligner-0-6b"

#: The Qwen3-ForcedAligner consumes 16 kHz mono audio.
ALIGNER_SAMPLE_RATE = 16000

#: A single non-autoregressive forward pass emits decided word/char timestamps
#: with no soft score; a placed cue is represented at full confidence (see the
#: calibration profile's notes on the reserved confidence gate).
DECIDED_ALIGNMENT_CONFIDENCE = 1.0
#: A cue the engine could not place (no audio window, or a degenerate result)
#: keeps its original subtitle time: a zero-confidence proposal the Adopted
#: alignment timing view never adopts (the low-confidence non-override rule).
UNPLACED_ALIGNMENT_CONFIDENCE = 0.0

#: The fallback audio breathing room around a cue when no profile pins one.
DEFAULT_WINDOW_PAD_SAMPLES = 8000

#: The child module that loads the model and runs alignment in the subprocess.
ALIGNER_CHILD_MODULE = "video_content_pipeline.alignment_engine_child"
#: A generous per-chunk budget: model load plus one batched alignment pass.
DEFAULT_TIMEOUT_SECONDS = 300.0

_CALIBRATION_PATH = Path("config") / "audio-analysis" / "qwen3-aligner-calibration.json"


class AlignmentEngineError(ValueError):
    """A rejected real-alignment precondition with a machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def default_aligner_command() -> list[str]:
    """The production child argv: this interpreter running the aligner child module."""

    return [sys.executable, "-m", ALIGNER_CHILD_MODULE]


@dataclass(frozen=True)
class Qwen3AlignerCalibration:
    """A model-specific forced-alignment calibration profile (ADR 0027).

    ``model_asset_sha256`` binds the profile to the exact acquired asset;
    ``backend`` / ``backend_version`` / ``precision`` / ``device_class`` /
    ``rules_fingerprint`` complete the bound identity (a change to any invalidates
    the profile for adoption). ``window_pad_samples`` is the audio breathing room
    the engine adds around a cue's original interval, ``minimum_confidence`` the
    adoption confidence gate, and ``duration_rules`` the versioned, language-aware
    minimum/maximum cue durations (ADR 0027 forbids a global count-based
    substitute). Without such a profile the engine keeps raw candidate proposals
    but publishes no adopted timing view.
    """

    calibration_version: str
    model_asset_sha256: str
    backend: str
    backend_version: str
    precision: str
    device_class: str
    rules_fingerprint: str
    sample_rate: int
    window_pad_samples: int
    minimum_confidence: float
    duration_rules: Mapping[str, tuple[ExactTime, ExactTime]]

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise AlignmentEngineError(
                "alignment_calibration_invalid", "Calibration sample rate must be positive."
            )
        if self.window_pad_samples < 0:
            raise AlignmentEngineError(
                "alignment_calibration_invalid", "window_pad_samples must be non-negative."
            )
        if not 0 <= self.minimum_confidence <= 1:
            raise AlignmentEngineError(
                "alignment_calibration_invalid", "minimum_confidence must lie in [0, 1]."
            )
        if not self.duration_rules:
            raise AlignmentEngineError(
                "alignment_calibration_invalid", "At least one language duration rule is required."
            )
        for minimum, maximum in self.duration_rules.values():
            if minimum <= ExactTime(0) or maximum < minimum:
                raise AlignmentEngineError(
                    "alignment_calibration_invalid", "A duration rule is out of range."
                )

    @classmethod
    def from_json(cls, decoded: object) -> Qwen3AlignerCalibration:
        """Parse and validate a calibration profile from its JSON document."""

        if not isinstance(decoded, Mapping) or decoded.get("schema_version") != 1:
            raise AlignmentEngineError(
                "alignment_calibration_invalid", "Calibration schema is invalid."
            )
        identity = decoded.get("model_identity")
        window = decoded.get("window")
        thresholds = decoded.get("thresholds")
        if (
            not isinstance(identity, Mapping)
            or not isinstance(window, Mapping)
            or not isinstance(thresholds, Mapping)
        ):
            raise AlignmentEngineError(
                "alignment_calibration_invalid", "Calibration fields are missing."
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
                sample_rate=_positive_int(decoded, "sample_rate"),
                window_pad_samples=_non_negative_int(window, "window_pad_samples"),
                minimum_confidence=_unit_float(thresholds, "minimum_confidence"),
                duration_rules=_duration_rules(thresholds.get("duration_rules")),
            )
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, AlignmentEngineError):
                raise
            raise AlignmentEngineError(
                "alignment_calibration_invalid", "Calibration fields are invalid."
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
            "sample_rate": self.sample_rate,
            "window": {"window_pad_samples": self.window_pad_samples},
            "thresholds": {
                "minimum_confidence": self.minimum_confidence,
                "duration_rules": {
                    language: {
                        "minimum_duration": _exact_time_as_json(minimum),
                        "maximum_duration": _exact_time_as_json(maximum),
                    }
                    for language, (minimum, maximum) in self.duration_rules.items()
                },
            },
        }


@dataclass(frozen=True)
class Qwen3AlignmentResult:
    """The real engine's output: raw candidate proposals and, if calibrated, a view.

    ``projected`` is always produced (retained candidate proposals, one per source
    cue). ``adopted_view`` is the Adopted alignment timing view, present only when
    a model-matched calibration profile drove it (ADR 0027). ``peak_memory_bytes``
    is the maximum measured child peak across the per-chunk subprocess runs, with
    ``chunk_peak_memory_bytes`` the per-run evidence.
    """

    source_id: str
    stream_index: int
    language: str
    projected: ProjectedAlignmentPart
    adopted_view: AdoptedAlignmentTimingView | None
    model_asset_sha256: str
    calibrated: bool
    peak_memory_bytes: int
    chunk_peak_memory_bytes: tuple[int, ...]


# --- calibration (ADR 0027 gate) ----------------------------------------------


def load_alignment_calibration(
    project_root: Path, *, expected_asset_sha256: str | None = None
) -> Qwen3AlignerCalibration:
    """Read and gate-check the forced-alignment calibration profile (ADR 0027).

    Validates the profile's schema and ranges and, when ``expected_asset_sha256``
    is given, that it was calibrated for that exact asset. Raises
    :class:`AlignmentEngineError` (``alignment_calibration_invalid`` or
    ``alignment_calibration_model_mismatch``) otherwise; a rejected profile means
    the caller may keep only raw candidate proposals.
    """

    path = project_root / _CALIBRATION_PATH
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AlignmentEngineError(
            "alignment_calibration_invalid", "The alignment calibration profile cannot be read."
        ) from error
    calibration = Qwen3AlignerCalibration.from_json(decoded)
    if calibration.sample_rate != ALIGNER_SAMPLE_RATE:
        raise AlignmentEngineError(
            "alignment_calibration_invalid",
            "Calibration must target the 16 kHz aligner configuration.",
        )
    if (
        expected_asset_sha256 is not None
        and calibration.model_asset_sha256 != expected_asset_sha256
    ):
        raise AlignmentEngineError(
            "alignment_calibration_model_mismatch",
            "The calibration profile was produced for a different model asset.",
        )
    return calibration


# --- asset loading (typed acquisition failure, never network) -----------------


def resolve_aligner_candidate(project_root: Path) -> Mapping[str, object]:
    """Return the ``qwen3-forced-aligner-0-6b`` candidate from the model registry."""

    registry_path = project_root / "models" / "registry.json"
    registry = load_registry_document(
        registry_path,
        invalid_error=lambda message: AlignmentEngineError("alignment_asset_unavailable", message),
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
    raise AlignmentEngineError(
        "alignment_candidate_absent", "The registry has no acquired forced-alignment candidate."
    )


def load_aligner_asset(
    project_root: Path, candidate: Mapping[str, object] | None = None
) -> tuple[Path, str]:
    """Verify the pinned aligner asset from disk and return ``(model_dir, asset_sha256)``.

    Re-hashes the whole vendored model tree against the registry manifest before
    the child ever loads it. A missing directory, a drifted file, or a mismatched
    manifest raises :class:`AlignmentEngineError`
    (``alignment_asset_unavailable`` / ``alignment_asset_mismatch``); the model is
    never fetched -- verification touches only local files, and the child that
    loads ``model_dir`` runs under the hub-offline guards.
    """

    if candidate is None:
        candidate = resolve_aligner_candidate(project_root)
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
        raise AlignmentEngineError(
            "alignment_asset_unavailable", "The forced-aligner registry entry is incomplete."
        )
    asset_root = (project_root / local_path).resolve()
    if not asset_root.is_dir():
        raise AlignmentEngineError(
            "alignment_asset_unavailable", f"The aligner asset tree is absent: {asset_root}"
        )
    try:
        verify_acquired_asset(manifest, asset_sha256, asset_root)
    except AssetVerificationError as error:
        raise AlignmentEngineError("alignment_asset_mismatch", str(error)) from error
    return asset_root, asset_sha256


# --- pure cue-to-chunk assignment and window derivation -----------------------


def assign_cues_to_chunks(
    source_cues: Sequence[AlignmentCue], chunks: Sequence[SpeechChunk]
) -> dict[int, list[AlignmentCue]]:
    """Group each cue under the first VAD chunk that fully contains it.

    Chunks are ordered and separated by silence. A cue is assigned only to a chunk
    whose source interval fully contains it, so it is aligned against audio that
    holds the whole cue. A cue that straddles a chunk boundary (its subtitle span
    crosses a silence-based cut) or falls outside every chunk is left unassigned --
    absent from the result -- and keeps its original time rather than being aligned
    against a truncated fragment. Pure and deterministic.
    """

    by_chunk: dict[int, list[AlignmentCue]] = {}
    for cue in source_cues:
        for chunk in chunks:
            if _chunk_contains_cue(chunk, cue):
                by_chunk.setdefault(chunk.chunk_index, []).append(cue)
                break
    return by_chunk


def _chunk_contains_cue(chunk: SpeechChunk, cue: AlignmentCue) -> bool:
    return (
        chunk.source_interval.start <= cue.interval.start
        and cue.interval.end <= chunk.source_interval.end
    )


def cue_window_samples(
    cue: AlignmentCue,
    chunk: SpeechChunk,
    mapping: DerivativeTimeMapping,
    pad_samples: int,
) -> tuple[int, int] | None:
    """The padded derivative-sample window a cue is aligned within, or ``None``.

    The cue's original source interval is mapped to derivative samples, padded by
    ``pad_samples`` on each side, and clamped to the containing chunk's sample
    bounds. Returns ``None`` when the clamped window is empty (nothing to align).
    """

    start = mapping.sample_for_source_time(cue.interval.start) - pad_samples
    end = mapping.sample_for_source_time(cue.interval.end) + pad_samples
    start = max(chunk.start_sample, start)
    end = min(chunk.end_sample, end)
    if end <= start:
        return None
    return start, end


def _clamp_sample(sample: int, mapping: DerivativeTimeMapping) -> int:
    return max(0, min(sample, mapping.sample_count))


# --- pure item-to-proposal projection -----------------------------------------


def proposal_from_alignment_items(
    cue: AlignmentCue,
    window_start_sample: int,
    items: Sequence[Mapping[str, object]],
    mapping: DerivativeTimeMapping,
    confidence: float,
) -> AlignmentProposal | None:
    """Project a cue's window-local word/char items into a source-time proposal.

    Each item's ``start`` / ``end`` seconds are relative to the cue's audio window;
    the cue's proposed interval spans the earliest item start to the latest item
    end, mapped from ``window_start_sample`` onto the authoritative source timeline.
    The proposal carries the *cue's own text* (the aligner refines times, never
    text). Returns ``None`` when there are no usable items or the span is
    degenerate, so the caller keeps the cue's original time.
    """

    starts: list[float] = []
    ends: list[float] = []
    for item in items:
        start = item.get("start")
        end = item.get("end")
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int | float)
            or not isinstance(end, int | float)
            or start < 0
            or end <= start
        ):
            continue
        starts.append(float(start))
        ends.append(float(end))
    if not starts:
        return None
    start_sample = _clamp_sample(
        window_start_sample + round(min(starts) * mapping.sample_rate), mapping
    )
    end_sample = _clamp_sample(
        window_start_sample + round(max(ends) * mapping.sample_rate), mapping
    )
    if end_sample <= start_sample:
        return None
    interval = mapping.source_interval_for_samples(start_sample, end_sample)
    return AlignmentProposal(cue.source_ordinal, cue.text, interval, confidence)


def unplaced_proposal(cue: AlignmentCue) -> AlignmentProposal:
    """A zero-confidence proposal that keeps a cue's original time (non-override)."""

    return AlignmentProposal(
        cue.source_ordinal, cue.text, cue.interval, UNPLACED_ALIGNMENT_CONFIDENCE
    )


def project_alignment_part(
    language: str,
    source_cues: Sequence[AlignmentCue],
    placed_by_ordinal: Mapping[int, AlignmentProposal],
) -> ProjectedAlignmentPart:
    """Assemble one proposal per source cue in cue order (placed or unplaced).

    Every source cue is represented exactly once, so the projection satisfies the
    Model-output projection contract's cue-identity requirement; a cue absent from
    ``placed_by_ordinal`` gets its original-time zero-confidence proposal.
    """

    proposals = tuple(
        placed_by_ordinal.get(cue.source_ordinal) or unplaced_proposal(cue) for cue in source_cues
    )
    return ProjectedAlignmentPart(language, proposals)


# --- subprocess round-trip (one call per chunk) -------------------------------


def align_chunk(
    model_path: Path,
    wav_path: Path,
    language: str,
    windowed_cues: Sequence[tuple[AlignmentCue, tuple[int, int]]],
    *,
    command: Sequence[str],
    timeout_seconds: float,
) -> tuple[dict[int, Sequence[Mapping[str, object]]], int]:
    """Run one chunk's cues through the aligner child and return items per ordinal.

    Serializes each cue's window (absolute derivative samples within the shared
    derivative wav) to the Model runtime subprocess, which loads the model once,
    slices and batch-aligns the windows, and returns word/char items per cue plus
    peak-memory evidence. Returns ``(items_by_ordinal, peak_memory_bytes)``. A
    malformed child response is a typed ``alignment_output_invalid`` failure;
    subprocess crashes/timeouts surface as :class:`ModelRuntimeError`.
    """

    request = EngineRequest(
        model_path=str(model_path),
        task={
            "wav_path": str(wav_path),
            "language": language,
            "cues": [
                {
                    "source_ordinal": cue.source_ordinal,
                    "text": cue.text,
                    "start_sample": window[0],
                    "end_sample": window[1],
                }
                for cue, window in windowed_cues
            ],
        },
    )
    result = run_engine_subprocess(command, request, timeout_seconds=timeout_seconds)
    return _parse_chunk_result(result.result), result.peak_memory_bytes


def _parse_chunk_result(
    result: Mapping[str, object],
) -> dict[int, Sequence[Mapping[str, object]]]:
    cues = result.get("cues")
    if not isinstance(cues, list):
        raise AlignmentEngineError(
            "alignment_output_invalid", "The aligner child response is missing 'cues'."
        )
    items_by_ordinal: dict[int, Sequence[Mapping[str, object]]] = {}
    for entry in cues:
        ordinal = entry.get("source_ordinal") if isinstance(entry, Mapping) else None
        items = entry.get("items") if isinstance(entry, Mapping) else None
        if not isinstance(ordinal, int) or isinstance(ordinal, bool) or not isinstance(items, list):
            raise AlignmentEngineError(
                "alignment_output_invalid", "An aligner child cue result is malformed."
            )
        items_by_ordinal[ordinal] = items
    return items_by_ordinal


# --- top-level real analysis --------------------------------------------------


def analyze_derivative_alignment(
    project_root: Path,
    wav_path: Path,
    mapping: DerivativeTimeMapping,
    *,
    source_id: str,
    stream_index: int,
    language: str,
    source_cues: Sequence[AlignmentCue],
    chunks: Sequence[SpeechChunk],
    usable_audio_intervals: tuple[HalfOpenInterval, ...] | None = None,
    voice_activity_intervals: tuple[VoiceActivityInterval, ...] = (),
    command: Sequence[str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Qwen3AlignmentResult:
    """Run the real Qwen3-ForcedAligner over one analysis-audio derivative.

    Verifies and loads the pinned asset, assigns each Primary subtitle cue to the
    VAD chunk it overlaps, and runs one Model runtime subprocess per chunk
    (ADR 0055) to refine those cues' times, projecting the aligner output into the
    per-cue :class:`AlignmentProposal` contract. If a model-matched calibration
    profile exists (ADR 0027), also drives the unchanged Adopted alignment timing
    view against ``usable_audio_intervals`` (defaulting to the whole derivative
    coverage) and ``voice_activity_intervals`` (the real VAD partition from ticket
    06); without a profile no adopted view is produced. The wav must be the 16 kHz
    mono derivative ``mapping`` describes and is passed to the child by path.
    """

    model_path, asset_sha256 = load_aligner_asset(project_root)

    # An absent profile is the legitimate uncalibrated state (ADR 0027); a profile
    # present but invalid or bound to a different asset is a typed failure.
    calibration: Qwen3AlignerCalibration | None = None
    if (project_root / _CALIBRATION_PATH).is_file():
        calibration = load_alignment_calibration(project_root, expected_asset_sha256=asset_sha256)
    pad = calibration.window_pad_samples if calibration is not None else DEFAULT_WINDOW_PAD_SAMPLES
    child_command = list(command) if command is not None else default_aligner_command()

    by_chunk = assign_cues_to_chunks(source_cues, chunks)
    placed_by_ordinal: dict[int, AlignmentProposal] = {}
    chunk_peaks: list[int] = []
    for chunk in chunks:
        windowed_cues = [
            (cue, window)
            for cue in by_chunk.get(chunk.chunk_index, [])
            if (window := cue_window_samples(cue, chunk, mapping, pad)) is not None
        ]
        if not windowed_cues:
            continue
        items_by_ordinal, peak = align_chunk(
            model_path,
            wav_path,
            language,
            windowed_cues,
            command=child_command,
            timeout_seconds=timeout_seconds,
        )
        chunk_peaks.append(peak)
        for cue, window in windowed_cues:
            proposal = proposal_from_alignment_items(
                cue,
                window[0],
                items_by_ordinal.get(cue.source_ordinal, ()),
                mapping,
                DECIDED_ALIGNMENT_CONFIDENCE,
            )
            if proposal is not None:
                placed_by_ordinal[cue.source_ordinal] = proposal

    projected = project_alignment_part(language, source_cues, placed_by_ordinal)

    # ADR 0027: adoption needs a language-aware duration rule for the subtitle
    # language. A calibrated profile that covers other languages but not this one
    # cannot create an Adopted alignment timing view here, so the engine degrades
    # to retained candidate proposals rather than raising -- the same shape as the
    # uncalibrated path.
    adopted_view: AdoptedAlignmentTimingView | None = None
    if calibration is not None and language in calibration.duration_rules:
        coverage = (
            usable_audio_intervals
            if usable_audio_intervals is not None
            else (mapping.source_interval,)
        )
        adopted_view = derive_adopted_alignment_timing_view(
            source_id=source_id,
            language=language,
            source_cues=tuple(source_cues),
            proposals=projected.proposals,
            usable_audio_intervals=coverage,
            voice_activity_intervals=voice_activity_intervals,
            minimum_confidence=calibration.minimum_confidence,
            duration_rules=calibration.duration_rules,
        )

    return Qwen3AlignmentResult(
        source_id=source_id,
        stream_index=stream_index,
        language=language,
        projected=projected,
        adopted_view=adopted_view,
        model_asset_sha256=asset_sha256,
        calibrated=calibration is not None,
        peak_memory_bytes=max(chunk_peaks) if chunk_peaks else 0,
        chunk_peak_memory_bytes=tuple(chunk_peaks),
    )


# --- small validators ---------------------------------------------------------


def _required_str(value: Mapping[str, object], key: str) -> str:
    item = value[key]
    if not isinstance(item, str) or not item:
        raise AlignmentEngineError(
            "alignment_calibration_invalid", f"'{key}' must be a non-empty string."
        )
    return item


def _positive_int(value: Mapping[str, object], key: str) -> int:
    item = value[key]
    if not isinstance(item, int) or isinstance(item, bool) or item <= 0:
        raise AlignmentEngineError(
            "alignment_calibration_invalid", f"'{key}' must be a positive integer."
        )
    return item


def _non_negative_int(value: Mapping[str, object], key: str) -> int:
    item = value[key]
    if not isinstance(item, int) or isinstance(item, bool) or item < 0:
        raise AlignmentEngineError(
            "alignment_calibration_invalid", f"'{key}' must be a non-negative integer."
        )
    return item


def _unit_float(value: Mapping[str, object], key: str) -> float:
    item = value[key]
    if isinstance(item, bool) or not isinstance(item, int | float) or not 0 <= item <= 1:
        raise AlignmentEngineError("alignment_calibration_invalid", f"'{key}' must lie in [0, 1].")
    return float(item)


def _duration_rules(value: object) -> dict[str, tuple[ExactTime, ExactTime]]:
    if not isinstance(value, Mapping) or not value:
        raise AlignmentEngineError(
            "alignment_calibration_invalid", "duration_rules must be a non-empty object."
        )
    rules: dict[str, tuple[ExactTime, ExactTime]] = {}
    for language, rule in value.items():
        if not isinstance(language, str) or not language or not isinstance(rule, Mapping):
            raise AlignmentEngineError(
                "alignment_calibration_invalid", "A duration rule entry is malformed."
            )
        rules[language] = (
            _exact_time(rule.get("minimum_duration")),
            _exact_time(rule.get("maximum_duration")),
        )
    return rules


def _exact_time(value: object) -> ExactTime:
    if not isinstance(value, Mapping):
        raise AlignmentEngineError("alignment_calibration_invalid", "A duration must be an object.")
    numerator = value.get("numerator")
    denominator = value.get("denominator")
    if (
        not isinstance(numerator, int)
        or isinstance(numerator, bool)
        or not isinstance(denominator, int)
        or isinstance(denominator, bool)
        or denominator == 0
    ):
        raise AlignmentEngineError(
            "alignment_calibration_invalid", "A duration needs integer numerator/denominator."
        )
    return ExactTime(numerator, denominator)


def _exact_time_as_json(value: ExactTime) -> dict[str, int]:
    return {"numerator": value.numerator, "denominator": value.denominator}
