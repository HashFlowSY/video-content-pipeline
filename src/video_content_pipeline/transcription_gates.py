"""Phase 7's canonical-timeline gates for projected ASR cues (ticket 04).

Ticket 03 turns a raw ASR model output into typed :class:`ProjectedAsrCue`
evidence. Before any projected cue may become *candidate* evidence it must sit
correctly on the canonical timeline, so this module applies the deterministic
timing gates the specification and ADR 0045 name -- structurally the same gates
alignment adoption uses:

* **exact rational times inside actual stream coverage** -- a cue is mapped
  through the existing Phase 2 coordinate systems (``RawPtsTime`` source time ->
  ``PartRelativeTime`` -> ``CollectionVirtualTime``) and rejected if it falls
  before the Part coverage start or past the Part coverage endpoint; Part
  boundaries stay hard, so a cue may never spill into the next Part;
* **monotonic order** -- ordinals and start times must strictly advance over the
  admitted cues;
* **half-open, positive duration** -- guaranteed for a projected cue by the
  ``HalfOpenInterval`` type and re-checked defensively on the mapped interval;
* **no processing duplication** -- a cue duplicating or overlapping the previous
  admitted cue (the classic overlapping-window artifact) is rejected; and
* **plausible duration-to-text relation** -- a cue whose duration is implausible
  for its visible text length under the versioned, ``calibration_required``
  ruleset is rejected.

Rejected cues are never repaired: each is retained with a structured reason so
the correction log can trace the decision. All arithmetic is exact rational
arithmetic over ``ExactTime``/``Fraction`` -- no float accumulation and no
container-duration guessing. No model is downloaded or executed. See
``docs/PHASE_07_SPECIFICATION.md``, the Transcription Context, and ADR 0045.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeGuard

from video_content_pipeline.coverage import StreamCoverage
from video_content_pipeline.timecode import (
    ExactTime,
    HalfOpenInterval,
    PartCoverageStart,
    PartRelativeTime,
    RawPtsTime,
    TimeValidationError,
)
from video_content_pipeline.timeline import (
    CollectionTimeline,
    CollectionVirtualTime,
    TimelineValidationError,
)
from video_content_pipeline.transcription_contracts import (
    AsrLanguageSpan,
    ProjectedAsrCue,
    ProjectedAsrToken,
)

_RULES_RELATIVE_PATH = ("config", "transcription", "transcription-rules.json")
_GATE_RULES_RELATIVE_PATH = ("config", "transcription", "timing-gate-rules.json")

# The per-cue rejection reasons; a rejected cue keeps exactly one of these.
_OUT_OF_COVERAGE = "cue_out_of_coverage"
_NON_MONOTONIC = "cue_non_monotonic"
_NON_POSITIVE_DURATION = "cue_non_positive_duration"
_PROCESSING_DUPLICATION = "cue_processing_duplication"
_MISSING_TEXT = "cue_missing_text"
_DURATION_IMPLAUSIBLE = "cue_duration_implausible"

# The mapping errors that mean a cue is out of coverage rather than an
# inconsistency in our own inputs (which the gate validates up front and raises).
_COVERAGE_MAPPING_REASONS = frozenset({"part_relative_negative", "part_relative_out_of_coverage"})


class TranscriptionGateError(ValueError):
    """A gate precondition failure over our own inputs, with a stable reason.

    The gate rejects untrusted *cues* (retaining a per-cue reason); this error is
    raised only when the caller's own gate inputs are inconsistent -- an
    indeterminate coverage envelope, a Part absent from the timeline, a timeline
    that disagrees with the coverage evidence, or an invalid ruleset.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


# --- Versioned, calibration-required ruleset --------------------------------


@dataclass(frozen=True)
class DurationToTextBounds:
    """Conservative bounds on the plausible seconds-per-visible-character rate."""

    minimum_seconds_per_character: ExactTime
    maximum_seconds_per_character: ExactTime

    def as_json(self) -> dict[str, object]:
        return {
            "minimum_seconds_per_character": _time_as_json(self.minimum_seconds_per_character),
            "maximum_seconds_per_character": _time_as_json(self.maximum_seconds_per_character),
        }


