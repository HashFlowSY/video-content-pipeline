"""Real ASR engines behind the Phase 7 transcription contracts (Phase 11 ticket 09).

Ticket 09 wires the two provider-neutral ASR capabilities to their real models,
each through its own Model runtime subprocess (ADR 0055 -- both are MLX-scale, so
unified memory is returned to the OS between stages on a 16 GiB machine):

* **asr_primary** -- ``Qwen3-ASR-1.7B-8bit`` via mlx-audio, run over the shared
  ticket-06 VAD chunk stream: one subprocess per <=5-minute chunk transcribes that
  chunk's speech window, and the per-chunk transcripts assemble into a monotonic,
  coverage-consistent :class:`~video_content_pipeline.transcription_contracts.ProjectedAsrCue`
  sequence on the authoritative source timeline (:func:`transcribe_derivative`).
* **asr_review** -- ``whisper-large-v3-mlx`` via mlx-whisper, run *only* over given
  suspicious intervals from VAD-trimmed audio (:func:`review_suspicious_intervals`),
  the anti-hallucination measure the research recorded. Its output is independent
  evidence for the existing Deterministic transcription arbitration, never
  automatic truth; a review whose asset identity equals the primary's is a
  same-model recovery, never independent review (proven with the real registry
  identities against the unchanged ``classify_review_attempt``).

Both engines verify their pinned model asset from disk before the child ever loads
it -- a missing or tampered asset is a typed :class:`AsrEngineError`, never a
network attempt (the child forces the hub-offline guards and only opens local
files). The real engines slot *beside* -- never replace -- the Controlled offline
ASR adapter that Phase 7's pytest gate uses (ADR 0037); the existing suspicion
detection, canonical-timeline gate, and arbitration are unchanged consumers of the
real output shapes, so they keep their tests and their meaning. Chunk assembly,
interval-window derivation, and result parsing are pure and model-free (unit-tested
without mlx, and the subprocess protocol against a stub executable); only the child
modules touch a model.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from video_content_pipeline.audio_derivation import DerivativeTimeMapping
from video_content_pipeline.capabilities import load_registry_document
from video_content_pipeline.model_acquisition import (
    AssetVerificationError,
    verify_acquired_asset,
)
from video_content_pipeline.model_runtime import EngineRequest, run_engine_subprocess
from video_content_pipeline.timecode import HalfOpenInterval
from video_content_pipeline.transcription_contracts import ProjectedAsrCue
from video_content_pipeline.vad_chunking import SpeechChunk

#: The provider-neutral primary/review capabilities and their acquired candidates.
CAPABILITY_PRIMARY = "asr_primary"
CANDIDATE_ID_PRIMARY = "qwen3-asr-1-7b"
CAPABILITY_REVIEW = "asr_review"
CANDIDATE_ID_REVIEW = "whisper-large-v3"

#: Both ASR engines consume 16 kHz mono audio (the analysis-audio derivative).
ASR_SAMPLE_RATE = 16000

#: The child modules that load a model and run inference in the subprocess.
PRIMARY_CHILD_MODULE = "video_content_pipeline.asr_primary_child"
REVIEW_CHILD_MODULE = "video_content_pipeline.asr_review_child"

#: A generous per-stage budget: a large MLX model load plus one inference pass over
#: a chunk (primary) or a trimmed suspicious interval (review).
DEFAULT_TIMEOUT_SECONDS = 600.0


class AsrEngineError(ValueError):
    """A rejected real-ASR precondition with a machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def default_primary_command() -> list[str]:
    """The production primary child argv: this interpreter running the primary module."""

    return [sys.executable, "-m", PRIMARY_CHILD_MODULE]


def default_review_command() -> list[str]:
    """The production review child argv: this interpreter running the review module."""

    return [sys.executable, "-m", REVIEW_CHILD_MODULE]


# --- asset loading (typed acquisition failure, never network) -----------------


def resolve_asr_candidate(
    project_root: Path, capability: str, candidate_id: str
) -> Mapping[str, object]:
    """Return the acquired ASR candidate for ``capability`` from the model registry."""

    registry_path = project_root / "models" / "registry.json"
    registry = load_registry_document(
        registry_path,
        invalid_error=lambda message: AsrEngineError("asr_asset_unavailable", message),
    )
    candidates = registry.get("candidates")
    if isinstance(candidates, list):
        for candidate in candidates:
            if (
                isinstance(candidate, Mapping)
                and candidate.get("candidate_id") == candidate_id
                and candidate.get("capability") == capability
            ):
                return candidate
    raise AsrEngineError(
        "asr_candidate_absent", f"The registry has no acquired {capability} candidate."
    )


