"""Phase 7's interval-scoped review and Deterministic transcription arbitration (ticket 06).

Ticket 05 flags *Suspicious intervals* over the gated primary ASR cues. Ticket 06
turns those flags into an independent second-ASR review scope, records whether the
review is genuinely independent, arbitrates each reviewed interval with versioned
deterministic rules (ADR 0044), and gates the Verbatim transcription artifact:

* **Review scoping** -- by default the second ASR reviews only the suspicious
  intervals (merged into the minimal set of non-overlapping windows); a
  full-length review of the whole Part happens only under an explicit recorded
  user decision, never implicitly.
* **The Independent-model review requirement** -- a review from a *different*
  eligible model is independent evidence; a same-model retry is recorded as
  *recovery* and never counts as independent review.
* **Deterministic arbitration** -- versioned preference rules decide between the
  primary and the independent-review candidate for one interval. A rule may only
  ever *adopt the review candidate* (ADR 0044): it never invents a third answer
  and never runs a confidence vote. When the candidates agree there is no
  conflict; when they disagree and no rule fires -- or the review was a recovery,
  not independent -- the primary text stands, both candidates are retained as
  evidence, and the interval becomes an Unresolved transcription conflict marked
  ``review-needed``.
* **The verbatim emission gate** -- only a complete full-ASR run that passed
  coverage checks may emit ``subtitles.verbatim.*`` / ``transcript.verbatim.*``
  and perform the Audio-completeness upgrade; anything else keeps
  ``audio_completeness=not_verified``.

Every function is a pure decision over typed inputs, all interval arithmetic is
exact rational arithmetic, and the ruleset is a versioned, ``calibration_required``
config whose version is recorded in every result -- real preference calibration
happens only in a separately authorized real-world session. No model is
downloaded or executed. See ``docs/PHASE_07_SPECIFICATION.md``, the Transcription
Context, and ADR 0044.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
from video_content_pipeline.transcription_suspicion import REASON_TEXT_OVER_SILENCE

_RULES_RELATIVE_PATH = ("config", "transcription", "transcription-rules.json")
_ARBITRATION_RULES_RELATIVE_PATH = ("config", "transcription", "arbitration-rules.json")

# The explicit recorded decision that widens review from the suspicious intervals
# to the whole Part; any other value is rejected rather than silently honored.
FULL_LENGTH_REVIEW_DECISION = "full_length_review_requested"

# The two review-attempt kinds. Independence is asset-level model identity: a
# review from a different eligible model is independent evidence; a same-model
# retry is recovery and never independent review.
REVIEW_INDEPENDENT = "independent_review"
REVIEW_RECOVERY = "recovery"

# The three arbitration outcomes. ``review-needed`` is the spec's contract token
# for an Unresolved transcription conflict and is preserved verbatim.
DECISION_AGREEMENT = "agreement"
DECISION_REVIEW_ADOPTED = "review_adopted"
REVIEW_NEEDED = "review-needed"

# The two versioned preference rules. Each may only adopt the review candidate,
# and each fires only where doing so is strictly conservative.
RULE_FILLS_GAP = "adopt_review_fills_gap"
RULE_DROPS_SILENCE = "adopt_review_drops_hallucinated_silence"
KNOWN_PREFERENCE_RULES = (RULE_FILLS_GAP, RULE_DROPS_SILENCE)

# The suspicion detector reason the drop-silence rule keys on is imported from the
# ticket-05 suspicion module that owns and stamps it, so the two modules share one
# source of truth rather than a hand-copied literal that could silently drift.

# The verbatim artifact classes a complete, coverage-checked run may emit.
VERBATIM_ARTIFACT_CLASSES = ("subtitles.verbatim", "transcript.verbatim")

_AUDIO_COMPLETENESS_VERIFIED = "verified"
_AUDIO_COMPLETENESS_NOT_VERIFIED = "not_verified"


class ArbitrationError(ValueError):
    """A rejected review-scoping, classification, or ruleset input with a stable reason.

    Arbitration flags untrusted *candidates* by returning ``review-needed``
    decisions; this error is raised only when our own inputs are inconsistent -- an
    unrecognized full-length review decision (``review_scope_invalid``), a blank
    model identity (``review_classification_invalid``), or a drifted ruleset
    (``arbitration_rules_invalid``).
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


# --- Review scoping ---------------------------------------------------------


