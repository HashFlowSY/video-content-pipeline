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
from hashlib import sha256
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
from video_content_pipeline.visual_text_contracts import (
    ocr_input_manifest_document,
    ocr_input_manifest_sha256,
)

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


_CONTRACT_FILES = (
    "rules.json",
    "ocr-projection-schema.json",
    "controlled-ocr-adapter.json",
)


def _install_rules(project_root: Path) -> None:
    """Copy the shipped versioned visual-text rules and OCR contracts into the fixture.

    The OCR projection schema and Controlled offline OCR adapter identities ship
    alongside the rules so an affirmative OCR decision can revalidate them; the
    shipped adapter carries no bound fixture, so an affirmative decision reaches
    ``model_acquisition_required`` until a test installs a fixture-bearing adapter.
    """

    source = Path(__file__).resolve().parents[2] / "config" / "visual-text"
    destination = project_root / "config" / "visual-text"
    destination.mkdir(parents=True, exist_ok=True)
    for name in _CONTRACT_FILES:
        (destination / name).write_text(
            (source / name).read_text(encoding="utf-8"), encoding="utf-8"
        )


def _install_rules_with_envelope(project_root: Path, *, max_selected_frames: int) -> None:
    """Install the shipped rules with a lowered OCR selected-frame envelope ceiling.

    Only the ``ocr_execution.max_selected_frames`` ceiling changes; the detection and
    sampling rule versions stay identical, so hash-pinned frame-metric fixtures remain
    valid and a small fixture can trip the Visual-text resource-envelope pause.
    """

    repo_rules = Path(__file__).resolve().parents[2] / "config" / "visual-text" / "rules.json"
    document = json.loads(repo_rules.read_text(encoding="utf-8"))
    document["ocr_execution"]["max_selected_frames"] = max_selected_frames
    destination = project_root / "config" / "visual-text" / "rules.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(document), encoding="utf-8")


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


# --- Ticket 04: the OCR resource confirmation pause and resume --------------


def _resume(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    project_root: Path,
    report_id: str,
    decision: str | None,
) -> tuple[int, dict[str, object]]:
    argv = ["resume-visual-text", report_id]
    if decision is not None:
        argv += ["--decision", decision]
    return _run(monkeypatch, capsys, project_root, argv)


