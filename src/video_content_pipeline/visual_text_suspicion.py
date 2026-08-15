"""Phase 8's Suspected embedded-media detection (ticket 06).

A Suspected embedded-media interval marks a *possible* embedded video at low
confidence -- never a confirmed fact, and never a description of what is on screen
(no clothing, environment, object, or action; ADR 0047). It is derived from the
picture alone: a sustained run of transition frames the deterministic page index
never settled into a Visual page is continuous on-screen motion, the kind an
embedded playing video produces. A single transition frame is an ordinary page
change; only a run of at least the versioned minimum is surfaced.

Provenance always states the marker's basis:

* ``picture_only`` when no Audio analysis report was supplied -- picture-only
  marking is permitted, and the provenance says so explicitly; and
* ``picture_plus_audio`` when a revalidated Audio analysis report was supplied.
  The report's voice-activity evidence is read through a tolerant reader and any
  active region overlapping the picture interval is recorded as corroboration; a
  real offline audio report is model-gated and legitimately carries none, which is
  not an error.

The rules are a versioned, ``calibration_required`` ruleset (real calibration
happens only in a separately authorized real-world session) and the version is
recorded in every result, so the same page index always yields the same markers.
No model is downloaded or executed. See ``docs/PHASE_08_SPECIFICATION.md``, the
Visual-Text Context, and ADR 0049.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeGuard

from video_content_pipeline.timecode import ExactTime, HalfOpenInterval, TimeValidationError
from video_content_pipeline.visual_page_index import PartPageIndex, RetainedFrame
from video_content_pipeline.visual_text import VisualTextError

# The two evidential bases a suspected interval may record. The basis is decided by
# whether a revalidated Audio analysis report was supplied, never asserted as fact.
BASIS_PICTURE_ONLY = "picture_only"
BASIS_PICTURE_PLUS_AUDIO = "picture_plus_audio"

# The one active voice-activity state that corroborates a picture interval; the
# non-speech and indeterminate states are read but never treated as activity.
_SPEECH_LIKELY = "speech_likely"
_VAD_STATES = (_SPEECH_LIKELY, "non_speech", "indeterminate")

_RULES_RELATIVE_PATH = ("config", "visual-text", "rules.json")


# --- Versioned, calibration-required ruleset --------------------------------


@dataclass(frozen=True)
class EmbeddedMediaRuleset:
    """The versioned deterministic bound for embedded-media suspicion.

    ``minimum_transition_run`` is the fewest consecutive transition frames that mark
    a sustained-motion interval; a shorter run is an ordinary page change and is not
    surfaced.
    """

    version: str
    calibration_required: bool
    minimum_transition_run: int

    def as_json(self) -> dict[str, object]:
        return {
            "version": self.version,
            "calibration_required": self.calibration_required,
            "minimum_transition_run": self.minimum_transition_run,
        }


# --- Audio activity evidence ------------------------------------------------


@dataclass(frozen=True)
class AudioActivityRegion:
    """One voice-activity region read from a supplied Audio analysis report."""

    interval: HalfOpenInterval
    state: str


# --- Detection outcomes -----------------------------------------------------


@dataclass(frozen=True)
class SuspectedEmbeddedMediaInterval:
    """One low-confidence marker for a possible embedded video in one Part.

    ``start`` and ``end`` are the first and last transition-frame times of the run
    (closed observed sample times, like a Page appearance record); ``basis`` states
    picture-only or picture-plus-audio; ``overlapping_audio`` records any corroborating
    active audio regions. ``low_confidence`` is always true -- this is never a fact.
    """

    part_id: str
    start: ExactTime
    end: ExactTime
    basis: str
    transition_frame_count: int
    overlapping_audio: tuple[HalfOpenInterval, ...]
    low_confidence: bool = True

    def as_json(self) -> dict[str, object]:
        return {
            "part_id": self.part_id,
            "start": _time_as_json(self.start),
            "end": _time_as_json(self.end),
            "basis": self.basis,
            "low_confidence": self.low_confidence,
            "transition_frame_count": self.transition_frame_count,
            "overlapping_audio": [
                _interval_as_json(interval) for interval in self.overlapping_audio
            ],
        }


@dataclass(frozen=True)
class PartEmbeddedMediaResult:
    """The deterministic embedded-media suspicion for one Part."""

    part_id: str
    rules_version: str
    calibration_required: bool
    intervals: tuple[SuspectedEmbeddedMediaInterval, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "part_id": self.part_id,
            "rules_version": self.rules_version,
            "calibration_required": self.calibration_required,
            "intervals": [interval.as_json() for interval in self.intervals],
        }


def detect_embedded_media(
    *,
    part_id: str,
    index: PartPageIndex,
    rules: EmbeddedMediaRuleset,
    audio_regions: Sequence[AudioActivityRegion] | None,
) -> PartEmbeddedMediaResult:
    """Flag sustained transition-frame runs as low-confidence embedded-media markers.

    Retained frames are already time-ordered. Each maximal run of consecutive frames
    belonging to no Visual page (transition frames) whose length reaches
    ``minimum_transition_run`` becomes one marker spanning the run's first and last
    frame time. ``audio_regions`` of ``None`` means no Audio analysis report was
    supplied (basis picture-only); a present sequence -- even an empty one -- means a
    revalidated report was supplied (basis picture-plus-audio), and any active region
    overlapping the interval is recorded as corroboration.
    """

    basis = BASIS_PICTURE_ONLY if audio_regions is None else BASIS_PICTURE_PLUS_AUDIO
    active = [region for region in (audio_regions or ()) if region.state == _SPEECH_LIKELY]
    intervals: list[SuspectedEmbeddedMediaInterval] = []
    for run in _transition_runs(index.retained_frames):
        if len(run) < rules.minimum_transition_run:
            continue
        start, end = run[0].pts, run[-1].pts
        intervals.append(
            SuspectedEmbeddedMediaInterval(
                part_id=part_id,
                start=start,
                end=end,
                basis=basis,
                transition_frame_count=len(run),
                overlapping_audio=_overlaps(active, start, end),
            )
        )
    return PartEmbeddedMediaResult(
        part_id=part_id,
        rules_version=rules.version,
        calibration_required=rules.calibration_required,
        intervals=tuple(intervals),
    )


def _transition_runs(frames: Sequence[RetainedFrame]) -> list[list[RetainedFrame]]:
    """Group time-ordered frames into maximal runs of consecutive transition frames."""

    runs: list[list[RetainedFrame]] = []
    current: list[RetainedFrame] = []
    for frame in frames:
        if frame.visual_page_id is None:
            current.append(frame)
            continue
        if current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


def _overlaps(
    active: Sequence[AudioActivityRegion], start: ExactTime, end: ExactTime
) -> tuple[HalfOpenInterval, ...]:
    """Return active audio intervals overlapping the closed picture range ``[start, end]``."""

    lo, hi = start.as_fraction(), end.as_fraction()
    return tuple(
        region.interval
        for region in active
        if region.interval.start.as_fraction() <= hi and region.interval.end.as_fraction() >= lo
    )


# --- Tolerant audio-report VAD reader ---------------------------------------


def audio_activity_regions(
    report_document: Mapping[str, object], source_id: str
) -> tuple[AudioActivityRegion, ...]:
    """Read one Part's voice-activity regions from a supplied Audio analysis report.

    The reader targets the report's formal VAD partition: a ``formal_evidence`` entry
    whose ``capability`` is ``vad`` carries a ``parts`` list keyed by ``source_id``,
    each with a ``voice_activity_intervals`` list of ``{interval, state}`` records. A
    model-gated offline report carries no such evidence, which is tolerated (an empty
    result, not an error). Only an evidence block that is present but malformed raises
    ``visual_text_audio_evidence_invalid`` -- the retained report is revalidated
    upstream, so its shape, when present, must be well-formed.
    """

    formal_evidence = report_document.get("formal_evidence")
    if formal_evidence is None:
        return ()
    if not isinstance(formal_evidence, list):
        raise _audio_invalid("Audio report formal_evidence is not a list.")
    regions: list[AudioActivityRegion] = []
    for entry in formal_evidence:
        if not isinstance(entry, Mapping) or entry.get("capability") != "vad":
            continue
        regions.extend(_vad_part_regions(entry, source_id))
    return tuple(regions)


def _vad_part_regions(entry: Mapping[str, object], source_id: str) -> list[AudioActivityRegion]:
    parts = entry.get("parts")
    if not isinstance(parts, list):
        raise _audio_invalid("Audio report VAD evidence omits a parts list.")
    regions: list[AudioActivityRegion] = []
    for part in parts:
        if not isinstance(part, Mapping) or part.get("source_id") != source_id:
            continue
        intervals = part.get("voice_activity_intervals")
        if not isinstance(intervals, list):
            raise _audio_invalid("Audio report VAD part omits a voice_activity_intervals list.")
        regions.extend(_vad_region(item) for item in intervals)
    return regions


def _vad_region(item: object) -> AudioActivityRegion:
    if not isinstance(item, Mapping):
        raise _audio_invalid("A voice-activity interval is not an object.")
    state = item.get("state")
    if state not in _VAD_STATES:
        raise _audio_invalid("A voice-activity interval has an unknown state.")
    return AudioActivityRegion(interval=_interval_from_json(item.get("interval")), state=state)


# --- Versioned ruleset loader -----------------------------------------------


def load_embedded_media_ruleset(project_root: Path) -> EmbeddedMediaRuleset:
    """Load and version-bind the embedded-media suspicion ruleset from config.

    The ``embedded_media`` section of ``config/visual-text/rules.json`` carries the
    version, the ``calibration_required`` mark, and a positive ``minimum_transition_run``.
    The ruleset is our own revalidated ground truth, so any missing or malformed field
    raises ``visual_text_rules_invalid`` before an attempt marks anything.
    """

    path = project_root.joinpath(*_RULES_RELATIVE_PATH)
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VisualTextError(
            "visual_text_rules_invalid", f"Visual-text rules cannot be read: {path}"
        ) from error
    if not isinstance(decoded, Mapping):
        raise VisualTextError("visual_text_rules_invalid", "Visual-text rules must be an object.")
    section = decoded.get("embedded_media")
    if not isinstance(section, Mapping):
        raise VisualTextError(
            "visual_text_rules_invalid", "Visual-text rules need an 'embedded_media' object."
        )
    version = section.get("version")
    if not isinstance(version, str) or not version:
        raise VisualTextError(
            "visual_text_rules_invalid", "Visual-text embedded_media rules need a version string."
        )
    if section.get("calibration_required") is not True:
        raise VisualTextError(
            "visual_text_rules_invalid",
            "Visual-text embedded_media rules must keep the calibration_required mark.",
        )
    run = section.get("minimum_transition_run")
    if not isinstance(run, int) or isinstance(run, bool) or run < 1:
        raise VisualTextError(
            "visual_text_rules_invalid",
            "Visual-text embedded_media rules need a positive minimum_transition_run.",
        )
    return EmbeddedMediaRuleset(
        version=version, calibration_required=True, minimum_transition_run=run
    )


# --- helpers ----------------------------------------------------------------


def _audio_invalid(message: str) -> VisualTextError:
    return VisualTextError("visual_text_audio_evidence_invalid", message)


def _interval_from_json(value: object) -> HalfOpenInterval:
    if not isinstance(value, Mapping):
        raise _audio_invalid("A voice-activity interval is not an object.")
    try:
        return HalfOpenInterval(
            _exact_time_from_json(value.get("start")), _exact_time_from_json(value.get("end"))
        )
    except TimeValidationError as error:
        raise _audio_invalid(
            "A voice-activity interval is not a positive half-open interval."
        ) from error


def _exact_time_from_json(value: object) -> ExactTime:
    if not isinstance(value, Mapping):
        raise _audio_invalid("A voice-activity time is not an object.")
    numerator = value.get("numerator")
    denominator = value.get("denominator")
    if not _is_int(numerator) or not _is_int(denominator) or denominator <= 0:
        raise _audio_invalid(
            "A voice-activity time omits an integer numerator or positive denominator."
        )
    return ExactTime(numerator, denominator)


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _interval_as_json(interval: HalfOpenInterval) -> dict[str, object]:
    return {"start": _time_as_json(interval.start), "end": _time_as_json(interval.end)}


def _time_as_json(value: ExactTime) -> dict[str, int]:
    return {"numerator": value.numerator, "denominator": value.denominator}


__all__ = [
    "BASIS_PICTURE_ONLY",
    "BASIS_PICTURE_PLUS_AUDIO",
    "AudioActivityRegion",
    "EmbeddedMediaRuleset",
    "PartEmbeddedMediaResult",
    "SuspectedEmbeddedMediaInterval",
    "audio_activity_regions",
    "detect_embedded_media",
    "load_embedded_media_ruleset",
]
