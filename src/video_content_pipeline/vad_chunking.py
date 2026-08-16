"""Shared VAD-derived chunk derivation: speech-anchored <=5-minute windows.

The upstream both ASR and forced alignment consume. A VAD partition's speech
runs (over one analysis-audio derivative) are grouped into windows of at most
five minutes, cut only in the non-speech gaps *between* runs, so no speech is
ever split across a window and the forced aligner's hard five-minute window is
respected by construction rather than per-engine improvisation. Each chunk
carries the exact derivative-sample range it covers and, through the
derivative's :class:`DerivativeTimeMapping`, the authoritative source-time
interval that range maps to -- so ASR and alignment evidence produced inside a
chunk lands back on the source timeline exactly.

The derivation is pure and model-free: it takes the speech runs (as
half-open ``[start_sample, end_sample)`` ranges of the derivative) plus the
derivative's mapping, and never touches a model or the filesystem. It is
deterministic -- a left-to-right greedy pack -- so the same partition always
produces the same chunks.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from video_content_pipeline.audio_derivation import DerivativeTimeMapping
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval, interval_as_json

#: The forced aligner's hard window and the shared default chunk ceiling.
FIVE_MINUTES = ExactTime(300)


class VadChunkingError(ValueError):
    """A rejected chunk-derivation input with a machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class SpeechChunk:
    """One speech-anchored window and its exact source-time mapping.

    ``start_sample``/``end_sample`` are the half-open derivative-sample bounds
    of the window; ``source_interval`` is that range mapped to the authoritative
    source clock; ``speech_runs`` are the coalesced speech spans it contains, in
    source time.
    """

    chunk_index: int
    start_sample: int
    end_sample: int
    source_interval: HalfOpenInterval
    speech_runs: tuple[HalfOpenInterval, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "chunk_index": self.chunk_index,
            "start_sample": self.start_sample,
            "end_sample": self.end_sample,
            "source_interval": interval_as_json(self.source_interval),
            "speech_runs": [interval_as_json(run) for run in self.speech_runs],
        }


def derive_speech_chunks(
    speech_runs_samples: Sequence[tuple[int, int]],
    mapping: DerivativeTimeMapping,
    *,
    max_chunk_duration: ExactTime = FIVE_MINUTES,
) -> tuple[SpeechChunk, ...]:
    """Group speech runs into <=``max_chunk_duration`` chunks cut only in silence.

    ``speech_runs_samples`` are the derivative's speech spans as half-open
    ``[start_sample, end_sample)`` integer ranges, ordered and non-overlapping
    (touching runs are coalesced). Each returned :class:`SpeechChunk` spans at
    most ``max_chunk_duration`` of source time, covers a contiguous group of
    speech runs, and is separated from its neighbours by a positive non-speech
    gap -- so every cut falls in silence. Raises :class:`VadChunkingError` for a
    non-positive window (``chunk_duration_invalid``), malformed runs
    (``chunk_speech_runs_invalid``), or a single speech run longer than the
    window with no internal silence to cut at (``chunk_window_exceeded``).
    """

    if max_chunk_duration <= ExactTime(0):
        raise VadChunkingError(
            "chunk_duration_invalid", "The maximum chunk duration must be positive."
        )
    runs = _coalesced_runs(speech_runs_samples, mapping)
    if not runs:
        return ()
    for start, end in runs:
        if _source_duration(mapping, start, end) > max_chunk_duration:
            raise VadChunkingError(
                "chunk_window_exceeded",
                "A continuous speech run exceeds the chunk window with no silence to cut at.",
            )

    chunks: list[SpeechChunk] = []
    group_start, group_end = runs[0]
    group: list[tuple[int, int]] = [runs[0]]
    for run in runs[1:]:
        if _source_duration(mapping, group_start, run[1]) <= max_chunk_duration:
            group.append(run)
            group_end = run[1]
            continue
        chunks.append(_chunk(len(chunks), group_start, group_end, group, mapping))
        group_start, group_end = run
        group = [run]
    chunks.append(_chunk(len(chunks), group_start, group_end, group, mapping))
    return tuple(chunks)


def _coalesced_runs(
    speech_runs_samples: Sequence[tuple[int, int]], mapping: DerivativeTimeMapping
) -> list[tuple[int, int]]:
    ordered = sorted(speech_runs_samples, key=lambda run: (run[0], run[1]))
    coalesced: list[tuple[int, int]] = []
    previous_end = 0
    for run in ordered:
        start, end = run
        if (
            not _is_index(start)
            or not _is_index(end)
            or start < 0
            or end <= start
            or end > mapping.sample_count
        ):
            raise VadChunkingError(
                "chunk_speech_runs_invalid",
                "Speech runs must be positive half-open sample ranges inside the derivative.",
            )
        if start < previous_end:
            raise VadChunkingError("chunk_speech_runs_invalid", "Speech runs must not overlap.")
        if coalesced and start == coalesced[-1][1]:
            coalesced[-1] = (coalesced[-1][0], end)
        else:
            coalesced.append((start, end))
        previous_end = end
    return coalesced


def _chunk(
    index: int,
    start_sample: int,
    end_sample: int,
    runs: Sequence[tuple[int, int]],
    mapping: DerivativeTimeMapping,
) -> SpeechChunk:
    return SpeechChunk(
        chunk_index=index,
        start_sample=start_sample,
        end_sample=end_sample,
        source_interval=mapping.source_interval_for_samples(start_sample, end_sample),
        speech_runs=tuple(mapping.source_interval_for_samples(start, end) for start, end in runs),
    )


def _source_duration(
    mapping: DerivativeTimeMapping, start_sample: int, end_sample: int
) -> ExactTime:
    return ExactTime(end_sample - start_sample, mapping.sample_rate)


def _is_index(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