def test_detection_pauses_at_the_ocr_resource_confirmation_with_estimates(
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
    # After detection the attempt stops at the OCR resource confirmation pause.
    assert response["status"] == "awaiting_ocr_resource_confirmation"
    report = response["report"]
    assert report["required_decision"] == {
        "reason": "ocr_resource_confirmation",
        "decision": "ocr_resource_confirmed",
    }
    # The pause presents selected frame counts and conservative time/memory/disk.
    resource = report["ocr_resource"]
    assert resource["selected_frame_count"] == 2  # aaa and bbb each select a representative
    assert resource["estimates"]["seconds"] > 0
    assert resource["estimates"]["peak_bytes"] > 0
    assert resource["estimates"]["disk_bytes"] > 0
    assert resource["within_envelope"] is True
    # OCR never started: the page index is retained but there are zero visual facts.
    assert report["ocr_evidence"] is None
    assert len(report["page_index"]["parts"]) == 1
    assert report["guarantees"] == _GUARANTEES


def test_empty_page_index_needs_no_pause_and_stays_model_acquisition_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # No frame fixture -> nothing to recognize -> no OCR pause, the terminal outcome.
    plan = _confirmed_plan(tmp_path)
    _install_rules(tmp_path)
    part_id = plan.source_artifacts[0].source_id

    code, response = _run(
        monkeypatch, capsys, tmp_path, ["visual-text", plan.plan_id, "--part", part_id]
    )

    assert code == 0
    assert response["status"] == "model_acquisition_required"
    assert response["report"]["required_decision"] is None
    assert response["report"]["ocr_resource"]["selected_frame_count"] == 0


def test_affirmative_resume_reaches_model_acquisition_required_with_page_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules(tmp_path)
    part_id = plan.source_artifacts[0].source_id
    _install_frame_metrics(tmp_path, part_id, _PAGE_FRAMES)
    _, paused = _run(
        monkeypatch, capsys, tmp_path, ["visual-text", plan.plan_id, "--part", part_id]
    )
    report_id = paused["report"]["report_id"]

    code, response = _resume(monkeypatch, capsys, tmp_path, report_id, "ocr_resource_confirmed")

    assert code == 0
    # An affirmative decision authorizes OCR; with no eligible model it acquisition-gates,
    # keeping the retained page index and zero visual facts.
    assert response["status"] == "model_acquisition_required"
    report = response["report"]
    assert report["ocr_evidence"] is None
    assert len(report["page_index"]["parts"]) == 1
    # The resume is a fresh attempt that records what it continued from.
    assert report["report_id"] != report_id
    assert report["input_evidence"]["resumed_from_report_id"] == report_id
    assert report["input_evidence"]["resumption_decision"] == "ocr_resource_confirmed"
    assert report["input_evidence"]["resumed_from_report"]["sha256"]


def test_declining_resume_retains_the_page_index_as_partial_with_zero_visual_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules(tmp_path)
    part_id = plan.source_artifacts[0].source_id
    _install_frame_metrics(tmp_path, part_id, _PAGE_FRAMES)
    _, paused = _run(
        monkeypatch, capsys, tmp_path, ["visual-text", plan.plan_id, "--part", part_id]
    )
    report_id = paused["report"]["report_id"]

    code, response = _resume(monkeypatch, capsys, tmp_path, report_id, "ocr_declined")

    assert code == 0
    assert response["status"] == "partial"
    report = response["report"]
    # The cheap structural result survives declining OCR: page index and inventory kept.
    (index,) = report["page_index"]["parts"]
    assert {page["visual_page_id"] for page in index["pages"]} == {"page-01", "page-02"}
    assert Path(index["inventory_artifact"]["path"]).exists()
    assert report["ocr_evidence"] is None
    assert report["required_decision"] is None


def test_resume_requires_an_explicit_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules(tmp_path)
    part_id = plan.source_artifacts[0].source_id
    _install_frame_metrics(tmp_path, part_id, _PAGE_FRAMES)
    _, paused = _run(
        monkeypatch, capsys, tmp_path, ["visual-text", plan.plan_id, "--part", part_id]
    )
    report_id = paused["report"]["report_id"]

    code, response = _resume(monkeypatch, capsys, tmp_path, report_id, None)

    assert code == 2
    assert response["reason"] == "visual_text_resume_invalid"


def test_resume_rejects_a_report_that_is_not_a_decision_pause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A no-frame run reaches model_acquisition_required, which is not a resumable pause.
    plan = _confirmed_plan(tmp_path)
    _install_rules(tmp_path)
    part_id = plan.source_artifacts[0].source_id
    _, terminal = _run(
        monkeypatch, capsys, tmp_path, ["visual-text", plan.plan_id, "--part", part_id]
    )
    report_id = terminal["report"]["report_id"]

    code, response = _resume(monkeypatch, capsys, tmp_path, report_id, "ocr_resource_confirmed")

    assert code == 2
    assert response["reason"] == "visual_text_resume_invalid"


def test_resume_rejects_an_unknown_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_rules(tmp_path)

    code, response = _resume(
        monkeypatch, capsys, tmp_path, "0" * 32, "ocr_resource_confirmed"
    )

    assert code == 2
    assert response["reason"] == "visual_text_report_invalid"


def test_a_plan_over_the_envelope_records_the_resource_envelope_pause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules_with_envelope(tmp_path, max_selected_frames=1)
    part_id = plan.source_artifacts[0].source_id
    # Two text-bearing pages -> two selected frames, over the ceiling of one.
    _install_frame_metrics(tmp_path, part_id, _PAGE_FRAMES)

    code, response = _run(
        monkeypatch, capsys, tmp_path, ["visual-text", plan.plan_id, "--part", part_id]
    )

    assert code == 0
    assert response["status"] == "resource_envelope_exceeded"
    report = response["report"]
    assert report["required_decision"] == {
        "reason": "resource_envelope_exceeded",
        "decision": "resource_configuration_changed",
    }
    assert report["ocr_resource"]["within_envelope"] is False
    assert report["diagnostics"][0]["reason"] == "resource_envelope_exceeded"
    # The candidate/resolution/batch is never silently altered: the page index is
    # retained exactly, and nothing is published.
    assert len(report["page_index"]["parts"]) == 1
    assert not (tmp_path / "outputs").exists()


def test_envelope_pause_rejects_the_ocr_confirmation_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules_with_envelope(tmp_path, max_selected_frames=1)
    part_id = plan.source_artifacts[0].source_id
    _install_frame_metrics(tmp_path, part_id, _PAGE_FRAMES)
    _, paused = _run(
        monkeypatch, capsys, tmp_path, ["visual-text", plan.plan_id, "--part", part_id]
    )
    report_id = paused["report"]["report_id"]

    code, response = _resume(monkeypatch, capsys, tmp_path, report_id, "ocr_resource_confirmed")

    assert code == 2
    assert response["reason"] == "visual_text_resume_invalid"


def test_envelope_resume_after_reconfiguration_reaches_the_ocr_pause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules_with_envelope(tmp_path, max_selected_frames=1)
    part_id = plan.source_artifacts[0].source_id
    _install_frame_metrics(tmp_path, part_id, _PAGE_FRAMES)
    _, paused = _run(
        monkeypatch, capsys, tmp_path, ["visual-text", plan.plan_id, "--part", part_id]
    )
    report_id = paused["report"]["report_id"]
    # The user reconfigures the approved envelope; the resume picks up the new ceiling.
    _install_rules_with_envelope(tmp_path, max_selected_frames=10)

    code, response = _resume(
        monkeypatch, capsys, tmp_path, report_id, "resource_configuration_changed"
    )

    assert code == 0
    assert response["status"] == "awaiting_ocr_resource_confirmation"
    assert response["report"]["input_evidence"]["resumption_decision"] == (
        "resource_configuration_changed"
    )


def test_resume_never_overwrites_the_paused_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules(tmp_path)
    part_id = plan.source_artifacts[0].source_id
    _install_frame_metrics(tmp_path, part_id, _PAGE_FRAMES)
    _, paused = _run(
        monkeypatch, capsys, tmp_path, ["visual-text", plan.plan_id, "--part", part_id]
    )
    paused_path = Path(paused["report"]["report_path"])
    paused_before = json.loads(paused_path.read_text(encoding="utf-8"))

    _, resumed = _resume(
        monkeypatch, capsys, tmp_path, paused["report"]["report_id"], "ocr_declined"
    )

    # Both attempts stay retained side by side; the paused report is byte-for-byte intact.
    assert Path(resumed["report"]["report_path"]).exists()
    assert json.loads(paused_path.read_text(encoding="utf-8")) == paused_before
    assert paused["report"]["report_id"] != resumed["report"]["report_id"]


def test_ocr_resource_plan_records_serialized_execution(
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
    # OCR shares the single heavy-task queue and releases before another heavy model loads.
    assert response["report"]["ocr_resource"]["serialized_execution"] is True
    assert response["report"]["guarantees"]["model_execution"] == "not_attempted"


# --- Ticket 05: the Controlled offline OCR adapter, projection, and item gates ---

# The two text-bearing pages of ``_PAGE_FRAMES`` each select a representative: page-01
# (aaa) at t=0 and page-02 (bbb) at t=3. The controlled fixture binds to exactly these.
_SELECTED = (("page-01", 0, "aaa"), ("page-02", 3, "bbb"))


def _selected_manifest_sha(plan_id: str, part_id: str) -> str:
    selections = [
        (part_id, page_id, ExactTime(pts), fingerprint)
        for page_id, pts, fingerprint in _SELECTED
    ]
    return ocr_input_manifest_sha256(ocr_input_manifest_document(plan_id, selections))


def _ocr_item(
    part_id: str,
    page_id: str,
    pts: int,
    *,
    text: str = "Text",
    confidence: float = 0.9,
    language_spans: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    item: dict[str, object] = {
        "part_id": part_id,
        "visual_page_id": page_id,
        "pts": {"numerator": pts, "denominator": 1},
        "text": text,
        "confidence": confidence,
    }
    if language_spans is not None:
        item["language_spans"] = language_spans
    return item


def _install_ocr_fixture(
    project_root: Path,
    *,
    input_sha: str,
    items: list[dict[str, object]],
    capability: str = "ocr_primary",
) -> None:
    """Rewrite the installed controlled OCR adapter with a bound synthetic output fixture.

    The Controlled offline OCR adapter is not a model asset: it returns exactly these
    fixed bytes, bound to the fixed selected-frame input identity ``input_sha``.
    """

    fixtures = project_root / "config" / "visual-text" / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    output = {
        "schema_version": 1,
        "projection_schema_version": "phase-08-ocr-projection-schema-v1",
        "adapter_identity": "phase-08-controlled-ocr-adapter-v1",
        "capability": capability,
        "result": {"items": items},
    }
    raw = json.dumps(output).encode("utf-8")
    (fixtures / "ocr-output.json").write_bytes(raw)
    adapter_path = project_root / "config" / "visual-text" / "controlled-ocr-adapter.json"
    document = json.loads(adapter_path.read_text(encoding="utf-8"))
    document["fixture"] = {
        "capability": "ocr_primary",
        "input_fixture_sha256": input_sha,
        "output_fixture_path": "config/visual-text/fixtures/ocr-output.json",
        "output_fixture_sha256": sha256(raw).hexdigest(),
    }
    adapter_path.write_text(json.dumps(document), encoding="utf-8")


def _pause_then_confirm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    plan_id: str,
    part_id: str,
) -> tuple[int, dict[str, object]]:
    _, paused = _run(
        monkeypatch, capsys, tmp_path, ["visual-text", plan_id, "--part", part_id]
    )
    report_id = paused["report"]["report_id"]
    return _resume(monkeypatch, capsys, tmp_path, report_id, "ocr_resource_confirmed")


def test_confirmed_ocr_projects_and_gates_items_to_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules(tmp_path)
    part_id = plan.source_artifacts[0].source_id
    _install_frame_metrics(tmp_path, part_id, _PAGE_FRAMES)
    _install_ocr_fixture(
        tmp_path,
        input_sha=_selected_manifest_sha(plan.plan_id, part_id),
        items=[
            _ocr_item(part_id, "page-01", 0, text="标题", confidence=0.95),
            _ocr_item(part_id, "page-02", 3, text="Slide", confidence=0.88),
        ],
    )

    code, response = _pause_then_confirm(tmp_path, monkeypatch, capsys, plan.plan_id, part_id)

    assert code == 0
    assert response["status"] == "complete"
    evidence_block = response["report"]["ocr_evidence"]
    assert evidence_block["state"] == "projected"
    (part_evidence,) = evidence_block["parts"]
    assert part_evidence["rejected"] == []
    # AC#3: every admitted item carries Part, PTS, visual_page_id, and confidence.
    admitted = {item["visual_page_id"]: item for item in part_evidence["admitted"]}
    assert set(admitted) == {"page-01", "page-02"}
    assert admitted["page-01"]["part_id"] == part_id
    assert admitted["page-01"]["pts"] == {"numerator": 0, "denominator": 1}
    assert admitted["page-01"]["confidence"] == 0.95
    # AC#1: the controlled adapter is described by implementation version + fixed hashes.
    contract = evidence_block["contract"]
    assert contract["implementation_version"] == "phase-08-controlled-ocr-adapter-impl-v1"
    assert contract["input_manifest"]["sha256"] == _selected_manifest_sha(plan.plan_id, part_id)
    assert contract["restricted_raw_output"]["restricted"] is True
    # The offline model-execution guarantee holds: the controlled adapter is not a model.
    assert response["report"]["guarantees"]["model_execution"] == "not_attempted"


def test_confirmed_ocr_preserves_mixed_chinese_english_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules(tmp_path)
    part_id = plan.source_artifacts[0].source_id
    _install_frame_metrics(tmp_path, part_id, _PAGE_FRAMES)
    _install_ocr_fixture(
        tmp_path,
        input_sha=_selected_manifest_sha(plan.plan_id, part_id),
        items=[
            _ocr_item(
                part_id,
                "page-01",
                0,
                text="登录 Login",
                language_spans=[
                    {"language": "zh", "start_char": 0, "end_char": 2},
                    {"language": "en", "start_char": 3, "end_char": 8},
                ],
            ),
            _ocr_item(part_id, "page-02", 3, text="下一步"),
        ],
    )

    code, response = _pause_then_confirm(tmp_path, monkeypatch, capsys, plan.plan_id, part_id)

    assert code == 0
    (part_evidence,) = response["report"]["ocr_evidence"]["parts"]
    page_one = next(i for i in part_evidence["admitted"] if i["visual_page_id"] == "page-01")
    # AC#4: OCR text keeps its source language, including mixed Chinese/English.
    assert page_one["text"] == "登录 Login"
    spans = [(s["language"], s["start_char"], s["end_char"]) for s in page_one["language_spans"]]
    assert spans == [("zh", 0, 2), ("en", 3, 8)]


def test_confirmed_ocr_rejects_an_out_of_gate_item_to_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules(tmp_path)
    part_id = plan.source_artifacts[0].source_id
    _install_frame_metrics(tmp_path, part_id, _PAGE_FRAMES)
    _install_ocr_fixture(
        tmp_path,
        input_sha=_selected_manifest_sha(plan.plan_id, part_id),
        items=[
            _ocr_item(part_id, "page-01", 0, text="ok"),
            # page-02 appears only at t=3; an item timed at t=10 is inconsistent with it.
            _ocr_item(part_id, "page-02", 10, text="drift"),
        ],
    )

    code, response = _pause_then_confirm(tmp_path, monkeypatch, capsys, plan.plan_id, part_id)

    assert code == 0
    assert response["status"] == "partial"
    (part_evidence,) = response["report"]["ocr_evidence"]["parts"]
    assert len(part_evidence["admitted"]) == 1
    (rejected,) = part_evidence["rejected"]
    # The offending item is retained with a structured reason, never silently repaired.
    assert rejected["reason"] == "ocr_item_page_time_mismatch"
    assert rejected["visual_page_id"] == "page-02"


def test_confirmed_ocr_invalidates_the_attempt_on_malformed_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules(tmp_path)
    part_id = plan.source_artifacts[0].source_id
    _install_frame_metrics(tmp_path, part_id, _PAGE_FRAMES)
    # A well-bound input but a schema-invalid capability in the output envelope.
    _install_ocr_fixture(
        tmp_path,
        input_sha=_selected_manifest_sha(plan.plan_id, part_id),
        items=[_ocr_item(part_id, "page-01", 0)],
        capability="ocr_bogus",
    )

    code, response = _pause_then_confirm(tmp_path, monkeypatch, capsys, plan.plan_id, part_id)

    assert code == 0
    # AC#2: an invalid projection invalidates the whole attempt.
    assert response["status"] == "failed"
    report = response["report"]
    assert report["ocr_evidence"]["state"] == "model_output_invalid"
    assert report["diagnostics"][0]["reason"] == "model_output_invalid"
    # The raw output is retained as restricted, audit-only local evidence.
    raw = report["ocr_evidence"]["contract"]["restricted_raw_output"]
    assert raw["restricted"] is True and raw["audit_only"] is True
    assert Path(raw["path"]).exists()
    # The page index survives so the failure is auditable, and nothing is published.
    assert len(report["page_index"]["parts"]) == 1
    assert not (tmp_path / "outputs").exists()


def test_confirmed_ocr_rejects_a_fixture_not_bound_to_selected_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_rules(tmp_path)
    part_id = plan.source_artifacts[0].source_id
    _install_frame_metrics(tmp_path, part_id, _PAGE_FRAMES)
    # A fixture bound to some other input identity must not be accepted for this scope.
    _install_ocr_fixture(
        tmp_path,
        input_sha="f" * 64,
        items=[_ocr_item(part_id, "page-01", 0)],
    )

    code, response = _pause_then_confirm(tmp_path, monkeypatch, capsys, plan.plan_id, part_id)

    assert code == 0
    assert response["status"] == "failed"
    assert response["report"]["diagnostics"][0]["reason"] == "visual_text_fixture_input_mismatch"
