"""Phase 7's Versioned suspicion detection rules (ticket 05).

Ticket 04 gates projected ASR cues onto the canonical timeline. Ticket 05 locates
*Suspicious intervals* -- the time ranges a later independent-review pass (ticket
06) should re-transcribe -- using six deterministic detectors over the gated cues
and the retained Audio analysis report (ADR 0043):

* **vad_coverage** -- ASR text sitting over a VAD non-speech region (the classic
  hallucinated-over-silence artifact);
* **coverage_checks** -- non-silent-but-textless speech: a VAD speech-likely
  region no admitted cue covers;
* **confidence** -- a cue carrying a sub-threshold token confidence;
* **repetition** -- a degenerate repeated token loop within one cue, or a run of
  consecutive identical cues;
* **language_switching** -- a cue switching source language more often than the
  threshold (ordinary mixed Chinese/English is *never* flagged, only heavy
  switching is surfaced for review); and
* **numbers_entities** -- a cue carrying multi-digit numbers or alphanumeric
  entities, both error-prone for ASR.

Every detector is a pure function over the typed projections and retained
evidence; each flagged interval records its detector identity, machine-readable
evidence, and an exact half-open time range in the Part's source-time coordinate
(the coordinate the gated cue's ``raw_interval`` and the VAD evidence natively
share). All arithmetic is exact rational arithmetic. Thresholds live in a
versioned, ``calibration_required`` ruleset -- real calibration happens only in a
separately authorized real-world session -- and the ruleset version is recorded
in every result. No model is downloaded or executed. See
``docs/PHASE_07_SPECIFICATION.md``, the Transcription Context, and ADR 0043/0044.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import TypeGuard

from video_content_pipeline.timecode import ExactTime, HalfOpenInterval, TimeValidationError
from video_content_pipeline.transcription_gates import GatedAsrCue

_RULES_RELATIVE_PATH = ("config", "transcription", "transcription-rules.json")
_SUSPICION_RULES_RELATIVE_PATH = ("config", "transcription", "suspicion-rules.json")

# The three formal voice-activity states the Audio analysis report records; the
# detectors read speech-likely and non-speech and ignore the indeterminate ones.
SPEECH_LIKELY = "speech_likely"
NON_SPEECH = "non_speech"
INDETERMINATE = "indeterminate"

# The six detector identities, in the fixed order the orchestrator runs them.
_DETECTOR_NAMES = (
    "vad_coverage",
    "coverage_checks",
    "confidence",
    "repetition",
    "language_switching",
    "numbers_entities",
)

# The per-detector machine-readable reasons; a flag carries exactly one, hoisted
# to constants as the sibling gate module does for its rejection reasons. The
# text-over-silence reason is public because ticket 06's arbitration keys its
# drop-silence rule on it: this module owns the token, so the consumer imports it
# rather than hand-copying the literal (the AGENTS.md "one owner per term" rule).
REASON_TEXT_OVER_SILENCE = "asr_text_over_silence"
_REASON_TEXTLESS_SPEECH = "non_silent_but_textless"
_REASON_LOW_CONFIDENCE = "low_confidence_tokens"
_REASON_REPETITION = "repeated_text_run"
_REASON_LANGUAGE_SWITCHING = "excessive_language_switching"
_REASON_NUMERIC_ENTITY = "numeric_or_entity_content"


class SuspicionRulesError(ValueError):
    """A rejected suspicion ruleset or retained-evidence input with a stable reason.

    The detectors flag untrusted *cues* by returning evidence; this error is raised
    only when our own ruleset config drifts (``suspicion_rules_invalid``) or the
    retained audio evidence -- our revalidated upstream ground truth -- is malformed
    (``suspicion_evidence_invalid``).
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


# --- Versioned, calibration-required ruleset --------------------------------


