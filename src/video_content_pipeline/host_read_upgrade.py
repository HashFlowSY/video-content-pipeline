"""Phase 8 ticket 08: the Host-read comment upgrade, owned by text-analysis.

ADR 0049 draws a bright line: visual-text *classifies* an on-screen comment as
``background_ui`` and never promotes it. The one permitted promotion -- a
background-UI comment becoming formal evidence because the host explicitly
selected or read it aloud -- is a cross-modal fact decision, so it lives in
text-analysis, which already holds both cue evidence and OCR evidence during
Affected-Part re-analysis.

This module is the deterministic decision, kept as a pure seam so it can be
exercised in isolation:

* a background-UI comment is upgraded only when a subtitle cue *near its page
  time* contains the comment's text -- cross-modal confirmation that the host read
  it (or, when a versioned selection marker such as "有人问" is present in that cue,
  that the host explicitly selected it);
* an item that finds no such supporting cue is never upgraded: it stays background
  UI and never enters formal content; and
* every upgrade carries the page time, the selection basis, and citations to both
  the OCR evidence item and the supporting cues, under a versioned
  ``calibration_required`` ruleset whose version is recorded so the same input and
  version always decide identically.

Matching is a deterministic substring test over whitespace- and
punctuation-normalized text (no model judges the comparison), guarded by a minimum
comment length so a trivially short comment cannot substring-match an unrelated
cue. Real marker and window calibration happens only in a separately authorized
real-world session. See ``docs/PHASE_08_SPECIFICATION.md``, ADR 0049, and the
Text-Analysis and Visual-Text Contexts.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeGuard

from video_content_pipeline.timecode import ExactTime

# The two selection bases an upgrade may record (ADR 0049): the host read the
# comment aloud, or the host explicitly selected it (a versioned deixis marker sits
# in the supporting cue's framing text). Both require the comment text to appear in
# a nearby cue.
SELECTION_BASIS_READ = "host_read"
SELECTION_BASIS_SELECTED = "host_selected"

_RULES_RELATIVE_PATH = ("config", "text-analysis", "host-read-upgrade.json")

# Whitespace and punctuation stripped before the substring comparison, so a cue
# that renders the same words with spaces or different punctuation still matches.
_STRIPPED = frozenset(
    " \t\r\n　，。！？：；、,.!?:;\"'“”‘’「」『』（）()【】[]…—~"
)


class HostReadUpgradeError(ValueError):
    """A rejected Host-read upgrade ruleset, with a machine-readable reason.

    Text-analysis owns the upgrade (ADR 0049), so the failure is named on the
    text-analysis side rather than borrowing the visual-text rules-error taxonomy.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


# --- Versioned, calibration-required ruleset --------------------------------


@dataclass(frozen=True)
class HostReadUpgradeRuleset:
    """The versioned deterministic rules governing the Host-read comment upgrade.

    ``minimum_comment_length`` is the shortest normalized comment that may be
    upgraded (a floor against trivial substring matches); ``match_window_seconds``
    bounds how far a supporting cue's interval may sit from the comment's page time;
    ``selection_markers`` are the verbatim deixis substrings that, when present in a
    supporting cue, record the stronger ``host_selected`` basis. The version is
    recorded in every result, so the same input and version always decide the same.
    """

    version: str
    calibration_required: bool
    minimum_comment_length: int
    match_window_seconds: int
    selection_markers: tuple[str, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "version": self.version,
            "calibration_required": self.calibration_required,
            "minimum_comment_length": self.minimum_comment_length,
            "match_window_seconds": self.match_window_seconds,
            "selection_markers": list(self.selection_markers),
        }


# --- Inputs and result ------------------------------------------------------