@dataclass(frozen=True)
class TimingGateRuleset:
    """The versioned timing-gate ruleset governing duration-to-text plausibility.

    The bounds are conservative defaults marked ``calibration_required``: real
    per-language rates are calibrated only in a separately authorized real-model
    session, so a ruleset that drops the mark is rejected by the loader.
    """

    version: str
    calibration_required: bool
    duration_to_text: DurationToTextBounds

    def as_json(self) -> dict[str, object]:
        return {
            "version": self.version,
            "calibration_required": self.calibration_required,
            "duration_to_text": self.duration_to_text.as_json(),
        }


# --- Gate outcomes ----------------------------------------------------------


@dataclass(frozen=True)
class GatedAsrCue:
    """One admitted cue carried across the three existing coordinate systems.

    ``raw_interval`` is the projected source-time interval; ``part_relative_interval``
    and ``collection_interval`` are its exact translations, so a later consumer
    reads the cue on whichever timeline it needs without re-deriving the mapping.
    """

    ordinal: int
    part_id: str
    text: str
    raw_interval: HalfOpenInterval
    part_relative_interval: HalfOpenInterval
    collection_interval: HalfOpenInterval
    tokens: tuple[ProjectedAsrToken, ...]
    language_spans: tuple[AsrLanguageSpan, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "ordinal": self.ordinal,
            "part_id": self.part_id,
            "text": self.text,
            "raw_interval": _interval_as_json(self.raw_interval),
            "part_relative_interval": _interval_as_json(self.part_relative_interval),
            "collection_interval": _interval_as_json(self.collection_interval),
            "tokens": [token.as_json() for token in self.tokens],
            "language_spans": [span.as_json() for span in self.language_spans],
        }


@dataclass(frozen=True)
class RejectedAsrCue:
    """One rejected cue retained with its structured reason -- never repaired."""

    ordinal: int
    raw_interval: HalfOpenInterval
    reason: str
    message: str

    def as_json(self) -> dict[str, object]:
        return {
            "ordinal": self.ordinal,
            "raw_interval": _interval_as_json(self.raw_interval),
            "reason": self.reason,
            "message": self.message,
        }


@dataclass(frozen=True)
class CanonicalTimelineGateResult:
    """The deterministic outcome of gating one Part's projected cues."""

    part_id: str
    gate_version: str
    calibration_required: bool
    admitted: tuple[GatedAsrCue, ...]
    rejected: tuple[RejectedAsrCue, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "part_id": self.part_id,
            "gate_version": self.gate_version,
            "calibration_required": self.calibration_required,
            "admitted": [cue.as_json() for cue in self.admitted],
            "rejected": [cue.as_json() for cue in self.rejected],
        }


def gate_projected_cues(
    *,
    part_id: str,
    cues: Sequence[ProjectedAsrCue],
    part_coverage: StreamCoverage,
    timeline: CollectionTimeline,
    rules: TimingGateRuleset,
) -> CanonicalTimelineGateResult:
    """Gate one Part's projected cues onto the canonical timeline.

    ``part_coverage`` is the Part's observed Phase 2 stream coverage (its envelope
    must be determinate) and ``timeline`` carries the Part's contiguous
    collection-virtual mapping. Cues are gated in their given order; each is
    admitted with its exact cross-coordinate mapping or rejected -- never
    repaired -- with a single structured reason. Monotonicity and duplication are
    assessed only against previously admitted cues, so a rejected cue never
    poisons a later one.
    """

    envelope = _determinate_envelope(part_coverage)
    coverage_start = _part_coverage_start(timeline, part_id, envelope)

    admitted: list[GatedAsrCue] = []
    rejected: list[RejectedAsrCue] = []
    previous: GatedAsrCue | None = None
    for cue in cues:
        outcome = _gate_one_cue(cue, part_id, coverage_start, timeline, rules, previous)
        if isinstance(outcome, RejectedAsrCue):
            rejected.append(outcome)
        else:
            admitted.append(outcome)
            previous = outcome

    return CanonicalTimelineGateResult(
        part_id=part_id,
        gate_version=rules.version,
        calibration_required=rules.calibration_required,
        admitted=tuple(admitted),
        rejected=tuple(rejected),
    )


