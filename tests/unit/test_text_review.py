"""Offline contract for Phase 6 append-only synthetic human-review records.

Ticket 07 proves only the *shape* of an append-only human text-analysis review:
an independent record may label its reviewed scope, is immutable and
append-only, and can never rewrite model output, evidence, or emit a
``human_verified`` result. This phase produces no real human verification.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from video_content_pipeline import text_review


def _retained_report(project_root: Path) -> str:
    report_id = uuid.uuid4().hex
    workspace = project_root / "work" / "text-analysis-reports" / report_id
    workspace.mkdir(parents=True)
    (workspace / "text-analysis-report.json").write_text(
        json.dumps({"report_id": report_id, "status": "partial"}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report_id


def test_append_human_review_records_are_sequential_and_append_only(tmp_path: Path) -> None:
    report_id = _retained_report(tmp_path)

    first = text_review.append_human_review_record(
        tmp_path,
        report_id,
        reviewer="synthetic-reviewer",
        scope={"kind": "segment", "id": "seg-1"},
        disposition="reviewed_ok",
        note="Structural review only.",
    )
    second = text_review.append_human_review_record(
        tmp_path,
        report_id,
        reviewer="synthetic-reviewer",
        scope={"kind": "report"},
        disposition="flagged",
        note="Needs a follow-up look.",
    )

    assert first["index"] == 0
    assert second["index"] == 1
    assert first["report_id"] == report_id
    assert first["boundary"] == "structural_review_only"
    assert first["human_verified"] is False
    assert first["disposition"] == "reviewed_ok"
    assert first["scope"] == {"kind": "segment", "id": "seg-1"}

    records_dir = tmp_path / "work" / "text-analysis-reports" / report_id / "human-review"
    assert {path.name for path in records_dir.glob("*.json")} == {"0000.json", "0001.json"}

    loaded = text_review.load_human_review_records(tmp_path, report_id)
    assert [record["index"] for record in loaded] == [0, 1]
    assert loaded[0] == first
    assert loaded[1] == second


def test_append_human_review_never_rewrites_a_prior_record(tmp_path: Path) -> None:
    report_id = _retained_report(tmp_path)
    first = text_review.append_human_review_record(
        tmp_path,
        report_id,
        reviewer="synthetic-reviewer",
        scope={"kind": "report"},
        disposition="reviewed_ok",
        note="First.",
    )
    first_path = (
        tmp_path / "work" / "text-analysis-reports" / report_id / "human-review" / "0000.json"
    )
    first_bytes = first_path.read_bytes()

    text_review.append_human_review_record(
        tmp_path,
        report_id,
        reviewer="synthetic-reviewer",
        scope={"kind": "report"},
        disposition="flagged",
        note="Second.",
    )

    assert first_path.read_bytes() == first_bytes
    assert json.loads(first_path.read_text(encoding="utf-8")) == first


def test_append_human_review_rejects_a_human_verified_disposition(tmp_path: Path) -> None:
    report_id = _retained_report(tmp_path)

    with pytest.raises(text_review.HumanReviewError) as excinfo:
        text_review.append_human_review_record(
            tmp_path,
            report_id,
            reviewer="synthetic-reviewer",
            scope={"kind": "report"},
            disposition="human_verified",
            note="Attempted certification.",
        )

    assert excinfo.value.reason == "human_review_disposition_invalid"
    records_dir = tmp_path / "work" / "text-analysis-reports" / report_id / "human-review"
    assert not records_dir.exists() or not list(records_dir.glob("*.json"))


def test_append_human_review_rejects_an_unknown_disposition(tmp_path: Path) -> None:
    report_id = _retained_report(tmp_path)

    with pytest.raises(text_review.HumanReviewError) as excinfo:
        text_review.append_human_review_record(
            tmp_path,
            report_id,
            reviewer="synthetic-reviewer",
            scope={"kind": "report"},
            disposition="approved",
            note="",
        )

    assert excinfo.value.reason == "human_review_disposition_invalid"


def test_append_human_review_rejects_an_invalid_scope(tmp_path: Path) -> None:
    report_id = _retained_report(tmp_path)

    with pytest.raises(text_review.HumanReviewError) as excinfo:
        text_review.append_human_review_record(
            tmp_path,
            report_id,
            reviewer="synthetic-reviewer",
            scope={"note": "no kind here"},
            disposition="reviewed_ok",
            note="",
        )

    assert excinfo.value.reason == "human_review_scope_invalid"


def test_append_human_review_rejects_a_missing_report(tmp_path: Path) -> None:
    (tmp_path / "work").mkdir()

    with pytest.raises(text_review.HumanReviewError) as excinfo:
        text_review.append_human_review_record(
            tmp_path,
            uuid.uuid4().hex,
            reviewer="synthetic-reviewer",
            scope={"kind": "report"},
            disposition="reviewed_ok",
            note="",
        )

    assert excinfo.value.reason == "text_analysis_report_invalid"


def test_append_human_review_rejects_an_invalid_report_id(tmp_path: Path) -> None:
    with pytest.raises(text_review.HumanReviewError) as excinfo:
        text_review.append_human_review_record(
            tmp_path,
            "not-a-uuid",
            reviewer="synthetic-reviewer",
            scope={"kind": "report"},
            disposition="reviewed_ok",
            note="",
        )

    assert excinfo.value.reason == "text_analysis_report_invalid"