@dataclass(frozen=True)
class ReviewScope:
    """The intervals the second ASR reviews and how that scope was chosen.

    ``decision`` records the explicit user decision that widened the scope to the
    whole Part, or ``None`` for the default suspicious-intervals scope, so the
    provenance shows a full-length review was a recorded choice, not an implicit one.
    """

    mode: str
    windows: tuple[HalfOpenInterval, ...]
    decision: str | None

    def as_json(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "windows": [_interval_as_json(window) for window in self.windows],
            "decision": self.decision,
        }


def plan_review_windows(
    *,
    suspicious_intervals: Sequence[HalfOpenInterval],
    part_interval: HalfOpenInterval,
    full_length_decision: str | None = None,
) -> ReviewScope:
    """Choose the review windows: the suspicious intervals by default, else the Part.

    With no ``full_length_decision`` the second ASR reviews only the suspicious
    intervals, merged into the minimal set of non-overlapping windows so
    overlapping detector flags are reviewed once. A whole-Part review requires the
    single explicit ``full_length_review_requested`` decision; any other value is
    an unrecognized decision and is rejected rather than silently honored.
    """

    if full_length_decision is None:
        return ReviewScope(
            "suspicious_intervals", _merge_intervals(suspicious_intervals), decision=None
        )
    if full_length_decision != FULL_LENGTH_REVIEW_DECISION:
        raise ArbitrationError(
            "review_scope_invalid",
            "A full-length review requires the explicit full_length_review_requested decision.",
        )
    return ReviewScope("full_length", (part_interval,), decision=full_length_decision)


def _merge_intervals(intervals: Sequence[HalfOpenInterval]) -> tuple[HalfOpenInterval, ...]:
    """Return the minimal non-overlapping cover of ``intervals``, sorted by start.

    Overlapping *and* touching half-open intervals are merged: ``[a, b)`` and
    ``[b, c)`` are contiguous and become ``[a, c)`` so an adjacent detector flag
    yields one review window, not two.
    """

    ordered = sorted(
        intervals, key=lambda interval: (interval.start.as_fraction(), interval.end.as_fraction())
    )
    merged: list[HalfOpenInterval] = []
    for interval in ordered:
        if merged and interval.start.as_fraction() <= merged[-1].end.as_fraction():
            if interval.end.as_fraction() > merged[-1].end.as_fraction():
                merged[-1] = HalfOpenInterval(merged[-1].start, interval.end)
            continue
        merged.append(interval)
    return tuple(merged)


# --- Independent-model review requirement -----------------------------------


@dataclass(frozen=True)
class ReviewAttemptClassification:
    """Whether a review attempt is independent evidence or a same-model recovery."""

    kind: str
    independent: bool
    primary_model_identity: str
    review_model_identity: str

    def as_json(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "independent": self.independent,
            "primary_model_identity": self.primary_model_identity,
            "review_model_identity": self.review_model_identity,
        }


def classify_review_attempt(
    *, primary_model_identity: str, review_model_identity: str
) -> ReviewAttemptClassification:
    """Record a review attempt as independent review or same-model recovery.

    Independence is asset-level model identity. A review whose model identity
    differs from the primary's is independent evidence; a same-model retry is a
    recovery attempt and is recorded as such, never as independent review. A blank
    identity is a caller contract failure, not a review outcome.
    """

    if not primary_model_identity or not review_model_identity:
        raise ArbitrationError(
            "review_classification_invalid",
            "Both the primary and review model identities are required to classify a review.",
        )
    independent = review_model_identity != primary_model_identity
    return ReviewAttemptClassification(
        kind=REVIEW_INDEPENDENT if independent else REVIEW_RECOVERY,
        independent=independent,
        primary_model_identity=primary_model_identity,
        review_model_identity=review_model_identity,
    )


# --- Arbitration candidates and inputs --------------------------------------


@dataclass(frozen=True)
class TranscriptionCandidate:
    """One retained candidate for an interval: its origin, model identity, and text."""

    origin: str
    model_identity: str
    text: str

    def as_json(self) -> dict[str, object]:
        return {
            "origin": self.origin,
            "model_identity": self.model_identity,
            "text": self.text,
        }


@dataclass(frozen=True)
class ReviewedInterval:
    """One suspicious interval with its primary and independent-review candidates.

    ``detector_reason`` is the suspicion reason that scoped this interval; the
    versioned preference rules read it so a rule fires only in the detector context
    that makes adopting the review conservative.
    """

    interval: HalfOpenInterval
    detector_reason: str
    primary: TranscriptionCandidate
    review: TranscriptionCandidate


# --- Versioned, calibration-required ruleset --------------------------------