def _gate_one_cue(
    cue: ProjectedAsrCue,
    part_id: str,
    coverage_start: PartCoverageStart,
    timeline: CollectionTimeline,
    rules: TimingGateRuleset,
    previous: GatedAsrCue | None,
) -> GatedAsrCue | RejectedAsrCue:
    mapped = _map_cue(cue, part_id, coverage_start, timeline)
    if isinstance(mapped, RejectedAsrCue):
        return mapped
    part_relative_interval, collection_interval = mapped

    # Reason precedence is most-structural first: coverage, then position in the
    # admitted sequence (monotonicity and processing duplication), then the
    # cue's own content plausibility. So a cue that is both a duplicate and
    # duration-implausible is recorded as the duplication, which auditors of the
    # "no processing duplication" gate need to see.
    order = _ordering_reason(cue, collection_interval, previous)
    if order is not None:
        return _reject(cue, order[0], order[1])

    content = _content_reason(cue, rules)
    if content is not None:
        return _reject(cue, content[0], content[1])

    return GatedAsrCue(
        ordinal=cue.ordinal,
        part_id=part_id,
        text=cue.text,
        raw_interval=cue.interval,
        part_relative_interval=part_relative_interval,
        collection_interval=collection_interval,
        tokens=cue.tokens,
        language_spans=cue.language_spans,
    )


def _map_cue(
    cue: ProjectedAsrCue,
    part_id: str,
    coverage_start: PartCoverageStart,
    timeline: CollectionTimeline,
) -> tuple[HalfOpenInterval, HalfOpenInterval] | RejectedAsrCue:
    """Map a cue's boundaries into Part-relative and collection-virtual time.

    The existing coordinate types enforce coverage: a start before the Part
    coverage start or an end past the Part coverage endpoint raises, and the gate
    turns that into an ``cue_out_of_coverage`` rejection. Part boundaries stay
    hard because the endpoint check forbids spilling into the next Part.
    """

    try:
        part_relative_start, collection_start = _map_boundary(
            cue.interval.start, part_id, coverage_start, timeline
        )
        part_relative_end, collection_end = _map_boundary(
            cue.interval.end, part_id, coverage_start, timeline
        )
    except TimelineValidationError as error:
        if error.reason in _COVERAGE_MAPPING_REASONS:
            return _reject(cue, _OUT_OF_COVERAGE, str(error))
        # part_coverage_mismatch / part_not_found mean our own inputs disagree;
        # those are validated up front, so reaching here is a gate contract bug.
        raise TranscriptionGateError("gate_timeline_mismatch", str(error)) from error
    except TimeValidationError as error:
        if error.reason in _COVERAGE_MAPPING_REASONS:
            return _reject(cue, _OUT_OF_COVERAGE, str(error))
        raise

    try:
        part_relative_interval = HalfOpenInterval(part_relative_start, part_relative_end)
        collection_interval = HalfOpenInterval(collection_start, collection_end)
    except TimeValidationError as error:
        # Translation preserves positive duration, so this is a defensive guard on
        # the mapped interval rather than a reachable path for a projected cue.
        return _reject(cue, _NON_POSITIVE_DURATION, str(error))
    return part_relative_interval, collection_interval


def _map_boundary(
    boundary: ExactTime,
    part_id: str,
    coverage_start: PartCoverageStart,
    timeline: CollectionTimeline,
) -> tuple[ExactTime, ExactTime]:
    part_relative = PartRelativeTime.from_raw(_as_raw_pts(boundary), coverage_start)
    collection: CollectionVirtualTime = timeline.map_part_relative_time(part_id, part_relative)
    return part_relative.time, collection.time


