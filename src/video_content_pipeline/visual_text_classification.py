"""Phase 8's Versioned OCR-item classification rules (ticket 06).

Ticket 05 turns a raw OCR model output into gated OCR evidence items sitting
correctly on the Part they name. This module gives each admitted item a
deterministic disposition (ADR 0049: visual-text *classifies*, it never upgrades
a fact):

* a **page category** -- page text, speaker supplement, or background UI -- the
  ordinary evidence categories a downstream semantic segment can own;
* **excluded, non-evidence** when the item matches platform noise (danmaku,
  high-speed chat, unrelated watermarks, logos, follow/gift prompts, or repeated
  platform shell). Excluded items are retained in the workspace with the marker
  that matched and marked non-evidence so they never become formal content; and
* **``classification_uncertain``** when the item's confidence sits below the
  versioned floor. An uncertain item is never forced into a page category.

The rules are a versioned, ``calibration_required`` ruleset -- real marker and
threshold calibration happens only in a separately authorized real-world session
-- and the version is recorded in every result, so the same input and the same
version always classify identically. Matching is a deterministic substring test
over the verbatim OCR text; no model judges a category (ADR 0047). Exclusion is
evaluated *before* the confidence floor: platform noise is withheld from evidence
even when the OCR read was low-confidence, so noise can never leak in as an
uncertain item. No model is downloaded or executed. See
``docs/PHASE_08_SPECIFICATION.md`` and the Visual-Text Context.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeGuard

from video_content_pipeline.timecode import ExactTime
from video_content_pipeline.visual_text import VisualTextError
from video_content_pipeline.visual_text_contracts import OcrLanguageSpan
from video_content_pipeline.visual_text_gates import GatedOcrItem

# The three ordinary page categories an admitted item may receive, plus the
# never-forced uncertain disposition (ADR 0049).
CATEGORY_PAGE_TEXT = "page_text"
CATEGORY_SPEAKER_SUPPLEMENT = "speaker_supplement"
CATEGORY_BACKGROUND_UI = "background_ui"
CLASSIFICATION_UNCERTAIN = "classification_uncertain"

# The platform-noise kinds an Excluded visual item may match, in the fixed
# precedence the classifier tests them; a more specific kind (a logo) is checked
# before a broader one (a watermark) so the recorded kind is deterministic.
EXCLUDED_KINDS: tuple[str, ...] = (
    "danmaku",
    "high_speed_chat",
    "follow_gift_prompt",
    "logo",
    "watermark",
    "platform_shell",
)

_RULES_RELATIVE_PATH = ("config", "visual-text", "rules.json")


# --- Versioned, calibration-required ruleset --------------------------------


@dataclass(frozen=True)
class ClassificationRuleset:
    """The versioned deterministic classification markers and confidence floor.

    ``excluded_markers`` maps each platform-noise kind to the verbatim substrings
    that mark an item as that kind of noise; ``background_ui_markers`` and
    ``speaker_supplement_markers`` steer the two non-default page categories. All
    marker matching is a plain substring test over the item's OCR text, so the same
    rule version always classifies the same text identically.
    """

    version: str
    calibration_required: bool
    minimum_classification_confidence: float
    excluded_markers: Mapping[str, tuple[str, ...]]
    background_ui_markers: tuple[str, ...]
    speaker_supplement_markers: tuple[str, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "version": self.version,
            "calibration_required": self.calibration_required,
            "minimum_classification_confidence": self.minimum_classification_confidence,
            "excluded_markers": {
                kind: list(self.excluded_markers[kind]) for kind in EXCLUDED_KINDS
            },
            "category_markers": {
                "background_ui": list(self.background_ui_markers),
                "speaker_supplement": list(self.speaker_supplement_markers),
            },
        }


# --- Classification outcomes ------------------------------------------------


@dataclass(frozen=True)
class ClassifiedOcrItem:
    """One admitted OCR evidence item with its deterministic page category.

    ``category`` is one of the three page categories or ``classification_uncertain``;
    the Part, page, PTS, verbatim text, confidence, and language spans are carried
    through unchanged so the evidence identity survives classification.
    """

    part_id: str
    visual_page_id: str
    pts: ExactTime
    text: str
    confidence: float
    language_spans: tuple[OcrLanguageSpan, ...]
    category: str

    def as_json(self) -> dict[str, object]:
        return {
            "part_id": self.part_id,
            "visual_page_id": self.visual_page_id,
            "pts": _time_as_json(self.pts),
            "text": self.text,
            "confidence": self.confidence,
            "language_spans": [span.as_json() for span in self.language_spans],
            "category": self.category,
        }


@dataclass(frozen=True)
class ExcludedVisualItem:
    """One item matched to platform noise: retained in the workspace, never evidence.

    ``excluded_kind`` names which platform-noise kind matched and ``matched_marker``
    records the exact substring that fired, so the exclusion is auditable. The item
    is retained with its identity but marked non-evidence and never appears as formal
    content.
    """

    part_id: str
    visual_page_id: str
    pts: ExactTime
    text: str
    confidence: float
    excluded_kind: str
    matched_marker: str

    def as_json(self) -> dict[str, object]:
        return {
            "part_id": self.part_id,
            "visual_page_id": self.visual_page_id,
            "pts": _time_as_json(self.pts),
            "text": self.text,
            "confidence": self.confidence,
            "excluded_kind": self.excluded_kind,
            "matched_marker": self.matched_marker,
            "non_evidence": True,
        }


@dataclass(frozen=True)
class PartClassificationResult:
    """The deterministic classification of one Part's admitted OCR evidence items."""

    part_id: str
    rules_version: str
    calibration_required: bool
    classified: tuple[ClassifiedOcrItem, ...]
    excluded: tuple[ExcludedVisualItem, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "part_id": self.part_id,
            "rules_version": self.rules_version,
            "calibration_required": self.calibration_required,
            "classified": [item.as_json() for item in self.classified],
            "excluded": [item.as_json() for item in self.excluded],
        }


