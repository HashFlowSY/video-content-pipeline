"""Maintainer-invoked heavy runs for the Phase 11 ticket 13 capability prototypes.

This is the reproducible ``vcp-prototype`` driver: it derives the analysis audio
and frames from a ticket-12 source, runs each real engine on the provisioned
machine (offline, from the pinned registry assets), measures the device baseline
(real-time factor and peak memory), runs the engineering checks, and writes a
retained :class:`~video_content_pipeline.prototype.PrototypeRecord` plus a short
zh/en sample for maintainer eyeball. Real-model quality is never asserted here --
it is the maintainer's review; this driver produces the evidence that review
reads. The heavy engine imports are deferred into the run functions so this
module (and its model-free glue tests) import cheaply.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Generic, TypeVar, cast

from video_content_pipeline.audio_derivation import DerivativeTimeMapping
from video_content_pipeline.durable_io import utc_now
from video_content_pipeline.external_tools import PinnedExternalTool, identify_external_tool
from video_content_pipeline.prototype import (
    DEVICE_CLASS,
    AssetIdentity,
    DeviceBaseline,
    EngineeringCheck,
    PrototypeError,
    PrototypeRecord,
    PrototypeTiming,
    envelope_check,
    offline_guard_names,
    render_sample_markdown,
)
from video_content_pipeline.text_generation import LoadedPart
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval

if TYPE_CHECKING:
    from video_content_pipeline.alignment_engine import Qwen3AlignmentResult
    from video_content_pipeline.asr_engine import (
        PrimaryTranscriptionResult,
        ReviewTranscriptionResult,
    )
    from video_content_pipeline.audio_analysis import (
        AlignmentCue,
        VoiceActivityInterval,
    )
    from video_content_pipeline.diarization_engine import SherpaDiarizationResult
    from video_content_pipeline.ocr_engine import OcrEngineResult
    from video_content_pipeline.text_generation import GeneratedSegment
    from video_content_pipeline.text_semantics_engine import Qwen3TextSemanticsResult
    from video_content_pipeline.transcription_contracts import ProjectedAsrCue
    from video_content_pipeline.vad_engine import SileroVadResult

T_co = TypeVar("T_co", covariant=True)

#: The ticket-12 prototype sources and the primary language each represents. The
#: full content-addressed source ids live under ``input/<id>/media`` (git-ignored
#: media; only the tracked prototype-media plan and this map name them).
PROTOTYPE_SOURCES: dict[str, str] = {
    "f6fd0cd7157b3d13502de534ed1d4930cb27f48192b57ce4f3ebcbefa26fd9da": "zh",
    "104eeec2d832d272ad36f9659aeddd5ba8c34ac35bc89f06ec672f3b3d2902fc": "en",
}

#: The 16 kHz mono analysis sample rate every audio engine consumes.
ANALYSIS_SAMPLE_RATE = 16000

#: Retained prototype evidence lives here (tracked, unlike the git-ignored media).
PROTOTYPE_EVIDENCE_DIR = ("docs", "phase-11-prototypes")

#: Device baselines that seed plan estimation are retained alongside the records.
DEVICE_BASELINES_RELPATH = "docs/phase-11-prototypes/device-baselines.json"

SAMPLE_ENTRY_LIMIT = 12
FRAME_FRACTIONS = (
    Fraction(1, 10),
    Fraction(3, 10),
    Fraction(1, 2),
    Fraction(7, 10),
    Fraction(9, 10),
)


def source_language(source_id: str) -> str:
    """Return the prototype source's language, raising ``KeyError`` if unknown."""

    return PROTOTYPE_SOURCES[source_id]


def timestamp_label(value: ExactTime) -> str:
    """Format an exact time as ``MM:SS`` (minutes may exceed 59)."""

    total = int(value.as_fraction())
    return f"{total // 60:02d}:{total % 60:02d}"


def format_transcript_entries(cues: Sequence[tuple[ExactTime, str]], *, limit: int) -> list[str]:
    """Render ``(start, text)`` cue pairs as ``MM:SS text`` sample lines."""

    return [f"{timestamp_label(start)} {text}" for start, text in cues][:limit]


