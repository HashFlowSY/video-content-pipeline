"""Offline unit contract for Phase 8 ticket 02 deterministic core.

Ticket 02 establishes the visual-text command boundary. These tests exercise the
pure functions directly: scope parsing (``--all``/``--part``/``--range`` with no
default and Part-relative seconds), per-Part video coverage projection, resolved
scope revalidation against retained Part identities and video coverage, and the
versioned rules loader. No model runs, no frame is extracted, and no network is
accessed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_content_pipeline.coverage import StreamCoverage
from video_content_pipeline.inspection import PlanInspectionEvidence
from video_content_pipeline.planning import (
    PlanState,
    create_plan_report,
)
from video_content_pipeline.probe import ProbeDocument
from video_content_pipeline.source import SourceArtifact
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
from video_content_pipeline.visual_text import VisualTextError
from video_content_pipeline.visual_text_command import (
    AllScope,
    PartScope,
    RangeScope,
    load_visual_text_rule_versions,
    parse_visual_text_scope,
    part_video_coverage,
    resolve_visual_text_scope,
)

# --- Scope parsing ----------------------------------------------------------


def test_parse_scope_rejects_an_unscoped_invocation() -> None:
    with pytest.raises(VisualTextError) as excinfo:
        parse_visual_text_scope(False, (), ())
    assert excinfo.value.reason == "visual_text_scope_missing"


def test_parse_scope_accepts_all() -> None:
    assert parse_visual_text_scope(True, (), ()) == (AllScope(),)


def test_parse_scope_rejects_all_combined_with_a_named_part() -> None:
    with pytest.raises(VisualTextError) as excinfo:
        parse_visual_text_scope(True, ("part-1",), ())
    assert excinfo.value.reason == "visual_text_scope_invalid"


def test_parse_scope_parses_parts_and_ranges() -> None:
    selectors = parse_visual_text_scope(False, ("part-1",), ("part-2:1.5-3",))
    assert selectors == (
        PartScope("part-1"),
        RangeScope("part-2", ExactTime(3, 2), ExactTime(3)),
    )


def test_parse_scope_rejects_a_malformed_range() -> None:
    with pytest.raises(VisualTextError) as excinfo:
        parse_visual_text_scope(False, (), ("part-2:not-a-range",))
    assert excinfo.value.reason == "visual_text_selector_invalid"


def test_parse_scope_rejects_an_empty_part() -> None:
    with pytest.raises(VisualTextError) as excinfo:
        parse_visual_text_scope(False, ("   ",), ())
    assert excinfo.value.reason == "visual_text_selector_invalid"


# --- Per-Part video coverage ------------------------------------------------


def _evidence(
    source_id: str,
    *,
    streams: list[dict[str, object]],
    coverage: tuple[tuple[int, StreamCoverage], ...],
) -> PlanInspectionEvidence:
    return PlanInspectionEvidence(
        source_id=source_id,
        structural_document=ProbeDocument(json.dumps({"streams": streams})),
        coverage_document=ProbeDocument('{"packets": []}'),
        coverage_by_stream=coverage,
        subtitle_tracks=(),
    )


def _video_coverage(start: int, end: int) -> StreamCoverage:
    return StreamCoverage(
        coverage=HalfOpenInterval(ExactTime(start), ExactTime(end)), gaps=(), diagnostics=()
    )


def _report_with(evidence: tuple[PlanInspectionEvidence, ...]) -> object:
    artifacts = tuple(
        SourceArtifact(e.source_id, e.source_id, 1, Path(f"/tmp/{e.source_id}"), origin_kind="x")
        for e in evidence
    )
    return create_plan_report(
        state=PlanState.READY_FOR_CONFIRMATION,
        source_artifacts=artifacts,
        tools=(),
        planned_increment_bytes=0,
        configuration_fingerprint="fixture",
        inspection_evidence=evidence,
    )


def test_part_video_coverage_reads_the_video_stream_envelope() -> None:
    report = _report_with(
        (
            _evidence(
                "part-1",
                streams=[
                    {"index": 0, "codec_type": "video", "time_base": "1/1000"},
                    {"index": 1, "codec_type": "audio", "time_base": "1/1000"},
                ],
                coverage=((0, _video_coverage(0, 30)),),
            ),
        )
    )
    coverage = part_video_coverage(report)  # type: ignore[arg-type]
    assert coverage == {"part-1": HalfOpenInterval(ExactTime(0), ExactTime(30))}


def test_part_video_coverage_omits_a_part_without_a_video_stream() -> None:
    report = _report_with(
        (
            _evidence(
                "audio-only",
                streams=[{"index": 0, "codec_type": "audio", "time_base": "1/1000"}],
                coverage=(),
            ),
        )
    )
    assert part_video_coverage(report) == {}  # type: ignore[arg-type]


def test_part_video_coverage_omits_indeterminate_video_coverage() -> None:
    report = _report_with(
        (
            _evidence(
                "part-1",
                streams=[{"index": 0, "codec_type": "video", "time_base": "1/1000"}],
                coverage=((0, StreamCoverage(coverage=None, gaps=(), diagnostics=())),),
            ),
        )
    )
    assert part_video_coverage(report) == {}  # type: ignore[arg-type]


# --- Resolved scope ---------------------------------------------------------

_PLAN_PARTS = {"part-1", "part-2"}
_COVERAGE = {
    "part-1": HalfOpenInterval(ExactTime(10), ExactTime(40)),  # 30s starting at raw PTS 10
    "part-2": HalfOpenInterval(ExactTime(0), ExactTime(20)),
}


def test_resolve_whole_part_is_full_relative_coverage() -> None:
    (scope,) = resolve_visual_text_scope(
        (PartScope("part-1"),), plan_part_ids=_PLAN_PARTS, coverage_by_part=_COVERAGE
    )
    assert scope.part_id == "part-1"
    assert scope.coverage_start == ExactTime(10)
    assert scope.coverage_duration == ExactTime(30)
    assert scope.intervals == (HalfOpenInterval(ExactTime(0), ExactTime(30)),)


def test_resolve_all_expands_to_every_covered_part_sorted() -> None:
    scopes = resolve_visual_text_scope(
        (AllScope(),), plan_part_ids=_PLAN_PARTS, coverage_by_part=_COVERAGE
    )
    assert [scope.part_id for scope in scopes] == ["part-1", "part-2"]


def test_resolve_all_with_no_covered_part_is_empty_scope() -> None:
    with pytest.raises(VisualTextError) as excinfo:
        resolve_visual_text_scope((AllScope(),), plan_part_ids=_PLAN_PARTS, coverage_by_part={})
    assert excinfo.value.reason == "visual_text_scope_empty"


def test_resolve_range_is_part_relative_within_duration() -> None:
    (scope,) = resolve_visual_text_scope(
        (RangeScope("part-1", ExactTime(5), ExactTime(25)),),
        plan_part_ids=_PLAN_PARTS,
        coverage_by_part=_COVERAGE,
    )
    assert scope.intervals == (HalfOpenInterval(ExactTime(5), ExactTime(25)),)


def test_resolve_merges_overlapping_selectors_in_one_part() -> None:
    (scope,) = resolve_visual_text_scope(
        (
            RangeScope("part-2", ExactTime(0), ExactTime(10)),
            RangeScope("part-2", ExactTime(5), ExactTime(15)),
        ),
        plan_part_ids=_PLAN_PARTS,
        coverage_by_part=_COVERAGE,
    )
    assert scope.intervals == (HalfOpenInterval(ExactTime(0), ExactTime(15)),)


def test_resolve_rejects_an_unknown_part() -> None:
    with pytest.raises(VisualTextError) as excinfo:
        resolve_visual_text_scope(
            (PartScope("part-9"),), plan_part_ids=_PLAN_PARTS, coverage_by_part=_COVERAGE
        )
    assert excinfo.value.reason == "visual_text_part_unknown"


def test_resolve_rejects_a_plan_part_without_video_coverage() -> None:
    with pytest.raises(VisualTextError) as excinfo:
        resolve_visual_text_scope(
            (PartScope("part-2"),),
            plan_part_ids=_PLAN_PARTS,
            coverage_by_part={"part-1": _COVERAGE["part-1"]},
        )
    assert excinfo.value.reason == "visual_text_part_uncovered"


def test_resolve_rejects_a_range_past_the_duration() -> None:
    with pytest.raises(VisualTextError) as excinfo:
        resolve_visual_text_scope(
            (RangeScope("part-1", ExactTime(0), ExactTime(31)),),
            plan_part_ids=_PLAN_PARTS,
            coverage_by_part=_COVERAGE,
        )
    assert excinfo.value.reason == "visual_text_range_out_of_coverage"


def test_resolve_rejects_an_inverted_range() -> None:
    with pytest.raises(VisualTextError) as excinfo:
        resolve_visual_text_scope(
            (RangeScope("part-1", ExactTime(10), ExactTime(5)),),
            plan_part_ids=_PLAN_PARTS,
            coverage_by_part=_COVERAGE,
        )
    assert excinfo.value.reason == "visual_text_range_invalid"


# --- Versioned rules loader -------------------------------------------------


def test_rules_loader_reads_the_repository_rules() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    versions = load_visual_text_rule_versions(repo_root)
    assert versions.detection == "phase-08-page-change-detection-v1"
    assert versions.sampling == "phase-08-frame-sampling-v1"
    assert versions.classification == "phase-08-ocr-item-classification-v1"
    assert len(versions.fingerprint) == 64


def test_rules_loader_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(VisualTextError) as excinfo:
        load_visual_text_rule_versions(tmp_path)
    assert excinfo.value.reason == "visual_text_rules_invalid"


def test_rules_loader_rejects_a_malformed_file(tmp_path: Path) -> None:
    path = tmp_path / "config" / "visual-text" / "rules.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"detection": {}}', encoding="utf-8")
    with pytest.raises(VisualTextError) as excinfo:
        load_visual_text_rule_versions(tmp_path)
    assert excinfo.value.reason == "visual_text_rules_invalid"
