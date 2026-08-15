"""Unit coverage for Phase 6 ticket 02 revalidation and resume guards."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_content_pipeline import text_analysis


def _write_rules(project_root: Path, payload: object) -> Path:
    rules_path = project_root / "config" / "text-analysis-rules.json"
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return rules_path


def test_text_analysis_rules_fingerprint_hashes_valid_rules(tmp_path: Path) -> None:
    rules_path = _write_rules(tmp_path, {"schema_version": 1, "id": "rules-v1"})

    fingerprint = text_analysis.text_analysis_rules_fingerprint(tmp_path)

    from hashlib import sha256

    assert fingerprint == sha256(rules_path.read_bytes()).hexdigest()


def test_text_analysis_rules_fingerprint_rejects_an_invalid_schema(tmp_path: Path) -> None:
    _write_rules(tmp_path, {"schema_version": 2, "id": "rules-v2"})

    with pytest.raises(text_analysis.TextAnalysisError) as excinfo:
        text_analysis.text_analysis_rules_fingerprint(tmp_path)

    assert excinfo.value.reason == "text_analysis_rules_invalid"


def test_text_analysis_rules_fingerprint_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(text_analysis.TextAnalysisError) as excinfo:
        text_analysis.text_analysis_rules_fingerprint(tmp_path)

    assert excinfo.value.reason == "text_analysis_rules_invalid"


def test_resume_text_analysis_requires_an_explicit_decision(tmp_path: Path) -> None:
    report_id = "4" * 32
    report_path = (
        tmp_path / "work" / "text-analysis-reports" / report_id / "text-analysis-report.json"
    )
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        json.dumps(
            {
                "report_id": report_id,
                "plan_id": "some-plan",
                "subtitle_report_id": "1" * 32,
                "status": "controlled_adapter_unavailable",
                "diagnostics": [],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(text_analysis.TextAnalysisError) as excinfo:
        text_analysis.resume_text_analysis(report_id, None, tmp_path)

    assert excinfo.value.reason == "text_analysis_resume_invalid"
    assert "decision" in str(excinfo.value).lower()


def test_resume_text_analysis_rejects_a_non_uuid_report_id(tmp_path: Path) -> None:
    with pytest.raises(text_analysis.TextAnalysisError) as excinfo:
        text_analysis.resume_text_analysis("not-a-uuid", "model_release_verified", tmp_path)

    assert excinfo.value.reason == "text_analysis_report_invalid"
