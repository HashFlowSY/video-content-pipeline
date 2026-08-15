"""Offline unit contract for Phase 7 ticket 05.

Ticket 05 implements the six deterministic Versioned suspicion detection rules --
VAD coverage, coverage checks, confidence, repetition, language switching, and
numbers/entities -- over gated ASR projections (ticket 04) and retained
audio-analysis evidence (ADR 0043). The detectors are a deterministic pure core,
so -- following the strict-TDD rule and the ticket-04 precedent -- they are
tested directly. These tests assert the externally observable contract:

* each flagged interval records its detector identity, machine-readable evidence,
  and an exact time range, and detectors are pure functions;
* the two audio-consuming detectors (VAD coverage and non-silent-but-textless
  coverage checks) read the retained VAD evidence;
* mixed Chinese/English is never flagged merely for being mixed; only heavy
  switching is surfaced for review; and
* the detector set and thresholds are read from a versioned,
  ``calibration_required`` ruleset whose version is recorded in every result.

No model is downloaded or executed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_content_pipeline import transcription_suspicion as suspicion
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
from video_content_pipeline.transcription_contracts import (
    AsrLanguageSpan,
    ProjectedAsrToken,
)
from video_content_pipeline.transcription_gates import GatedAsrCue

_PART = "part-1"


def _interval(start: int | ExactTime, end: int | ExactTime) -> HalfOpenInterval:
    start_time = start if isinstance(start, ExactTime) else ExactTime(start)
    end_time = end if isinstance(end, ExactTime) else ExactTime(end)
    return HalfOpenInterval(start_time, end_time)


def _cue(
    ordinal: int,
    start: int | ExactTime,
    end: int | ExactTime,
    *,
    text: str = "hello world",
    tokens: tuple[ProjectedAsrToken, ...] = (),
    language_spans: tuple[AsrLanguageSpan, ...] = (),
    part_id: str = _PART,
) -> GatedAsrCue:
    interval = _interval(start, end)
    return GatedAsrCue(
        ordinal=ordinal,
        part_id=part_id,
        text=text,
        raw_interval=interval,
        part_relative_interval=interval,
        collection_interval=interval,
        tokens=tokens,
        language_spans=language_spans,
    )


def _speech(start: int, end: int) -> suspicion.VadRegion:
    return suspicion.VadRegion(interval=_interval(start, end), state=suspicion.SPEECH_LIKELY)


def _silence(start: int, end: int) -> suspicion.VadRegion:
    return suspicion.VadRegion(interval=_interval(start, end), state=suspicion.NON_SPEECH)


# A ruleset matching the shipped conservative defaults so unit tests exercise the
# same bounds the loader returns.
def _ruleset() -> suspicion.SuspicionRuleset:
    return suspicion.SuspicionRuleset(
        version="phase-07-suspicion-rules-v1",
        calibration_required=True,
        vad_coverage=suspicion.VadCoverageRule(
            calibration_required=True, minimum_silence_overlap_seconds=ExactTime(1)
        ),
        coverage_checks=suspicion.CoverageCheckRule(
            calibration_required=True, minimum_textless_speech_seconds=ExactTime(1)
        ),
        confidence=suspicion.ConfidenceRule(
            calibration_required=True, minimum_token_confidence=0.5
        ),
        repetition=suspicion.RepetitionRule(
            calibration_required=True, maximum_consecutive_repetitions=3
        ),
        language_switching=suspicion.LanguageSwitchingRule(
            calibration_required=True, maximum_language_switches=2
        ),
        numbers_entities=suspicion.NumbersEntitiesRule(
            calibration_required=True, minimum_digit_run=2
        ),
    )


# --- VAD coverage: ASR text over silence ------------------------------------


def test_vad_coverage_flags_text_overlapping_a_silence_region() -> None:
    rules = _ruleset()
    cues = (_cue(0, 10, 18, text="hallucinated words"),)
    regions = (_silence(12, 20),)

    flags = suspicion.detect_vad_coverage(
        part_id=_PART, cues=cues, vad_regions=regions, rule=rules.vad_coverage
    )

    (flag,) = flags
    assert flag.detector == "vad_coverage"
    assert flag.part_id == _PART
    # The flagged range is the exact overlap of the cue with the silence.
    assert flag.interval == _interval(12, 18)
    assert flag.evidence["cue_ordinal"] == 0


def test_vad_coverage_ignores_overlap_below_the_conservative_threshold() -> None:
    rules = _ruleset()
    # Only a half-second of the cue sits over silence; below the 1s default.
    cues = (_cue(0, 0, ExactTime(21, 2)),)
    regions = (_silence(10, 20),)

    flags = suspicion.detect_vad_coverage(
        part_id=_PART, cues=cues, vad_regions=regions, rule=rules.vad_coverage
    )

    assert flags == ()


def test_vad_coverage_does_not_flag_text_over_speech() -> None:
    rules = _ruleset()
    cues = (_cue(0, 10, 18),)
    regions = (_speech(0, 30),)

    flags = suspicion.detect_vad_coverage(
        part_id=_PART, cues=cues, vad_regions=regions, rule=rules.vad_coverage
    )

    assert flags == ()


# --- Coverage checks: non-silent-but-textless -------------------------------


def test_coverage_checks_flags_uncovered_speech() -> None:
    rules = _ruleset()
    # Speech spans 0..30; a single cue covers only 0..10, leaving 10..30 textless.
    cues = (_cue(0, 0, 10),)
    regions = (_speech(0, 30),)

    flags = suspicion.detect_coverage_checks(
        part_id=_PART, cues=cues, vad_regions=regions, rule=rules.coverage_checks
    )

    (flag,) = flags
    assert flag.detector == "coverage_checks"
    assert flag.interval == _interval(10, 30)


def test_coverage_checks_ignores_a_small_textless_gap() -> None:
    rules = _ruleset()
    # A half-second gap between two cues over the speech region is below 1s.
    cues = (_cue(0, 0, 10), _cue(1, ExactTime(21, 2), 30))
    regions = (_speech(0, 30),)

    flags = suspicion.detect_coverage_checks(
        part_id=_PART, cues=cues, vad_regions=regions, rule=rules.coverage_checks
    )

    assert flags == ()


def test_coverage_checks_passes_when_speech_is_fully_covered() -> None:
    rules = _ruleset()
    cues = (_cue(0, 0, 15), _cue(1, 15, 30))
    regions = (_speech(0, 30),)

    flags = suspicion.detect_coverage_checks(
        part_id=_PART, cues=cues, vad_regions=regions, rule=rules.coverage_checks
    )

    assert flags == ()


# --- Confidence -------------------------------------------------------------


def test_confidence_flags_a_cue_with_a_low_confidence_token() -> None:
    rules = _ruleset()
    tokens = (ProjectedAsrToken("你好", 0.9), ProjectedAsrToken("world", 0.2))
    cues = (_cue(0, 0, 5, tokens=tokens),)

    flags = suspicion.detect_confidence(part_id=_PART, cues=cues, rule=rules.confidence)

    (flag,) = flags
    assert flag.detector == "confidence"
    assert flag.interval == _interval(0, 5)
    assert flag.evidence["low_confidence_tokens"] == [{"text": "world", "confidence": 0.2}]


def test_confidence_does_not_flag_confident_cues() -> None:
    rules = _ruleset()
    tokens = (ProjectedAsrToken("你好", 0.9), ProjectedAsrToken("world", 0.8))
    cues = (_cue(0, 0, 5, tokens=tokens),)

    flags = suspicion.detect_confidence(part_id=_PART, cues=cues, rule=rules.confidence)

    assert flags == ()


def test_confidence_ignores_cues_without_scored_tokens() -> None:
    rules = _ruleset()
    tokens = (ProjectedAsrToken("你好", None), ProjectedAsrToken("world", None))
    cues = (_cue(0, 0, 5, tokens=tokens), _cue(1, 5, 10))

    flags = suspicion.detect_confidence(part_id=_PART, cues=cues, rule=rules.confidence)

    assert flags == ()


# --- Repetition -------------------------------------------------------------


def test_repetition_flags_a_within_cue_token_loop() -> None:
    rules = _ruleset()
    # Five consecutive identical tokens exceed the maximum of three.
    tokens = tuple(ProjectedAsrToken("好", 0.9) for _ in range(5))
    cues = (_cue(0, 0, 5, text="好 好 好 好 好", tokens=tokens),)

    flags = suspicion.detect_repetition(part_id=_PART, cues=cues, rule=rules.repetition)

    (flag,) = flags
    assert flag.detector == "repetition"
    assert flag.interval == _interval(0, 5)
    assert flag.evidence["run_length"] == 5


def test_repetition_allows_a_run_at_the_threshold() -> None:
    rules = _ruleset()
    tokens = tuple(ProjectedAsrToken("好", 0.9) for _ in range(3))
    cues = (_cue(0, 0, 5, text="好 好 好", tokens=tokens),)

    flags = suspicion.detect_repetition(part_id=_PART, cues=cues, rule=rules.repetition)

    assert flags == ()


def test_repetition_flags_consecutive_identical_cues() -> None:
    rules = _ruleset()
    cues = tuple(_cue(i, i * 2, i * 2 + 2, text="重复") for i in range(4))

    flags = suspicion.detect_repetition(part_id=_PART, cues=cues, rule=rules.repetition)

    (flag,) = flags
    assert flag.evidence["scope"] == "across_cues"
    # The flagged range spans the whole repeated run.
    assert flag.interval == _interval(0, 8)


# --- Language switching -----------------------------------------------------


def test_language_switching_does_not_flag_ordinary_mixed_language() -> None:
    rules = _ruleset()
    spans = (AsrLanguageSpan("zh", 0, 1), AsrLanguageSpan("en", 1, 2))
    cues = (_cue(0, 0, 5, text="你好 world", language_spans=spans),)

    flags = suspicion.detect_language_switching(
        part_id=_PART, cues=cues, rule=rules.language_switching
    )

    assert flags == ()


def test_language_switching_flags_excessive_switching() -> None:
    rules = _ruleset()
    spans = (
        AsrLanguageSpan("zh", 0, 1),
        AsrLanguageSpan("en", 1, 2),
        AsrLanguageSpan("zh", 2, 3),
        AsrLanguageSpan("en", 3, 4),
    )
    cues = (_cue(0, 0, 5, text="你 a 好 b", language_spans=spans),)

    flags = suspicion.detect_language_switching(
        part_id=_PART, cues=cues, rule=rules.language_switching
    )

    (flag,) = flags
    assert flag.detector == "language_switching"
    assert flag.evidence["switch_count"] == 3


# --- Numbers / entities -----------------------------------------------------


def test_numbers_entities_flags_a_multi_digit_number() -> None:
    rules = _ruleset()
    cues = (_cue(0, 0, 5, text="他在 2026 年出发"),)

    flags = suspicion.detect_numbers_entities(
        part_id=_PART, cues=cues, rule=rules.numbers_entities
    )

    (flag,) = flags
    assert flag.detector == "numbers_entities"
    assert "2026" in flag.evidence["matches"]


def test_numbers_entities_flags_alphanumeric_entities() -> None:
    rules = _ruleset()
    cues = (_cue(0, 0, 5, text="the iPhone15 is here"),)

    flags = suspicion.detect_numbers_entities(
        part_id=_PART, cues=cues, rule=rules.numbers_entities
    )

    (flag,) = flags
    assert "iPhone15" in flag.evidence["matches"]


def test_numbers_entities_ignores_a_single_digit() -> None:
    rules = _ruleset()
    cues = (_cue(0, 0, 5, text="chapter 1 begins"),)

    flags = suspicion.detect_numbers_entities(
        part_id=_PART, cues=cues, rule=rules.numbers_entities
    )

    assert flags == ()


# --- Orchestration ----------------------------------------------------------


def test_detect_records_the_versioned_ruleset_and_orders_by_time() -> None:
    rules = _ruleset()
    low = (ProjectedAsrToken("word", 0.1),)
    cues = (
        _cue(0, 0, 5, text="his 2026 plan"),
        _cue(1, 5, 10, text="uncertain", tokens=low),
    )
    regions = (_speech(0, 30),)

    result = suspicion.detect_suspicious_intervals(
        part_id=_PART, cues=cues, vad_regions=regions, rules=rules
    )

    assert result.part_id == _PART
    assert result.rules_version == "phase-07-suspicion-rules-v1"
    assert result.calibration_required is True
    starts = [flag.interval.start for flag in result.suspicious_intervals]
    assert starts == sorted(starts, key=lambda time: time.as_fraction())
    detectors = {flag.detector for flag in result.suspicious_intervals}
    # numbers/entities on cue 0, confidence on cue 1, and the textless tail 10..30.
    assert {"numbers_entities", "confidence", "coverage_checks"} <= detectors


def test_result_serializes_flags_with_identity_evidence_and_exact_range() -> None:
    rules = _ruleset()
    cues = (_cue(0, 0, 5, text="year 2026"),)

    result = suspicion.detect_suspicious_intervals(
        part_id=_PART, cues=cues, vad_regions=(), rules=rules
    )

    document = result.as_json()
    assert document["part_id"] == _PART
    assert document["rules_version"] == "phase-07-suspicion-rules-v1"
    assert document["calibration_required"] is True
    (flag,) = document["suspicious_intervals"]
    assert flag["detector"] == "numbers_entities"
    assert flag["reason"]
    assert flag["interval"] == {
        "start": {"numerator": 0, "denominator": 1},
        "end": {"numerator": 5, "denominator": 1},
    }
    assert "evidence" in flag


# --- Retained VAD evidence adapter ------------------------------------------


def test_vad_regions_from_part_evidence_reads_the_voice_activity_partition() -> None:
    part_evidence = {
        "source_id": "s1",
        "stream_index": 1,
        "voice_activity_intervals": [
            {
                "interval": {
                    "start": {"numerator": 0, "denominator": 1},
                    "end": {"numerator": 10, "denominator": 1},
                },
                "state": "speech_likely",
            },
            {
                "interval": {
                    "start": {"numerator": 10, "denominator": 1},
                    "end": {"numerator": 20, "denominator": 1},
                },
                "state": "non_speech",
            },
        ],
        # long_silences is a derived subset of the non_speech partition; it must not
        # be re-added as extra regions or every silence would be double-counted.
        "long_silences": [
            {
                "interval": {
                    "start": {"numerator": 10, "denominator": 1},
                    "end": {"numerator": 20, "denominator": 1},
                }
            }
        ],
    }

    regions = suspicion.vad_regions_from_part_evidence(part_evidence)

    states = [region.state for region in regions]
    assert states == [suspicion.SPEECH_LIKELY, suspicion.NON_SPEECH]


def test_vad_coverage_does_not_double_flag_a_derived_long_silence() -> None:
    rules = _ruleset()
    part_evidence = {
        "voice_activity_intervals": [
            {
                "interval": {
                    "start": {"numerator": 0, "denominator": 1},
                    "end": {"numerator": 30, "denominator": 1},
                },
                "state": "non_speech",
            }
        ],
        "long_silences": [
            {
                "interval": {
                    "start": {"numerator": 0, "denominator": 1},
                    "end": {"numerator": 30, "denominator": 1},
                }
            }
        ],
    }
    regions = suspicion.vad_regions_from_part_evidence(part_evidence)
    cues = (_cue(0, 5, 15, text="hallucinated"),)

    flags = suspicion.detect_vad_coverage(
        part_id=_PART, cues=cues, vad_regions=regions, rule=rules.vad_coverage
    )

    # Exactly one flag despite the silence being reported twice by the report.
    assert len(flags) == 1


def test_vad_regions_from_part_evidence_rejects_malformed_evidence() -> None:
    with pytest.raises(suspicion.SuspicionRulesError) as error:
        suspicion.vad_regions_from_part_evidence({"voice_activity_intervals": "nope"})
    assert error.value.reason == "suspicion_evidence_invalid"


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
    }
    suspicion_rules: dict[str, object] = {
        "schema_version": 1,
        "version": "phase-07-suspicion-rules-v1",
        "calibration_required": True,
        "detectors": {
            "vad_coverage": {
                "calibration_required": True,
                "minimum_silence_overlap_seconds": {"numerator": 1, "denominator": 1},
            },
            "coverage_checks": {
                "calibration_required": True,
                "minimum_textless_speech_seconds": {"numerator": 1, "denominator": 1},
            },
            "confidence": {"calibration_required": True, "minimum_token_confidence": 0.5},
            "repetition": {"calibration_required": True, "maximum_consecutive_repetitions": 3},
            "language_switching": {
                "calibration_required": True,
                "maximum_language_switches": 2,
            },
            "numbers_entities": {"calibration_required": True, "minimum_digit_run": 2},
        },
    }
    suspicion_rules.update(overrides)
    (config / "transcription-rules.json").write_text(
        json.dumps(transcription_rules, sort_keys=True) + "\n", encoding="utf-8"
    )
    (config / "suspicion-rules.json").write_text(
        json.dumps(suspicion_rules, sort_keys=True) + "\n", encoding="utf-8"
    )


def test_loader_binds_the_versioned_conservative_ruleset(tmp_path: Path) -> None:
    _write_rules(tmp_path)

    ruleset = suspicion.load_suspicion_ruleset(tmp_path)

    assert ruleset.version == "phase-07-suspicion-rules-v1"
    assert ruleset.calibration_required is True
    assert ruleset.confidence.minimum_token_confidence == 0.5
    assert ruleset.numbers_entities.minimum_digit_run == 2
    assert ruleset.vad_coverage.minimum_silence_overlap_seconds == ExactTime(1)


def test_loader_rejects_a_version_mismatch(tmp_path: Path) -> None:
    _write_rules(tmp_path, version="phase-07-suspicion-rules-DRIFT")

    with pytest.raises(suspicion.SuspicionRulesError) as error:
        suspicion.load_suspicion_ruleset(tmp_path)
    assert error.value.reason == "suspicion_rules_invalid"


def test_loader_requires_the_top_level_calibration_mark(tmp_path: Path) -> None:
    _write_rules(tmp_path, calibration_required=False)

    with pytest.raises(suspicion.SuspicionRulesError) as error:
        suspicion.load_suspicion_ruleset(tmp_path)
    assert error.value.reason == "suspicion_rules_invalid"


def test_loader_requires_each_detector_calibration_mark(tmp_path: Path) -> None:
    _write_rules(
        tmp_path,
        detectors={
            "vad_coverage": {
                "calibration_required": False,
                "minimum_silence_overlap_seconds": {"numerator": 1, "denominator": 1},
            },
            "coverage_checks": {
                "calibration_required": True,
                "minimum_textless_speech_seconds": {"numerator": 1, "denominator": 1},
            },
            "confidence": {"calibration_required": True, "minimum_token_confidence": 0.5},
            "repetition": {"calibration_required": True, "maximum_consecutive_repetitions": 3},
            "language_switching": {
                "calibration_required": True,
                "maximum_language_switches": 2,
            },
            "numbers_entities": {"calibration_required": True, "minimum_digit_run": 2},
        },
    )

    with pytest.raises(suspicion.SuspicionRulesError) as error:
        suspicion.load_suspicion_ruleset(tmp_path)
    assert error.value.reason == "suspicion_rules_invalid"


def test_loader_rejects_a_missing_detector(tmp_path: Path) -> None:
    _write_rules(
        tmp_path,
        detectors={
            "confidence": {"calibration_required": True, "minimum_token_confidence": 0.5},
        },
    )

    with pytest.raises(suspicion.SuspicionRulesError) as error:
        suspicion.load_suspicion_ruleset(tmp_path)
    assert error.value.reason == "suspicion_rules_invalid"


def test_loader_rejects_a_confidence_out_of_range(tmp_path: Path) -> None:
    _write_rules(
        tmp_path,
        detectors={
            "vad_coverage": {
                "calibration_required": True,
                "minimum_silence_overlap_seconds": {"numerator": 1, "denominator": 1},
            },
            "coverage_checks": {
                "calibration_required": True,
                "minimum_textless_speech_seconds": {"numerator": 1, "denominator": 1},
            },
            "confidence": {"calibration_required": True, "minimum_token_confidence": 1.5},
            "repetition": {"calibration_required": True, "maximum_consecutive_repetitions": 3},
            "language_switching": {
                "calibration_required": True,
                "maximum_language_switches": 2,
            },
            "numbers_entities": {"calibration_required": True, "minimum_digit_run": 2},
        },
    )

    with pytest.raises(suspicion.SuspicionRulesError) as error:
        suspicion.load_suspicion_ruleset(tmp_path)
    assert error.value.reason == "suspicion_rules_invalid"


def test_the_shipped_project_ruleset_is_valid() -> None:
    ruleset = suspicion.load_suspicion_ruleset(Path.cwd())

    assert ruleset.calibration_required is True
    assert ruleset.vad_coverage.calibration_required is True
    assert ruleset.repetition.maximum_consecutive_repetitions >= 1


def test_ruleset_serializes_its_version_and_calibrated_detector_bounds() -> None:
    document = _ruleset().as_json()

    assert document["version"] == "phase-07-suspicion-rules-v1"
    assert document["calibration_required"] is True
    detectors = document["detectors"]
    assert set(detectors) == {
        "vad_coverage",
        "coverage_checks",
        "confidence",
        "repetition",
        "language_switching",
        "numbers_entities",
    }
    # Every serialized detector keeps its calibration_required mark.
    assert all(block["calibration_required"] is True for block in detectors.values())
    assert detectors["numbers_entities"]["minimum_digit_run"] == 2
