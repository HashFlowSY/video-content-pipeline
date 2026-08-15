"""Offline unit contract for Phase 8 ticket 05: the OCR item gates.

Before a projected OCR item may become evidence it must sit correctly on the Part
it claims. These tests exercise ``gate_ocr_items`` directly: an item inside
coverage and consistent with its page's appearance records is admitted carrying
Part/PTS/page/confidence; an item outside coverage, naming an unknown page, or
timed outside every recorded appearance of its page is rejected -- never repaired
-- with a single structured reason.
"""

from __future__ import annotations

from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
from video_content_pipeline.visual_page_index import PageAppearance, PartPageIndex, VisualPage
from video_content_pipeline.visual_text_contracts import OcrLanguageSpan, ProjectedOcrItem
from video_content_pipeline.visual_text_gates import gate_ocr_items


def _page(page_id: str, appearances: list[tuple[int, int]], selected: int | None) -> VisualPage:
    return VisualPage(
        visual_page_id=page_id,
        content_fingerprint=page_id,
        appearances=tuple(
            PageAppearance(ExactTime(start), ExactTime(end), frame_count=1)
            for start, end in appearances
        ),
        selected_frame_pts=None if selected is None else ExactTime(selected),
    )


def _index(pages: list[VisualPage]) -> PartPageIndex:
    return PartPageIndex(
        part_id="part-1",
        detection_version="d",
        sampling_version="s",
        pages=tuple(pages),
        retained_frames=(),
    )


def _item(
    page_id: str, pts: int, *, text: str = "text", confidence: float = 0.8
) -> ProjectedOcrItem:
    return ProjectedOcrItem(
        part_id="part-1",
        visual_page_id=page_id,
        pts=ExactTime(pts),
        text=text,
        confidence=confidence,
        language_spans=(),
    )


_COVERAGE = HalfOpenInterval(ExactTime(0), ExactTime(30))


def test_item_inside_coverage_and_appearance_is_admitted() -> None:
    index = _index([_page("page-01", [(0, 2), (5, 5)], selected=0)])
    item = _item("page-01", 0, text="登录", confidence=0.75)
    result = gate_ocr_items(part_id="part-1", items=[item], page_index=index, coverage=_COVERAGE)
    assert result.rejected == ()
    (admitted,) = result.admitted
    # AC#3: the admitted item carries Part, PTS, visual_page_id, and confidence.
    assert admitted.part_id == "part-1"
    assert admitted.visual_page_id == "page-01"
    assert admitted.pts == ExactTime(0)
    assert admitted.confidence == 0.75
    assert admitted.text == "登录"


def test_item_carries_its_language_spans_through_the_gate() -> None:
    index = _index([_page("page-01", [(0, 2)], selected=0)])
    item = ProjectedOcrItem(
        part_id="part-1",
        visual_page_id="page-01",
        pts=ExactTime(0),
        text="登录 Login",
        confidence=0.9,
        language_spans=(OcrLanguageSpan("zh", 0, 2), OcrLanguageSpan("en", 3, 8)),
    )
    result = gate_ocr_items(part_id="part-1", items=[item], page_index=index, coverage=_COVERAGE)
    (admitted,) = result.admitted
    assert admitted.language_spans == (OcrLanguageSpan("zh", 0, 2), OcrLanguageSpan("en", 3, 8))


def test_item_past_coverage_is_rejected_out_of_coverage() -> None:
    index = _index([_page("page-01", [(0, 2)], selected=0)])
    item = _item("page-01", 99)
    result = gate_ocr_items(part_id="part-1", items=[item], page_index=index, coverage=_COVERAGE)
    assert result.admitted == ()
    (rejected,) = result.rejected
    assert rejected.reason == "ocr_item_out_of_coverage"


def test_item_naming_an_unknown_page_is_rejected() -> None:
    index = _index([_page("page-01", [(0, 2)], selected=0)])
    item = _item("page-77", 1)
    result = gate_ocr_items(part_id="part-1", items=[item], page_index=index, coverage=_COVERAGE)
    (rejected,) = result.rejected
    assert rejected.reason == "ocr_item_unknown_page"


def test_item_timed_outside_its_pages_appearances_is_rejected() -> None:
    # page-01 appears only at [0,2] and [5,5]; an item at t=3 sits in the gap.
    index = _index([_page("page-01", [(0, 2), (5, 5)], selected=0)])
    item = _item("page-01", 3)
    result = gate_ocr_items(part_id="part-1", items=[item], page_index=index, coverage=_COVERAGE)
    (rejected,) = result.rejected
    assert rejected.reason == "ocr_item_page_time_mismatch"


def test_a_rejected_item_never_affects_another() -> None:
    index = _index([_page("page-01", [(0, 2)], selected=0)])
    good = _item("page-01", 1)
    bad = _item("page-01", 3)
    result = gate_ocr_items(
        part_id="part-1", items=[bad, good], page_index=index, coverage=_COVERAGE
    )
    assert len(result.admitted) == 1 and len(result.rejected) == 1
    assert result.admitted[0].pts == ExactTime(1)