def format_speaker_turn_entries(
    turns: Sequence[tuple[HalfOpenInterval, str]], *, limit: int
) -> list[str]:
    """Render ``(interval, label)`` turn pairs as ``MM:SS–MM:SS label`` lines."""

    return [
        f"{timestamp_label(interval.start)}–{timestamp_label(interval.end)} {label}"
        for interval, label in turns
    ][:limit]


def format_ocr_entries(items: Sequence[tuple[str, float]], *, limit: int) -> list[str]:
    """Render ``(text, confidence)`` OCR items as ``text (0.97)`` sample lines."""

    return [f"{text} ({confidence:.2f})" for text, confidence in items][:limit]


def format_segment_entries(segments: Sequence[GeneratedSegment], *, limit: int) -> list[str]:
    """Render verified semantic segments as ``segment N: <title or cue count>`` lines."""

    entries: list[str] = []
    for segment in segments:
        title = segment.content.title
        label = title.text if title is not None else f"{len(segment.cue_ids)} cited cues"
        entries.append(f"segment {segment.ordinal}: {label}")
    return entries[:limit]


def suspicious_intervals_from_chunks(
    chunks: Sequence[HalfOpenInterval], *, limit: int
) -> tuple[HalfOpenInterval, ...]:
    """Pick the leading speech chunk intervals as the review's suspicious set.

    The prototype has no arbitration signal to source real suspicion from, so it
    exercises the review path (VAD-trim + independent second model) over the
    first few speech chunks -- enough to prove the engine and gate, no more.
    """

    return tuple(chunks[:limit])


def loaded_part_for_cues(part_id: str, track_id: str, cue_count: int) -> LoadedPart:
    """Build a text-analysis Part whose authoritative cue ids index the cues."""

    return LoadedPart(
        part_id=part_id,
        track_id=track_id,
        cue_ids=tuple(f"{part_id}:{ordinal}" for ordinal in range(cue_count)),
    )


def sample_relpath(capability: str, source_id: str, language: str) -> str:
    """The tracked relative path of one capability sample for a source/language."""

    return f"{'/'.join(PROTOTYPE_EVIDENCE_DIR)}/{capability}/{source_id[:8]}-{language}.md"


def _record_relpath(capability: str, source_id: str, language: str) -> str:
    return f"{'/'.join(PROTOTYPE_EVIDENCE_DIR)}/{capability}/{source_id[:8]}-{language}.record.json"


# --- shared prep + measurement -------------------------------------------------


@dataclass(frozen=True)
class Measured(Generic[T_co]):
    """One engine run's typed result and the device baseline it measured."""

    result: T_co
    wall_seconds: Fraction
    peak_memory_bytes: int


def _elapsed_fraction(seconds: float) -> Fraction:
    """Convert measured wall-clock seconds to an exact millisecond fraction."""

    return Fraction(max(1, round(seconds * 1000)), 1000)


def _process_peak_bytes() -> int:
    from video_content_pipeline.model_runtime import process_peak_rss_bytes

    return process_peak_rss_bytes()


