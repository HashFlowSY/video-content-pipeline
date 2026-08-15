"""Offline unit contract for Phase 7 ticket 04.

Ticket 04 gates the projected ASR cues of ticket 03 onto the canonical timeline
before they may become candidate evidence. The gates are a deterministic pure
core, so -- following the strict-TDD rule and the Phase 6 segmentation
precedent -- they are tested directly rather than only through the CLI. These
tests assert the externally observable gate contract:

* a valid cue is admitted and carries its exact times across the three existing
  coordinate systems (RawPtsTime source time, PartRelativeTime, and
  CollectionVirtualTime) with no float accumulation;
* an out-of-coverage, non-monotonic, negative-duration, duplicated, or
  duration-implausible cue is rejected -- never repaired -- with a structured
  per-cue reason, and Part boundaries stay hard; and
* the duration-to-text bounds are read from a versioned, ``calibration_required``
  ruleset with conservative defaults.

No model is downloaded or executed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_content_pipeline import transcription_gates as gates
from video_content_pipeline.coverage import CoverageDiagnostic, StreamCoverage
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
from video_content_pipeline.timeline import CollectionTimeline, TimelinePart
from video_content_pipeline.transcription_contracts import (
    AsrLanguageSpan,
    ProjectedAsrCue,
    ProjectedAsrToken,
)

# A ruleset wide enough that ordinary cues clear the duration-to-text gate; it
# matches the conservative shipped defaults so timing tests exercise the same
# bounds the loader returns.
_RULES = gates.TimingGateRuleset(
    version="phase-07-timing-gate-rules-v1",
    calibration_required=True,
    duration_to_text=gates.DurationToTextBounds(
        minimum_seconds_per_character=ExactTime(1, 100),
        maximum_seconds_per_character=ExactTime(30),
    ),
)


def _coverage(start: int, end: int) -> StreamCoverage:
    return StreamCoverage(
        coverage=HalfOpenInterval(ExactTime(start), ExactTime(end)),
        gaps=(),
        diagnostics=(),
    )


def _single_part_timeline(start: int, end: int, part_id: str = "part-1") -> CollectionTimeline:
    return CollectionTimeline(
        parts=(
            TimelinePart(
                part_id=part_id,
                coverage=HalfOpenInterval(ExactTime(start), ExactTime(end)),
            ),
        )
    )


def _cue(
    ordinal: int,
    start: ExactTime,
    end: ExactTime,
    *,
    text: str = "你好 world",
    tokens: tuple[ProjectedAsrToken, ...] = (),
    language_spans: tuple[AsrLanguageSpan, ...] = (),
) -> ProjectedAsrCue:
    return ProjectedAsrCue(
        ordinal=ordinal,
        interval=HalfOpenInterval(start, end),
        text=text,
        tokens=tokens,
        language_spans=language_spans,
    )


def _gate(
    cues: tuple[ProjectedAsrCue, ...],
    *,
    part_coverage: StreamCoverage,
    timeline: CollectionTimeline,
    part_id: str = "part-1",
) -> gates.CanonicalTimelineGateResult:
    return gates.gate_projected_cues(
        part_id=part_id,
        cues=cues,
        part_coverage=part_coverage,
        timeline=timeline,
        rules=_RULES,
    )


# --- Admission and cross-coordinate mapping ---------------------------------


def test_admits_valid_cues_and_reports_the_versioned_gate_identity() -> None:
    result = _gate(
        (
            _cue(0, ExactTime(0), ExactTime(5)),
            _cue(1, ExactTime(5), ExactTime(10)),
        ),
        part_coverage=_coverage(0, 20),
        timeline=_single_part_timeline(0, 20),
    )

    assert result.gate_version == "phase-07-timing-gate-rules-v1"
    assert result.calibration_required is True
    assert tuple(cue.ordinal for cue in result.admitted) == (0, 1)
    assert result.rejected == ()


def test_admitted_cue_maps_exact_times_across_the_three_coordinate_systems() -> None:
    # part-2 has a nonzero source origin and follows a 10-second part-1, so the
    # three coordinate systems disagree and each mapping must be exact.
    timeline = CollectionTimeline(
        parts=(
            TimelinePart("part-1", HalfOpenInterval(ExactTime(0), ExactTime(10))),
            TimelinePart("part-2", HalfOpenInterval(ExactTime(100), ExactTime(110))),
        )
    )
    result = gates.gate_projected_cues(
        part_id="part-2",
        cues=(_cue(0, ExactTime(102), ExactTime(105)),),
        part_coverage=_coverage(100, 110),
        timeline=timeline,
        rules=_RULES,
    )

    (admitted,) = result.admitted
    assert admitted.part_id == "part-2"
    assert admitted.raw_interval == HalfOpenInterval(ExactTime(102), ExactTime(105))
    assert admitted.part_relative_interval == HalfOpenInterval(ExactTime(2), ExactTime(5))
    assert admitted.collection_interval == HalfOpenInterval(ExactTime(12), ExactTime(15))


def test_admitted_cue_preserves_fractional_times_and_carried_evidence() -> None:
    tokens = (ProjectedAsrToken("你好", 0.9), ProjectedAsrToken("world", None))
    spans = (AsrLanguageSpan("zh", 0, 1), AsrLanguageSpan("en", 1, 2))
    result = _gate(
        (_cue(0, ExactTime(1, 3), ExactTime(7, 3), tokens=tokens, language_spans=spans),),
        part_coverage=_coverage(0, 20),
        timeline=_single_part_timeline(0, 20),
    )

    (admitted,) = result.admitted
    # A one-third-second origin survives as an exact rational, not a float.
    assert admitted.part_relative_interval.start == ExactTime(1, 3)
    assert admitted.collection_interval.end == ExactTime(7, 3)
    assert admitted.tokens == tokens
    assert admitted.language_spans == spans


# --- Rejection: never repair, keep structured reasons -----------------------


def test_rejects_a_cue_starting_before_the_part_coverage() -> None:
    result = _gate(
        (_cue(0, ExactTime(5), ExactTime(8)),),
        part_coverage=_coverage(10, 20),
        timeline=_single_part_timeline(10, 20),
    )

    assert result.admitted == ()
    (rejected,) = result.rejected
    assert rejected.reason == "cue_out_of_coverage"
    assert rejected.raw_interval == HalfOpenInterval(ExactTime(5), ExactTime(8))


def test_rejects_a_cue_spilling_past_the_hard_part_boundary() -> None:
    # part-1 ends at 10; a cue running to 12 would spill into the next Part.
    timeline = CollectionTimeline(
        parts=(
            TimelinePart("part-1", HalfOpenInterval(ExactTime(0), ExactTime(10))),
            TimelinePart("part-2", HalfOpenInterval(ExactTime(10), ExactTime(20))),
        )
    )
    result = gates.gate_projected_cues(
        part_id="part-1",
        cues=(_cue(0, ExactTime(8), ExactTime(12)),),
        part_coverage=_coverage(0, 10),
        timeline=timeline,
        rules=_RULES,
    )

    (rejected,) = result.rejected
    assert rejected.reason == "cue_out_of_coverage"


def test_a_cue_ending_exactly_on_the_coverage_endpoint_is_admitted() -> None:
    result = _gate(
        (_cue(0, ExactTime(5), ExactTime(10)),),
        part_coverage=_coverage(0, 10),
        timeline=_single_part_timeline(0, 10),
    )

    assert tuple(cue.ordinal for cue in result.admitted) == (0,)
    assert result.rejected == ()


def test_rejects_a_time_non_monotonic_cue() -> None:
    result = _gate(
        (
            _cue(0, ExactTime(5), ExactTime(10)),
            _cue(1, ExactTime(0), ExactTime(3)),
        ),
        part_coverage=_coverage(0, 20),
        timeline=_single_part_timeline(0, 20),
    )

    assert tuple(cue.ordinal for cue in result.admitted) == (0,)
    (rejected,) = result.rejected
    assert rejected.reason == "cue_non_monotonic"


def test_rejects_an_ordinal_non_monotonic_cue() -> None:
    result = _gate(
        (
            _cue(3, ExactTime(0), ExactTime(5)),
            _cue(3, ExactTime(5), ExactTime(10)),
        ),
        part_coverage=_coverage(0, 20),
        timeline=_single_part_timeline(0, 20),
    )

    assert tuple(cue.ordinal for cue in result.admitted) == (3,)
    (rejected,) = result.rejected
    assert rejected.reason == "cue_non_monotonic"


def test_rejects_an_exact_duplicate_cue() -> None:
    result = _gate(
        (
            _cue(0, ExactTime(0), ExactTime(5)),
            _cue(1, ExactTime(0), ExactTime(5)),
        ),
        part_coverage=_coverage(0, 20),
        timeline=_single_part_timeline(0, 20),
    )

    assert tuple(cue.ordinal for cue in result.admitted) == (0,)
    (rejected,) = result.rejected
    assert rejected.reason == "cue_processing_duplication"


def test_rejects_an_overlapping_processing_window_cue() -> None:
    result = _gate(
        (
            _cue(0, ExactTime(0), ExactTime(5)),
            _cue(1, ExactTime(3), ExactTime(8)),
        ),
        part_coverage=_coverage(0, 20),
        timeline=_single_part_timeline(0, 20),
    )

    assert tuple(cue.ordinal for cue in result.admitted) == (0,)
    (rejected,) = result.rejected
    assert rejected.reason == "cue_processing_duplication"


def test_touching_half_open_cues_do_not_count_as_duplication() -> None:
    result = _gate(
        (
            _cue(0, ExactTime(0), ExactTime(5)),
            _cue(1, ExactTime(5), ExactTime(10)),
        ),
        part_coverage=_coverage(0, 20),
        timeline=_single_part_timeline(0, 20),
    )

    assert tuple(cue.ordinal for cue in result.admitted) == (0, 1)
    assert result.rejected == ()


def test_rejects_an_implausibly_fast_cue_for_its_text_length() -> None:
    fast = _cue(0, ExactTime(0), ExactTime(1), text="a" * 300)
    result = _gate(
        (fast,),
        part_coverage=_coverage(0, 20),
        timeline=_single_part_timeline(0, 20),
    )

    (rejected,) = result.rejected
    assert rejected.reason == "cue_duration_implausible"


def test_rejects_an_implausibly_slow_cue_for_its_text_length() -> None:
    slow = _cue(0, ExactTime(0), ExactTime(60), text="a")
    result = _gate(
        (slow,),
        part_coverage=_coverage(0, 120),
        timeline=_single_part_timeline(0, 120),
    )

    (rejected,) = result.rejected
    assert rejected.reason == "cue_duration_implausible"


def test_rejects_an_empty_text_cue_with_a_distinct_missing_text_reason() -> None:
    result = _gate(
        (_cue(0, ExactTime(0), ExactTime(5), text="   "),),
        part_coverage=_coverage(0, 20),
        timeline=_single_part_timeline(0, 20),
    )

    (rejected,) = result.rejected
    assert rejected.reason == "cue_missing_text"


def test_duplication_is_reported_ahead_of_duration_implausibility() -> None:
    # A cue that is both an exact duplicate and duration-implausible is recorded
    # as the duplication, so the "no processing duplication" gate stays auditable.
    result = _gate(
        (
            _cue(0, ExactTime(0), ExactTime(1)),
            _cue(1, ExactTime(0), ExactTime(1), text="a" * 300),
        ),
        part_coverage=_coverage(0, 20),
        timeline=_single_part_timeline(0, 20),
    )

    assert tuple(cue.ordinal for cue in result.admitted) == (0,)
    (rejected,) = result.rejected
    assert rejected.reason == "cue_processing_duplication"


def test_a_rejected_cue_does_not_poison_the_monotonic_order_of_later_cues() -> None:
    result = _gate(
        (
            _cue(0, ExactTime(0), ExactTime(5)),
            _cue(1, ExactTime(50), ExactTime(60)),  # out of coverage, rejected
            _cue(2, ExactTime(5), ExactTime(10)),  # still monotonic vs cue 0
        ),
        part_coverage=_coverage(0, 20),
        timeline=_single_part_timeline(0, 20),
    )

    assert tuple(cue.ordinal for cue in result.admitted) == (0, 2)
    assert tuple(cue.reason for cue in result.rejected) == ("cue_out_of_coverage",)


# --- Preconditions on our own inputs ----------------------------------------


def test_indeterminate_coverage_is_a_precondition_failure() -> None:
    indeterminate = StreamCoverage(
        coverage=None,
        gaps=(),
        diagnostics=(CoverageDiagnostic("coverage_indeterminate", "x", "no coverage"),),
    )
    with pytest.raises(gates.TranscriptionGateError) as error:
        _gate(
            (_cue(0, ExactTime(0), ExactTime(5)),),
            part_coverage=indeterminate,
            timeline=_single_part_timeline(0, 20),
        )
    assert error.value.reason == "gate_coverage_indeterminate"


def test_a_part_missing_from_the_timeline_is_a_precondition_failure() -> None:
    with pytest.raises(gates.TranscriptionGateError) as error:
        gates.gate_projected_cues(
            part_id="part-absent",
            cues=(_cue(0, ExactTime(0), ExactTime(5)),),
            part_coverage=_coverage(0, 20),
            timeline=_single_part_timeline(0, 20),
            rules=_RULES,
        )
    assert error.value.reason == "gate_timeline_mismatch"


def test_a_timeline_coverage_disagreement_is_a_precondition_failure() -> None:
    with pytest.raises(gates.TranscriptionGateError) as error:
        _gate(
            (_cue(0, ExactTime(0), ExactTime(5)),),
            part_coverage=_coverage(0, 20),
            timeline=_single_part_timeline(0, 30),  # endpoint disagrees with coverage
        )
    assert error.value.reason == "gate_timeline_mismatch"


def test_result_serializes_admitted_and_rejected_cues() -> None:
    result = _gate(
        (
            _cue(0, ExactTime(0), ExactTime(5)),
            _cue(1, ExactTime(0), ExactTime(5)),
        ),
        part_coverage=_coverage(0, 20),
        timeline=_single_part_timeline(0, 20),
    )

    document = result.as_json()
    assert document["part_id"] == "part-1"
    assert document["gate_version"] == "phase-07-timing-gate-rules-v1"
    assert document["calibration_required"] is True
    (admitted,) = document["admitted"]
    assert admitted["ordinal"] == 0
    assert admitted["collection_interval"] == {
        "start": {"numerator": 0, "denominator": 1},
        "end": {"numerator": 5, "denominator": 1},
    }
    (rejected,) = document["rejected"]
    assert rejected["reason"] == "cue_processing_duplication"
    assert "message" in rejected


# --- Versioned, calibration-required ruleset loader -------------------------


def _write_gate_rules(project_root: Path, **overrides: object) -> None:
    config = project_root / "config" / "transcription"
    config.mkdir(parents=True, exist_ok=True)
    rules = {
        "schema_version": 1,
        "id": "phase-07-transcription-rules-v1",
        "projection_schema_version": "phase-07-asr-projection-schema-v1",
        "controlled_adapter_identity": "phase-07-controlled-asr-adapter-v1",
        "timing_gate_version": "phase-07-timing-gate-rules-v1",
    }
    gate_rules = {
        "schema_version": 1,
        "version": "phase-07-timing-gate-rules-v1",
        "calibration_required": True,
        "duration_to_text": {
            "minimum_seconds_per_character": {"numerator": 1, "denominator": 100},
            "maximum_seconds_per_character": {"numerator": 30, "denominator": 1},
        },
    }
    gate_rules.update(overrides)
    (config / "transcription-rules.json").write_text(
        json.dumps(rules, sort_keys=True) + "\n", encoding="utf-8"
    )
    (config / "timing-gate-rules.json").write_text(
        json.dumps(gate_rules, sort_keys=True) + "\n", encoding="utf-8"
    )


def test_loader_binds_the_versioned_conservative_ruleset(tmp_path: Path) -> None:
    _write_gate_rules(tmp_path)

    ruleset = gates.load_timing_gate_ruleset(tmp_path)

    assert ruleset.version == "phase-07-timing-gate-rules-v1"
    assert ruleset.calibration_required is True
    assert ruleset.duration_to_text.minimum_seconds_per_character == ExactTime(1, 100)
    assert ruleset.duration_to_text.maximum_seconds_per_character == ExactTime(30)


def test_loader_rejects_a_version_mismatch(tmp_path: Path) -> None:
    _write_gate_rules(tmp_path, version="phase-07-timing-gate-rules-DRIFT")

    with pytest.raises(gates.TranscriptionGateError) as error:
        gates.load_timing_gate_ruleset(tmp_path)
    assert error.value.reason == "timing_gate_rules_invalid"


def test_loader_requires_the_calibration_required_mark(tmp_path: Path) -> None:
    _write_gate_rules(tmp_path, calibration_required=False)

    with pytest.raises(gates.TranscriptionGateError) as error:
        gates.load_timing_gate_ruleset(tmp_path)
    assert error.value.reason == "timing_gate_rules_invalid"


def test_loader_rejects_a_non_positive_duration_bound(tmp_path: Path) -> None:
    _write_gate_rules(
        tmp_path,
        duration_to_text={
            "minimum_seconds_per_character": {"numerator": 0, "denominator": 1},
            "maximum_seconds_per_character": {"numerator": 30, "denominator": 1},
        },
    )

    with pytest.raises(gates.TranscriptionGateError) as error:
        gates.load_timing_gate_ruleset(tmp_path)
    assert error.value.reason == "timing_gate_rules_invalid"


def test_the_shipped_project_ruleset_is_valid() -> None:
    ruleset = gates.load_timing_gate_ruleset(Path.cwd())

    assert ruleset.calibration_required is True
    assert ruleset.duration_to_text.minimum_seconds_per_character > ExactTime(0)