def resolve_primary_candidate(project_root: Path) -> Mapping[str, object]:
    """Return the ``qwen3-asr-1-7b`` (asr_primary) candidate from the model registry."""

    return resolve_asr_candidate(project_root, CAPABILITY_PRIMARY, CANDIDATE_ID_PRIMARY)


def resolve_review_candidate(project_root: Path) -> Mapping[str, object]:
    """Return the ``whisper-large-v3`` (asr_review) candidate from the model registry."""

    return resolve_asr_candidate(project_root, CAPABILITY_REVIEW, CANDIDATE_ID_REVIEW)


def load_asr_asset(project_root: Path, candidate: Mapping[str, object]) -> tuple[Path, str]:
    """Verify a pinned ASR asset tree from disk and return ``(model_dir, asset_sha256)``.

    Re-hashes the whole vendored model tree against the registry manifest before
    the child ever loads it. A missing directory, a drifted file, or a mismatched
    manifest raises :class:`AsrEngineError` (``asr_asset_unavailable`` /
    ``asr_asset_mismatch``); the model is never fetched -- verification touches only
    local files, and the child that loads ``model_dir`` runs under the hub-offline
    guards.
    """

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
        raise AsrEngineError("asr_asset_unavailable", "The ASR registry entry is incomplete.")
    asset_root = (project_root / local_path).resolve()
    if not asset_root.is_dir():
        raise AsrEngineError("asr_asset_unavailable", f"The ASR asset tree is absent: {asset_root}")
    try:
        verify_acquired_asset(manifest, asset_sha256, asset_root)
    except AssetVerificationError as error:
        raise AsrEngineError("asr_asset_mismatch", str(error)) from error
    return asset_root, asset_sha256


def load_primary_asset(project_root: Path) -> tuple[Path, str]:
    """Verify the pinned Qwen3-ASR primary asset and return ``(model_dir, asset_sha256)``."""

    return load_asr_asset(project_root, resolve_primary_candidate(project_root))


def load_review_asset(project_root: Path) -> tuple[Path, str]:
    """Verify the pinned whisper review asset and return ``(model_dir, asset_sha256)``."""

    return load_asr_asset(project_root, resolve_review_candidate(project_root))


# --- primary: pure chunk-to-cue assembly --------------------------------------


@dataclass(frozen=True)
class PrimaryTranscriptionResult:
    """The real primary engine's output: assembled cues plus peak-memory evidence.

    ``cues`` is the monotonic, coverage-consistent transcript on the authoritative
    source timeline (one cue per speech chunk that produced visible text), ready for
    the unchanged canonical-timeline gate. ``peak_memory_bytes`` is the maximum
    measured child peak across the per-chunk subprocess runs, with
    ``chunk_peak_memory_bytes`` the per-run evidence.
    """

    source_id: str
    stream_index: int
    language: str
    cues: tuple[ProjectedAsrCue, ...]
    model_asset_sha256: str
    peak_memory_bytes: int
    chunk_peak_memory_bytes: tuple[int, ...]


def assemble_transcript_cues(
    chunk_transcripts: Sequence[tuple[SpeechChunk, str]],
) -> tuple[ProjectedAsrCue, ...]:
    """Assemble per-chunk transcripts into a monotonic, coverage-consistent cue list.

    ``chunk_transcripts`` are ``(chunk, text)`` pairs in chunk order (as the VAD
    chunk stream produces them). Each chunk that yielded visible text becomes one
    cue spanning that chunk's source interval, carrying the chunk transcript; a
    chunk whose transcript is empty or whitespace-only (no speech recognized) yields
    no cue. Because the chunks are ordered and separated by non-speech gaps, the
    resulting cues carry strictly advancing ordinals and non-overlapping,
    time-ordered intervals -- the monotonicity the canonical-timeline gate requires.
    The engine produces no per-token confidences or language spans (the Qwen3-ASR
    path emits neither), so ``tokens`` and ``language_spans`` are empty. Pure and
    deterministic.
    """

    cues: list[ProjectedAsrCue] = []
    ordinal = 0
    for chunk, text in chunk_transcripts:
        if not _has_visible_text(text):
            continue
        cues.append(
            ProjectedAsrCue(
                ordinal=ordinal,
                interval=chunk.source_interval,
                text=text,
                tokens=(),
                language_spans=(),
            )
        )
        ordinal += 1
    return tuple(cues)


def _has_visible_text(text: str) -> bool:
    return any(not character.isspace() for character in text)


# --- primary: subprocess round-trip (one call per chunk) ----------------------