@dataclass(frozen=True)
class ArbitrationRule:
    """One enabled versioned preference rule."""

    rule_id: str
    calibration_required: bool

    def as_json(self) -> dict[str, object]:
        return {"rule_id": self.rule_id, "calibration_required": self.calibration_required}


@dataclass(frozen=True)
class ArbitrationRuleset:
    """The versioned, ordered preference rules and their conservative marks.

    The enabled rule set is itself a versioned decision: adding or removing a rule
    changes the version, so a report's ``rules_version`` pins exactly which rules
    could have fired. Every rule keeps its ``calibration_required`` mark because
    real preference tuning is deferred to a separately authorized session.
    """

    version: str
    calibration_required: bool
    preference_rules: tuple[ArbitrationRule, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "version": self.version,
            "calibration_required": self.calibration_required,
            "preference_rules": [rule.as_json() for rule in self.preference_rules],
        }


# --- Arbitration outcomes ---------------------------------------------------


@dataclass(frozen=True)
class ArbitrationDecision:
    """The deterministic outcome for one reviewed interval.

    ``decision`` is ``agreement`` (identical candidates, no conflict),
    ``review_adopted`` (a preference rule replaced the primary with the review),
    or ``review-needed`` (an Unresolved transcription conflict: the primary text
    stands, both candidates are retained, and the interval awaits adjudication).
    """

    interval: HalfOpenInterval
    detector_reason: str
    decision: str
    rule: str | None
    resolved_text: str
    review_needed: bool
    primary_candidate: TranscriptionCandidate
    review_candidate: TranscriptionCandidate

    def as_json(self) -> dict[str, object]:
        return {
            "interval": _interval_as_json(self.interval),
            "detector_reason": self.detector_reason,
            "decision": self.decision,
            "rule": self.rule,
            "resolved_text": self.resolved_text,
            "review_needed": self.review_needed,
            "primary_candidate": self.primary_candidate.as_json(),
            "review_candidate": self.review_candidate.as_json(),
        }


@dataclass(frozen=True)
class ArbitrationResult:
    """The deterministic arbitration outcome over one Part's reviewed intervals.

    ``review_classification`` and ``review_independent`` carry the recorded review
    kind through to the report, so an auditor sees that a resolved interval was
    resolved against genuinely independent evidence rather than a same-model retry.
    """

    rules_version: str
    calibration_required: bool
    review_classification: str
    review_independent: bool
    decisions: tuple[ArbitrationDecision, ...]

    def unresolved_conflicts(self) -> tuple[ArbitrationDecision, ...]:
        """Return the retained Unresolved transcription conflicts, in interval order."""

        return tuple(decision for decision in self.decisions if decision.review_needed)

    def as_json(self) -> dict[str, object]:
        return {
            "rules_version": self.rules_version,
            "calibration_required": self.calibration_required,
            "review_classification": self.review_classification,
            "review_independent": self.review_independent,
            "decisions": [decision.as_json() for decision in self.decisions],
        }


def arbitrate(
    *,
    reviewed_intervals: Sequence[ReviewedInterval],
    classification: ReviewAttemptClassification,
    rules: ArbitrationRuleset,
) -> ArbitrationResult:
    """Arbitrate each reviewed interval deterministically and record the ruleset.

    Independence is read from the recorded ``classification`` rather than a free
    flag, so the Independent-model review requirement is enforced at the seam that
    resolves intervals: a recovery classification cannot be arbitrated as though it
    were independent. A recovery review is not independent evidence, so no interval
    resolves through it -- every reviewed interval is a retained conflict awaiting
    an independent look. An independent review resolves an interval only by exact
    agreement or by a versioned preference rule adopting the review candidate; any
    other disagreement is an Unresolved transcription conflict. Decisions are
    returned in the given interval order.
    """

    review_is_independent = classification.independent
    decisions = tuple(
        _arbitrate_one(reviewed, review_is_independent, rules) for reviewed in reviewed_intervals
    )
    return ArbitrationResult(
        rules_version=rules.version,
        calibration_required=rules.calibration_required,
        review_classification=classification.kind,
        review_independent=review_is_independent,
        decisions=decisions,
    )


def _arbitrate_one(
    reviewed: ReviewedInterval, review_is_independent: bool, rules: ArbitrationRuleset
) -> ArbitrationDecision:
    if not review_is_independent:
        # Recovery evidence cannot decide truth (Independent-model review
        # requirement): the interval stays review-needed for a later independent
        # review, even if the same-model texts happen to match.
        return _conflict(reviewed)
    if reviewed.primary.text == reviewed.review.text:
        return _resolved(reviewed, DECISION_AGREEMENT, rule=None, text=reviewed.primary.text)
    fired = _first_firing_rule(reviewed, rules)
    if fired is not None:
        return _resolved(reviewed, DECISION_REVIEW_ADOPTED, rule=fired, text=reviewed.review.text)
    return _conflict(reviewed)


