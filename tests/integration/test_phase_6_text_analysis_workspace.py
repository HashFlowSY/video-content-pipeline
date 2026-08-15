"""Offline contract for the first Phase 6 text-analysis slice.

Ticket 01 establishes the immutable text-analysis workspace, its domain records,
the report identity, and the ``controlled_adapter_unavailable`` result. The
public CLI and full input revalidation belong to later tickets, so these tests
exercise ``analyze_text`` directly.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from video_content_pipeline import text_analysis
from video_content_pipeline.planning import (
    RunPlan,
    inspection_evidence_fingerprints,
)
from video_content_pipeline.source import (
    SourceArtifact,
    calculate_disk_headroom,
    sha256_file,
)
from video_content_pipeline.subtitle_pipeline import (
    CandidateReportState,
    CandidateState,
    SubtitleCandidate,
    SubtitleCandidateReport,
    subtitle_rules_fingerprint,
)


def _retained_plan(project_root: Path) -> RunPlan:
    """Write a minimal retained RunPlan with one snapshotted SourceArtifact."""

    media_path = project_root / "input" / "source" / "synthetic-media"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"phase-6-text-analysis-fixture")
    digest, byte_count = sha256_file(media_path)
    artifact = SourceArtifact(
        digest, digest, byte_count, media_path, origin_kind="synthetic_fixture"
    )
    plan = RunPlan(
        plan_id="confirmed-phase-6-fixture-plan",
        report_id="0" * 32,
        source_artifacts=(artifact,),
        tools=(),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint="phase-03-fixture",
        inspection_evidence_fingerprints=inspection_evidence_fingerprints(()),
    )
    plan_path = project_root / "plans" / plan.plan_id / "run-plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(
        json.dumps(plan.as_json(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return plan


def _retained_subtitle_report(project_root: Path, plan: RunPlan) -> SubtitleCandidateReport:
    """Write a retained valid subtitle candidate report bound to ``plan``."""

    report_id = "1" * 32
    report_path = (
        project_root
        / "work"
        / plan.source_artifacts[0].source_id
        / report_id
        / "candidate-report.json"
    )
    rules_path = project_root / "config" / "subtitle-rules.json"
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text(
        '{"schema_version": 1, "id": "phase-04-fixture-rules"}\n', encoding="utf-8"
    )
    source_artifact_path = report_path.parent / "source.vtt"
    readable_artifact_path = report_path.parent / "readable.vtt"
    source_candidate_path = report_path.parent / "source-candidate.json"
    source_artifact_path.parent.mkdir(parents=True)
    source_artifact_path.write_text("WEBVTT\n\n", encoding="utf-8")
    readable_artifact_path.write_text("WEBVTT\n\n", encoding="utf-8")
    source_candidate_path.write_text(
        json.dumps({"schema_version": 1, "cues": []}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report = SubtitleCandidateReport(
        report_id=report_id,
        plan_id=plan.plan_id,
        state=CandidateReportState.COMPLETED,
        subtitle_rules_fingerprint=subtitle_rules_fingerprint(project_root),
        candidates=(
            SubtitleCandidate(
                source_id=plan.source_artifacts[0].source_id,
                stream_index=1,
                state=CandidateState.VALID,
                source_candidate_path=source_candidate_path.as_posix(),
                source_candidate_sha256=sha256(source_candidate_path.read_bytes()).hexdigest(),
                source_vtt_path=source_artifact_path.as_posix(),
                readable_vtt_path=readable_artifact_path.as_posix(),
                raw_pts_cue_intervals=(),
            ),
        ),
        diagnostics=(),
        report_path=report_path,
    )
    report_path.write_text(
        json.dumps(report.as_json(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return report


def test_analyze_text_retains_a_controlled_adapter_unavailable_report_without_side_effects(
    tmp_path: Path,
) -> None:
    plan = _retained_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    plan_path = tmp_path / "plans" / plan.plan_id / "run-plan.json"
    plan_before = plan_path.read_bytes()
    subtitles_before = subtitle_report.report_path.read_bytes()
    subtitle_artifacts_before = {
        path: path.read_bytes()
        for path in (
            Path(subtitle_report.candidates[0].source_vtt_path or ""),
            Path(subtitle_report.candidates[0].readable_vtt_path or ""),
            Path(subtitle_report.candidates[0].source_candidate_path or ""),
        )
    }

    response = text_analysis.analyze_text(plan.plan_id, subtitle_report.report_id, tmp_path)

    assert response["status"] == "controlled_adapter_unavailable"
    report = response["report"]
    assert report["status"] == "controlled_adapter_unavailable"
    assert report["plan_id"] == plan.plan_id
    assert report["subtitle_report_id"] == subtitle_report.report_id
    assert report["controlled_text_adapter"]["state"] == "controlled_adapter_unavailable"
    assert report["controlled_text_adapter"]["model"] is None
    assert report["segments"] == []
    assert report["chapters"] == []
    assert report["collection_summary"] is None
    assert report["restricted_raw_output"] == []
    assert report["guarantees"] == {
        "asr_or_ocr": "not_attempted",
        "external_knowledge": "not_used",
        "model_acquisition": "not_attempted",
        "model_execution": "not_attempted",
        "network_access": "not_attempted",
        "outputs_publication": "not_attempted",
        "run_plan_mutation": "not_attempted",
        "subtitle_artifact_mutation": "not_attempted",
        "translation": "not_attempted",
        "user_media_access": "not_attempted",
    }
    assert report["diagnostics"] == [
        {
            "reason": "controlled_adapter_unavailable",
            "message": (
                "No Controlled offline text adapter is available; no semantic "
                "content was generated."
            ),
        }
    ]

    report_path = Path(report["report_path"])
    assert report_path.parent == Path(report["workspace_path"])
    assert report_path.name == "text-analysis-report.json"
    assert json.loads(report_path.read_text(encoding="utf-8")) == report

    assert plan_path.read_bytes() == plan_before
    assert subtitle_report.report_path.read_bytes() == subtitles_before
    assert {path: path.read_bytes() for path in subtitle_artifacts_before} == (
        subtitle_artifacts_before
    )
    assert not (tmp_path / "outputs").exists()


def test_analyze_text_records_input_evidence_hashes(tmp_path: Path) -> None:
    plan = _retained_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    plan_path = tmp_path / "plans" / plan.plan_id / "run-plan.json"

    report = text_analysis.analyze_text(plan.plan_id, subtitle_report.report_id, tmp_path)["report"]

    input_evidence = report["input_evidence"]
    assert input_evidence["run_plan"]["sha256"] == sha256(plan_path.read_bytes()).hexdigest()
    assert input_evidence["run_plan"]["byte_count"] == plan_path.stat().st_size
    assert (
        input_evidence["subtitle_candidate_report"]["sha256"]
        == sha256(subtitle_report.report_path.read_bytes()).hexdigest()
    )
    assert "controlled_text_adapter" not in input_evidence


def test_analyze_text_report_id_is_a_fresh_uuid_hex_per_attempt(tmp_path: Path) -> None:
    plan = _retained_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)

    first = text_analysis.analyze_text(plan.plan_id, subtitle_report.report_id, tmp_path)
    second = text_analysis.analyze_text(plan.plan_id, subtitle_report.report_id, tmp_path)

    first_id = first["report"]["report_id"]
    second_id = second["report"]["report_id"]
    assert first_id != second_id
    assert len(first_id) == 32 and int(first_id, 16) >= 0
    assert Path(first["report"]["workspace_path"]).name == first_id
    assert Path(first["report"]["workspace_path"]) != Path(second["report"]["workspace_path"])


def test_analyze_text_rejects_a_subtitle_report_for_another_plan(tmp_path: Path) -> None:
    plan = _retained_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    invalid_report = subtitle_report.as_json()
    invalid_report["plan_id"] = "another-confirmed-plan"
    subtitle_report.report_path.write_text(
        json.dumps(invalid_report, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )

    report = text_analysis.analyze_text(plan.plan_id, subtitle_report.report_id, tmp_path)["report"]

    assert report["status"] == "failed"
    assert report["controlled_text_adapter"]["state"] == "controlled_adapter_unavailable"
    assert report["input_evidence"]["run_plan"] is None
    assert report["diagnostics"] == [
        {
            "reason": "subtitle_report_mismatch",
            "message": "Subtitle candidate report does not belong to this RunPlan.",
        }
    ]


def test_analyze_text_rejects_a_run_plan_identity_mismatch(tmp_path: Path) -> None:
    plan = _retained_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    plan_path = tmp_path / "plans" / plan.plan_id / "run-plan.json"
    drifted = json.loads(plan_path.read_text(encoding="utf-8"))
    drifted["plan_id"] = "a-different-plan-id"
    plan_path.write_text(json.dumps(drifted, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    report = text_analysis.analyze_text(plan.plan_id, subtitle_report.report_id, tmp_path)["report"]

    assert report["status"] == "failed"
    assert report["diagnostics"][0]["reason"] == "run_plan_not_confirmed"


def test_analyze_text_fails_when_the_run_plan_is_missing(tmp_path: Path) -> None:
    (tmp_path / "plans").mkdir()

    report = text_analysis.analyze_text("no-such-plan", "1" * 32, tmp_path)["report"]

    assert report["status"] == "failed"
    assert report["input_evidence"]["run_plan"] is None
    assert report["diagnostics"] != []


def test_analyze_text_does_not_read_source_media(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _retained_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    original_sha256_file = text_analysis.sha256_file
    hashed_paths: list[Path] = []

    def record_hash(path: Path) -> tuple[str, int]:
        hashed_paths.append(path)
        return original_sha256_file(path)

    monkeypatch.setattr(text_analysis, "sha256_file", record_hash)

    text_analysis.analyze_text(plan.plan_id, subtitle_report.report_id, tmp_path)

    assert plan.source_artifacts[0].media_path not in hashed_paths


def test_analyze_text_workspace_is_write_once_immutable(tmp_path: Path) -> None:
    plan = _retained_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)

    report = text_analysis.analyze_text(plan.plan_id, subtitle_report.report_id, tmp_path)["report"]
    report_path = Path(report["report_path"])

    with pytest.raises(text_analysis.TextAnalysisError) as excinfo:
        text_analysis._write_json_once(report_path, {"different": "content"})
    assert excinfo.value.reason == "text_analysis_report_conflict"
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