def _load_tool(project_root: Path, tool_id: str) -> PinnedExternalTool:
    try:
        decoded = json.loads((project_root / "config" / "tools.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PrototypeError(
            "prototype_tool_registry_invalid", "Tool registry unreadable."
        ) from error
    for tool in decoded.get("tools", []):
        if (
            isinstance(tool, dict)
            and tool.get("id") == tool_id
            and isinstance(tool.get("path"), str)
        ):
            return identify_external_tool(tool_id, Path(tool["path"]))
    raise PrototypeError("prototype_tool_missing", f"Tool registry has no {tool_id} entry.")


class PrototypeContext:
    """Lazily derives and caches the shared prep every capability run reuses."""

    def __init__(self, project_root: Path, source_id: str) -> None:
        self.project_root = project_root
        self.source_id = source_id
        self.language = source_language(source_id)
        self.work_dir = project_root / "work" / "prototype" / source_id[:8]
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._ffmpeg: PinnedExternalTool | None = None
        self._audio: tuple[Path, DerivativeTimeMapping, Fraction] | None = None
        self._vad: Measured[SileroVadResult] | None = None
        self._asr_primary: Measured[PrimaryTranscriptionResult] | None = None

    @property
    def ffmpeg(self) -> PinnedExternalTool:
        if self._ffmpeg is None:
            self._ffmpeg = _load_tool(self.project_root, "ffmpeg")
        return self._ffmpeg

    @property
    def media_path(self) -> Path:
        path = self.project_root / "input" / self.source_id / "media"
        if not path.is_file():
            raise PrototypeError("prototype_source_absent", f"No prototype media at {path}.")
        return path

    def audio(self) -> tuple[Path, DerivativeTimeMapping, Fraction]:
        """Decode the source to a 16 kHz mono analysis wav (once) and map it."""

        if self._audio is not None:
            return self._audio
        wav_path = self.work_dir / "analysis-16k.wav"
        if not wav_path.exists():
            subprocess.run(
                (
                    str(self.ffmpeg.path),
                    "-v",
                    "error",
                    "-nostdin",
                    "-y",
                    "-i",
                    str(self.media_path),
                    "-ac",
                    "1",
                    "-ar",
                    str(ANALYSIS_SAMPLE_RATE),
                    "-f",
                    "wav",
                    str(wav_path),
                ),
                check=True,
            )
        with wave.open(str(wav_path), "rb") as handle:
            if handle.getframerate() != ANALYSIS_SAMPLE_RATE or handle.getnchannels() != 1:
                raise PrototypeError("prototype_audio_invalid", "Analysis wav is not 16 kHz mono.")
            sample_count = handle.getnframes()
        mapping = DerivativeTimeMapping(
            HalfOpenInterval(ExactTime(0), ExactTime(sample_count, ANALYSIS_SAMPLE_RATE)),
            ANALYSIS_SAMPLE_RATE,
            sample_count,
        )
        media_seconds = Fraction(sample_count, ANALYSIS_SAMPLE_RATE)
        self._audio = (wav_path, mapping, media_seconds)
        return self._audio

    @property
    def media_seconds(self) -> Fraction:
        return self.audio()[2]

    def measured_vad(self) -> Measured[SileroVadResult]:
        if self._vad is not None:
            return self._vad
        from video_content_pipeline.vad_engine import analyze_derivative_vad

        wav_path, mapping, _ = self.audio()
        start = time.monotonic()
        result = analyze_derivative_vad(
            self.project_root,
            wav_path,
            mapping,
            source_id=self.source_id,
            stream_index=0,
        )
        wall = _elapsed_fraction(time.monotonic() - start)
        self._vad = Measured(result, wall, _process_peak_bytes())
        return self._vad

    def measured_asr_primary(self) -> Measured[PrimaryTranscriptionResult]:
        if self._asr_primary is not None:
            return self._asr_primary
        from video_content_pipeline.asr_engine import transcribe_derivative

        wav_path, _, _ = self.audio()
        chunks = self.measured_vad().result.chunks
        start = time.monotonic()
        result = transcribe_derivative(
            self.project_root,
            wav_path,
            source_id=self.source_id,
            stream_index=0,
            language=self.language,
            chunks=chunks,
        )
        wall = _elapsed_fraction(time.monotonic() - start)
        self._write_asr_cache(result.cues)
        self._asr_primary = Measured(result, wall, result.peak_memory_bytes)
        return self._asr_primary

    def _asr_cache_path(self) -> Path:
        return self.work_dir / "asr-primary-cues.json"

    def _write_asr_cache(self, cues: Sequence[ProjectedAsrCue]) -> None:
        """Cache the primary transcript so alignment/text_semantics prep reuses it.

        Each single-capability process is self-contained; caching the cues lets a
        later ``forced_alignment`` or ``text_semantics`` run reuse the real
        transcript rather than re-running ASR as untimed prep.
        """

        data = [
            {
                "ordinal": cue.ordinal,
                "start": [cue.interval.start.numerator, cue.interval.start.denominator],
                "end": [cue.interval.end.numerator, cue.interval.end.denominator],
                "text": cue.text,
            }
            for cue in cues
        ]
        self._asr_cache_path().write_text(json.dumps({"cues": data}), encoding="utf-8")

    def _asr_cue_data(self) -> list[dict[str, object]]:
        cache = self._asr_cache_path()
        if not cache.exists():
            self.measured_asr_primary()
        loaded = json.loads(cache.read_text(encoding="utf-8"))["cues"]
        return list(loaded)

    def alignment_cues(self) -> tuple[AlignmentCue, ...]:
        """The primary transcript rebuilt as timing-only alignment cues."""

        from video_content_pipeline.audio_analysis import AlignmentCue

        def _exact(pair: object) -> ExactTime:
            numerator, denominator = cast("list[int]", pair)
            return ExactTime(numerator, denominator)

        return tuple(
            AlignmentCue(
                source_ordinal=cast(int, datum["ordinal"]),
                text=cast(str, datum["text"]),
                interval=HalfOpenInterval(_exact(datum["start"]), _exact(datum["end"])),
            )
            for datum in self._asr_cue_data()
        )

    def asr_cue_count(self) -> int:
        return len(self._asr_cue_data())

    def measured_diarization(self) -> Measured[SherpaDiarizationResult]:
        from video_content_pipeline.diarization_engine import analyze_derivative_diarization

        wav_path, mapping, _ = self.audio()
        intervals = self.measured_vad().result.part_evidence.voice_activity_intervals
        start = time.monotonic()
        result = analyze_derivative_diarization(
            self.project_root,
            wav_path,
            mapping,
            source_id=self.source_id,
            stream_index=0,
            part_label="part-1",
            voice_activity_intervals=intervals,
        )
        return Measured(result, _elapsed_fraction(time.monotonic() - start), _process_peak_bytes())

    def measured_alignment(self) -> Measured[Qwen3AlignmentResult]:
        from video_content_pipeline.alignment_engine import analyze_derivative_alignment

        wav_path, mapping, _ = self.audio()
        vad = self.measured_vad().result
        source_cues = self.alignment_cues()
        start = time.monotonic()
        result = analyze_derivative_alignment(
            self.project_root,
            wav_path,
            mapping,
            source_id=self.source_id,
            stream_index=0,
            language=self.language,
            source_cues=source_cues,
            chunks=vad.chunks,
            voice_activity_intervals=vad.part_evidence.voice_activity_intervals,
        )
        return Measured(
            result, _elapsed_fraction(time.monotonic() - start), result.peak_memory_bytes
        )

    def measured_asr_review(self) -> Measured[ReviewTranscriptionResult]:
        from video_content_pipeline.asr_engine import review_suspicious_intervals

        wav_path, mapping, _ = self.audio()
        vad = self.measured_vad().result
        speech_intervals = tuple(
            item.interval for item in vad.part_evidence.voice_activity_intervals if _is_speech(item)
        )
        suspicious = suspicious_intervals_from_chunks(
            tuple(chunk.source_interval for chunk in vad.chunks),
            limit=2,
        )
        start = time.monotonic()
        result = review_suspicious_intervals(
            self.project_root,
            wav_path,
            mapping,
            source_id=self.source_id,
            stream_index=0,
            language=self.language,
            intervals=suspicious,
            speech_intervals=speech_intervals,
        )
        return Measured(
            result, _elapsed_fraction(time.monotonic() - start), result.peak_memory_bytes
        )

    def measured_ocr(self) -> Measured[OcrEngineResult]:
        from video_content_pipeline.ocr_engine import OcrFrame, analyze_frames_ocr

        frames = self._extract_frames()
        ocr_frames = tuple(
            OcrFrame(part_id="part-1", visual_page_id=f"page-{ordinal}", pts=pts, image_path=path)
            for ordinal, (pts, path) in enumerate(frames)
        )
        start = time.monotonic()
        result = analyze_frames_ocr(self.project_root, ocr_frames)
        return Measured(result, _elapsed_fraction(time.monotonic() - start), _process_peak_bytes())

    def measured_text_semantics(self) -> Measured[Qwen3TextSemanticsResult]:
        from video_content_pipeline.text_contracts import revalidate_text_generation_contracts
        from video_content_pipeline.text_semantics_engine import generate_text_semantics

        contracts = revalidate_text_generation_contracts(self.project_root)
        part = loaded_part_for_cues("part-1", "asr-primary", self.asr_cue_count())
        start = time.monotonic()
        result = generate_text_semantics(
            self.project_root,
            self.work_dir / "text-semantics",
            contracts,
            source_id=self.source_id,
            stream_index=0,
            available=(part,),
        )
        return Measured(
            result, _elapsed_fraction(time.monotonic() - start), result.peak_memory_bytes
        )

    def _extract_frames(self) -> list[tuple[ExactTime, Path]]:
        frames: list[tuple[ExactTime, Path]] = []
        duration = self.media_seconds
        frames_dir = self.work_dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        for ordinal, fraction in enumerate(FRAME_FRACTIONS):
            at_seconds = duration * fraction
            image_path = frames_dir / f"frame-{ordinal}.png"
            if not image_path.exists():
                subprocess.run(
                    (
                        str(self.ffmpeg.path),
                        "-v",
                        "error",
                        "-nostdin",
                        "-y",
                        "-ss",
                        str(float(at_seconds)),
                        "-i",
                        str(self.media_path),
                        "-frames:v",
                        "1",
                        str(image_path),
                    ),
                    check=True,
                )
            frames.append((ExactTime(int(at_seconds)), image_path))
        return frames


def _is_speech(item: VoiceActivityInterval) -> bool:
    from video_content_pipeline.audio_analysis import VoiceActivityState

    return item.state == VoiceActivityState.SPEECH_LIKELY


# --- per-capability record builders -------------------------------------------


@dataclass(frozen=True)
class RunOutput:
    """One capability prototype run's retained record, sample, and baseline."""

    record: PrototypeRecord
    sample_markdown: str
    baseline: DeviceBaseline


def _build(
    ctx: PrototypeContext,
    capability: str,
    candidate_id: str,
    measured: Measured[object],
    *,
    asset_identities: Sequence[AssetIdentity],
    checks: Sequence[EngineeringCheck],
    entries: Sequence[str],
    truncated: bool,
) -> RunOutput:
    timing = PrototypeTiming(media_seconds=ctx.media_seconds, wall_seconds=measured.wall_seconds)
    relpath = sample_relpath(capability, ctx.source_id, ctx.language)
    all_checks = (envelope_check(measured.peak_memory_bytes), *checks)
    record = PrototypeRecord(
        capability=capability,
        candidate_id=candidate_id,
        language=ctx.language,
        source_id=ctx.source_id,
        device_class=DEVICE_CLASS,
        command=("vcp-prototype", capability, ctx.source_id),
        asset_identities=tuple(asset_identities),
        timing=timing,
        peak_memory_bytes=measured.peak_memory_bytes,
        checks=all_checks,
        offline_guards=offline_guard_names(),
        sample_relpath=relpath,
        created_at=utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    sample = render_sample_markdown(
        capability=capability,
        candidate_id=candidate_id,
        language=ctx.language,
        source_id=ctx.source_id,
        timing=timing,
        peak_memory_bytes=measured.peak_memory_bytes,
        entries=entries,
        truncated=truncated,
    )
    baseline = DeviceBaseline(
        capability=capability,
        candidate_id=candidate_id,
        device_class=DEVICE_CLASS,
        real_time_factor=timing.real_time_factor,
        peak_memory_bytes=measured.peak_memory_bytes,
        basis=f"prototype:{ctx.source_id[:8]}:{ctx.language}",
    )
    return RunOutput(record, sample, baseline)


def run_vad(ctx: PrototypeContext) -> RunOutput:
    measured = ctx.measured_vad()
    result = measured.result
    intervals = result.part_evidence.voice_activity_intervals
    coverage = ctx.audio()[1].source_interval
    contiguous = all(
        a.interval.end == b.interval.start for a, b in zip(intervals, intervals[1:], strict=False)
    )
    complete = (
        bool(intervals)
        and intervals[0].interval.start == coverage.start
        and (intervals[-1].interval.end == coverage.end)
    )
    chunks = result.chunks
    entries = format_speaker_turn_entries(
        tuple((chunk.source_interval, f"chunk-{chunk.chunk_index}") for chunk in chunks),
        limit=SAMPLE_ENTRY_LIMIT,
    ) or [f"{sum(1 for i in intervals if _is_speech(i))} speech-likely intervals, 0 chunks"]
    checks = (
        EngineeringCheck("partition_complete", complete and contiguous, "gap-free coverage tiling"),
        EngineeringCheck("calibrated", bool(result.calibrated), "calibration record matched"),
    )
    return _build(
        ctx,
        "vad",
        "silero-vad",
        measured,
        asset_identities=(AssetIdentity("silero_vad.onnx", result.model_asset_sha256),),
        checks=checks,
        entries=entries,
        truncated=len(chunks) > SAMPLE_ENTRY_LIMIT,
    )


def run_diarization(ctx: PrototypeContext) -> RunOutput:
    measured = ctx.measured_diarization()
    result = measured.result
    partition = result.partition
    turns = partition.published if partition is not None else ()
    entries = format_speaker_turn_entries(
        tuple((turn.interval, turn.speaker_label) for turn in turns), limit=SAMPLE_ENTRY_LIMIT
    ) or [f"{len(result.raw_turns)} raw candidate turns, no formal partition"]
    checks = (
        EngineeringCheck("calibrated", bool(result.calibrated), "both assets matched calibration"),
        EngineeringCheck(
            "labels_anonymous",
            all(turn.speaker_label.startswith("speaker-") for turn in turns),
            "Part-local anonymous labels only (ADR 0030)",
        ),
    )
    return _build(
        ctx,
        "diarization",
        "sherpa-onnx-pyannote-segmentation-3-0",
        measured,
        asset_identities=(
            AssetIdentity("segmentation", result.segmentation_asset_sha256),
            AssetIdentity("embedding", result.embedding_asset_sha256),
        ),
        checks=checks,
        entries=entries,
        truncated=len(turns) > SAMPLE_ENTRY_LIMIT,
    )


def run_asr_primary(ctx: PrototypeContext) -> RunOutput:
    measured = ctx.measured_asr_primary()
    result = measured.result
    cues = result.cues
    monotonic = all(
        a.interval.end.as_fraction() <= b.interval.start.as_fraction()
        for a, b in zip(cues, cues[1:], strict=False)
    )
    entries = format_transcript_entries(
        tuple((cue.interval.start, cue.text) for cue in cues), limit=SAMPLE_ENTRY_LIMIT
    )
    checks = (
        EngineeringCheck("transcript_monotonic", monotonic, "cues non-overlapping and ordered"),
        EngineeringCheck("cues_present", bool(cues), "at least one transcript cue"),
    )
    return _build(
        ctx,
        "asr_primary",
        "qwen3-asr-1-7b",
        measured,
        asset_identities=(AssetIdentity("qwen3-asr", result.model_asset_sha256),),
        checks=checks,
        entries=entries,
        truncated=len(cues) > SAMPLE_ENTRY_LIMIT,
    )


def run_asr_review(ctx: PrototypeContext) -> RunOutput:
    measured = ctx.measured_asr_review()
    result = measured.result
    reviews = result.reviews
    primary_sha = ctx.measured_asr_primary().result.model_asset_sha256
    entries = format_transcript_entries(
        tuple((review.interval.start, review.text or "(silence)") for review in reviews),
        limit=SAMPLE_ENTRY_LIMIT,
    ) or ["no suspicious intervals selected"]
    checks = (
        EngineeringCheck(
            "independent_model",
            result.model_asset_sha256 != primary_sha,
            "review asset differs from primary (Independent-model contract)",
        ),
        EngineeringCheck("reviews_present", bool(reviews), "at least one interval reviewed"),
    )
    return _build(
        ctx,
        "asr_review",
        "whisper-large-v3",
        measured,
        asset_identities=(AssetIdentity("whisper-large-v3", result.model_asset_sha256),),
        checks=checks,
        entries=entries,
        truncated=len(reviews) > SAMPLE_ENTRY_LIMIT,
    )


def run_alignment(ctx: PrototypeContext) -> RunOutput:
    measured = ctx.measured_alignment()
    result = measured.result
    proposals = result.projected.proposals
    entries = format_transcript_entries(
        tuple(
            (proposal.interval.start, f"[{proposal.confidence:.2f}] {proposal.text}")
            for proposal in proposals
        ),
        limit=SAMPLE_ENTRY_LIMIT,
    )
    checks = (
        EngineeringCheck(
            "proposal_per_cue",
            len(proposals) == ctx.asr_cue_count(),
            "one alignment proposal per source cue",
        ),
        EngineeringCheck("proposals_present", bool(proposals), "at least one proposal"),
    )
    return _build(
        ctx,
        "forced_alignment",
        "qwen3-forced-aligner-0-6b",
        measured,
        asset_identities=(AssetIdentity("qwen3-forced-aligner", result.model_asset_sha256),),
        checks=checks,
        entries=entries,
        truncated=len(proposals) > SAMPLE_ENTRY_LIMIT,
    )


def run_ocr(ctx: PrototypeContext) -> RunOutput:
    measured = ctx.measured_ocr()
    result = measured.result
    items = result.items
    entries = format_ocr_entries(
        tuple((item.text, item.confidence) for item in items), limit=SAMPLE_ENTRY_LIMIT
    ) or ["no text recognised in the sampled frames"]
    checks = (
        EngineeringCheck(
            "confidence_in_range",
            all(0.0 <= item.confidence <= 1.0 for item in items),
            "every item confidence within the schema range",
        ),
        EngineeringCheck(
            "frames_processed",
            result.frames_processed > 0,
            "frames were decoded and read",
        ),
    )
    return _build(
        ctx,
        "ocr_primary",
        "rapidocr",
        measured,
        asset_identities=(AssetIdentity("rapidocr-bundled", result.asset_sha256),),
        checks=checks,
        entries=entries,
        truncated=len(items) > SAMPLE_ENTRY_LIMIT,
    )


def run_text_semantics(ctx: PrototypeContext) -> RunOutput:
    measured = ctx.measured_text_semantics()
    result = measured.result
    segments = result.segments
    entries = format_segment_entries(segments, limit=SAMPLE_ENTRY_LIMIT) or [
        f"status={result.status}, {len(segments)} segments"
    ]
    checks = (
        EngineeringCheck(
            "status_valid",
            result.status in {"complete", "partial", "model_output_invalid"},
            "engine concluded a valid status, never crashed",
        ),
        EngineeringCheck(
            "raw_retained",
            result.restricted_raw_output is not None,
            "raw output retained as restricted audit evidence",
        ),
    )
    return _build(
        ctx,
        "text_semantics",
        "qwen3-4b-instruct-2507-8bit",
        measured,
        asset_identities=(AssetIdentity("qwen3-4b-instruct", result.model_asset_sha256),),
        checks=checks,
        entries=entries,
        truncated=len(segments) > SAMPLE_ENTRY_LIMIT,
    )


_RUNNERS = {
    "vad": run_vad,
    "diarization": run_diarization,
    "forced_alignment": run_alignment,
    "asr_primary": run_asr_primary,
    "asr_review": run_asr_review,
    "ocr_primary": run_ocr,
    "text_semantics": run_text_semantics,
}


# --- evidence persistence + CLI -----------------------------------------------


def write_run_output(project_root: Path, output: RunOutput) -> None:
    """Write one run's sample (.md) and record (.record.json) into docs evidence."""

    record = output.record
    sample_path = project_root / record.sample_relpath
    record_path = project_root / _record_relpath(
        record.capability, record.source_id, record.language
    )
    sample_path.parent.mkdir(parents=True, exist_ok=True)
    sample_path.write_text(output.sample_markdown, encoding="utf-8")
    record_path.write_text(
        json.dumps(record.as_json(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run one capability prototype over one ticket-12 source, in a fresh process.

    One capability per invocation keeps the measured peak honest: the in-process
    engines (vad, diarization, ocr_primary) report the process high-water RSS,
    which would be contaminated by earlier engines if several ran in one process.
    zh+en coverage comes from invoking each capability once per source id.
    """

    parser = argparse.ArgumentParser(prog="vcp-prototype")
    parser.add_argument("capability", choices=tuple(_RUNNERS))
    parser.add_argument("source_id")
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args(argv)
    project_root = Path(__file__).resolve().parents[2]
    ctx = PrototypeContext(project_root, arguments.source_id)

    from video_content_pipeline.prototype import write_device_baselines

    output = _RUNNERS[arguments.capability](ctx)
    write_run_output(project_root, output)
    write_device_baselines(project_root / DEVICE_BASELINES_RELPATH, [output.baseline])
    summary = {
        "status": "ok",
        "source_id": arguments.source_id,
        "language": ctx.language,
        "run": output.record.as_json(),
    }
    print(
        json.dumps(
            summary, ensure_ascii=False, sort_keys=True, indent=2 if not arguments.json else None
        )
    )
    return 0 if output.record.engineering_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