@dataclass(frozen=True)
class VadCoverageRule:
    """Conservative minimum text-over-silence overlap that flags a cue."""

    calibration_required: bool
    minimum_silence_overlap_seconds: ExactTime

    def as_json(self) -> dict[str, object]:
        return {
            "calibration_required": self.calibration_required,
            "minimum_silence_overlap_seconds": _time_as_json(self.minimum_silence_overlap_seconds),
        }


@dataclass(frozen=True)
class CoverageCheckRule:
    """Conservative minimum uncovered-speech duration that flags a textless gap."""

    calibration_required: bool
    minimum_textless_speech_seconds: ExactTime

    def as_json(self) -> dict[str, object]:
        return {
            "calibration_required": self.calibration_required,
            "minimum_textless_speech_seconds": _time_as_json(self.minimum_textless_speech_seconds),
        }


@dataclass(frozen=True)
class ConfidenceRule:
    """Conservative token-confidence floor below which a cue is flagged."""

    calibration_required: bool
    minimum_token_confidence: float

    def as_json(self) -> dict[str, object]:
        return {
            "calibration_required": self.calibration_required,
            "minimum_token_confidence": self.minimum_token_confidence,
        }


@dataclass(frozen=True)
class RepetitionRule:
    """The maximum consecutive repetition allowed before a run is flagged."""

    calibration_required: bool
    maximum_consecutive_repetitions: int

    def as_json(self) -> dict[str, object]:
        return {
            "calibration_required": self.calibration_required,
            "maximum_consecutive_repetitions": self.maximum_consecutive_repetitions,
        }


@dataclass(frozen=True)
class LanguageSwitchingRule:
    """The maximum in-cue language switches allowed before a cue is flagged."""

    calibration_required: bool
    maximum_language_switches: int

    def as_json(self) -> dict[str, object]:
        return {
            "calibration_required": self.calibration_required,
            "maximum_language_switches": self.maximum_language_switches,
        }


@dataclass(frozen=True)
class NumbersEntitiesRule:
    """The minimum consecutive-digit run length that marks numeric content."""

    calibration_required: bool
    minimum_digit_run: int

    def as_json(self) -> dict[str, object]:
        return {
            "calibration_required": self.calibration_required,
            "minimum_digit_run": self.minimum_digit_run,
        }


@dataclass(frozen=True)
class SuspicionRuleset:
    """The versioned detector set and its conservative, calibration-required bounds."""

    version: str
    calibration_required: bool
    vad_coverage: VadCoverageRule
    coverage_checks: CoverageCheckRule
    confidence: ConfidenceRule
    repetition: RepetitionRule
    language_switching: LanguageSwitchingRule
    numbers_entities: NumbersEntitiesRule

    def as_json(self) -> dict[str, object]:
        return {
            "version": self.version,
            "calibration_required": self.calibration_required,
            "detectors": {
                "vad_coverage": self.vad_coverage.as_json(),
                "coverage_checks": self.coverage_checks.as_json(),
                "confidence": self.confidence.as_json(),
                "repetition": self.repetition.as_json(),
                "language_switching": self.language_switching.as_json(),
                "numbers_entities": self.numbers_entities.as_json(),
            },
        }


# --- Retained VAD evidence --------------------------------------------------


@dataclass(frozen=True)
class VadRegion:
    """One typed voice-activity region in the Part's source-time coordinate."""

    interval: HalfOpenInterval
    state: str


# --- Detection outcomes -----------------------------------------------------


