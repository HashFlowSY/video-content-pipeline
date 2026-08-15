"""Offline CLI contract for Phase 8 ticket 02 (visual-text command boundary).

Ticket 02 adds ``vcp visual-text <plan-id>`` with a mandatory explicit scope
(``--all``, ``--part``, ``--range`` in Part-relative seconds). An unscoped
invocation is an error that creates no workspace; a scoped invocation revalidates
the confirmed RunPlan and SourceArtifact hashes, the retained inspection evidence,
the versioned rules, and every named Part and range against retained Part
identities and actual video coverage. Each attempt owns a fresh immutable
workspace and an authoritative ``visual-report.json`` recording capability state,
rule versions, scope, status, limitations, and diagnostics. No model runs, no
frame of user media is extracted, and no network is accessed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_content_pipeline import cli, evidence
from video_content_pipeline.coverage import StreamCoverage
from video_content_pipeline.inspection import PlanInspectionEvidence
from video_content_pipeline.planning import (
    PlanState,
    RunPlan,
    create_plan_report,
    inspection_evidence_fingerprints,
    persist_plan_report,
)
from video_content_pipeline.probe import ProbeDocument
from video_content_pipeline.source import SourceArtifact, sha256_file
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval

_GUARANTEES = {
    "frame_extraction": "not_attempted",
    "model_acquisition": "not_attempted",
    "model_execution": "not_attempted",
    "network_access": "not_attempted",
    "outputs_publication": "not_attempted",
}


def _video_evidence(source_id: str, *, duration: int) -> PlanInspectionEvidence:
    return PlanInspectionEvidence(
        source_id=source_id,
        structural_document=ProbeDocument(
            json.dumps({"streams": [{"index": 0, "codec_type": "video", "time_base": "1/1000"}]})
        ),
        coverage_document=ProbeDocument('{"packets": []}'),
        coverage_by_stream=(
            (
                0,
                StreamCoverage(
                    coverage=HalfOpenInterval(ExactTime(0), ExactTime(duration)),
                    gaps=(),
                    diagnostics=(),
                ),
            ),
        ),
        subtitle_tracks=(),
    )


def _confirmed_plan(
    project_root: Path,
    *,
    plan_id: str = "confirmed-phase-8-visual-plan",
    parts: int = 1,
    durations: tuple[int, ...] = (30,),
) -> RunPlan:
    artifacts: list[SourceArtifact] = []
    evidence_records: list[PlanInspectionEvidence] = []
    for ordinal in range(parts):
        media_path = project_root / "input" / f"source-{ordinal}" / "synthetic-media"
        media_path.parent.mkdir(parents=True)
        media_path.write_bytes(f"phase-8-visual-fixture-{ordinal}".encode())
        digest, byte_count = sha256_file(media_path)
        artifact = SourceArtifact(
            digest, digest, byte_count, media_path, origin_kind="synthetic_fixture"
        )
        artifacts.append(artifact)
        evidence_records.append(
            _video_evidence(artifact.source_id, duration=durations[ordinal])
        )
    plan_report = create_plan_report(
        state=PlanState.READY_FOR_CONFIRMATION,
        source_artifacts=tuple(artifacts),
        tools=(),
        planned_increment_bytes=0,
        configuration_fingerprint="phase-03-fixture",
        inspection_evidence=tuple(evidence_records),
    )
    persist_plan_report(plan_report, project_root / "plans")
    plan = RunPlan(
        plan_id=plan_id,
        report_id=plan_report.report_id,
        source_artifacts=tuple(artifacts),
        tools=(),
        disk_headroom=plan_report.disk_headroom,
        configuration_fingerprint=plan_report.configuration_fingerprint,
        inspection_evidence_fingerprints=inspection_evidence_fingerprints(tuple(evidence_records)),
    )
    plan_path = project_root / "plans" / plan.plan_id / "run-plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(
        json.dumps(plan.as_json(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return plan


def _install_rules(project_root: Path) -> None:
    """Copy the shipped versioned visual-text rules into the fixture project root."""

    repo_rules = Path(__file__).resolve().parents[2] / "config" / "visual-text" / "rules.json"
    destination = project_root / "config" / "visual-text" / "rules.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(repo_rules.read_text(encoding="utf-8"), encoding="utf-8")


def _configure_cli(project_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "assert_runtime_policy", lambda: None)
    monkeypatch.setattr(cli, "assert_project_venv", lambda: object())
    monkeypatch.setattr(cli, "_project_root", lambda: project_root)


def _run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    project_root: Path,
    argv: list[str],
) -> tuple[int, dict[str, object]]:
    _configure_cli(project_root, monkeypatch)
    code = cli.main(argv)
    return code, json.loads(capsys.readouterr().out)


# --- Unscoped invocation: error, no workspace ------------------------------


def test_visual_text_without_scope_errors_and_creates_no_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules(tmp_path)

    code, response = _run(monkeypatch, capsys, tmp_path, ["visual-text", plan.plan_id, "--json"])

    assert code == 2
    assert response["status"] == "error"
    assert response["reason"] == "visual_text_scope_missing"
    # No attempt was minted, so the workspace root never came into existence.
    assert not (tmp_path / "work" / "visual-text-reports").exists()


# --- Each scope form revalidates and reaches the terminal outcome ----------


def test_visual_text_all_scope_reaches_model_acquisition_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules(tmp_path)
    part_id = plan.source_artifacts[0].source_id

    code, response = _run(monkeypatch, capsys, tmp_path, ["visual-text", plan.plan_id, "--all"])

    assert code == 0
    assert response["status"] == "model_acquisition_required"
    report = response["report"]
    assert report["scope"]["requested"] == "all"
    assert [part["part_id"] for part in report["scope"]["parts"]] == [part_id]
    assert report["capability"]["result"] == "model_acquisition_required"
    assert report["rule_versions"]["detection"] == "phase-08-page-change-detection-v1"
    assert report["guarantees"] == _GUARANTEES
    # The report is retained in an immutable workspace and nothing is published.
    report_path = Path(report["report_path"])
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    assert not (tmp_path / "outputs").exists()


def test_visual_text_part_scope_records_full_relative_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules(tmp_path)
    part_id = plan.source_artifacts[0].source_id

    code, response = _run(
        monkeypatch, capsys, tmp_path, ["visual-text", plan.plan_id, "--part", part_id]
    )

    assert code == 0
    assert response["status"] == "model_acquisition_required"
    (scope_part,) = response["report"]["scope"]["parts"]
    assert scope_part["part_id"] == part_id
    assert scope_part["coverage_duration"] == {"numerator": 30, "denominator": 1}
    assert scope_part["intervals"] == [
        {"start": {"numerator": 0, "denominator": 1}, "end": {"numerator": 30, "denominator": 1}}
    ]


def test_visual_text_range_scope_is_part_relative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules(tmp_path)
    part_id = plan.source_artifacts[0].source_id

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["visual-text", plan.plan_id, "--range", f"{part_id}:5-12.5"],
    )

    assert code == 0
    (scope_part,) = response["report"]["scope"]["parts"]
    assert scope_part["intervals"] == [
        {"start": {"numerator": 5, "denominator": 1}, "end": {"numerator": 25, "denominator": 2}}
    ]


def test_visual_text_all_scope_covers_multiple_parts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path, parts=2, durations=(30, 20))
    _install_rules(tmp_path)
    part_ids = sorted(artifact.source_id for artifact in plan.source_artifacts)

    code, response = _run(monkeypatch, capsys, tmp_path, ["visual-text", plan.plan_id, "--all"])

    assert code == 0
    assert [part["part_id"] for part in response["report"]["scope"]["parts"]] == part_ids


# --- Scope revalidation drift ----------------------------------------------


def test_visual_text_rejects_a_range_past_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules(tmp_path)
    part_id = plan.source_artifacts[0].source_id

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["visual-text", plan.plan_id, "--range", f"{part_id}:0-99"],
    )

    assert code == 0
    assert response["status"] == "failed"
    assert response["report"]["diagnostics"][0]["reason"] == "visual_text_range_out_of_coverage"
    assert response["report"]["scope"]["parts"] == []


def test_visual_text_rejects_an_unknown_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules(tmp_path)

    code, response = _run(
        monkeypatch, capsys, tmp_path, ["visual-text", plan.plan_id, "--part", "not-a-part"]
    )

    assert code == 0
    assert response["status"] == "failed"
    assert response["report"]["diagnostics"][0]["reason"] == "visual_text_part_unknown"


def test_visual_text_blocks_on_inspection_evidence_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules(tmp_path)
    part_id = plan.source_artifacts[0].source_id
    # Mutate the confirmed PlanReport's retained inspection evidence so its
    # fingerprints no longer match the RunPlan the attempt revalidates.
    report_path = (
        tmp_path / "plans" / "reports" / plan.report_id / "plan-report.json"
    )
    document = json.loads(report_path.read_text(encoding="utf-8"))
    document["inspection_evidence"][0]["stream_coverage"] = []
    report_path.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    code, response = _run(
        monkeypatch, capsys, tmp_path, ["visual-text", plan.plan_id, "--part", part_id]
    )

    assert code == 0
    assert response["status"] == "failed"
    assert response["report"]["diagnostics"][0]["reason"] == "inspection_evidence_changed"


def test_visual_text_blocks_on_rules_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules(tmp_path)
    part_id = plan.source_artifacts[0].source_id
    (tmp_path / "config" / "visual-text" / "rules.json").write_text(
        '{"schema_version": 1, "detection": {}}', encoding="utf-8"
    )

    code, response = _run(
        monkeypatch, capsys, tmp_path, ["visual-text", plan.plan_id, "--part", part_id]
    )

    assert code == 0
    assert response["status"] == "failed"
    assert response["report"]["diagnostics"][0]["reason"] == "visual_text_rules_invalid"


def test_visual_text_fails_on_an_unknown_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_rules(tmp_path)

    code, response = _run(
        monkeypatch, capsys, tmp_path, ["visual-text", "no-such-plan", "--all"]
    )

    assert code == 0
    assert response["status"] == "failed"
    assert response["report"]["diagnostics"][0]["reason"] == "run_plan_not_confirmed"


# --- Immutability + no source-media read -----------------------------------


def test_visual_text_attempts_never_overwrite_each_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules(tmp_path)
    part_id = plan.source_artifacts[0].source_id

    _, first = _run(
        monkeypatch, capsys, tmp_path, ["visual-text", plan.plan_id, "--part", part_id]
    )
    _, second = _run(
        monkeypatch, capsys, tmp_path, ["visual-text", plan.plan_id, "--part", part_id]
    )

    assert first["report"]["report_id"] != second["report"]["report_id"]
    assert first["report"]["report_path"] != second["report"]["report_path"]
    # Both attempts remain retained side by side.
    assert Path(first["report"]["report_path"]).exists()
    assert Path(second["report"]["report_path"]).exists()


def test_visual_text_reads_no_source_media(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules(tmp_path)
    part_id = plan.source_artifacts[0].source_id
    hashed: list[Path] = []
    real_sha256_file = evidence.sha256_file

    def _spy(path: Path) -> tuple[str, int]:
        hashed.append(Path(path))
        return real_sha256_file(path)

    monkeypatch.setattr(evidence, "sha256_file", _spy)

    code, _response = _run(
        monkeypatch, capsys, tmp_path, ["visual-text", plan.plan_id, "--part", part_id]
    )

    assert code == 0
    assert plan.source_artifacts[0].media_path not in hashed
