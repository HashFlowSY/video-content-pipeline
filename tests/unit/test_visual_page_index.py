"""Offline unit contract for Phase 8 ticket 03 (deterministic Part-local page index).

Ticket 03 builds the deterministic page-change detection and Versioned
frame-sampling engine: from hash-pinned synthetic frame-metric fixtures
(stability, edge density, region-scoped frame difference) it derives Part-local
Visual pages, Page appearance records (first appearance and every reappearance),
and a complete Retained frame inventory carrying the reason each frame was or was
not selected for OCR. No model runs anywhere in detection, no OCR is consulted,
and no frame is extracted from user media.

These tests exercise the pure engine directly. Determinism, Part-local page
identity, appearance records, full frame retention with selection reasons, and
scope restriction are all asserted without touching the filesystem.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
from video_content_pipeline.visual_page_index import (
    SELECTED_PAGE_REPRESENTATIVE,
    UNSELECTED_BELOW_TEXT_VALUE,
    UNSELECTED_DUPLICATE_OF_SELECTED,
    UNSELECTED_TRANSITION_FRAME,
    FrameMetric,
    PageIndexRules,
    build_part_page_index,
    frames_in_scope,
    load_frame_metric_fixture,
    load_page_index_rules,
)
from video_content_pipeline.visual_text import VisualTextError

# A settled, text-bearing page: stable, low change, high edge density.
_RULES = PageIndexRules(
    detection_version="detect-v1",
    sampling_version="sample-v1",
    stability_min=60,
    region_diff_change=40,
    text_value_min=30,
)


def _frame(
    pts: int,
    fingerprint: str,
    *,
    stability: int = 100,
    edge_density: int = 80,
    region_diff: int = 0,
) -> FrameMetric:
    return FrameMetric(
        pts=ExactTime(pts),
        content_fingerprint=fingerprint,
        stability=stability,
        edge_density=edge_density,
        region_diff=region_diff,
    )


# --- Page identity and appearance records ----------------------------------


def test_single_stable_page_is_one_part_local_page_with_one_appearance() -> None:
    index = build_part_page_index(
        "part-1", (_frame(0, "aaa"), _frame(1, "aaa"), _frame(2, "aaa")), _RULES
    )
    assert [page.visual_page_id for page in index.pages] == ["page-01"]
    (page,) = index.pages
    assert page.content_fingerprint == "aaa"
    assert len(page.appearances) == 1
    assert page.appearances[0].start == ExactTime(0)
    assert page.appearances[0].end == ExactTime(2)
    assert page.appearances[0].frame_count == 3


def test_reappearance_reuses_the_page_id_and_records_every_appearance() -> None:
    # aaa, then a change to bbb, then aaa returns: page-01 reappears.
    frames = (
        _frame(0, "aaa"),
        _frame(1, "aaa"),
        _frame(2, "bbb", region_diff=90),  # transition into bbb
        _frame(3, "bbb"),
        _frame(4, "aaa", region_diff=90),  # transition back to aaa
        _frame(5, "aaa"),
    )
    index = build_part_page_index("part-1", frames, _RULES)
    ids = [page.visual_page_id for page in index.pages]
    assert ids == ["page-01", "page-02"]
    page_aaa = index.pages[0]
    assert page_aaa.content_fingerprint == "aaa"
    # First appearance [0,1], reappearance [5,5] -- exact times, both retained.
    assert [(a.start, a.end) for a in page_aaa.appearances] == [
        (ExactTime(0), ExactTime(1)),
        (ExactTime(5), ExactTime(5)),
    ]
    page_bbb = index.pages[1]
    assert [(a.start, a.end) for a in page_bbb.appearances] == [(ExactTime(3), ExactTime(3))]


def test_no_cross_part_correlation_page_ids_restart_per_part() -> None:
    # The same fingerprint in two Parts gets an independent Part-local id.
    first = build_part_page_index("part-1", (_frame(0, "shared"),), _RULES)
    second = build_part_page_index("part-2", (_frame(0, "shared"),), _RULES)
    assert first.pages[0].visual_page_id == "page-01"
    assert second.pages[0].visual_page_id == "page-01"
    assert first.part_id == "part-1"
    assert second.part_id == "part-2"


# --- Sampling: exactly one representative per text-bearing page -------------


def test_first_text_bearing_frame_of_a_page_is_the_representative() -> None:
    frames = (
        _frame(0, "aaa", edge_density=10),  # below text value
        _frame(1, "aaa", edge_density=90),  # first text-bearing -> representative
        _frame(2, "aaa", edge_density=90),  # duplicate of the representative
    )
    index = build_part_page_index("part-1", frames, _RULES)
    assert index.pages[0].selected_frame_pts == ExactTime(1)
    reasons = {frame.pts: frame.selection_reason for frame in index.retained_frames}
    selected = {frame.pts: frame.selected for frame in index.retained_frames}
    assert reasons[ExactTime(0)] == UNSELECTED_BELOW_TEXT_VALUE
    assert reasons[ExactTime(1)] == SELECTED_PAGE_REPRESENTATIVE
    assert reasons[ExactTime(2)] == UNSELECTED_DUPLICATE_OF_SELECTED
    assert selected == {ExactTime(0): False, ExactTime(1): True, ExactTime(2): False}


def test_a_page_with_no_text_value_is_detected_but_selects_no_frame() -> None:
    frames = (_frame(0, "aaa", edge_density=5), _frame(1, "aaa", edge_density=10))
    index = build_part_page_index("part-1", frames, _RULES)
    assert index.pages[0].selected_frame_pts is None
    assert all(not frame.selected for frame in index.retained_frames)
    assert all(
        frame.selection_reason == UNSELECTED_BELOW_TEXT_VALUE for frame in index.retained_frames
    )


def test_representative_is_the_earliest_text_frame_across_reappearances() -> None:
    frames = (
        _frame(0, "aaa", edge_density=10),  # page appears, no text yet
        _frame(1, "bbb", region_diff=90),
        _frame(2, "aaa", region_diff=90, edge_density=90),  # transition frame, not settled
        _frame(3, "aaa", edge_density=90),  # earliest settled text frame of aaa
    )
    index = build_part_page_index("part-1", frames, _RULES)
    page_aaa = next(page for page in index.pages if page.content_fingerprint == "aaa")
    assert page_aaa.selected_frame_pts == ExactTime(3)


# --- Retained frame inventory: nothing discarded ---------------------------


def test_every_frame_is_retained_once_and_transition_frames_belong_to_no_page() -> None:
    frames = (
        _frame(0, "aaa"),
        _frame(1, "bbb", region_diff=90),  # transition frame
        _frame(2, "bbb"),
    )
    index = build_part_page_index("part-1", frames, _RULES)
    assert len(index.retained_frames) == len(frames)
    transition = next(frame for frame in index.retained_frames if frame.pts == ExactTime(1))
    assert transition.visual_page_id is None
    assert transition.selection_reason == UNSELECTED_TRANSITION_FRAME
    assert not transition.selected
    # No frame is discarded: the inventory covers exactly the input timestamps.
    assert {frame.pts for frame in index.retained_frames} == {
        ExactTime(0),
        ExactTime(1),
        ExactTime(2),
    }


def test_low_stability_frame_is_a_transition_frame() -> None:
    frames = (_frame(0, "aaa"), _frame(1, "aaa", stability=10), _frame(2, "aaa"))
    index = build_part_page_index("part-1", frames, _RULES)
    unstable = next(frame for frame in index.retained_frames if frame.pts == ExactTime(1))
    assert unstable.selection_reason == UNSELECTED_TRANSITION_FRAME
    assert unstable.visual_page_id is None
    # The stable frames on either side are the same page reappearing.
    assert [page.visual_page_id for page in index.pages] == ["page-01"]
    assert len(index.pages[0].appearances) == 2


# --- Determinism ------------------------------------------------------------


def test_same_input_and_rules_produce_identical_json() -> None:
    frames = (
        _frame(0, "aaa"),
        _frame(1, "bbb", region_diff=90),
        _frame(2, "bbb"),
        _frame(3, "aaa", region_diff=90),
    )
    first = build_part_page_index("part-1", frames, _RULES)
    second = build_part_page_index("part-1", frames, _RULES)
    assert first.as_json() == second.as_json()


def test_frames_are_ordered_by_pts_before_detection() -> None:
    ordered = build_part_page_index(
        "part-1", (_frame(0, "aaa"), _frame(1, "aaa")), _RULES
    )
    shuffled = build_part_page_index(
        "part-1", (_frame(1, "aaa"), _frame(0, "aaa")), _RULES
    )
    assert ordered.as_json() == shuffled.as_json()


# --- Scope restriction ------------------------------------------------------


def test_frames_in_scope_keeps_only_frames_inside_the_intervals() -> None:
    frames = tuple(_frame(pts, "aaa") for pts in range(6))
    kept = frames_in_scope(
        frames,
        (
            HalfOpenInterval(ExactTime(1), ExactTime(3)),
            HalfOpenInterval(ExactTime(4), ExactTime(5)),
        ),
    )
    assert [frame.pts for frame in kept] == [ExactTime(1), ExactTime(2), ExactTime(4)]


# --- Rules loader -----------------------------------------------------------


def test_page_index_rules_load_from_the_repository_rules() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    rules = load_page_index_rules(repo_root)
    assert rules.detection_version == "phase-08-page-change-detection-v1"
    assert rules.sampling_version == "phase-08-frame-sampling-v1"
    assert 0 <= rules.stability_min <= 100
    assert 0 <= rules.region_diff_change <= 100
    assert 0 <= rules.text_value_min <= 100


def test_page_index_rules_reject_a_missing_threshold(tmp_path: Path) -> None:
    path = tmp_path / "config" / "visual-text" / "rules.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "detection": {"version": "d"},
                "sampling": {"version": "s", "text_value_min": 30},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(VisualTextError) as excinfo:
        load_page_index_rules(tmp_path)
    assert excinfo.value.reason == "visual_text_rules_invalid"


# --- Frame-metric fixture loader -------------------------------------------


def _write_fixture(path: Path, part_id: str, frames: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "part_id": part_id,
                "detection_rule_version": "phase-08-page-change-detection-v1",
                "sampling_rule_version": "phase-08-frame-sampling-v1",
                "frames": frames,
            }
        ),
        encoding="utf-8",
    )


def test_fixture_loader_reads_frames_and_hash_pins_the_input(tmp_path: Path) -> None:
    path = tmp_path / "metrics.json"
    _write_fixture(
        path,
        "part-1",
        [
            {
                "pts": {"numerator": 0, "denominator": 1},
                "content_fingerprint": "aaa",
                "stability": 100,
                "edge_density": 80,
                "region_diff": 0,
            }
        ],
    )
    fixture = load_frame_metric_fixture(path, "part-1")
    assert fixture.part_id == "part-1"
    assert fixture.detection_version == "phase-08-page-change-detection-v1"
    assert fixture.frames[0].content_fingerprint == "aaa"
    assert fixture.evidence.sha256 and fixture.evidence.byte_count > 0


def test_fixture_loader_rejects_a_part_id_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "metrics.json"
    _write_fixture(path, "other-part", [])
    with pytest.raises(VisualTextError) as excinfo:
        load_frame_metric_fixture(path, "part-1")
    assert excinfo.value.reason == "visual_text_frame_metrics_invalid"


def test_fixture_loader_rejects_a_malformed_frame(tmp_path: Path) -> None:
    path = tmp_path / "metrics.json"
    _write_fixture(path, "part-1", [{"pts": {"numerator": 0, "denominator": 1}}])
    with pytest.raises(VisualTextError) as excinfo:
        load_frame_metric_fixture(path, "part-1")
    assert excinfo.value.reason == "visual_text_frame_metrics_invalid"
