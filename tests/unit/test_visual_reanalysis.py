"""Unit coverage for Phase 8 ticket 07 visual-text re-analysis pure seams.

ADR 0046 recomputes semantic analysis at Part granularity; ticket 07 makes a
retained visual-text report an Optional visual-text context input to that
re-analysis (ADR 0047/0049). These tests exercise the deterministic building
blocks in isolation -- loading a retained visual-text report into domain
objects, selecting the Parts that carry new visual evidence, deriving Visual
page-change boundary candidates, and assigning each admitted page-text fact to
exactly one segment -- and assert contract behavior, never prose quality.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_content_pipeline import visual_reanalysis as vr
from video_content_pipeline.text_segmentation import BOUNDARY_EMPTY
from video_content_pipeline.timecode import ExactTime


def _time(seconds: int) -> dict[str, int]:
    return {"numerator": seconds, "denominator": 1}


def _classified(page_id: str, pts: int, *, category: str, text: str = "内容") -> dict[str, object]:
    return {
        "part_id": "part-a",
        "visual_page_id": page_id,
        "pts": _time(pts),
        "text": text,
        "confidence": 0.9,
        "language_spans": [],
        "category": category,
    }


def _page(page_id: str, appearances: list[tuple[int, int]]) -> dict[str, object]:
    return {
        "visual_page_id": page_id,
        "content_fingerprint": page_id,
        "appearances": [
            {"start": _time(start), "end": _time(end), "frame_count": 1}
            for start, end in appearances
        ],
        "selected_frame_pts": _time(appearances[0][0]),
    }


def _visual_report(
    tmp_path: Path,
    *,
    status: str = "complete",
    plan_id: str = "plan-1",
    classified: list[dict[str, object]] | None = None,
    pages: list[dict[str, object]] | None = None,
    part_id: str = "part-a",
) -> Path:
    document: dict[str, object] = {
        "report_id": "11111111111111111111111111111111",
        "plan_id": plan_id,
        "status": status,
        "page_index": {
            "parts": [
                {
                    "part_id": part_id,
                    "detection_version": "d1",
                    "sampling_version": "s1",
                    "pages": pages if pages is not None else [],
                    "retained_frames": [],
                }
            ]
        },
        "classification": (
            {
                "version": "phase-08-ocr-item-classification-v1",
                "calibration_required": True,
                "parts": [
                    {
                        "part_id": part_id,
                        "rules_version": "phase-08-ocr-item-classification-v1",
                        "calibration_required": True,
                        "classified": classified if classified is not None else [],
                        "excluded": [],
                    }
                ],
            }
            if classified is not None
            else None
        ),
        "ocr_evidence": {"state": "projected"} if classified is not None else None,
    }
    path = tmp_path / "visual-report.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


# --- load_visual_text_report ------------------------------------------------


def test_load_extracts_page_text_facts_and_page_change_times(tmp_path: Path) -> None:
    path = _visual_report(
        tmp_path,
        classified=[
            _classified("page-01", 0, category="page_text", text="第一章"),
            _classified("page-02", 5, category="background_ui", text="下一页"),
            _classified("page-02", 6, category="page_text", text="结论"),
        ],
        pages=[_page("page-01", [(0, 4)]), _page("page-02", [(5, 9)])],
    )

    loaded = vr.load_visual_text_report(path)

    assert loaded.plan_id == "plan-1"
    assert loaded.status == "complete"
    evidence = loaded.parts_by_id["part-a"]
    # Only page_text classified items become cited page facts (ADR 0049).
    assert tuple(fact.text for fact in evidence.page_facts) == ("第一章", "结论")
    assert all(fact.part_id == "part-a" for fact in evidence.page_facts)
    # The second page's start is a page change; the first Part start is not.
    assert evidence.page_change_times == (ExactTime(5),)
    assert evidence.has_visual_evidence


def test_load_marks_no_evidence_when_single_page_and_no_page_text(tmp_path: Path) -> None:
    path = _visual_report(
        tmp_path,
        classified=[_classified("page-01", 0, category="background_ui", text="下一页")],
        pages=[_page("page-01", [(0, 9)])],
    )

    evidence = vr.load_visual_text_report(path).parts_by_id["part-a"]

    assert evidence.page_facts == ()
    assert evidence.page_change_times == ()
    assert not evidence.has_visual_evidence


def test_load_rejects_unloadable_status(tmp_path: Path) -> None:
    path = _visual_report(tmp_path, status="model_acquisition_required")

    with pytest.raises(vr.VisualReanalysisError) as excinfo:
        vr.load_visual_text_report(path)

    assert excinfo.value.reason == "visual_text_report_not_loadable"


def test_load_rejects_missing_plan_identity(tmp_path: Path) -> None:
    document = {"report_id": "x", "status": "complete"}
    path = tmp_path / "visual-report.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(vr.VisualReanalysisError) as excinfo:
        vr.load_visual_text_report(path)

    assert excinfo.value.reason == "visual_text_report_unloadable"


def test_load_declined_partial_has_no_facts(tmp_path: Path) -> None:
    # A declined OCR run is ``partial`` with no classification: loadable, zero facts.
    path = _visual_report(tmp_path, status="partial", pages=[_page("page-01", [(0, 9)])])

    loaded = vr.load_visual_text_report(path)

    assert loaded.status == "partial"
    assert loaded.parts_by_id["part-a"].page_facts == ()


# --- select_visually_affected_parts -----------------------------------------


def test_selection_picks_only_available_parts_with_evidence(tmp_path: Path) -> None:
    path = _visual_report(
        tmp_path,
        classified=[_classified("page-01", 0, category="page_text")],
        pages=[_page("page-01", [(0, 9)])],
        part_id="part-a",
    )
    loaded = vr.load_visual_text_report(path)

    # part-a carries evidence and is available; part-z is available but absent
    # from the visual report; part-a stays affected, part-z is carried forward.
    affected = vr.select_visually_affected_parts(loaded, ("part-a", "part-z"))

    assert affected == ("part-a",)


def test_selection_excludes_parts_absent_from_prior_available(tmp_path: Path) -> None:
    path = _visual_report(
        tmp_path,
        classified=[_classified("page-01", 0, category="page_text")],
        pages=[_page("page-01", [(0, 9)])],
    )
    loaded = vr.load_visual_text_report(path)

    # A Part with visual evidence that is not an available prior Part cannot be
    # regenerated (no cue basis) and is not selected.
    assert vr.select_visually_affected_parts(loaded, ("part-z",)) == ()


# --- visual_boundary_candidates ---------------------------------------------


def _cues(*starts: int) -> tuple[vr.TimedCue, ...]:
    return tuple(
        vr.TimedCue(cue_id=f"part-a:stream-1:{ordinal}", start=ExactTime(start))
        for ordinal, start in enumerate(starts)
    )


def test_boundary_candidates_split_at_page_change() -> None:
    cues = _cues(0, 2, 4, 6)
    candidates = vr.visual_boundary_candidates("part-a", (ExactTime(4),), cues)

    # A change at t=4 splits before the cue whose start >= 4: [0,2] then [4,6].
    assert [(c.start_cue_id, c.end_cue_id) for c in candidates] == [
        ("part-a:stream-1:0", "part-a:stream-1:1"),
        ("part-a:stream-1:2", "part-a:stream-1:3"),
    ]
    assert all(c.technical_block_id == vr.VISUAL_PAGE_CHANGE_ORIGIN for c in candidates)


def test_boundary_candidates_are_adjudicable_cue_pairs() -> None:
    # A candidate must never invert its cue pair (which the adjudicator would
    # reject as an empty boundary); every emitted span is start <= end in order.
    from video_content_pipeline.text_segmentation import (
        PartCueInventory,
        adjudicate_part_segments,
    )

    cues = _cues(0, 2, 4, 6)
    candidates = vr.visual_boundary_candidates("part-a", (ExactTime(4),), cues)
    inventory = PartCueInventory(
        part_id="part-a", cue_ids=tuple(cue.cue_id for cue in cues)
    )
    outcome = adjudicate_part_segments(inventory, candidates)

    # Page changes alone tile the Part into two adjudicated segments.
    assert not outcome.used_fallback
    assert len(outcome.segments) == 2
    assert not any(d.reason == BOUNDARY_EMPTY for d in outcome.rejected)


def test_boundary_candidates_empty_without_change() -> None:
    assert vr.visual_boundary_candidates("part-a", (), _cues(0, 2)) == ()


# --- assign_page_facts -------------------------------------------------------


def _fact(pts: int, text: str = "f") -> vr.VisualPageFact:
    return vr.VisualPageFact(
        part_id="part-a", visual_page_id="page-01", pts=ExactTime(pts), text=text, confidence=0.9
    )


def test_assign_owns_each_fact_by_the_segment_in_effect() -> None:
    facts = [_fact(1, "a"), _fact(5, "b"), _fact(9, "c")]
    # Segment 0 begins at t=0, segment 1 at t=4.
    starts = [(0, ExactTime(0)), (1, ExactTime(4))]

    owned = vr.assign_page_facts(facts, starts)

    assert [fact.text for fact in owned[0]] == ["a"]
    assert [fact.text for fact in owned[1]] == ["b", "c"]


def test_assign_is_total_and_exactly_once() -> None:
    facts = [_fact(1), _fact(5), _fact(9)]
    starts = [(0, ExactTime(0)), (1, ExactTime(4))]

    owned = vr.assign_page_facts(facts, starts)

    total = sum(len(items) for items in owned.values())
    assert total == len(facts)  # every fact owned exactly once


def test_assign_before_first_segment_falls_to_first() -> None:
    facts = [_fact(1)]
    starts = [(3, ExactTime(5))]  # only segment begins after the fact's pts

    owned = vr.assign_page_facts(facts, starts)

    assert list(owned) == [3]
    assert len(owned[3]) == 1
