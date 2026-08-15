"""Unit coverage for Phase 8 ticket 08's Host-read comment upgrade engine.

ADR 0049 keeps visual-text a pure evidence producer: it classifies an on-screen
comment as ``background_ui`` and never promotes it. The one permitted promotion --
a background-UI comment becoming formal evidence because the host explicitly
selected or read it aloud -- is a cross-modal fact decision owned by text-analysis.
These tests exercise the deterministic decision in isolation: the same comments,
cues, and rule version always upgrade the same items with the same selection basis,
and an item that fails the comparison is never upgraded.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_content_pipeline import host_read_upgrade as hru
from video_content_pipeline.timecode import ExactTime


def _ruleset(
    *,
    version: str = "phase-08-host-read-upgrade-v1",
    minimum_comment_length: int = 3,
    match_window_seconds: int = 2,
    selection_markers: tuple[str, ...] = ("这条", "有人问"),
) -> hru.HostReadUpgradeRuleset:
    return hru.HostReadUpgradeRuleset(
        version=version,
        calibration_required=True,
        minimum_comment_length=minimum_comment_length,
        match_window_seconds=match_window_seconds,
        selection_markers=selection_markers,
    )


def _comment(pts: int, text: str, *, page_id: str = "page-02") -> hru.BackgroundUiComment:
    return hru.BackgroundUiComment(
        part_id="part-a", visual_page_id=page_id, pts=ExactTime(pts), text=text, confidence=0.9
    )


def _cue(cue_id: str, start: int, end: int, text: str) -> hru.CueText:
    return hru.CueText(cue_id=cue_id, start=ExactTime(start), end=ExactTime(end), text=text)


# --- evaluate_host_read_upgrades: the read basis -----------------------------


def test_comment_read_aloud_is_upgraded_with_read_basis() -> None:
    comments = [_comment(10, "这个功能怎么用")]
    cues = [_cue("c0", 9, 11, "他问，这个功能怎么用？")]

    upgrades = hru.evaluate_host_read_upgrades(comments=comments, cues=cues, rules=_ruleset())

    assert len(upgrades) == 1
    upgrade = upgrades[0]
    assert upgrade.selection_basis == hru.SELECTION_BASIS_READ
    assert upgrade.supporting_cue_ids == ("c0",)
    assert upgrade.rules_version == "phase-08-host-read-upgrade-v1"
    # AC4: the record cites both the OCR evidence item and the supporting cues.
    record = upgrade.as_json()
    assert record["ocr_evidence_item"]["text"] == "这个功能怎么用"
    assert record["supporting_cue_ids"] == ["c0"]
    # AC1: the record carries the page time (page id + PTS).
    assert record["page_time"]["visual_page_id"] == "page-02"
    assert record["page_time"]["pts"] == {"numerator": 10, "denominator": 1}


def test_selection_marker_in_a_supporting_cue_gives_selected_basis() -> None:
    comments = [_comment(10, "这个功能怎么用")]
    cues = [_cue("c0", 9, 11, "有人问这个功能怎么用")]

    upgrades = hru.evaluate_host_read_upgrades(comments=comments, cues=cues, rules=_ruleset())

    assert upgrades[0].selection_basis == hru.SELECTION_BASIS_SELECTED


def test_selection_marker_that_is_a_substring_of_the_comment_does_not_force_selected() -> None:
    # The marker "这个问题" is embedded in the comment itself; a plain read where the
    # cue only echoes the comment must stay host_read, not be mislabeled host_selected.
    comments = [_comment(10, "这个问题问得好")]
    cues = [_cue("c0", 9, 11, "我来回答这个问题问得好")]
    rules = _ruleset(selection_markers=("这个问题", "有人问"))

    upgrades = hru.evaluate_host_read_upgrades(comments=comments, cues=cues, rules=rules)

    assert upgrades[0].selection_basis == hru.SELECTION_BASIS_READ


# --- items that fail the comparison remain background UI (AC2) ---------------


def test_comment_with_no_matching_cue_is_not_upgraded() -> None:
    comments = [_comment(10, "这个功能怎么用")]
    cues = [_cue("c0", 9, 11, "今天我们讲一个完全不同的话题")]

    assert hru.evaluate_host_read_upgrades(comments=comments, cues=cues, rules=_ruleset()) == ()


def test_matching_cue_outside_the_time_window_is_not_upgraded() -> None:
    comments = [_comment(100, "这个功能怎么用")]
    # A cue reading the same text but far from the comment's page time (window=2s).
    cues = [_cue("c0", 9, 11, "他问，这个功能怎么用？")]

    assert hru.evaluate_host_read_upgrades(comments=comments, cues=cues, rules=_ruleset()) == ()


def test_trivially_short_comment_is_never_upgraded() -> None:
    # A one-character comment would substring-match nearly any cue; the floor guards it.
    comments = [_comment(10, "好")]
    cues = [_cue("c0", 9, 11, "好的我们开始吧")]

    assert hru.evaluate_host_read_upgrades(comments=comments, cues=cues, rules=_ruleset()) == ()


def test_upgrade_is_deterministic_and_ordered_by_page_time() -> None:
    comments = [_comment(20, "第二个问题"), _comment(10, "第一个问题")]
    cues = [
        _cue("c0", 9, 11, "有人问第一个问题"),
        _cue("c1", 19, 21, "他念了第二个问题"),
    ]

    first = hru.evaluate_host_read_upgrades(comments=comments, cues=cues, rules=_ruleset())
    second = hru.evaluate_host_read_upgrades(comments=comments, cues=cues, rules=_ruleset())

    assert [u.comment.text for u in first] == ["第一个问题", "第二个问题"]
    assert first == second


def test_punctuation_and_whitespace_are_normalized_before_comparison() -> None:
    comments = [_comment(10, "这个功能怎么用？")]
    # The cue spells the same words with spaces and different punctuation.
    cues = [_cue("c0", 9, 11, "他 问： 这个功能 怎么用。")]

    upgrades = hru.evaluate_host_read_upgrades(comments=comments, cues=cues, rules=_ruleset())

    assert len(upgrades) == 1


def test_all_supporting_cues_within_window_are_cited() -> None:
    comments = [_comment(10, "这个功能怎么用")]
    cues = [
        _cue("c0", 9, 11, "他问这个功能怎么用"),
        _cue("c1", 10, 12, "对，这个功能怎么用呢"),
        _cue("c2", 50, 52, "这个功能怎么用"),  # same text but outside the window
    ]

    upgrade = hru.evaluate_host_read_upgrades(comments=comments, cues=cues, rules=_ruleset())[0]

    assert upgrade.supporting_cue_ids == ("c0", "c1")


# --- load_host_read_upgrade_ruleset ------------------------------------------


def _write_rules(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_loader_binds_the_versioned_ruleset(tmp_path: Path) -> None:
    _write_rules(
        tmp_path / "config" / "text-analysis" / "host-read-upgrade.json",
        {
            "schema_version": 1,
            "version": "phase-08-host-read-upgrade-v1",
            "calibration_required": True,
            "minimum_comment_length": 3,
            "match_window_seconds": 2,
            "selection_markers": ["这条", "有人问"],
        },
    )

    rules = hru.load_host_read_upgrade_ruleset(tmp_path)

    assert rules.version == "phase-08-host-read-upgrade-v1"
    assert rules.calibration_required is True
    assert rules.minimum_comment_length == 3
    assert rules.match_window_seconds == 2
    assert rules.selection_markers == ("这条", "有人问")


def test_loader_requires_the_calibration_mark(tmp_path: Path) -> None:
    _write_rules(
        tmp_path / "config" / "text-analysis" / "host-read-upgrade.json",
        {
            "schema_version": 1,
            "version": "phase-08-host-read-upgrade-v1",
            "calibration_required": False,
            "minimum_comment_length": 3,
            "match_window_seconds": 2,
            "selection_markers": ["这条"],
        },
    )

    with pytest.raises(hru.HostReadUpgradeError) as excinfo:
        hru.load_host_read_upgrade_ruleset(tmp_path)

    assert excinfo.value.reason == "host_read_upgrade_rules_invalid"


def test_loader_rejects_a_missing_artifact(tmp_path: Path) -> None:
    with pytest.raises(hru.HostReadUpgradeError) as excinfo:
        hru.load_host_read_upgrade_ruleset(tmp_path)

    assert excinfo.value.reason == "host_read_upgrade_rules_invalid"


def test_loader_rejects_a_negative_window(tmp_path: Path) -> None:
    _write_rules(
        tmp_path / "config" / "text-analysis" / "host-read-upgrade.json",
        {
            "schema_version": 1,
            "version": "phase-08-host-read-upgrade-v1",
            "calibration_required": True,
            "minimum_comment_length": 3,
            "match_window_seconds": -1,
            "selection_markers": ["这条"],
        },
    )

    with pytest.raises(hru.HostReadUpgradeError) as excinfo:
        hru.load_host_read_upgrade_ruleset(tmp_path)

    assert excinfo.value.reason == "host_read_upgrade_rules_invalid"