def transcribe_chunk(
    model_path: Path,
    wav_path: Path,
    language: str,
    window: tuple[int, int],
    *,
    command: Sequence[str],
    timeout_seconds: float,
) -> tuple[str, int]:
    """Transcribe one chunk's speech window through the primary child; return ``(text, peak)``.

    Serializes the chunk's absolute derivative-sample window (within the shared
    derivative wav) to the Model runtime subprocess, which loads Qwen3-ASR once,
    slices that window out, transcribes it, and returns the chunk transcript plus
    peak-memory evidence. A malformed child response is a typed
    ``asr_output_invalid`` failure; subprocess crashes/timeouts surface as
    :class:`~video_content_pipeline.model_runtime.ModelRuntimeError`.
    """

    request = EngineRequest(
        model_path=str(model_path),
        task={
            "wav_path": str(wav_path),
            "language": language,
            "start_sample": window[0],
            "end_sample": window[1],
        },
    )
    result = run_engine_subprocess(command, request, timeout_seconds=timeout_seconds)
    return _parse_text_result(result.result), result.peak_memory_bytes


def transcribe_derivative(
    project_root: Path,
    wav_path: Path,
    *,
    source_id: str,
    stream_index: int,
    language: str,
    chunks: Sequence[SpeechChunk],
    command: Sequence[str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> PrimaryTranscriptionResult:
    """Run the real primary ASR over one analysis-audio derivative's VAD chunk stream.

    Verifies and loads the pinned Qwen3-ASR asset, then runs one Model runtime
    subprocess per VAD chunk (ADR 0055) to transcribe that chunk's speech window,
    assembling the per-chunk transcripts into the monotonic, coverage-consistent
    :class:`ProjectedAsrCue` transcript on the authoritative source timeline. Each
    :class:`SpeechChunk` carries its own derivative-sample bounds and source-time
    interval, so no time mapping is needed here; the wav must be the 16 kHz mono
    derivative those chunks were derived from and is passed to the child by path.
    """

    model_path, asset_sha256 = load_primary_asset(project_root)
    child_command = list(command) if command is not None else default_primary_command()

    chunk_transcripts: list[tuple[SpeechChunk, str]] = []
    chunk_peaks: list[int] = []
    for chunk in chunks:
        text, peak = transcribe_chunk(
            model_path,
            wav_path,
            language,
            (chunk.start_sample, chunk.end_sample),
            command=child_command,
            timeout_seconds=timeout_seconds,
        )
        chunk_transcripts.append((chunk, text))
        chunk_peaks.append(peak)

    return PrimaryTranscriptionResult(
        source_id=source_id,
        stream_index=stream_index,
        language=language,
        cues=assemble_transcript_cues(chunk_transcripts),
        model_asset_sha256=asset_sha256,
        peak_memory_bytes=max(chunk_peaks) if chunk_peaks else 0,
        chunk_peak_memory_bytes=tuple(chunk_peaks),
    )


# --- review: pure VAD-trimmed interval-window derivation ----------------------


@dataclass(frozen=True)
class IntervalReview:
    """One suspicious interval's independent-review transcript on the source timeline.

    ``text`` is the second model's transcript of that interval's *speech only*: an
    interval trimmed to no speech at all is reviewed as empty text (VAD says the
    interval is silence, the anti-hallucination whole point), never fed to the model.
    """

    interval: HalfOpenInterval
    text: str
    reviewed_with_model: bool


@dataclass(frozen=True)
class ReviewTranscriptionResult:
    """The real review engine's output: per-interval reviews plus peak-memory evidence.

    ``reviews`` carries one entry per suspicious interval, each the independent
    second-model transcript of that interval's VAD-trimmed (speech-only) audio -- the
    review candidate the Deterministic transcription arbitration consumes.
    ``model_asset_sha256`` is the asset identity the Independent-model review
    requirement compares against the primary's. ``interval_peak_memory_bytes`` has
    one entry per interval that actually ran the model (silence-only intervals are
    resolved as empty without a subprocess).
    """

    source_id: str
    stream_index: int
    language: str
    reviews: tuple[IntervalReview, ...]
    model_asset_sha256: str
    peak_memory_bytes: int
    interval_peak_memory_bytes: tuple[int, ...]


def trim_interval_to_speech(
    interval: HalfOpenInterval,
    speech_intervals: Sequence[HalfOpenInterval],
    mapping: DerivativeTimeMapping,
) -> tuple[tuple[int, int], ...]:
    """The speech-only derivative-sample windows within a suspicious interval.

    Each VAD speech region is intersected with the suspicious interval and mapped to
    a clamped, positive ``[start_sample, end_sample)`` derivative window; the
    silence *between and around* those regions is dropped. Feeding whisper only these
    speech windows is the recorded anti-hallucination measure -- whisper hallucinates
    over silence, so silence never reaches it. Returns the windows in source order,
    or an empty tuple when the interval overlaps no speech (it is entirely silence).
    Pure and deterministic.
    """

    windows: list[tuple[int, int]] = []
    for speech in speech_intervals:
        overlap = _intersection(interval, speech)
        if overlap is None:
            continue
        start = _clamp_sample(mapping.sample_for_source_time(overlap.start), mapping)
        end = _clamp_sample(mapping.sample_for_source_time(overlap.end), mapping)
        if end > start:
            windows.append((start, end))
    return tuple(sorted(windows))


def _intersection(left: HalfOpenInterval, right: HalfOpenInterval) -> HalfOpenInterval | None:
    start = left.start if left.start >= right.start else right.start
    end = left.end if left.end <= right.end else right.end
    if end <= start:
        return None
    return HalfOpenInterval(start, end)


def _clamp_sample(sample: int, mapping: DerivativeTimeMapping) -> int:
    return max(0, min(sample, mapping.sample_count))


# --- review: subprocess round-trip (one call per interval) --------------------


def review_windows(
    model_path: Path,
    wav_path: Path,
    language: str,
    windows: Sequence[tuple[int, int]],
    *,
    command: Sequence[str],
    timeout_seconds: float,
) -> tuple[str, int]:
    """Review one interval's VAD-trimmed speech windows; return ``(text, peak)``.

    Serializes the interval's speech-only derivative-sample windows to the Model
    runtime subprocess, which loads whisper-large-v3 once, slices and concatenates
    those windows out of the shared derivative wav (dropping the interval's silence),
    transcribes the concatenation, and returns the interval transcript plus
    peak-memory evidence. A malformed child response is a typed ``asr_output_invalid``
    failure; subprocess crashes/timeouts surface as :class:`ModelRuntimeError`.
    """

    request = EngineRequest(
        model_path=str(model_path),
        task={
            "wav_path": str(wav_path),
            "language": language,
            "windows": [[start, end] for start, end in windows],
        },
    )
    result = run_engine_subprocess(command, request, timeout_seconds=timeout_seconds)
    return _parse_text_result(result.result), result.peak_memory_bytes


def review_suspicious_intervals(
    project_root: Path,
    wav_path: Path,
    mapping: DerivativeTimeMapping,
    *,
    source_id: str,
    stream_index: int,
    language: str,
    intervals: Sequence[HalfOpenInterval],
    speech_intervals: Sequence[HalfOpenInterval],
    command: Sequence[str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> ReviewTranscriptionResult:
    """Run the real review ASR over the given suspicious intervals, VAD-trimmed.

    Verifies and loads the pinned whisper asset, then reviews each suspicious
    interval over its *speech-only* audio: ``speech_intervals`` (the VAD speech-likely
    partition in source time) trims each interval to its speech windows, and one
    Model runtime subprocess per interval with speech (ADR 0055) transcribes just
    those windows. An interval that overlaps no speech is resolved as empty text
    without running the model -- the VAD-trimmed input is empty, so the review
    transcript is empty (the anti-hallucination measure). The result carries the
    review's asset identity so the caller can classify the attempt against the
    primary's identity (the Independent-model review requirement). The wav must be
    the 16 kHz mono derivative ``mapping`` describes.
    """

    model_path, asset_sha256 = load_review_asset(project_root)
    child_command = list(command) if command is not None else default_review_command()

    reviews: list[IntervalReview] = []
    interval_peaks: list[int] = []
    for interval in intervals:
        windows = trim_interval_to_speech(interval, speech_intervals, mapping)
        if not windows:
            # No speech in the interval: the VAD-trimmed input is empty, so the
            # review is empty without ever feeding silence to the model.
            reviews.append(IntervalReview(interval=interval, text="", reviewed_with_model=False))
            continue
        text, peak = review_windows(
            model_path,
            wav_path,
            language,
            windows,
            command=child_command,
            timeout_seconds=timeout_seconds,
        )
        reviews.append(IntervalReview(interval=interval, text=text, reviewed_with_model=True))
        interval_peaks.append(peak)

    return ReviewTranscriptionResult(
        source_id=source_id,
        stream_index=stream_index,
        language=language,
        reviews=tuple(reviews),
        model_asset_sha256=asset_sha256,
        peak_memory_bytes=max(interval_peaks) if interval_peaks else 0,
        interval_peak_memory_bytes=tuple(interval_peaks),
    )


# --- shared child-response parsing --------------------------------------------


def _parse_text_result(result: Mapping[str, object]) -> str:
    text = result.get("text")
    if not isinstance(text, str):
        raise AsrEngineError(
            "asr_output_invalid", "The ASR child response is missing a 'text' string."
        )
    return text