def classify_ocr_items(
    *, part_id: str, items: Sequence[GatedOcrItem], rules: ClassificationRuleset
) -> PartClassificationResult:
    """Classify one Part's admitted OCR evidence items deterministically.

    Each item is evaluated independently in a fixed precedence: a platform-noise
    marker excludes it as non-evidence (checked first, so low-confidence noise never
    leaks in as an uncertain item); otherwise a sub-floor confidence marks it
    ``classification_uncertain``; otherwise a background-UI or speaker-supplement
    marker chooses that category; otherwise the item is page text. The versioned
    rule identity is recorded so the same input and version always classify the same
    way.
    """

    classified: list[ClassifiedOcrItem] = []
    excluded: list[ExcludedVisualItem] = []
    for item in items:
        noise = _matched_noise(item.text, rules)
        if noise is not None:
            kind, marker = noise
            excluded.append(_excluded(item, kind, marker))
            continue
        classified.append(_classify(item, rules))
    return PartClassificationResult(
        part_id=part_id,
        rules_version=rules.version,
        calibration_required=rules.calibration_required,
        classified=tuple(classified),
        excluded=tuple(excluded),
    )


def _matched_noise(text: str, rules: ClassificationRuleset) -> tuple[str, str] | None:
    for kind in EXCLUDED_KINDS:
        for marker in rules.excluded_markers.get(kind, ()):
            if marker in text:
                return kind, marker
    return None


def _classify(item: GatedOcrItem, rules: ClassificationRuleset) -> ClassifiedOcrItem:
    if item.confidence < rules.minimum_classification_confidence:
        category = CLASSIFICATION_UNCERTAIN
    elif _matches_any(item.text, rules.background_ui_markers):
        category = CATEGORY_BACKGROUND_UI
    elif _matches_any(item.text, rules.speaker_supplement_markers):
        category = CATEGORY_SPEAKER_SUPPLEMENT
    else:
        category = CATEGORY_PAGE_TEXT
    return ClassifiedOcrItem(
        part_id=item.part_id,
        visual_page_id=item.visual_page_id,
        pts=item.pts,
        text=item.text,
        confidence=item.confidence,
        language_spans=item.language_spans,
        category=category,
    )