def _first_firing_rule(reviewed: ReviewedInterval, rules: ArbitrationRuleset) -> str | None:
    """Return the id of the first enabled preference rule that adopts the review."""

    for rule in rules.preference_rules:
        if _rule_fires(rule.rule_id, reviewed):
            return rule.rule_id
    return None


def _rule_fires(rule_id: str, reviewed: ReviewedInterval) -> bool:
    primary_visible = _has_visible_text(reviewed.primary.text)
    review_visible = _has_visible_text(reviewed.review.text)
    if rule_id == RULE_FILLS_GAP:
        # The primary carried no text where an independent review found some:
        # adopting the review fills a gap without overwriting any primary content.
        return not primary_visible and review_visible
    if rule_id == RULE_DROPS_SILENCE:
        # Independent silence corroborates a text-over-silence flag: dropping the
        # primary's hallucinated text is conservative only in that detector context.
        return (
            reviewed.detector_reason == REASON_TEXT_OVER_SILENCE
            and primary_visible
            and not review_visible
        )
    return False


def _resolved(
    reviewed: ReviewedInterval, decision: str, *, rule: str | None, text: str
) -> ArbitrationDecision:
    return ArbitrationDecision(
        interval=reviewed.interval,
        detector_reason=reviewed.detector_reason,
        decision=decision,
        rule=rule,
        resolved_text=text,
        review_needed=False,
        primary_candidate=reviewed.primary,
        review_candidate=reviewed.review,
    )


def _conflict(reviewed: ReviewedInterval) -> ArbitrationDecision:
    """Retain an Unresolved transcription conflict: primary stands, both candidates kept."""

    return ArbitrationDecision(
        interval=reviewed.interval,
        detector_reason=reviewed.detector_reason,
        decision=REVIEW_NEEDED,
        rule=None,
        resolved_text=reviewed.primary.text,
        review_needed=True,
        primary_candidate=reviewed.primary,
        review_candidate=reviewed.review,
    )


def _has_visible_text(text: str) -> bool:
    return any(not character.isspace() for character in text)


# --- Verbatim emission gate and the Audio-completeness upgrade ---------------


@dataclass(frozen=True)
class VerbatimEmissionDecision:
    """Whether a run may emit the verbatim artifacts and upgrade audio completeness."""

    may_emit: bool
    audio_completeness: str
    emitted_artifact_classes: tuple[str, ...]
    reason: str | None

    def as_json(self) -> dict[str, object]:
        return {
            "may_emit": self.may_emit,
            "audio_completeness": self.audio_completeness,
            "emitted_artifact_classes": list(self.emitted_artifact_classes),
            "reason": self.reason,
        }


def decide_verbatim_emission(
    *, run_complete: bool, coverage_checks_passed: bool
) -> VerbatimEmissionDecision:
    """Gate the Verbatim transcription artifact and the Audio-completeness upgrade.

    Only a complete full-ASR run (every selected Part gated with no unresolved
    conflict or pending decision, per the run-status contract) that additionally
    passed its coverage checks may emit ``subtitles.verbatim.*`` /
    ``transcript.verbatim.*`` and upgrade ``audio_completeness`` to ``verified``.
    Any other run emits no verbatim artifact and keeps ``not_verified``, so a
    ``verbatim`` label always means the coverage it claims.
    """

    if not run_complete:
        return _blocked_verbatim("run_not_complete")
    if not coverage_checks_passed:
        return _blocked_verbatim("coverage_checks_failed")
    return VerbatimEmissionDecision(
        may_emit=True,
        audio_completeness=_AUDIO_COMPLETENESS_VERIFIED,
        emitted_artifact_classes=VERBATIM_ARTIFACT_CLASSES,
        reason=None,
    )


def _blocked_verbatim(reason: str) -> VerbatimEmissionDecision:
    return VerbatimEmissionDecision(
        may_emit=False,
        audio_completeness=_AUDIO_COMPLETENESS_NOT_VERIFIED,
        emitted_artifact_classes=(),
        reason=reason,
    )


# --- Versioned ruleset loader -----------------------------------------------


