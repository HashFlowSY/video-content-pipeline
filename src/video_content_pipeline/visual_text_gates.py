"""Phase 8's Part-local coverage and page-consistency gates for projected OCR items.

Ticket 05 turns a raw OCR model output into typed :class:`ProjectedOcrItem`
evidence. Before any projected item may become OCR evidence it must sit correctly
on the Part it claims, so this module applies the deterministic gates the
specification names -- structurally the mirror of the Phase 7 canonical-timeline
cue gates:

* **inside actual stream coverage** -- an item whose Part-relative PTS falls
  before the Part's coverage start or at/after its coverage endpoint is rejected;
  the item never spills outside the Part it names;
* **a known Part-local page** -- an item naming a ``visual_page_id`` that the
  Part's deterministic page index never recorded is rejected; and
* **consistent with the page's appearance records** -- an item whose PTS does not
  fall inside any recorded appearance of its page (the page was not on screen at
  that time) is rejected.

Rejected items are never repaired: each is retained with a Part, its page, its
PTS, and a single structured reason so the evidence trail can trace the decision.
All time comparison is exact rational arithmetic over ``ExactTime`` -- no float
accumulation. No model is downloaded or executed. See
``docs/PHASE_08_SPECIFICATION.md`` and the Visual-Text Context (ADR 0048's
Part-local page identity).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
from video_content_pipeline.visual_page_index import PartPageIndex, VisualPage
from video_content_pipeline.visual_text_contracts import OcrLanguageSpan, ProjectedOcrItem

# The per-item rejection reasons; a rejected item keeps exactly one of these.
_OUT_OF_COVERAGE = "ocr_item_out_of_coverage"
_UNKNOWN_PAGE = "ocr_item_unknown_page"
_PAGE_TIME_MISMATCH = "ocr_item_page_time_mismatch"


@dataclass(frozen=True)
class GatedOcrItem:
    """One admitted OCR evidence item carrying Part, PTS, page, confidence, and text."""

    part_id: str
    visual_page_id: str
    pts: ExactTime
    text: str
    confidence: float
    language_spans: tuple[OcrLanguageSpan, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "part_id": self.part_id,
            "visual_page_id": self.visual_page_id,
            "pts": _time_as_json(self.pts),
            "text": self.text,
            "confidence": self.confidence,
            "language_spans": [span.as_json() for span in self.language_spans],
        }


@dataclass(frozen=True)
class RejectedOcrItem:
    """One rejected OCR item retained with its structured reason -- never repaired."""

    part_id: str
    visual_page_id: str
    pts: ExactTime
    reason: str
    message: str

    def as_json(self) -> dict[str, object]:
        return {
            "part_id": self.part_id,
            "visual_page_id": self.visual_page_id,
            "pts": _time_as_json(self.pts),
            "reason": self.reason,
            "message": self.message,
        }


@dataclass(frozen=True)
class OcrItemGateResult:
    """The deterministic outcome of gating one Part's projected OCR items."""

    part_id: str
    admitted: tuple[GatedOcrItem, ...]
    rejected: tuple[RejectedOcrItem, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "part_id": self.part_id,
            "admitted": [item.as_json() for item in self.admitted],
            "rejected": [item.as_json() for item in self.rejected],
        }


def gate_ocr_items(
    *,
    part_id: str,
    items: Sequence[ProjectedOcrItem],
    page_index: PartPageIndex,
    coverage: HalfOpenInterval,
) -> OcrItemGateResult:
    """Gate one Part's projected OCR items against its coverage and page index.

    ``coverage`` is the Part-relative video coverage envelope (``[0, duration)``)
    and ``page_index`` is the Part's deterministic page index whose appearance
    records place each page on the Part clock. Each item is admitted with its Part,
    page, PTS, confidence, and verbatim text, or rejected -- never repaired -- with
    a single structured reason. Reason precedence is most-structural first:
    coverage, then page existence, then appearance consistency. Items are
    independent, so one rejection never affects another.
    """

    pages_by_id: Mapping[str, VisualPage] = {page.visual_page_id: page for page in page_index.pages}
    admitted: list[GatedOcrItem] = []
    rejected: list[RejectedOcrItem] = []
    for item in items:
        outcome = _gate_one_item(item, part_id, coverage, pages_by_id)
        if isinstance(outcome, RejectedOcrItem):
            rejected.append(outcome)
        else:
            admitted.append(outcome)
    return OcrItemGateResult(
        part_id=part_id,
        admitted=tuple(admitted),
        rejected=tuple(rejected),
    )


def _gate_one_item(
    item: ProjectedOcrItem,
    part_id: str,
    coverage: HalfOpenInterval,
    pages_by_id: Mapping[str, VisualPage],
) -> GatedOcrItem | RejectedOcrItem:
    if not (coverage.start <= item.pts and item.pts < coverage.end):
        return _reject(
            item,
            part_id,
            _OUT_OF_COVERAGE,
            "Item PTS falls outside the Part's determinate video coverage.",
        )
    page = pages_by_id.get(item.visual_page_id)
    if page is None:
        return _reject(
            item,
            part_id,
            _UNKNOWN_PAGE,
            f"Item names page {item.visual_page_id!r}, absent from this Part's page index.",
        )
    if not _within_an_appearance(item.pts, page):
        return _reject(
            item,
            part_id,
            _PAGE_TIME_MISMATCH,
            "Item PTS is not inside any recorded appearance of its page.",
        )
    return GatedOcrItem(
        part_id=part_id,
        visual_page_id=item.visual_page_id,
        pts=item.pts,
        text=item.text,
        confidence=item.confidence,
        language_spans=item.language_spans,
    )


def _within_an_appearance(pts: ExactTime, page: VisualPage) -> bool:
    """Return whether ``pts`` lies inside a closed appearance interval of ``page``.

    Appearance records are closed observed sample times (``start`` and ``end`` may
    be equal for a single-frame appearance), so the check is inclusive on both ends.
    """

    return any(
        appearance.start <= pts and pts <= appearance.end for appearance in page.appearances
    )


def _reject(
    item: ProjectedOcrItem, part_id: str, reason: str, message: str
) -> RejectedOcrItem:
    return RejectedOcrItem(
        part_id=part_id,
        visual_page_id=item.visual_page_id,
        pts=item.pts,
        reason=reason,
        message=message,
    )


def _time_as_json(value: ExactTime) -> dict[str, int]:
    return {"numerator": value.numerator, "denominator": value.denominator}


__all__ = [
    "GatedOcrItem",
    "OcrItemGateResult",
    "RejectedOcrItem",
    "gate_ocr_items",
]