def _matches_any(text: str, markers: Sequence[str]) -> bool:
    return any(marker in text for marker in markers)


def _excluded(item: GatedOcrItem, kind: str, marker: str) -> ExcludedVisualItem:
    return ExcludedVisualItem(
        part_id=item.part_id,
        visual_page_id=item.visual_page_id,
        pts=item.pts,
        text=item.text,
        confidence=item.confidence,
        excluded_kind=kind,
        matched_marker=marker,
    )


# --- Versioned ruleset loader -----------------------------------------------


def load_classification_ruleset(project_root: Path) -> ClassificationRuleset:
    """Load and version-bind the classification ruleset from the visual-text rules.

    The ``classification`` section of ``config/visual-text/rules.json`` carries the
    version already recorded in rule-version provenance, the ``calibration_required``
    mark, a confidence floor in ``[0, 1]``, a marker list for each of the six
    platform-noise kinds, and the two category marker lists. The ruleset is our own
    revalidated ground truth, so any missing or malformed field raises
    ``visual_text_rules_invalid`` before an attempt classifies anything.
    """

    document = _read_rules(project_root)
    section = _section(document, "classification")
    version = _string(section, "classification", "version")
    if section.get("calibration_required") is not True:
        raise VisualTextError(
            "visual_text_rules_invalid",
            "Visual-text classification rules must keep the calibration_required mark.",
        )
    excluded = _section(section, "excluded_markers")
    category = _section(section, "category_markers")
    return ClassificationRuleset(
        version=version,
        calibration_required=True,
        minimum_classification_confidence=_unit_interval(
            section.get("minimum_classification_confidence")
        ),
        excluded_markers={kind: _markers(excluded, kind) for kind in EXCLUDED_KINDS},
        background_ui_markers=_markers(category, "background_ui"),
        speaker_supplement_markers=_markers(category, "speaker_supplement"),
    )


def _read_rules(project_root: Path) -> Mapping[str, object]:
    path = project_root.joinpath(*_RULES_RELATIVE_PATH)
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VisualTextError(
            "visual_text_rules_invalid", f"Visual-text rules cannot be read: {path}"
        ) from error
    if not isinstance(decoded, Mapping):
        raise VisualTextError("visual_text_rules_invalid", "Visual-text rules must be an object.")
    return decoded


def _section(document: Mapping[str, object], key: str) -> Mapping[str, object]:
    section = document.get(key)
    if not isinstance(section, Mapping):
        raise VisualTextError(
            "visual_text_rules_invalid", f"Visual-text rules need a {key!r} object."
        )
    return section


def _string(section: Mapping[str, object], key: str, field: str) -> str:
    value = section.get(field)
    if not isinstance(value, str) or not value:
        raise VisualTextError(
            "visual_text_rules_invalid", f"Visual-text {key!r} rules need a {field!r} string."
        )
    return value


def _markers(section: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = section.get(key)
    if not isinstance(value, list) or not all(_is_nonempty_str(marker) for marker in value):
        raise VisualTextError(
            "visual_text_rules_invalid",
            f"Visual-text classification rules need a string marker list for {key!r}.",
        )
    return tuple(marker for marker in value if isinstance(marker, str))


def _unit_interval(value: object) -> float:
    if not _is_real(value) or not 0.0 <= float(value) <= 1.0:
        raise VisualTextError(
            "visual_text_rules_invalid",
            "Visual-text classification confidence floor must lie in [0, 1].",
        )
    return float(value)


def _is_nonempty_str(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value)


def _is_real(value: object) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _time_as_json(value: ExactTime) -> dict[str, int]:
    return {"numerator": value.numerator, "denominator": value.denominator}


__all__ = [
    "CATEGORY_BACKGROUND_UI",
    "CATEGORY_PAGE_TEXT",
    "CATEGORY_SPEAKER_SUPPLEMENT",
    "CLASSIFICATION_UNCERTAIN",
    "EXCLUDED_KINDS",
    "ClassificationRuleset",
    "ClassifiedOcrItem",
    "ExcludedVisualItem",
    "PartClassificationResult",
    "classify_ocr_items",
    "load_classification_ruleset",
]