@dataclass(frozen=True)
class SuspiciousInterval:
    """One flagged interval: its detector identity, evidence, and exact time range.

    ``interval`` is a half-open range in the Part's source-time coordinate; a cue
    detector reports the triggering cue's ``raw_interval`` and a VAD detector
    reports the exact overlapping or uncovered audio sub-range. ``evidence`` is a
    machine-readable, JSON-ready record of why the detector fired.
    """

    detector: str
    part_id: str
    interval: HalfOpenInterval
    reason: str
    evidence: Mapping[str, object]

    def as_json(self) -> dict[str, object]:
        return {
            "detector": self.detector,
            "part_id": self.part_id,
            "reason": self.reason,
            "interval": _interval_as_json(self.interval),
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class SuspicionDetectionResult:
    """The deterministic outcome of running every detector over one Part."""

    part_id: str
    rules_version: str
    calibration_required: bool
    suspicious_intervals: tuple[SuspiciousInterval, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "part_id": self.part_id,
            "rules_version": self.rules_version,
            "calibration_required": self.calibration_required,
            "suspicious_intervals": [flag.as_json() for flag in self.suspicious_intervals],
        }


def detect_suspicious_intervals(
    *,
    part_id: str,
    cues: Sequence[GatedAsrCue],
    vad_regions: Sequence[VadRegion],
    rules: SuspicionRuleset,
) -> SuspicionDetectionResult:
    """Run all six detectors over one Part and record the versioned ruleset.

    Every flag is retained; the result is sorted deterministically by interval start
    then end then detector order, so the same inputs always produce the same report.
    """

    flags: list[SuspiciousInterval] = []
    flags.extend(
        detect_vad_coverage(
            part_id=part_id, cues=cues, vad_regions=vad_regions, rule=rules.vad_coverage
        )
    )
    flags.extend(
        detect_coverage_checks(
            part_id=part_id, cues=cues, vad_regions=vad_regions, rule=rules.coverage_checks
        )
    )
    flags.extend(detect_confidence(part_id=part_id, cues=cues, rule=rules.confidence))
    flags.extend(detect_repetition(part_id=part_id, cues=cues, rule=rules.repetition))
    flags.extend(
        detect_language_switching(part_id=part_id, cues=cues, rule=rules.language_switching)
    )
    flags.extend(
        detect_numbers_entities(part_id=part_id, cues=cues, rule=rules.numbers_entities)
    )
    ordered = tuple(sorted(flags, key=_flag_sort_key))
    return SuspicionDetectionResult(
        part_id=part_id,
        rules_version=rules.version,
        calibration_required=rules.calibration_required,
        suspicious_intervals=ordered,
    )


def _flag_sort_key(flag: SuspiciousInterval) -> tuple[Fraction, Fraction, int]:
    return (
        flag.interval.start.as_fraction(),
        flag.interval.end.as_fraction(),
        _DETECTOR_NAMES.index(flag.detector),
    )


# --- Detector 1: VAD coverage (ASR text over silence) -----------------------


def detect_vad_coverage(
    *,
    part_id: str,
    cues: Sequence[GatedAsrCue],
    vad_regions: Sequence[VadRegion],
    rule: VadCoverageRule,
) -> tuple[SuspiciousInterval, ...]:
    """Flag admitted cues that sit over a VAD non-speech region.

    Each (cue, silence) overlap of at least the conservative minimum duration is a
    separate flag whose range is the exact overlap. Text placed over silence is the
    canonical hallucination artifact, so it is surfaced for independent review.
    """

    threshold = rule.minimum_silence_overlap_seconds.as_fraction()
    silences = [region for region in vad_regions if region.state == NON_SPEECH]
    flags: list[SuspiciousInterval] = []
    for cue in cues:
        for silence in silences:
            overlap = _intersection(cue.raw_interval, silence.interval)
            if overlap is None or _duration(overlap) < threshold:
                continue
            flags.append(
                SuspiciousInterval(
                    detector="vad_coverage",
                    part_id=part_id,
                    interval=overlap,
                    reason=REASON_TEXT_OVER_SILENCE,
                    evidence={
                        "cue_ordinal": cue.ordinal,
                        "cue_text": cue.text,
                        "silence_interval": _interval_as_json(silence.interval),
                        "overlap_seconds": _fraction_as_json(_duration(overlap)),
                    },
                )
            )
    return tuple(flags)


# --- Detector 2: coverage checks (non-silent-but-textless) ------------------


def detect_coverage_checks(
    *,
    part_id: str,
    cues: Sequence[GatedAsrCue],
    vad_regions: Sequence[VadRegion],
    rule: CoverageCheckRule,
) -> tuple[SuspiciousInterval, ...]:
    """Flag VAD speech-likely regions no admitted cue covers.

    The union of admitted cue intervals is subtracted from each speech-likely
    region; every remaining sub-interval of at least the conservative minimum
    duration is a textless-speech gap surfaced for review.
    """

    threshold = rule.minimum_textless_speech_seconds.as_fraction()
    covered = [cue.raw_interval for cue in cues]
    flags: list[SuspiciousInterval] = []
    for region in vad_regions:
        if region.state != SPEECH_LIKELY:
            continue
        for gap in _subtract(region.interval, covered):
            if _duration(gap) < threshold:
                continue
            flags.append(
                SuspiciousInterval(
                    detector="coverage_checks",
                    part_id=part_id,
                    interval=gap,
                    reason=_REASON_TEXTLESS_SPEECH,
                    evidence={
                        "speech_interval": _interval_as_json(region.interval),
                        "textless_seconds": _fraction_as_json(_duration(gap)),
                    },
                )
            )
    return tuple(flags)


# --- Detector 3: confidence -------------------------------------------------


def detect_confidence(
    *, part_id: str, cues: Sequence[GatedAsrCue], rule: ConfidenceRule
) -> tuple[SuspiciousInterval, ...]:
    """Flag cues carrying a scored token below the conservative confidence floor.

    Cues without any scored token carry no confidence evidence and are never
    flagged -- absent confidence is not low confidence.
    """

    flags: list[SuspiciousInterval] = []
    for cue in cues:
        low = [
            {"text": token.text, "confidence": token.confidence}
            for token in cue.tokens
            if token.confidence is not None and token.confidence < rule.minimum_token_confidence
        ]
        if not low:
            continue
        flags.append(
            SuspiciousInterval(
                detector="confidence",
                part_id=part_id,
                interval=cue.raw_interval,
                reason=_REASON_LOW_CONFIDENCE,
                evidence={
                    "cue_ordinal": cue.ordinal,
                    "minimum_token_confidence": rule.minimum_token_confidence,
                    "low_confidence_tokens": low,
                },
            )
        )
    return tuple(flags)


# --- Detector 4: repetition -------------------------------------------------


def detect_repetition(
    *, part_id: str, cues: Sequence[GatedAsrCue], rule: RepetitionRule
) -> tuple[SuspiciousInterval, ...]:
    """Flag a degenerate repeated run within a cue or across consecutive cues.

    Within a cue, the longest run of consecutive identical units (tokens when
    present, otherwise whitespace-split words) is measured; across cues, a run of
    consecutive cues with identical text is measured. A run strictly longer than the
    allowed maximum is flagged -- the classic decoder-loop artifact.
    """

    flags: list[SuspiciousInterval] = []
    for cue in cues:
        units = (
            [token.text for token in cue.tokens] if cue.tokens else cue.text.split()
        )
        unit, run_length = _longest_consecutive_run(units)
        if run_length > rule.maximum_consecutive_repetitions:
            flags.append(
                SuspiciousInterval(
                    detector="repetition",
                    part_id=part_id,
                    interval=cue.raw_interval,
                    reason=_REASON_REPETITION,
                    evidence={
                        "scope": "within_cue",
                        "cue_ordinal": cue.ordinal,
                        "repeated_unit": unit,
                        "run_length": run_length,
                    },
                )
            )
    flags.extend(_across_cue_repetition(part_id, cues, rule))
    return tuple(flags)


def _across_cue_repetition(
    part_id: str, cues: Sequence[GatedAsrCue], rule: RepetitionRule
) -> list[SuspiciousInterval]:
    flags: list[SuspiciousInterval] = []
    run_start = 0
    while run_start < len(cues):
        run_end = run_start + 1
        while run_end < len(cues) and cues[run_end].text == cues[run_start].text:
            run_end += 1
        run_length = run_end - run_start
        if run_length > rule.maximum_consecutive_repetitions:
            span = HalfOpenInterval(
                cues[run_start].raw_interval.start, cues[run_end - 1].raw_interval.end
            )
            ordinals = [cues[index].ordinal for index in range(run_start, run_end)]
            flags.append(
                SuspiciousInterval(
                    detector="repetition",
                    part_id=part_id,
                    interval=span,
                    reason=_REASON_REPETITION,
                    evidence={
                        "scope": "across_cues",
                        "repeated_text": cues[run_start].text,
                        "run_length": run_length,
                        "cue_ordinals": ordinals,
                    },
                )
            )
        run_start = run_end
    return flags


def _longest_consecutive_run(units: Sequence[str]) -> tuple[str, int]:
    """Return the unit and length of the longest run of consecutive equal units."""

    best_unit = ""
    best_length = 0
    current_length = 0
    for index, unit in enumerate(units):
        if index > 0 and unit == units[index - 1]:
            current_length += 1
        else:
            current_length = 1
        if current_length > best_length:
            best_length = current_length
            best_unit = unit
    return best_unit, best_length


# --- Detector 5: language switching -----------------------------------------


def detect_language_switching(
    *, part_id: str, cues: Sequence[GatedAsrCue], rule: LanguageSwitchingRule
) -> tuple[SuspiciousInterval, ...]:
    """Flag cues that switch source language more often than the threshold.

    Mixed Chinese/English is expressed as adjacent language spans and is *never*
    rewritten; a couple of switches per cue is ordinary and passes. Only heavy
    switching -- an error-prone boundary density -- is surfaced for review.
    """

    flags: list[SuspiciousInterval] = []
    for cue in cues:
        languages = [span.language for span in cue.language_spans]
        switches = sum(
            1 for index in range(1, len(languages)) if languages[index] != languages[index - 1]
        )
        if switches > rule.maximum_language_switches:
            flags.append(
                SuspiciousInterval(
                    detector="language_switching",
                    part_id=part_id,
                    interval=cue.raw_interval,
                    reason=_REASON_LANGUAGE_SWITCHING,
                    evidence={
                        "cue_ordinal": cue.ordinal,
                        "languages": languages,
                        "switch_count": switches,
                    },
                )
            )
    return tuple(flags)


# --- Detector 6: numbers / entities -----------------------------------------


def detect_numbers_entities(
    *, part_id: str, cues: Sequence[GatedAsrCue], rule: NumbersEntitiesRule
) -> tuple[SuspiciousInterval, ...]:
    """Flag cues carrying multi-digit numbers or alphanumeric entities.

    Deterministic surface matches only: a maximal run of at least
    ``minimum_digit_run`` decimal digits (a number), or a whitespace-separated word
    mixing letters and digits (an entity-like token such as a model name). Both are
    error-prone for ASR and worth an independent look; richer named-entity detection
    is a ``calibration_required`` concern deferred to real-world testing.
    """

    flags: list[SuspiciousInterval] = []
    for cue in cues:
        matches = _numeric_and_entity_matches(cue.text, rule.minimum_digit_run)
        if not matches:
            continue
        flags.append(
            SuspiciousInterval(
                detector="numbers_entities",
                part_id=part_id,
                interval=cue.raw_interval,
                reason=_REASON_NUMERIC_ENTITY,
                evidence={"cue_ordinal": cue.ordinal, "matches": matches},
            )
        )
    return tuple(flags)


def _numeric_and_entity_matches(text: str, minimum_digit_run: int) -> list[str]:
    matches: list[str] = []
    for word in text.split():
        has_letter = any(character.isalpha() for character in word)
        has_digit = any(character.isdigit() for character in word)
        if has_letter and has_digit:
            matches.append(word)
    for run in _digit_runs(text):
        if len(run) >= minimum_digit_run:
            matches.append(run)
    return matches


def _digit_runs(text: str) -> list[str]:
    runs: list[str] = []
    current = ""
    for character in text:
        if character.isdigit():
            current += character
        elif current:
            runs.append(current)
            current = ""
    if current:
        runs.append(current)
    return runs


# --- Retained VAD evidence adapter ------------------------------------------


def vad_regions_from_part_evidence(part_evidence: Mapping[str, object]) -> tuple[VadRegion, ...]:
    """Read typed VAD regions from one retained Audio analysis report Part.

    The report's ``voice_activity_intervals`` are the complete formal partition of
    usable audio into speech-likely, non-speech, and indeterminate; the detectors
    read that partition directly and carry each interval through as-is. The report's
    ``long_silences`` are a *derived subset* of those non-speech intervals, so they
    are deliberately not re-added here -- doing so would double-count every silence
    and let ``detect_vad_coverage`` flag the same text-over-silence twice. The
    retained report is our revalidated upstream ground truth, so a malformed shape
    raises ``suspicion_evidence_invalid`` rather than silently producing no regions.
    """

    intervals = part_evidence.get("voice_activity_intervals")
    if not isinstance(intervals, list):
        raise SuspicionRulesError(
            "suspicion_evidence_invalid", "VAD evidence omits a voice_activity_intervals list."
        )
    regions: list[VadRegion] = []
    for item in intervals:
        if not isinstance(item, Mapping):
            raise SuspicionRulesError(
                "suspicion_evidence_invalid", "A voice-activity interval is not an object."
            )
        state = item.get("state")
        if state not in (SPEECH_LIKELY, NON_SPEECH, INDETERMINATE):
            raise SuspicionRulesError(
                "suspicion_evidence_invalid", "A voice-activity interval has an unknown state."
            )
        regions.append(VadRegion(interval=_interval_from_json(item.get("interval")), state=state))
    return tuple(regions)


# --- Versioned ruleset loader -----------------------------------------------


def load_suspicion_ruleset(project_root: Path) -> SuspicionRuleset:
    """Load and version-bind the conservative suspicion ruleset from config.

    ``transcription-rules.json`` names the ``suspicion_rules_version`` and the bound
    ``suspicion-rules.json`` must declare it, keep the top-level and every
    per-detector ``calibration_required`` mark, and carry a valid, conservative
    threshold for each of the six detectors. Any drift or malformed value raises
    ``suspicion_rules_invalid`` -- the ruleset is our own revalidated ground truth.
    """

    expected_version = _suspicion_rules_version(project_root)
    document = _read_json_mapping(project_root.joinpath(*_SUSPICION_RULES_RELATIVE_PATH))
    if document.get("schema_version") != 1 or document.get("version") != expected_version:
        raise SuspicionRulesError(
            "suspicion_rules_invalid", "Suspicion rules do not match the bound version identity."
        )
    if document.get("calibration_required") is not True:
        raise SuspicionRulesError(
            "suspicion_rules_invalid", "Suspicion rules must keep the top-level calibration mark."
        )
    detectors = document.get("detectors")
    if not isinstance(detectors, Mapping):
        raise SuspicionRulesError(
            "suspicion_rules_invalid", "Suspicion rules omit a detectors block."
        )
    return SuspicionRuleset(
        version=expected_version,
        calibration_required=True,
        vad_coverage=VadCoverageRule(
            calibration_required=True,
            minimum_silence_overlap_seconds=_positive_exact_time(
                _detector(detectors, "vad_coverage").get("minimum_silence_overlap_seconds")
            ),
        ),
        coverage_checks=CoverageCheckRule(
            calibration_required=True,
            minimum_textless_speech_seconds=_positive_exact_time(
                _detector(detectors, "coverage_checks").get("minimum_textless_speech_seconds")
            ),
        ),
        confidence=ConfidenceRule(
            calibration_required=True,
            minimum_token_confidence=_unit_interval(
                _detector(detectors, "confidence").get("minimum_token_confidence")
            ),
        ),
        repetition=RepetitionRule(
            calibration_required=True,
            maximum_consecutive_repetitions=_positive_int(
                _detector(detectors, "repetition").get("maximum_consecutive_repetitions")
            ),
        ),
        language_switching=LanguageSwitchingRule(
            calibration_required=True,
            maximum_language_switches=_non_negative_int(
                _detector(detectors, "language_switching").get("maximum_language_switches")
            ),
        ),
        numbers_entities=NumbersEntitiesRule(
            calibration_required=True,
            minimum_digit_run=_positive_int(
                _detector(detectors, "numbers_entities").get("minimum_digit_run")
            ),
        ),
    )


def _suspicion_rules_version(project_root: Path) -> str:
    rules = _read_json_mapping(project_root.joinpath(*_RULES_RELATIVE_PATH))
    if rules.get("schema_version") != 1:
        raise SuspicionRulesError(
            "suspicion_rules_invalid", "Transcription rules have an invalid schema."
        )
    version = rules.get("suspicion_rules_version")
    if not isinstance(version, str) or not version:
        raise SuspicionRulesError(
            "suspicion_rules_invalid", "Transcription rules omit a valid suspicion_rules_version."
        )
    return version


def _detector(detectors: Mapping[str, object], name: str) -> Mapping[str, object]:
    block = detectors.get(name)
    if not isinstance(block, Mapping):
        raise SuspicionRulesError(
            "suspicion_rules_invalid", f"Suspicion rules omit the {name!r} detector."
        )
    if block.get("calibration_required") is not True:
        raise SuspicionRulesError(
            "suspicion_rules_invalid",
            f"Suspicion detector {name!r} must keep its calibration_required mark.",
        )
    return block


def _positive_exact_time(value: object) -> ExactTime:
    if not isinstance(value, Mapping):
        raise SuspicionRulesError(
            "suspicion_rules_invalid", "A suspicion duration bound is not an object."
        )
    numerator = value.get("numerator")
    denominator = value.get("denominator")
    if not _is_int(numerator) or not _is_int(denominator) or denominator <= 0:
        raise SuspicionRulesError(
            "suspicion_rules_invalid",
            "A suspicion duration bound omits an integer numerator or positive denominator.",
        )
    exact = ExactTime(numerator, denominator)
    if exact <= ExactTime(0):
        raise SuspicionRulesError(
            "suspicion_rules_invalid", "A suspicion duration bound must be positive."
        )
    return exact


def _unit_interval(value: object) -> float:
    if not _is_real(value) or not 0.0 <= float(value) <= 1.0:
        raise SuspicionRulesError(
            "suspicion_rules_invalid", "A suspicion confidence bound must lie in [0, 1]."
        )
    return float(value)


def _positive_int(value: object) -> int:
    if not _is_int(value) or value < 1:
        raise SuspicionRulesError(
            "suspicion_rules_invalid", "A suspicion count bound must be a positive integer."
        )
    return value


def _non_negative_int(value: object) -> int:
    if not _is_int(value) or value < 0:
        raise SuspicionRulesError(
            "suspicion_rules_invalid", "A suspicion count bound must be a non-negative integer."
        )
    return value


def _read_json_mapping(path: Path) -> Mapping[str, object]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SuspicionRulesError(
            "suspicion_rules_invalid", f"{path.name} cannot be read."
        ) from error
    if not isinstance(decoded, Mapping):
        raise SuspicionRulesError(
            "suspicion_rules_invalid", f"{path.name} is not a JSON object."
        )
    return decoded


# --- Exact interval helpers -------------------------------------------------


def _intersection(left: HalfOpenInterval, right: HalfOpenInterval) -> HalfOpenInterval | None:
    start = max(left.start, right.start, key=lambda time: time.as_fraction())
    end = min(left.end, right.end, key=lambda time: time.as_fraction())
    if start.as_fraction() >= end.as_fraction():
        return None
    return HalfOpenInterval(start, end)


def _subtract(
    interval: HalfOpenInterval, covers: Sequence[HalfOpenInterval]
) -> tuple[HalfOpenInterval, ...]:
    """Return the parts of ``interval`` left after removing every covering interval."""

    relevant = sorted(
        (cover for cover in covers if cover.overlaps(interval)),
        key=lambda cover: cover.start.as_fraction(),
    )
    remaining: list[HalfOpenInterval] = []
    cursor = interval.start
    for cover in relevant:
        if cover.start.as_fraction() > cursor.as_fraction():
            remaining.append(HalfOpenInterval(cursor, _min_time(cover.start, interval.end)))
        if cover.end.as_fraction() > cursor.as_fraction():
            cursor = cover.end
        if cursor.as_fraction() >= interval.end.as_fraction():
            break
    if cursor.as_fraction() < interval.end.as_fraction():
        remaining.append(HalfOpenInterval(cursor, interval.end))
    return tuple(remaining)


def _min_time(left: ExactTime, right: ExactTime) -> ExactTime:
    return left if left.as_fraction() <= right.as_fraction() else right


def _duration(interval: HalfOpenInterval) -> Fraction:
    return interval.end.as_fraction() - interval.start.as_fraction()


def _interval_from_json(value: object) -> HalfOpenInterval:
    if not isinstance(value, Mapping):
        raise SuspicionRulesError(
            "suspicion_evidence_invalid", "A VAD interval is not an object."
        )
    try:
        return HalfOpenInterval(
            _exact_time_from_json(value.get("start")), _exact_time_from_json(value.get("end"))
        )
    except TimeValidationError as error:
        raise SuspicionRulesError(
            "suspicion_evidence_invalid", "A VAD interval is not a positive half-open interval."
        ) from error


def _exact_time_from_json(value: object) -> ExactTime:
    if not isinstance(value, Mapping):
        raise SuspicionRulesError("suspicion_evidence_invalid", "A VAD time is not an object.")
    numerator = value.get("numerator")
    denominator = value.get("denominator")
    if not _is_int(numerator) or not _is_int(denominator) or denominator <= 0:
        raise SuspicionRulesError(
            "suspicion_evidence_invalid",
            "A VAD time omits an integer numerator or positive denominator.",
        )
    return ExactTime(numerator, denominator)


def _interval_as_json(interval: HalfOpenInterval) -> dict[str, object]:
    return {"start": _time_as_json(interval.start), "end": _time_as_json(interval.end)}


def _time_as_json(value: ExactTime) -> dict[str, int]:
    return {"numerator": value.numerator, "denominator": value.denominator}


def _fraction_as_json(value: Fraction) -> dict[str, int]:
    return {"numerator": value.numerator, "denominator": value.denominator}


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_real(value: object) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool)


__all__ = [
    "INDETERMINATE",
    "NON_SPEECH",
    "REASON_TEXT_OVER_SILENCE",
    "SPEECH_LIKELY",
    "ConfidenceRule",
    "CoverageCheckRule",
    "LanguageSwitchingRule",
    "NumbersEntitiesRule",
    "RepetitionRule",
    "SuspicionDetectionResult",
    "SuspicionRuleset",
    "SuspicionRulesError",
    "SuspiciousInterval",
    "VadCoverageRule",
    "VadRegion",
    "detect_confidence",
    "detect_coverage_checks",
    "detect_language_switching",
    "detect_numbers_entities",
    "detect_repetition",
    "detect_suspicious_intervals",
    "detect_vad_coverage",
    "load_suspicion_ruleset",
    "vad_regions_from_part_evidence",
]