@dataclass(frozen=True)
class BackgroundUiComment:
    """One background-UI OCR item -- a candidate for the Host-read comment upgrade.

    It carries the evidence identity visual-text classified: the Part, page, PTS,
    verbatim on-screen text, and confidence. Nothing here is formal evidence until
    text-analysis upgrades it.
    """

    part_id: str
    visual_page_id: str
    pts: ExactTime
    text: str
    confidence: float

    def as_json(self) -> dict[str, object]:
        return {
            "part_id": self.part_id,
            "visual_page_id": self.visual_page_id,
            "pts": _time_as_json(self.pts),
            "text": self.text,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class CueText:
    """One subtitle cue's identity, retained raw-PTS interval, and verbatim text.

    The cross-modal comparison reads all three: the identity to cite the supporting
    cue, the interval to bound it to the comment's page time, and the text to test
    whether the host read the comment.
    """

    cue_id: str
    start: ExactTime
    end: ExactTime
    text: str


@dataclass(frozen=True)
class HostReadUpgrade:
    """One background-UI comment promoted to formal evidence, fully cited.

    ``selection_basis`` records whether the host read or explicitly selected the
    comment; ``supporting_cue_ids`` are the cues whose text confirmed it. The record
    carries the page time and citations to both the OCR evidence item and the cues,
    and never mutates the visual-side report (ADR 0049).
    """

    comment: BackgroundUiComment
    selection_basis: str
    supporting_cue_ids: tuple[str, ...]
    rules_version: str

    @property
    def pts(self) -> ExactTime:
        """The comment's page time -- the axis a formal segment owns it by."""

        return self.comment.pts

    def as_json(self) -> dict[str, object]:
        return {
            "selection_basis": self.selection_basis,
            "rules_version": self.rules_version,
            "page_time": {
                "visual_page_id": self.comment.visual_page_id,
                "pts": _time_as_json(self.comment.pts),
            },
            "ocr_evidence_item": self.comment.as_json(),
            "supporting_cue_ids": list(self.supporting_cue_ids),
        }


def evaluate_host_read_upgrades(
    *,
    comments: Sequence[BackgroundUiComment],
    cues: Sequence[CueText],
    rules: HostReadUpgradeRuleset,
) -> tuple[HostReadUpgrade, ...]:
    """Decide which background-UI comments the host read or selected, deterministically.

    Each comment is tested independently: it is upgraded only when at least one cue
    whose interval lies within ``match_window_seconds`` of the comment's page time
    contains the comment's normalized text. A comment shorter than the floor, or with
    no supporting cue in the window, is never upgraded -- it stays background UI. When
    any supporting cue also contains a selection marker, the record's basis is
    ``host_selected``; otherwise it is ``host_read``. Results are ordered by page
    time, so the same input and rule version always produce the same records.
    """

    upgrades: list[HostReadUpgrade] = []
    for comment in sorted(comments, key=lambda item: item.pts.as_fraction()):
        normalized_comment = _normalize(comment.text)
        if len(normalized_comment) < rules.minimum_comment_length:
            continue
        supporting = _supporting_cues(normalized_comment, comment.pts, cues, rules)
        if not supporting:
            continue
        basis = (
            SELECTION_BASIS_SELECTED
            if any(
                _has_selection_marker(cue.text, normalized_comment, rules)
                for cue in supporting
            )
            else SELECTION_BASIS_READ
        )
        upgrades.append(
            HostReadUpgrade(
                comment=comment,
                selection_basis=basis,
                supporting_cue_ids=tuple(cue.cue_id for cue in supporting),
                rules_version=rules.version,
            )
        )
    return tuple(upgrades)


def _supporting_cues(
    normalized_comment: str,
    pts: ExactTime,
    cues: Sequence[CueText],
    rules: HostReadUpgradeRuleset,
) -> tuple[CueText, ...]:
    """Return the in-window cues that contain the comment text, in start order."""

    window = ExactTime(rules.match_window_seconds)
    lower = (pts - window).as_fraction()
    upper = (pts + window).as_fraction()
    matched = [
        cue
        for cue in cues
        if cue.start.as_fraction() <= upper
        and cue.end.as_fraction() >= lower
        and normalized_comment in _normalize(cue.text)
    ]
    return tuple(sorted(matched, key=lambda cue: (cue.start.as_fraction(), cue.cue_id)))


def _has_selection_marker(
    cue_text: str, normalized_comment: str, rules: HostReadUpgradeRuleset
) -> bool:
    """Whether the cue's *framing* text carries a selection deixis marker.

    A supporting cue always contains the comment text by construction, so a marker
    that is itself a substring of the comment (e.g. "这个问题" inside "这个问题问得好")
    would trivially fire and mislabel a plain read as ``host_selected``. We therefore
    strip the quoted comment out first and look for the marker only in what remains --
    the host's own framing ("有人问 ...", "我们来看这条 ...").
    """

    framing = _normalize(cue_text).replace(normalized_comment, "", 1)
    return any(_normalize(marker) in framing for marker in rules.selection_markers)


def _normalize(text: str) -> str:
    return "".join(character for character in text if character not in _STRIPPED)


# --- Versioned ruleset loader -----------------------------------------------


def load_host_read_upgrade_ruleset(project_root: Path) -> HostReadUpgradeRuleset:
    """Load and version-bind the Host-read comment upgrade ruleset.

    ``config/text-analysis/host-read-upgrade.json`` carries the version recorded in
    provenance, the ``calibration_required`` mark, a non-negative comment-length
    floor and match window, and the selection-marker list. The ruleset is our own
    revalidated ground truth, so a missing artifact or any malformed field raises
    ``visual_text_rules_invalid`` before an attempt upgrades anything.
    """

    document = _read_rules(project_root)
    version = _string(document, "version")
    if document.get("calibration_required") is not True:
        raise HostReadUpgradeError(
            "host_read_upgrade_rules_invalid",
            "Host-read comment upgrade rules must keep the calibration_required mark.",
        )
    return HostReadUpgradeRuleset(
        version=version,
        calibration_required=True,
        minimum_comment_length=_non_negative_int(
            document.get("minimum_comment_length"), "minimum_comment_length"
        ),
        match_window_seconds=_non_negative_int(
            document.get("match_window_seconds"), "match_window_seconds"
        ),
        selection_markers=_markers(document, "selection_markers"),
    )


def _read_rules(project_root: Path) -> Mapping[str, object]:
    path = project_root.joinpath(*_RULES_RELATIVE_PATH)
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HostReadUpgradeError(
            "host_read_upgrade_rules_invalid",
            f"Host-read comment upgrade rules cannot be read: {path}",
        ) from error
    if not isinstance(decoded, Mapping):
        raise HostReadUpgradeError(
            "host_read_upgrade_rules_invalid", "Host-read comment upgrade rules must be an object."
        )
    return decoded


def _string(document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise HostReadUpgradeError(
            "host_read_upgrade_rules_invalid",
            f"Host-read comment upgrade rules need a {field!r} string.",
        )
    return value


def _non_negative_int(value: object, field: str) -> int:
    if not _is_int(value) or value < 0:
        raise HostReadUpgradeError(
            "host_read_upgrade_rules_invalid",
            f"Host-read comment upgrade rules need a non-negative integer {field!r}.",
        )
    return value


def _markers(document: Mapping[str, object], field: str) -> tuple[str, ...]:
    value = document.get(field)
    if not isinstance(value, list) or not all(_is_nonempty_str(marker) for marker in value):
        raise HostReadUpgradeError(
            "host_read_upgrade_rules_invalid",
            f"Host-read comment upgrade rules need a string marker list for {field!r}.",
        )
    return tuple(marker for marker in value if isinstance(marker, str))


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_nonempty_str(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value)


def _time_as_json(value: ExactTime) -> dict[str, int]:
    return {"numerator": value.numerator, "denominator": value.denominator}


__all__ = [
    "SELECTION_BASIS_READ",
    "SELECTION_BASIS_SELECTED",
    "BackgroundUiComment",
    "CueText",
    "HostReadUpgrade",
    "HostReadUpgradeError",
    "HostReadUpgradeRuleset",
    "evaluate_host_read_upgrades",
    "load_host_read_upgrade_ruleset",
]
