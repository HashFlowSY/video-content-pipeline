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


def _install_frame_metrics(
    project_root: Path,
    part_id: str,
    frames: list[dict[str, object]],
    *,
    detection_version: str = "phase-08-page-change-detection-v1",
    sampling_version: str = "phase-08-frame-sampling-v1",
) -> None:
    """Write a hash-pinned synthetic frame-metric fixture for one Part."""

    path = project_root / "input" / "visual-text-frame-metrics" / f"{part_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "part_id": part_id,
                "detection_rule_version": detection_version,
                "sampling_rule_version": sampling_version,
                "frames": frames,
            }
        ),
        encoding="utf-8",
    )


def _metric(
    pts: int,
    fingerprint: str,
    *,
    stability: int = 100,
    edge_density: int = 80,
    region_diff: int = 0,
) -> dict[str, object]:
    return {
        "pts": {"numerator": pts, "denominator": 1},
        "content_fingerprint": fingerprint,
        "stability": stability,
        "edge_density": edge_density,
        "region_diff": region_diff,
    }


# A page that appears, changes, and reappears -- with a below-text-value opening.
_PAGE_FRAMES = [
    _metric(0, "aaa"),
    _metric(1, "aaa"),
    _metric(2, "bbb", region_diff=90),  # transition frame into bbb
    _metric(3, "bbb"),
    _metric(4, "aaa", region_diff=90),  # transition frame back to aaa
    _metric(5, "aaa"),
]


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


# --- Ticket 03: the deterministic Part-local page index --------------------


def test_page_index_records_part_local_pages_and_appearances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules(tmp_path)
    part_id = plan.source_artifacts[0].source_id
    _install_frame_metrics(tmp_path, part_id, _PAGE_FRAMES)

    code, response = _run(
        monkeypatch, capsys, tmp_path, ["visual-text", plan.plan_id, "--part", part_id]
    )

    assert code == 0
    (index,) = response["report"]["page_index"]["parts"]
    assert index["part_id"] == part_id
    assert index["detection_version"] == "phase-08-page-change-detection-v1"
    # Part-local pages: aaa reappears under one id, bbb is a second page.
    pages = {page["visual_page_id"]: page for page in index["pages"]}
    assert set(pages) == {"page-01", "page-02"}
    assert pages["page-01"]["content_fingerprint"] == "aaa"
    # aaa appears first at t=0 and reappears at t=5 -- both retained with exact times.
    assert [(a["start"], a["end"]) for a in pages["page-01"]["appearances"]] == [
        (
            {"numerator": 0, "denominator": 1},
            {"numerator": 1, "denominator": 1},
        ),
        (
            {"numerator": 5, "denominator": 1},
            {"numerator": 5, "denominator": 1},
        ),
    ]
    assert pages["page-01"]["selected_frame_pts"] == {"numerator": 0, "denominator": 1}


def test_page_index_retains_every_frame_with_a_reason_unpublished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules(tmp_path)
    part_id = plan.source_artifacts[0].source_id
    _install_frame_metrics(tmp_path, part_id, _PAGE_FRAMES)

    code, response = _run(
        monkeypatch, capsys, tmp_path, ["visual-text", plan.plan_id, "--part", part_id]
    )

    assert code == 0
    (index,) = response["report"]["page_index"]["parts"]
    frames = index["retained_frames"]
    # Nothing is discarded pipeline-side: every input frame is inventoried once.
    assert len(frames) == len(_PAGE_FRAMES)
    assert all(frame["published"] is False for frame in frames)
    reasons = {frame["pts"]["numerator"]: frame["selection_reason"] for frame in frames}
    assert reasons[0] == "selected_page_representative"
    assert reasons[1] == "unselected_duplicate_of_selected"
    assert reasons[2] == "unselected_transition_frame"
    # The full inventory is a workspace-internal artifact, not a formal output.
    artifact = index["inventory_artifact"]
    assert artifact["published"] is False
    inventory_path = Path(artifact["path"])
    assert inventory_path.exists()
    assert not (tmp_path / "outputs").exists()


def test_page_index_is_deterministic_across_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules(tmp_path)
    part_id = plan.source_artifacts[0].source_id
    _install_frame_metrics(tmp_path, part_id, _PAGE_FRAMES)

    _, first = _run(
        monkeypatch, capsys, tmp_path, ["visual-text", plan.plan_id, "--part", part_id]
    )
    _, second = _run(
        monkeypatch, capsys, tmp_path, ["visual-text", plan.plan_id, "--part", part_id]
    )

    (first_index,) = first["report"]["page_index"]["parts"]
    (second_index,) = second["report"]["page_index"]["parts"]
    # Same input and rule versions select the same frames, pages, and appearances;
    # the content-addressed inventory hash matches even though the workspace differs.
    assert first_index["pages"] == second_index["pages"]
    assert first_index["retained_frames"] == second_index["retained_frames"]
    assert (
        first_index["inventory_artifact"]["sha256"]
        == second_index["inventory_artifact"]["sha256"]
    )


def test_range_scope_indexes_only_frames_inside_the_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules(tmp_path)
    part_id = plan.source_artifacts[0].source_id
    _install_frame_metrics(tmp_path, part_id, _PAGE_FRAMES)

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["visual-text", plan.plan_id, "--range", f"{part_id}:3-6"],
    )

    assert code == 0
    (index,) = response["report"]["page_index"]["parts"]
    # Only frames at t=3,4,5 fall inside [3,6); the earlier aaa/bbb frames are excluded.
    assert [frame["pts"]["numerator"] for frame in index["retained_frames"]] == [3, 4, 5]


def test_absent_frame_fixture_is_a_limitation_not_a_silent_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules(tmp_path)
    part_id = plan.source_artifacts[0].source_id

    code, response = _run(
        monkeypatch, capsys, tmp_path, ["visual-text", plan.plan_id, "--part", part_id]
    )

    assert code == 0
    assert response["report"]["page_index"]["parts"] == []
    reasons = [limitation["reason"] for limitation in response["report"]["limitations"]]
    assert "visual_text_frame_metrics_absent" in reasons


def test_stale_frame_fixture_rule_version_blocks_the_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules(tmp_path)
    part_id = plan.source_artifacts[0].source_id
    _install_frame_metrics(
        tmp_path, part_id, _PAGE_FRAMES, detection_version="phase-08-page-change-detection-v0"
    )

    code, response = _run(
        monkeypatch, capsys, tmp_path, ["visual-text", plan.plan_id, "--part", part_id]
    )

    assert code == 0
    assert response["status"] == "failed"
    assert response["report"]["diagnostics"][0]["reason"] == "visual_text_frame_metrics_stale"
    # A blocked attempt leaves no partial inventory behind.
    assert response["report"]["page_index"]["parts"] == []
