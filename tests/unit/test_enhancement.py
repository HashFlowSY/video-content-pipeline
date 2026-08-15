"""Offline unit contract for Phase 7 ticket 07's deterministic enhancement core.

Ticket 07 merges ASR cues into user-named intervals by Gate-checked interval
replacement (ADR 0045). Following the strict-TDD rule and the Phase 6 precedent,
the deterministic core is tested directly as pure functions rather than only
through the CLI: selector parsing, scope resolution against retained cue
identities and stream coverage, and the interval-grained replacement rule with
its per-cue provenance and correction log. These tests assert the externally
observable contract -- provenance labels, gate-failure fallback, no cue-level
interleaving, untouched cues preserved, and recorded reasons. No model is
downloaded or executed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_content_pipeline import enhancement as enh
from video_content_pipeline.enhancement import (
    CORRECTION_REJECTION,
    CORRECTION_REPLACEMENT,
    PROVENANCE_ASR,
    PROVENANCE_SUBTITLE_TRACK,
    CueSelector,
    EnhancementError,
    PartSelector,
    RangeSelector,
    RetainedSubtitleCue,
    gate_checked_interval_replacement,
    load_retained_subtitle_cues,
    parse_selectors,
    resolve_enhancement_scope,
)
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
from video_content_pipeline.transcription_gates import (
    CanonicalTimelineGateResult,
    GatedAsrCue,
    RejectedAsrCue,
)


def _interval(start: int, end: int) -> HalfOpenInterval:
    return HalfOpenInterval(ExactTime(start), ExactTime(end))


def _retained(part_id: str, ordinal: int, start: int, end: int, text: str) -> RetainedSubtitleCue:
    return RetainedSubtitleCue(
        part_id=part_id,
        track_id="stream-1",
        source_ordinal=ordinal,
        interval=_interval(start, end),
        text=text,
    )


def _admitted(part_id: str, ordinal: int, start: int, end: int, text: str) -> GatedAsrCue:
    return GatedAsrCue(
        ordinal=ordinal,
        part_id=part_id,
        text=text,
        raw_interval=_interval(start, end),
        part_relative_interval=_interval(start, end),
        collection_interval=_interval(start, end),
        tokens=(),
        language_spans=(),
    )


def _rejected(ordinal: int, start: int, end: int, reason: str) -> RejectedAsrCue:
    return RejectedAsrCue(
        ordinal=ordinal, raw_interval=_interval(start, end), reason=reason, message=reason
    )


def _gate_result(
    part_id: str,
    admitted: tuple[GatedAsrCue, ...],
    rejected: tuple[RejectedAsrCue, ...] = (),
) -> CanonicalTimelineGateResult:
    return CanonicalTimelineGateResult(
        part_id=part_id,
        gate_version="phase-07-timing-gate-rules-v1",
        calibration_required=True,
        admitted=admitted,
        rejected=rejected,
    )


# --- Selector parsing -------------------------------------------------------


def test_parse_selectors_reads_part_range_and_cue_forms() -> None:
    selectors = parse_selectors(["part-1"], ["part-1:1-3.5"], ["part-1:4"])
    assert selectors[0] == PartSelector("part-1")
    assert selectors[1] == RangeSelector("part-1", ExactTime(1), ExactTime(7, 2))
    assert selectors[2] == CueSelector("part-1", 4)


def test_parse_selectors_accepts_the_full_retained_cue_identity() -> None:
    # ``--cue <part-id>:<cue-id>`` also accepts the full retained identity string.
    (selector,) = parse_selectors([], [], ["part-1:part-1:stream-1:4"])
    assert selector == CueSelector("part-1", 4)


def test_parse_selectors_requires_at_least_one_selector() -> None:
    with pytest.raises(EnhancementError) as excinfo:
        parse_selectors([], [], [])
    assert excinfo.value.reason == "enhancement_scope_missing"


@pytest.mark.parametrize(
    "part,range_,cue",
    [
        (["  "], [], []),
        ([], ["part-1:notatime-3"], []),
        ([], ["part-1:1"], []),
        ([], [], ["part-1:notanordinal"]),
    ],
)
def test_parse_selectors_rejects_malformed_selectors(
    part: list[str], range_: list[str], cue: list[str]
) -> None:
    with pytest.raises(EnhancementError) as excinfo:
        parse_selectors(part, range_, cue)
    assert excinfo.value.reason == "enhancement_selector_invalid"


# --- Scope resolution against retained cue identities and coverage ----------


def _cue_basis() -> dict[str, tuple[RetainedSubtitleCue, ...]]:
    return {
        "part-1": (
            _retained("part-1", 0, 0, 5, "第一句"),
            _retained("part-1", 1, 5, 10, "second line"),
            _retained("part-1", 2, 10, 20, "第三句"),
        )
    }


def test_resolve_scope_part_selector_covers_the_whole_part() -> None:
    scope = resolve_enhancement_scope([PartSelector("part-1")], cues_by_part=_cue_basis())
    assert len(scope) == 1
    assert scope[0].part_id == "part-1"
    assert scope[0].intervals == (_interval(0, 20),)


def test_resolve_scope_merges_overlapping_selectors_per_part() -> None:
    scope = resolve_enhancement_scope(
        [RangeSelector("part-1", ExactTime(0), ExactTime(6)), CueSelector("part-1", 1)],
        cues_by_part=_cue_basis(),
    )
    # The range [0,6) and cue 1's [5,10) merge into one window.
    assert scope[0].intervals == (_interval(0, 10),)


def test_resolve_scope_rejects_range_outside_retained_coverage() -> None:
    with pytest.raises(EnhancementError) as excinfo:
        resolve_enhancement_scope(
            [RangeSelector("part-1", ExactTime(0), ExactTime(25))], cues_by_part=_cue_basis()
        )
    assert excinfo.value.reason == "enhancement_range_out_of_coverage"


def test_resolve_scope_rejects_unknown_cue_ordinal() -> None:
    with pytest.raises(EnhancementError) as excinfo:
        resolve_enhancement_scope([CueSelector("part-1", 9)], cues_by_part=_cue_basis())
    assert excinfo.value.reason == "enhancement_cue_unknown"


def test_resolve_scope_rejects_unknown_part() -> None:
    with pytest.raises(EnhancementError) as excinfo:
        resolve_enhancement_scope([PartSelector("part-9")], cues_by_part=_cue_basis())
    assert excinfo.value.reason == "enhancement_part_unknown"


# --- Gate-checked interval replacement --------------------------------------


def test_admitted_asr_replaces_the_display_layer_with_asr_provenance() -> None:
    retained = (
        _retained("part-1", 0, 0, 5, "原句甲"),
        _retained("part-1", 1, 5, 10, "原句乙"),
        _retained("part-1", 2, 10, 15, "untouched tail"),
    )
    gate = _gate_result(
        "part-1",
        admitted=(
            _admitted("part-1", 0, 0, 5, "ASR 甲"),
            _admitted("part-1", 1, 5, 10, "ASR 乙"),
        ),
    )
    result = gate_checked_interval_replacement(
        part_id="part-1",
        retained_cues=retained,
        enhancement_intervals=(_interval(0, 10),),
        gate_result=gate,
    )

    provenances = [cue.provenance for cue in result.cues]
    texts = [cue.text for cue in result.cues]
    assert provenances == [PROVENANCE_ASR, PROVENANCE_ASR, PROVENANCE_SUBTITLE_TRACK]
    assert texts == ["ASR 甲", "ASR 乙", "untouched tail"]
    (entry,) = result.corrections
    assert entry.kind == CORRECTION_REPLACEMENT
    assert entry.replaced_cue_ids == ("part-1:stream-1:0", "part-1:stream-1:1")
    assert entry.asr_cue_ordinals == (0, 1)


def test_gate_failure_keeps_original_cues_with_a_recorded_reason() -> None:
    retained = (
        _retained("part-1", 0, 0, 5, "原句甲"),
        _retained("part-1", 1, 5, 10, "原句乙"),
    )
    gate = _gate_result(
        "part-1",
        admitted=(),
        rejected=(_rejected(0, 0, 5, "cue_duration_implausible"),),
    )
    result = gate_checked_interval_replacement(
        part_id="part-1",
        retained_cues=retained,
        enhancement_intervals=(_interval(0, 10),),
        gate_result=gate,
    )

    # On gate failure the whole interval keeps the original subtitle cues.
    assert [cue.provenance for cue in result.cues] == [
        PROVENANCE_SUBTITLE_TRACK,
        PROVENANCE_SUBTITLE_TRACK,
    ]
    assert [cue.text for cue in result.cues] == ["原句甲", "原句乙"]
    (entry,) = result.corrections
    assert entry.kind == CORRECTION_REJECTION
    assert entry.reason == "asr_cues_failed_gates"
    assert entry.gate_reasons == ("cue_duration_implausible",)
    assert result.rejected_interval_count() == 1


def test_absent_asr_candidate_keeps_originals_without_gate_reasons() -> None:
    retained = (_retained("part-1", 0, 0, 5, "原句甲"),)
    result = gate_checked_interval_replacement(
        part_id="part-1",
        retained_cues=retained,
        enhancement_intervals=(_interval(0, 5),),
        gate_result=_gate_result("part-1", admitted=()),
    )

    (entry,) = result.corrections
    assert entry.kind == CORRECTION_REJECTION
    assert entry.reason == "no_asr_candidate"
    assert entry.gate_reasons == ()
    assert [cue.provenance for cue in result.cues] == [PROVENANCE_SUBTITLE_TRACK]


def test_cues_outside_every_replaced_interval_are_carried_through_unchanged() -> None:
    retained = (
        _retained("part-1", 0, 0, 5, "before"),
        _retained("part-1", 1, 5, 10, "原句乙"),
        _retained("part-1", 2, 10, 15, "after"),
    )
    gate = _gate_result("part-1", admitted=(_admitted("part-1", 5, 5, 10, "ASR 乙"),))
    result = gate_checked_interval_replacement(
        part_id="part-1",
        retained_cues=retained,
        enhancement_intervals=(_interval(5, 10),),
        gate_result=gate,
    )

    # Only the middle interval is replaced; the flanking cues are exact and kept.
    assert [(cue.provenance, cue.text) for cue in result.cues] == [
        (PROVENANCE_SUBTITLE_TRACK, "before"),
        (PROVENANCE_ASR, "ASR 乙"),
        (PROVENANCE_SUBTITLE_TRACK, "after"),
    ]


def test_subtitle_cue_straddling_the_interval_boundary_is_kept_not_dropped() -> None:
    # A --range that cuts across a cue leaves that cue partly outside the user's
    # scope, so it must be preserved rather than deleted from the display layer.
    retained = (
        _retained("part-1", 0, 0, 5, "原句甲"),
        _retained("part-1", 1, 5, 12, "straddles the boundary"),
    )
    gate = _gate_result("part-1", admitted=(_admitted("part-1", 0, 0, 5, "ASR 甲"),))
    result = gate_checked_interval_replacement(
        part_id="part-1",
        retained_cues=retained,
        enhancement_intervals=(_interval(0, 10),),
        gate_result=gate,
    )

    assert [(cue.provenance, cue.text) for cue in result.cues] == [
        (PROVENANCE_ASR, "ASR 甲"),
        (PROVENANCE_SUBTITLE_TRACK, "straddles the boundary"),
    ]
    # Only the fully-enclosed cue is recorded as replaced.
    assert result.corrections[0].replaced_cue_ids == ("part-1:stream-1:0",)


def test_rejected_asr_cue_inside_interval_blocks_the_whole_interval() -> None:
    # One admitted and one rejected ASR cue target the same interval: the interval
    # is interval-grained, so a single rejection keeps the entire interval original.
    retained = (_retained("part-1", 0, 0, 10, "原句"),)
    gate = _gate_result(
        "part-1",
        admitted=(_admitted("part-1", 0, 0, 5, "ASR 前"),),
        rejected=(_rejected(1, 5, 10, "cue_processing_duplication"),),
    )
    result = gate_checked_interval_replacement(
        part_id="part-1",
        retained_cues=retained,
        enhancement_intervals=(_interval(0, 10),),
        gate_result=gate,
    )

    assert [cue.provenance for cue in result.cues] == [PROVENANCE_SUBTITLE_TRACK]
    assert result.corrections[0].kind == CORRECTION_REJECTION


# --- Retained cue loading ---------------------------------------------------


def test_load_retained_subtitle_cues_reads_ordinals_text_and_intervals(tmp_path: Path) -> None:
    candidate = tmp_path / "source-candidate.json"
    candidate.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cues": [
                    {
                        "source_ordinal": 1,
                        "text": "second",
                        "raw_pts_interval": {
                            "start": {"numerator": 5, "denominator": 1},
                            "end": {"numerator": 10, "denominator": 1},
                        },
                    },
                    {
                        "source_ordinal": 0,
                        "text": "first",
                        "raw_pts_interval": {
                            "start": {"numerator": 0, "denominator": 1},
                            "end": {"numerator": 5, "denominator": 1},
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    cues = load_retained_subtitle_cues(candidate, part_id="part-1", stream_index=1)

    # Cues are returned in start order and carry stable Part-local identities.
    assert [cue.source_ordinal for cue in cues] == [0, 1]
    assert [cue.cue_identity for cue in cues] == ["part-1:stream-1:0", "part-1:stream-1:1"]
    assert cues[0].text == "first"


def test_load_retained_subtitle_cues_rejects_malformed_evidence(tmp_path: Path) -> None:
    candidate = tmp_path / "source-candidate.json"
    candidate.write_text(json.dumps({"schema_version": 1, "cues": "nope"}), encoding="utf-8")
    with pytest.raises(EnhancementError) as excinfo:
        load_retained_subtitle_cues(candidate, part_id="part-1", stream_index=1)
    assert excinfo.value.reason == "enhancement_cue_basis_invalid"


# --- Readable correction report ---------------------------------------------


@pytest.mark.parametrize("numerator,denominator", [(1, 3), (7, 2), (5, 1), (0, 1)])
def test_range_text_round_trips_exactly_for_the_resume_path(
    numerator: int, denominator: int
) -> None:
    # The resume path rebuilds --range strings from retained intervals; the text
    # must re-parse to the identical exact time so a resumed scope never drifts.
    value = ExactTime(numerator, denominator)
    assert enh._seconds_to_exact(enh._seconds_text(value)) == value


def test_render_correction_report_defaults_to_chinese_prose() -> None:
    result = gate_checked_interval_replacement(
        part_id="part-1",
        retained_cues=(_retained("part-1", 0, 0, 5, "原句"),),
        enhancement_intervals=(_interval(0, 5),),
        gate_result=_gate_result("part-1", admitted=(_admitted("part-1", 0, 0, 5, "ASR"),)),
    )
    rendered = enh.render_correction_report((result,))
    assert "增强修正报告" in rendered
    assert "替换" in rendered