def load_arbitration_ruleset(project_root: Path) -> ArbitrationRuleset:
    """Load and version-bind the conservative arbitration ruleset from config.

    ``transcription-rules.json`` names the ``arbitration_rules_version`` and the
    bound ``arbitration-rules.json`` must declare it, keep the top-level and every
    per-rule ``calibration_required`` mark, and list only known preference rules
    with no duplicates. Any drift or malformed value raises
    ``arbitration_rules_invalid`` -- the ruleset is our own revalidated ground truth.
    """

    expected_version = _arbitration_rules_version(project_root)
    document = _read_json_mapping(project_root.joinpath(*_ARBITRATION_RULES_RELATIVE_PATH))
    if document.get("schema_version") != 1 or document.get("version") != expected_version:
        raise ArbitrationError(
            "arbitration_rules_invalid",
            "Arbitration rules do not match the bound version identity.",
        )
    if document.get("calibration_required") is not True:
        raise ArbitrationError(
            "arbitration_rules_invalid",
            "Arbitration rules must keep the top-level calibration mark.",
        )
    return ArbitrationRuleset(
        version=expected_version,
        calibration_required=True,
        preference_rules=_parse_preference_rules(document.get("preference_rules")),
    )


def _parse_preference_rules(value: object) -> tuple[ArbitrationRule, ...]:
    if not isinstance(value, list):
        raise ArbitrationError(
            "arbitration_rules_invalid", "Arbitration rules omit a preference_rules list."
        )
    rules: list[ArbitrationRule] = []
    seen: set[str] = set()
    for entry in value:
        if not isinstance(entry, Mapping):
            raise ArbitrationError(
                "arbitration_rules_invalid", "A preference rule is not an object."
            )
        rule_id = entry.get("rule_id")
        if rule_id not in KNOWN_PREFERENCE_RULES:
            raise ArbitrationError(
                "arbitration_rules_invalid", f"Preference rule {rule_id!r} is not a known rule."
            )
        if rule_id in seen:
            raise ArbitrationError(
                "arbitration_rules_invalid", f"Preference rule {rule_id!r} is listed twice."
            )
        if entry.get("calibration_required") is not True:
            raise ArbitrationError(
                "arbitration_rules_invalid",
                f"Preference rule {rule_id!r} must keep its calibration_required mark.",
            )
        seen.add(rule_id)
        rules.append(ArbitrationRule(rule_id=rule_id, calibration_required=True))
    return tuple(rules)


def _arbitration_rules_version(project_root: Path) -> str:
    rules = _read_json_mapping(project_root.joinpath(*_RULES_RELATIVE_PATH))
    if rules.get("schema_version") != 1:
        raise ArbitrationError(
            "arbitration_rules_invalid", "Transcription rules have an invalid schema."
        )
    version = rules.get("arbitration_rules_version")
    if not isinstance(version, str) or not version:
        raise ArbitrationError(
            "arbitration_rules_invalid",
            "Transcription rules omit a valid arbitration_rules_version.",
        )
    return version


def _read_json_mapping(path: Path) -> Mapping[str, object]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ArbitrationError(
            "arbitration_rules_invalid", f"{path.name} cannot be read."
        ) from error
    if not isinstance(decoded, Mapping):
        raise ArbitrationError("arbitration_rules_invalid", f"{path.name} is not a JSON object.")
    return decoded


# --- Shared serialization helpers -------------------------------------------


def _interval_as_json(interval: HalfOpenInterval) -> dict[str, object]:
    return {"start": _time_as_json(interval.start), "end": _time_as_json(interval.end)}


def _time_as_json(value: ExactTime) -> dict[str, int]:
    return {"numerator": value.numerator, "denominator": value.denominator}


__all__ = [
    "DECISION_AGREEMENT",
    "DECISION_REVIEW_ADOPTED",
    "FULL_LENGTH_REVIEW_DECISION",
    "KNOWN_PREFERENCE_RULES",
    "REVIEW_INDEPENDENT",
    "REVIEW_NEEDED",
    "REVIEW_RECOVERY",
    "RULE_DROPS_SILENCE",
    "RULE_FILLS_GAP",
    "VERBATIM_ARTIFACT_CLASSES",
    "ArbitrationDecision",
    "ArbitrationError",
    "ArbitrationResult",
    "ArbitrationRule",
    "ArbitrationRuleset",
    "ReviewAttemptClassification",
    "ReviewScope",
    "ReviewedInterval",
    "TranscriptionCandidate",
    "VerbatimEmissionDecision",
    "arbitrate",
    "classify_review_attempt",
    "decide_verbatim_emission",
    "load_arbitration_ruleset",
    "plan_review_windows",
]