def _ordering_reason(
    cue: ProjectedAsrCue,
    collection_interval: HalfOpenInterval,
    previous: GatedAsrCue | None,
) -> tuple[str, str] | None:
    if previous is None:
        return None
    if cue.ordinal <= previous.ordinal:
        return _NON_MONOTONIC, (
            f"Cue ordinal {cue.ordinal} does not advance past the previous admitted "
            f"ordinal {previous.ordinal}."
        )
    if collection_interval.start < previous.collection_interval.start:
        return _NON_MONOTONIC, "Cue start precedes the previous admitted cue start."
    if collection_interval == previous.collection_interval:
        return _PROCESSING_DUPLICATION, "Cue duplicates the previous admitted cue interval."
    if collection_interval.start < previous.collection_interval.end:
        return _PROCESSING_DUPLICATION, (
            "Cue overlaps the previous admitted cue, a processing-window duplication."
        )
    return None


def _content_reason(cue: ProjectedAsrCue, rules: TimingGateRuleset) -> tuple[str, str] | None:
    """Reject a cue with no visible text or an implausible duration-to-text rate.

    An empty cue is a missing-text rejection in its own right, kept distinct from
    the duration-to-text band so the two content failures never share one reason.
    """

    visible_characters = sum(1 for character in cue.text if not character.isspace())
    if visible_characters == 0:
        return _MISSING_TEXT, "Cue carries no visible text for its positive interval."
    duration = (cue.interval.end - cue.interval.start).as_fraction()
    low = rules.duration_to_text.minimum_seconds_per_character.as_fraction() * visible_characters
    high = rules.duration_to_text.maximum_seconds_per_character.as_fraction() * visible_characters
    if duration < low:
        return _DURATION_IMPLAUSIBLE, (
            f"Cue duration is implausibly short for {visible_characters} characters "
            f"(under {low} seconds)."
        )
    if duration > high:
        return _DURATION_IMPLAUSIBLE, (
            f"Cue duration is implausibly long for {visible_characters} characters "
            f"(over {high} seconds)."
        )
    return None


def _reject(cue: ProjectedAsrCue, reason: str, message: str) -> RejectedAsrCue:
    return RejectedAsrCue(
        ordinal=cue.ordinal,
        raw_interval=cue.interval,
        reason=reason,
        message=message,
    )


# --- Input preconditions ----------------------------------------------------


def _determinate_envelope(part_coverage: StreamCoverage) -> HalfOpenInterval:
    if part_coverage.coverage is None or part_coverage.diagnostics:
        raise TranscriptionGateError(
            "gate_coverage_indeterminate",
            "Gating requires a determinate observed stream-coverage envelope.",
        )
    return part_coverage.coverage


def _part_coverage_start(
    timeline: CollectionTimeline, part_id: str, envelope: HalfOpenInterval
) -> PartCoverageStart:
    """Bind the Part's coverage start, proving the timeline matches the evidence.

    The timeline must carry ``part_id`` with the same coverage envelope as the
    observed evidence; otherwise the gate cannot map cues consistently and the
    disagreement is a caller contract failure rather than a per-cue rejection.
    """

    part = next((candidate for candidate in timeline.parts if candidate.part_id == part_id), None)
    if part is None:
        raise TranscriptionGateError(
            "gate_timeline_mismatch",
            f"Collection timeline has no Part {part_id!r} to gate.",
        )
    if part.coverage != envelope:
        raise TranscriptionGateError(
            "gate_timeline_mismatch",
            "Collection timeline Part coverage disagrees with the observed coverage evidence.",
        )
    return PartCoverageStart(_as_raw_pts(envelope.start))


def _as_raw_pts(value: ExactTime) -> RawPtsTime:
    """Carry an exact source coordinate as a raw PTS in its own exact time base.

    A normalized ``ExactTime`` is already the exact source coordinate, so a raw
    PTS equal to its numerator over a ``1/denominator`` time base reproduces it
    exactly, letting the existing Phase 2 mapping types do the translation.
    """

    return RawPtsTime(raw_pts=value.numerator, time_base=ExactTime(1, value.denominator))


# --- Versioned ruleset loader -----------------------------------------------


