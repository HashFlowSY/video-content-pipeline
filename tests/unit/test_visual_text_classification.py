"""Offline unit contract for Phase 8 ticket 06: the OCR-item classification engine.

Every admitted OCR evidence item is classified deterministically into exactly one
of three page categories -- page text, speaker supplement, or background UI --
unless it is either platform noise (retained but marked non-evidence) or too
low-confidence to categorize (``classification_uncertain``, never forced). These
tests exercise ``classify_ocr_items`` directly against an in-memory versioned
ruleset: the same input and rule version always classify identically, excluded
items keep their matched noise kind, and low-confidence items are never forced
into a category.
"""

from __future__ import annotations

from video_content_pipeline.timecode import ExactTime
from video_content_pipeline.visual_text_classification import (
    CATEGORY_BACKGROUND_UI,
    CATEGORY_PAGE_TEXT,
    CATEGORY_SPEAKER_SUPPLEMENT,
    CLASSIFICATION_UNCERTAIN,
    ClassificationRuleset,
    classify_ocr_items,
)
from video_content_pipeline.visual_text_contracts import OcrLanguageSpan
from video_content_pipeline.visual_text_gates import GatedOcrItem

_RULES = ClassificationRuleset(
    version="test-classification-v1",
    calibration_required=True,
    minimum_classification_confidence=0.55,
    excluded_markers={
        "danmaku": ("弹幕",),
        "high_speed_chat": ("聊天室",),
        "watermark": ("水印",),
        "logo": ("台标",),
        "follow_gift_prompt": ("关注", "投币"),
        "platform_shell": ("正在直播",),
    },
    background_ui_markers=("下一页", "设置"),
    speaker_supplement_markers=("补充", "旁白"),
)


def _item(
    text: str,
    *,
    page_id: str = "page-01",
    pts: int = 1,
    confidence: float = 0.9,
    language_spans: tuple[OcrLanguageSpan, ...] = (),
) -> GatedOcrItem:
    return GatedOcrItem(
        part_id="part-1",
        visual_page_id=page_id,
        pts=ExactTime(pts),
        text=text,
        confidence=confidence,
        language_spans=language_spans,
    )


def test_default_high_confidence_item_is_page_text() -> None:
    result = classify_ocr_items(part_id="part-1", items=[_item("第一章 引言")], rules=_RULES)
    assert result.rules_version == "test-classification-v1"
    assert result.calibration_required is True
    assert result.excluded == ()
    (classified,) = result.classified
    assert classified.category == CATEGORY_PAGE_TEXT
    # The admitted evidence identity is carried through unchanged.
    assert classified.part_id == "part-1"
    assert classified.visual_page_id == "page-01"
    assert classified.pts == ExactTime(1)
    assert classified.confidence == 0.9
    assert classified.text == "第一章 引言"


def test_background_ui_marker_classifies_as_background_ui() -> None:
    (classified,) = classify_ocr_items(
        part_id="part-1", items=[_item("点击下一页")], rules=_RULES
    ).classified
    assert classified.category == CATEGORY_BACKGROUND_UI


def test_speaker_supplement_marker_classifies_as_speaker_supplement() -> None:
    (classified,) = classify_ocr_items(
        part_id="part-1", items=[_item("补充说明")], rules=_RULES
    ).classified
    assert classified.category == CATEGORY_SPEAKER_SUPPLEMENT


def test_low_confidence_item_is_uncertain_and_never_forced() -> None:
    # Below the confidence floor: even though it also matches a background-UI marker,
    # it is never forced into that category.
    (classified,) = classify_ocr_items(
        part_id="part-1", items=[_item("下一页", confidence=0.4)], rules=_RULES
    ).classified
    assert classified.category == CLASSIFICATION_UNCERTAIN


def test_platform_noise_is_excluded_non_evidence_with_its_kind() -> None:
    items = [
        _item("弹幕：好看", page_id="page-01"),
        _item("关注主播不迷路", page_id="page-02"),
        _item("台标水印", page_id="page-03"),
    ]
    result = classify_ocr_items(part_id="part-1", items=items, rules=_RULES)
    # None of the platform-noise items become formal classified evidence.
    assert result.classified == ()
    kinds = {excluded.visual_page_id: excluded.excluded_kind for excluded in result.excluded}
    assert kinds["page-01"] == "danmaku"
    assert kinds["page-02"] == "follow_gift_prompt"
    # Fixed excluded-kind precedence: 'logo' (台标) is checked before 'watermark' (水印).
    assert kinds["page-03"] == "logo"
    # Excluded items are retained with the marker that matched and marked non-evidence.
    page_one = next(e for e in result.excluded if e.visual_page_id == "page-01")
    assert page_one.matched_marker == "弹幕"
    assert page_one.as_json()["non_evidence"] is True


def test_exclusion_takes_precedence_over_low_confidence() -> None:
    # A low-confidence platform-noise item is still excluded non-evidence, so noise
    # can never leak into evidence as an 'uncertain' item.
    result = classify_ocr_items(
        part_id="part-1", items=[_item("弹幕", confidence=0.1)], rules=_RULES
    )
    assert result.classified == ()
    (excluded,) = result.excluded
    assert excluded.excluded_kind == "danmaku"


def test_classification_is_deterministic() -> None:
    items = [_item("弹幕"), _item("下一页"), _item("普通正文"), _item("低分", confidence=0.2)]
    first = classify_ocr_items(part_id="part-1", items=items, rules=_RULES).as_json()
    second = classify_ocr_items(part_id="part-1", items=items, rules=_RULES).as_json()
    assert first == second


def test_classified_item_preserves_language_spans() -> None:
    spans = (OcrLanguageSpan("zh", 0, 2), OcrLanguageSpan("en", 3, 8))
    (classified,) = classify_ocr_items(
        part_id="part-1", items=[_item("登录 Login", language_spans=spans)], rules=_RULES
    ).classified
    assert classified.language_spans == spans
    assert classified.as_json()["language_spans"] == [span.as_json() for span in spans]
