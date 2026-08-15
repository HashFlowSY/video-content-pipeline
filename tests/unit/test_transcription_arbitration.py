"""Offline unit contract for Phase 7 ticket 06.

Ticket 06 turns the suspicious intervals ticket 05 flagged into an independent
second-ASR review scope, records whether that review is genuinely independent,
arbitrates each reviewed interval deterministically (ADR 0044), and gates the
Verbatim transcription artifact on a complete, coverage-checked run. All four
pieces are a deterministic pure core, so -- following the strict-TDD rule and the
ticket-04/05 precedent -- they are tested directly. These tests assert the
externally observable contract:

* review runs only on the suspicious intervals by default and covers the whole
  Part only under an explicit recorded user decision;
* a same-model retry is recorded as recovery, never independent review;
* versioned preference rules decide between the primary and independent-review
  candidates, and when no rule decides the primary text stands, both candidates
  are retained, and the interval is marked ``review-needed``; and
* only a complete full-ASR run that passed coverage checks may emit the
  ``verbatim`` artifacts and perform the Audio-completeness upgrade.

No model is downloaded or executed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_content_pipeline import transcription_arbitration as arbitration
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval

_PRIMARY = "sha256-primary"
_REVIEW = "sha256-review"


def _interval(start: int, end: int) -> HalfOpenInterval:
    return HalfOpenInterval(ExactTime(start), ExactTime(end))


def _primary(text: str, *, identity: str = _PRIMARY) -> arbitration.TranscriptionCandidate:
    return arbitration.TranscriptionCandidate(
        origin="asr_primary", model_identity=identity, text=text
    )


def _review(text: str, *, identity: str = _REVIEW) -> arbitration.TranscriptionCandidate:
    return arbitration.TranscriptionCandidate(
        origin="asr_review", model_identity=identity, text=text
    )


def _reviewed(
    start: int,
    end: int,
    *,
    reason: str,
    primary_text: str,
    review_text: str,
    review_identity: str = _REVIEW,
) -> arbitration.ReviewedInterval:
    return arbitration.ReviewedInterval(
        interval=_interval(start, end),
        detector_reason=reason,
        primary=_primary(primary_text),
        review=_review(review_text, identity=review_identity),
    )


def _ruleset(*rule_ids: str) -> arbitration.ArbitrationRuleset:
    ids = rule_ids or arbitration.KNOWN_PREFERENCE_RULES
    return arbitration.ArbitrationRuleset(
        version="phase-07-arbitration-rules-v1",
        calibration_required=True,
        preference_rules=tuple(
            arbitration.ArbitrationRule(rule_id=rule_id, calibration_required=True)
            for rule_id in ids
        ),
    )


def _independent() -> arbitration.ReviewAttemptClassification:
    return arbitration.classify_review_attempt(
        primary_model_identity=_PRIMARY, review_model_identity=_REVIEW
    )


def _recovery() -> arbitration.ReviewAttemptClassification:
    return arbitration.classify_review_attempt(
        primary_model_identity=_PRIMARY, review_model_identity=_PRIMARY
    )


# --- Review scoping: suspicious intervals by default ------------------------


def test_review_scope_defaults_to_the_suspicious_intervals() -> None:
    intervals = (_interval(10, 20), _interval(40, 50))

    scope = arbitration.plan_review_windows(
        suspicious_intervals=intervals, part_interval=_interval(0, 100)
    )

    assert scope.mode == "suspicious_intervals"
    assert scope.windows == intervals
    # The default scope records no decision -- review of the flagged set is implicit.
    assert scope.decision is None


def test_review_scope_merges_overlapping_and_touching_intervals() -> None:
    # Two detectors flag overlapping ranges and a third abuts the second; the
    # review windows are the minimal non-overlapping cover, sorted by start.
    intervals = (_interval(40, 50), _interval(10, 25), _interval(20, 30), _interval(50, 55))

    scope = arbitration.plan_review_windows(
        suspicious_intervals=intervals, part_interval=_interval(0, 100)
    )

    assert scope.mode == "suspicious_intervals"
    assert scope.windows == (_interval(10, 30), _interval(40, 55))


def test_review_scope_is_empty_when_nothing_is_suspicious() -> None:
    scope = arbitration.plan_review_windows(
        suspicious_intervals=(), part_interval=_interval(0, 100)
    )

    assert scope.mode == "suspicious_intervals"
    assert scope.windows == ()


def test_full_length_review_requires_the_explicit_recorded_decision() -> None:
    scope = arbitration.plan_review_windows(
        suspicious_intervals=(_interval(10, 20),),
        part_interval=_interval(0, 100),
        full_length_decision=arbitration.FULL_LENGTH_REVIEW_DECISION,
    )

    assert scope.mode == "full_length"
    # The whole Part is reviewed as one window, regardless of the flagged set.
    assert scope.windows == (_interval(0, 100),)
    # The honored decision is recorded in the scope's provenance.
    assert scope.decision == arbitration.FULL_LENGTH_REVIEW_DECISION


def test_an_unrecognized_full_length_decision_is_rejected() -> None:
    with pytest.raises(arbitration.ArbitrationError) as error:
        arbitration.plan_review_windows(
            suspicious_intervals=(_interval(10, 20),),
            part_interval=_interval(0, 100),
            full_length_decision="just_do_it",
        )

    assert error.value.reason == "review_scope_invalid"


# --- Independent-model review requirement -----------------------------------


def test_review_from_a_different_model_is_independent() -> None:
    classification = arbitration.classify_review_attempt(
        primary_model_identity=_PRIMARY, review_model_identity=_REVIEW
    )

    assert classification.kind == arbitration.REVIEW_INDEPENDENT
    assert classification.independent is True


def test_same_model_retry_is_recorded_as_recovery_never_independent_review() -> None:
    classification = arbitration.classify_review_attempt(
        primary_model_identity=_PRIMARY, review_model_identity=_PRIMARY
    )

    assert classification.kind == arbitration.REVIEW_RECOVERY
    assert classification.independent is False


def test_classification_rejects_a_blank_model_identity() -> None:
    with pytest.raises(arbitration.ArbitrationError) as error:
        arbitration.classify_review_attempt(
            primary_model_identity=_PRIMARY, review_model_identity=""
        )

    assert error.value.reason == "review_classification_invalid"


# --- Arbitration: agreement -------------------------------------------------


def test_identical_candidates_are_recorded_as_agreement() -> None:
    reviewed = _reviewed(
        10, 20, reason="low_confidence_tokens", primary_text="上海 today", review_text="上海 today"
    )

    result = arbitration.arbitrate(
        reviewed_intervals=(reviewed,), classification=_independent(), rules=_ruleset()
    )

    (decision,) = result.decisions
    assert decision.decision == arbitration.DECISION_AGREEMENT
    assert decision.review_needed is False
    assert decision.rule is None
    assert decision.resolved_text == "上海 today"
    # Both candidates are always retained as evidence.
    assert decision.primary_candidate.text == "上海 today"
    assert decision.review_candidate.text == "上海 today"
    assert result.unresolved_conflicts() == ()


# --- Arbitration: a versioned preference rule adopts the review -------------


def test_fills_gap_rule_adopts_review_text_over_a_textless_primary() -> None:
    reviewed = _reviewed(
        10, 20, reason="non_silent_but_textless", primary_text="   ", review_text="missed speech"
    )

    result = arbitration.arbitrate(
        reviewed_intervals=(reviewed,), classification=_independent(), rules=_ruleset()
    )

    (decision,) = result.decisions
    assert decision.decision == arbitration.DECISION_REVIEW_ADOPTED
    assert decision.rule == arbitration.RULE_FILLS_GAP
    assert decision.resolved_text == "missed speech"
    assert decision.review_needed is False


def test_drops_silence_rule_adopts_the_empty_review_over_hallucinated_text() -> None:
    reviewed = _reviewed(
        10, 20, reason="asr_text_over_silence", primary_text="ghost words", review_text=""
    )

    result = arbitration.arbitrate(
        reviewed_intervals=(reviewed,), classification=_independent(), rules=_ruleset()
    )

    (decision,) = result.decisions
    assert decision.decision == arbitration.DECISION_REVIEW_ADOPTED
    assert decision.rule == arbitration.RULE_DROPS_SILENCE
    assert decision.resolved_text == ""
    assert decision.review_needed is False


def test_drops_silence_rule_does_not_fire_outside_a_silence_flag() -> None:
    # An empty independent review over a non-silence flag is not a licence to drop
    # the primary text: the primary might be right and the review simply failed.
    reviewed = _reviewed(
        10, 20, reason="low_confidence_tokens", primary_text="real words", review_text=""
    )

    result = arbitration.arbitrate(
        reviewed_intervals=(reviewed,), classification=_independent(), rules=_ruleset()
    )

    (decision,) = result.decisions
    assert decision.decision == arbitration.REVIEW_NEEDED
    assert decision.review_needed is True


# --- Arbitration: undecided disagreements are retained conflicts ------------


def test_two_different_non_empty_candidates_are_left_review_needed() -> None:
    reviewed = _reviewed(
        10,
        20,
        reason="numeric_or_entity_content",
        primary_text="flight 815",
        review_text="flight 850",
    )

    result = arbitration.arbitrate(
        reviewed_intervals=(reviewed,), classification=_independent(), rules=_ruleset()
    )

    (decision,) = result.decisions
    assert decision.decision == arbitration.REVIEW_NEEDED
    assert decision.rule is None
    assert decision.review_needed is True
    # The primary text stands and both candidates remain retained evidence.
    assert decision.resolved_text == "flight 815"
    assert decision.primary_candidate.text == "flight 815"
    assert decision.review_candidate.text == "flight 850"
    assert result.unresolved_conflicts() == (decision,)


def test_a_recovery_review_can_never_decide_even_when_texts_match() -> None:
    # A same-model retry is not independent evidence, so it never resolves a
    # suspicious interval -- it stays review-needed for a later independent look.
    reviewed = _reviewed(
        10,
        20,
        reason="asr_text_over_silence",
        primary_text="same words",
        review_text="same words",
        review_identity=_PRIMARY,
    )

    result = arbitration.arbitrate(
        reviewed_intervals=(reviewed,), classification=_recovery(), rules=_ruleset()
    )

    (decision,) = result.decisions
    # Independence is read from the recorded classification, not a free flag, so a
    # recovery attempt can never be arbitrated as though it were independent.
    assert result.review_independent is False
    assert result.review_classification == arbitration.REVIEW_RECOVERY
    assert decision.decision == arbitration.REVIEW_NEEDED
    assert decision.review_needed is True
    assert decision.rule is None


def test_a_disabled_rule_no_longer_decides_its_case() -> None:
    # With only the fills-gap rule enabled, the drop-silence case becomes an
    # unresolved conflict -- the enabled rule set is a versioned decision.
    reviewed = _reviewed(
        10, 20, reason="asr_text_over_silence", primary_text="ghost words", review_text=""
    )

    result = arbitration.arbitrate(
        reviewed_intervals=(reviewed,),
        classification=_independent(),
        rules=_ruleset(arbitration.RULE_FILLS_GAP),
    )

    (decision,) = result.decisions
    assert decision.decision == arbitration.REVIEW_NEEDED


def test_arbitration_records_the_versioned_ruleset_and_serializes() -> None:
    reviewed = (
        _reviewed(10, 20, reason="non_silent_but_textless", primary_text="", review_text="a"),
        _reviewed(30, 40, reason="repeated_text_run", primary_text="x", review_text="y"),
    )

    result = arbitration.arbitrate(
        reviewed_intervals=reviewed, classification=_independent(), rules=_ruleset()
    )
    document = result.as_json()

    assert document["rules_version"] == "phase-07-arbitration-rules-v1"
    assert document["calibration_required"] is True
    assert document["review_classification"] == arbitration.REVIEW_INDEPENDENT
    assert document["review_independent"] is True
    assert [decision["decision"] for decision in document["decisions"]] == [
        arbitration.DECISION_REVIEW_ADOPTED,
        arbitration.REVIEW_NEEDED,
    ]
    # A serialized conflict retains both candidates and the exact interval.
    conflict = document["decisions"][1]
    assert conflict["primary_candidate"]["text"] == "x"
    assert conflict["review_candidate"]["text"] == "y"
    assert conflict["interval"] == {
        "start": {"numerator": 30, "denominator": 1},
        "end": {"numerator": 40, "denominator": 1},
    }


# --- Verbatim emission gate and the Audio-completeness upgrade ---------------


def test_verbatim_emits_only_from_a_complete_coverage_checked_run() -> None:
    decision = arbitration.decide_verbatim_emission(run_complete=True, coverage_checks_passed=True)

    assert decision.may_emit is True
    assert decision.audio_completeness == "verified"
    assert decision.emitted_artifact_classes == arbitration.VERBATIM_ARTIFACT_CLASSES
    assert "subtitles.verbatim" in decision.emitted_artifact_classes
    assert "transcript.verbatim" in decision.emitted_artifact_classes
    assert decision.reason is None


def test_an_incomplete_run_never_emits_verbatim_or_upgrades_completeness() -> None:
    decision = arbitration.decide_verbatim_emission(run_complete=False, coverage_checks_passed=True)

    assert decision.may_emit is False
    assert decision.audio_completeness == "not_verified"
    assert decision.emitted_artifact_classes == ()
    assert decision.reason == "run_not_complete"


def test_a_failed_coverage_check_blocks_verbatim() -> None:
    decision = arbitration.decide_verbatim_emission(run_complete=True, coverage_checks_passed=False)

    assert decision.may_emit is False
    assert decision.audio_completeness == "not_verified"
    assert decision.reason == "coverage_checks_failed"


# --- Versioned, calibration-required ruleset loader -------------------------


def _write_rules(project_root: Path, **overrides: object) -> None:
    config = project_root / "config" / "transcription"
    config.mkdir(parents=True, exist_ok=True)
    transcription_rules = {
        "schema_version": 1,
        "id": "phase-07-transcription-rules-v1",
        "projection_schema_version": "phase-07-asr-projection-schema-v1",
        "controlled_adapter_identity": "phase-07-controlled-asr-adapter-v1",
        "timing_gate_version": "phase-07-timing-gate-rules-v1",
        "suspicion_rules_version": "phase-07-suspicion-rules-v1",
        "arbitration_rules_version": "phase-07-arbitration-rules-v1",
    }
    arbitration_rules: dict[str, object] = {
        "schema_version": 1,
        "version": "phase-07-arbitration-rules-v1",
        "calibration_required": True,
        "preference_rules": [
            {"rule_id": "adopt_review_fills_gap", "calibration_required": True},
            {"rule_id": "adopt_review_drops_hallucinated_silence", "calibration_required": True},
        ],
    }
    arbitration_rules.update(overrides)
    (config / "transcription-rules.json").write_text(
        json.dumps(transcription_rules, sort_keys=True) + "\n", encoding="utf-8"
    )
    (config / "arbitration-rules.json").write_text(
        json.dumps(arbitration_rules, sort_keys=True) + "\n", encoding="utf-8"
    )


def test_loader_binds_the_versioned_conservative_ruleset(tmp_path: Path) -> None:
    _write_rules(tmp_path)

    ruleset = arbitration.load_arbitration_ruleset(tmp_path)

    assert ruleset.version == "phase-07-arbitration-rules-v1"
    assert ruleset.calibration_required is True
    assert tuple(rule.rule_id for rule in ruleset.preference_rules) == (
        arbitration.RULE_FILLS_GAP,
        arbitration.RULE_DROPS_SILENCE,
    )
    assert all(rule.calibration_required for rule in ruleset.preference_rules)


def test_loader_rejects_a_version_mismatch(tmp_path: Path) -> None:
    _write_rules(tmp_path, version="phase-07-arbitration-rules-DRIFT")

    with pytest.raises(arbitration.ArbitrationError) as error:
        arbitration.load_arbitration_ruleset(tmp_path)
    assert error.value.reason == "arbitration_rules_invalid"


def test_loader_requires_the_top_level_calibration_mark(tmp_path: Path) -> None:
    _write_rules(tmp_path, calibration_required=False)

    with pytest.raises(arbitration.ArbitrationError) as error:
        arbitration.load_arbitration_ruleset(tmp_path)
    assert error.value.reason == "arbitration_rules_invalid"


def test_loader_requires_each_rule_calibration_mark(tmp_path: Path) -> None:
    _write_rules(
        tmp_path,
        preference_rules=[
            {"rule_id": "adopt_review_fills_gap", "calibration_required": False},
        ],
    )

    with pytest.raises(arbitration.ArbitrationError) as error:
        arbitration.load_arbitration_ruleset(tmp_path)
    assert error.value.reason == "arbitration_rules_invalid"


def test_loader_rejects_an_unknown_preference_rule(tmp_path: Path) -> None:
    _write_rules(
        tmp_path,
        preference_rules=[{"rule_id": "prefer_the_longer_text", "calibration_required": True}],
    )

    with pytest.raises(arbitration.ArbitrationError) as error:
        arbitration.load_arbitration_ruleset(tmp_path)
    assert error.value.reason == "arbitration_rules_invalid"


def test_loader_rejects_a_duplicated_preference_rule(tmp_path: Path) -> None:
    _write_rules(
        tmp_path,
        preference_rules=[
            {"rule_id": "adopt_review_fills_gap", "calibration_required": True},
            {"rule_id": "adopt_review_fills_gap", "calibration_required": True},
        ],
    )

    with pytest.raises(arbitration.ArbitrationError) as error:
        arbitration.load_arbitration_ruleset(tmp_path)
    assert error.value.reason == "arbitration_rules_invalid"