def load_timing_gate_ruleset(project_root: Path) -> TimingGateRuleset:
    """Load and version-bind the conservative timing-gate ruleset from config.

    ``transcription-rules.json`` names the ``timing_gate_version`` and the bound
    ``timing-gate-rules.json`` must declare it, keep the ``calibration_required``
    mark, and carry a valid positive duration-to-text band. Any drift or
    malformed value raises ``timing_gate_rules_invalid`` -- the ruleset is our own
    revalidated ground truth.
    """

    expected_version = _timing_gate_version(project_root)
    document = _read_json_mapping(project_root.joinpath(*_GATE_RULES_RELATIVE_PATH))
    if document.get("schema_version") != 1 or document.get("version") != expected_version:
        raise TranscriptionGateError(
            "timing_gate_rules_invalid",
            "Timing-gate rules do not match the bound version identity.",
        )
    if document.get("calibration_required") is not True:
        raise TranscriptionGateError(
            "timing_gate_rules_invalid",
            "Timing-gate rules must keep the calibration_required mark.",
        )
    return TimingGateRuleset(
        version=expected_version,
        calibration_required=True,
        duration_to_text=_duration_to_text_bounds(document.get("duration_to_text")),
    )


def _timing_gate_version(project_root: Path) -> str:
    rules = _read_json_mapping(project_root.joinpath(*_RULES_RELATIVE_PATH))
    if rules.get("schema_version") != 1:
        raise TranscriptionGateError(
            "timing_gate_rules_invalid", "Transcription rules have an invalid schema."
        )
    version = rules.get("timing_gate_version")
    if not isinstance(version, str) or not version:
        raise TranscriptionGateError(
            "timing_gate_rules_invalid", "Transcription rules omit a valid timing_gate_version."
        )
    return version


def _duration_to_text_bounds(value: object) -> DurationToTextBounds:
    if not isinstance(value, Mapping):
        raise TranscriptionGateError(
            "timing_gate_rules_invalid", "Timing-gate rules omit a duration_to_text band."
        )
    minimum = _positive_exact_time(value.get("minimum_seconds_per_character"))
    maximum = _positive_exact_time(value.get("maximum_seconds_per_character"))
    if maximum < minimum:
        raise TranscriptionGateError(
            "timing_gate_rules_invalid",
            "Timing-gate maximum seconds-per-character is below the minimum.",
        )
    return DurationToTextBounds(
        minimum_seconds_per_character=minimum,
        maximum_seconds_per_character=maximum,
    )


def _positive_exact_time(value: object) -> ExactTime:
    if not isinstance(value, Mapping):
        raise TranscriptionGateError(
            "timing_gate_rules_invalid", "A timing-gate duration bound is not an object."
        )
    numerator = value.get("numerator")
    denominator = value.get("denominator")
    if not _is_int(numerator) or not _is_int(denominator) or denominator <= 0:
        raise TranscriptionGateError(
            "timing_gate_rules_invalid",
            "A timing-gate duration bound omits an integer numerator or positive denominator.",
        )
    exact = ExactTime(numerator, denominator)
    if exact <= ExactTime(0):
        raise TranscriptionGateError(
            "timing_gate_rules_invalid", "A timing-gate duration bound must be positive."
        )
    return exact


def _read_json_mapping(path: Path) -> Mapping[str, object]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TranscriptionGateError(
            "timing_gate_rules_invalid", f"{path.name} cannot be read."
        ) from error
    if not isinstance(decoded, Mapping):
        raise TranscriptionGateError(
            "timing_gate_rules_invalid", f"{path.name} is not a JSON object."
        )
    return decoded


# --- Small shared helpers ---------------------------------------------------


def _interval_as_json(interval: HalfOpenInterval) -> dict[str, object]:
    return {"start": _time_as_json(interval.start), "end": _time_as_json(interval.end)}


def _time_as_json(value: ExactTime) -> dict[str, int]:
    return {"numerator": value.numerator, "denominator": value.denominator}


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


__all__ = [
    "CanonicalTimelineGateResult",
    "DurationToTextBounds",
    "GatedAsrCue",
    "RejectedAsrCue",
    "TimingGateRuleset",
    "TranscriptionGateError",
    "gate_projected_cues",
    "load_timing_gate_ruleset",
]
