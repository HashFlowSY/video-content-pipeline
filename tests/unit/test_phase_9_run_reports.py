"""Offline contract for the always-published audit layer (Phase 9 ticket 09).

These tests use only synthetic, hash-pinned fixtures inside synthetic project
roots. They assert the deterministic contract properties of the run reports,
the machine inventory, and the Minimal RunBundle floor: every plan §18.1
section is present with the fixed project-stage line; the inventory carries the
eleven-field records with deletion classes across models, caches, workspaces,
staging and published files; the quality report aggregates recorded gate
outcomes and the projection's timing-view selections without re-running gates;
and every ordinary failure path publishes the six-piece floor.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from video_content_pipeline.orchestration import RunLayout, initialize_run_workspace
from video_content_pipeline.planning import RunPlan, calculate_disk_headroom
from video_content_pipeline.publication import (
    MANIFEST_FILENAME,
    publish_run_bundle,
    verify_published_bundle,
)
from video_content_pipeline.publication_projection import (
    ArtifactKind,
    ArtifactStatus,
    PlainArtifactEvidence,
    ProjectedArtifact,
    ProjectionEvidence,
    ProjectionResult,
    PublicationBasis,
    TimedArtifactEvidence,
    TimingView,
    expected_subtitle_bases,
    project_publication,
)
from video_content_pipeline.run_choices import (
    COLLECTION_SCOPE,
    KEY_ASR_MODE,
    KEY_VISUAL_TEXT_ENABLED,
    STAGE_RUN,
    AsrMode,
    ChoiceProvenance,
    RunChoice,
    RunPlanChoices,
)
from video_content_pipeline.run_reports import (
    DIAGNOSTICS_EVENTS_PATH,
    MINIMAL_RUN_BUNDLE_DOCUMENTS,
    PROCESSING_REPORT_SECTIONS,
    PROJECT_STAGE_LINE,
    DeletionClass,
    EnvironmentInfo,
    GateOutcome,
    GateStatus,
    InputRecord,
    InventoryAction,
    InventoryEntry,
    InventoryKind,
    ModelRecord,
    ParameterRecord,
    ProcessingReport,
    ResourceUsage,
    ReviewNeededInterval,
    RunReportError,
    StageGateReport,
    ToolRecord,
    VerificationLevel,
    assemble_minimal_run_bundle,
    build_quality_report,
    build_run_inventory,
    published_content_entries,
    render_events_snapshot,
    timing_selections_from_projection,
)
from video_content_pipeline.run_state import EventKind, RunEvent, RunStatus
from video_content_pipeline.source import SourceArtifact

_PART_A = "a" * 64
_PART_B = "b" * 64
_SOURCE_ID = "c" * 64
_RUN_ID = "20260816T083000Z-0123456789abcdef"
_PLAN_ID = "plan0123456789abcdef0123"
_CONFIG = "cfg" + "0" * 61
_NOW = datetime(2026, 8, 16, 9, 0, 0, tzinfo=UTC)


def _choice(key: str, value: str) -> RunChoice:
    return RunChoice(
        stage=STAGE_RUN,
        key=key,
        scope=COLLECTION_SCOPE,
        value=value,
        provenance=ChoiceProvenance.USER_CHOSEN,
    )


def _mode_choices(mode: AsrMode) -> RunPlanChoices:
    return RunPlanChoices.build(
        (
            _choice(KEY_ASR_MODE, mode.value),
            _choice(KEY_VISUAL_TEXT_ENABLED, "false"),
        )
    )


def _source_artifact(content_hash: str) -> SourceArtifact:
    return SourceArtifact(
        source_id=content_hash,
        sha256=content_hash,
        byte_count=1,
        media_path=Path("input") / content_hash / "media",
    )


def _plan(mode: AsrMode, *parts: str) -> RunPlan:
    return RunPlan(
        plan_id=_PLAN_ID,
        report_id="0" * 32,
        source_artifacts=tuple(_source_artifact(part) for part in (parts or (_PART_A,))),
        tools=(),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint=_CONFIG,
        run_choices=_mode_choices(mode),
    )


def _full_evidence(plan: RunPlan) -> ProjectionEvidence:
    mode = plan.run_choices.asr_mode()
    assert mode is not None
    bases = expected_subtitle_bases(mode)
    parts = tuple(artifact.source_id for artifact in plan.source_artifacts)
    part_subs: dict[tuple[str, PublicationBasis], TimedArtifactEvidence] = {}
    coll_subs: dict[PublicationBasis, TimedArtifactEvidence] = {}
    for basis in bases:
        coll_subs[basis] = TimedArtifactEvidence(original=f"COLL-{basis.value}")
        for part in parts:
            part_subs[(part, basis)] = TimedArtifactEvidence(original=f"{part[:4]}-{basis.value}")
    return ProjectionEvidence(
        part_subtitles=part_subs,
        collection_subtitles=coll_subs,
        collection_transcript=TimedArtifactEvidence(original="TRANSCRIPT"),
        content_report=PlainArtifactEvidence(content="# 报告"),
        segments=PlainArtifactEvidence(content="[]"),
        correction_log=PlainArtifactEvidence(content="[]"),
    )


def _layout(root: Path) -> RunLayout:
    return RunLayout(project_root=root, source_id=_SOURCE_ID, run_id=_RUN_ID)


def _inventory_entry(
    path: str,
    kind: InventoryKind,
    action: InventoryAction,
    deletion_class: DeletionClass,
) -> InventoryEntry:
    return InventoryEntry(
        path=path,
        kind=kind,
        action=action,
        purpose="synthetic",
        size_bytes=1,
        sha256=None,
        stage="synthetic",
        used_by=(),
        rebuildable=deletion_class is DeletionClass.REBUILDABLE,
        deletion_class=deletion_class,
        deletion_consequence="synthetic consequence",
    )


def _coverage_inventory() -> tuple[InventoryEntry, ...]:
    """An inventory spanning models, caches, workspaces, staging, and published."""

    return (
        _inventory_entry(
            "models/asr/model.bin",
            InventoryKind.MODEL,
            InventoryAction.READ,
            DeletionClass.KEEP_FOR_AUDIT,
        ),
        _inventory_entry(
            "cache/probe/xyz.json",
            InventoryKind.CACHE,
            InventoryAction.CREATED,
            DeletionClass.REBUILDABLE,
        ),
        _inventory_entry(
            f"work/{_SOURCE_ID}/{_RUN_ID}/stages/subtitles",
            InventoryKind.DIRECTORY,
            InventoryAction.CREATED,
            DeletionClass.REBUILDABLE,
        ),
        _inventory_entry(
            f"work/{_SOURCE_ID}/{_RUN_ID}/staging",
            InventoryKind.DIRECTORY,
            InventoryAction.CREATED,
            DeletionClass.SAFE_TO_DELETE,
        ),
        _inventory_entry(
            f"outputs/{_SOURCE_ID}/{_RUN_ID}/subtitles.source.srt",
            InventoryKind.FILE,
            InventoryAction.PUBLISHED,
            DeletionClass.PUBLISHED,
        ),
        _inventory_entry(
            "input/original.mkv",
            InventoryKind.EXTERNAL_SOURCE,
            InventoryAction.READ,
            DeletionClass.KEEP_FOR_AUDIT,
        ),
    )


def _events() -> tuple[RunEvent, ...]:
    return (
        RunEvent(
            sequence=0,
            at="2026-08-16T09:00:00+00:00",
            kind=EventKind.TRANSITION,
            data={"from": None, "to": "planned"},
        ),
        RunEvent(
            sequence=1,
            at="2026-08-16T09:00:01+00:00",
            kind=EventKind.TRANSITION,
            data={"from": "planned", "to": "queued"},
        ),
    )


# --- Quality report ---------------------------------------------------------


def test_quality_report_aggregates_recorded_gates_and_timing_selections() -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A, _PART_B)
    projection = project_publication(plan, _full_evidence(plan))
    stage_reports = (
        StageGateReport(
            stage="subtitles",
            scope=_PART_A,
            outcomes=(
                GateOutcome("encoding_valid", GateStatus.PASSED),
                GateOutcome("cue_sequence_legal", GateStatus.WARNING, "one overlap"),
            ),
        ),
    )
    review = (ReviewNeededInterval(scope=_PART_B, reason="low_confidence", detail="names"),)

    report = build_quality_report(
        source_id=_SOURCE_ID,
        run_id=_RUN_ID,
        run_status=RunStatus.COMPLETE_WITH_WARNINGS,
        projection=projection,
        stage_reports=stage_reports,
        review_needed=review,
    )

    payload = json.loads(report.to_json_text())
    assert payload["schema_version"] == 1
    assert payload["run_status"] == "complete_with_warnings"
    assert payload["verification_level"] == "model_audited"
    # Gate outcomes are carried through verbatim — nothing is re-evaluated.
    assert payload["stage_reports"][0]["outcomes"][1] == {
        "gate": "cue_sequence_legal",
        "status": "warning",
        "detail": "one overlap",
    }
    # Timing selections are exactly what the projection recorded.
    assert payload["timing_selections"] == [
        selection.as_json() for selection in timing_selections_from_projection(projection)
    ]
    assert payload["timing_selections"], "coordinate-bearing artifacts must record a timing view"
    assert payload["review_needed"] == [
        {"scope": _PART_B, "reason": "low_confidence", "detail": "names"}
    ]


def test_quality_report_markdown_is_chinese_and_carries_project_stage_line() -> None:
    plan = _plan(AsrMode.FULL_ASR)
    projection = project_publication(plan, _full_evidence(plan))
    report = build_quality_report(
        source_id=_SOURCE_ID,
        run_id=_RUN_ID,
        run_status=RunStatus.COMPLETE,
        projection=projection,
    )
    markdown = report.to_markdown()
    assert markdown.startswith("# 质量报告")
    assert PROJECT_STAGE_LINE in markdown
    assert "## 各阶段门禁结果" in markdown
    assert "## 时间视图选择" in markdown
    assert "无已记录的门禁结果" in markdown  # empty stage reports still render a placeholder


def test_quality_report_refuses_human_verified() -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST)
    projection = project_publication(plan, _full_evidence(plan))
    with pytest.raises(RunReportError) as excinfo:
        build_quality_report(
            source_id=_SOURCE_ID,
            run_id=_RUN_ID,
            run_status=RunStatus.COMPLETE,
            projection=projection,
            verification_level=VerificationLevel.HUMAN_VERIFIED,
        )
    assert excinfo.value.reason == "human_verification_unsupported"


def test_verification_level_never_claims_model_audited_for_non_complete_runs() -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST)
    projection = project_publication(plan, _full_evidence(plan))
    for status in (RunStatus.FAILED, RunStatus.INCOMPLETE, RunStatus.CANCELLED):
        report = build_quality_report(
            source_id=_SOURCE_ID, run_id=_RUN_ID, run_status=status, projection=projection
        )
        # A run that did not clear every automated gate must not claim a clean
        # audit — the honest default is review_needed (plan §17.6).
        assert report.verification_level is VerificationLevel.REVIEW_NEEDED
    for status in (RunStatus.COMPLETE, RunStatus.COMPLETE_WITH_WARNINGS):
        report = build_quality_report(
            source_id=_SOURCE_ID, run_id=_RUN_ID, run_status=status, projection=projection
        )
        assert report.verification_level is VerificationLevel.MODEL_AUDITED


def test_quality_report_json_is_deterministic() -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A, _PART_B)
    projection = project_publication(plan, _full_evidence(plan))
    reports_out_of_order = (
        StageGateReport(stage="subtitles", scope=_PART_B),
        StageGateReport(stage="subtitles", scope=_PART_A),
    )
    first = build_quality_report(
        source_id=_SOURCE_ID,
        run_id=_RUN_ID,
        run_status=RunStatus.COMPLETE,
        projection=projection,
        stage_reports=reports_out_of_order,
    ).to_json_text()
    second = build_quality_report(
        source_id=_SOURCE_ID,
        run_id=_RUN_ID,
        run_status=RunStatus.COMPLETE,
        projection=projection,
        stage_reports=tuple(reversed(reports_out_of_order)),
    ).to_json_text()
    assert first == second
    scopes = [report["scope"] for report in json.loads(first)["stage_reports"]]
    assert scopes == [_PART_A, _PART_B]


# --- Run inventory ----------------------------------------------------------


def test_inventory_records_eleven_fields_across_all_categories() -> None:
    inventory = build_run_inventory(
        source_id=_SOURCE_ID, run_id=_RUN_ID, entries=_coverage_inventory()
    )
    payload = json.loads(inventory.to_json_text())
    assert payload["schema_version"] == 1
    entries = payload["entries"]
    kinds = {entry["kind"] for entry in entries}
    assert {"model", "cache", "directory", "file", "external_source"} <= kinds
    # Every record carries the plan §18.2 eleven fields with a deletion class
    # and its consequence.
    expected_fields = {
        "path",
        "kind",
        "action",
        "purpose",
        "size_bytes",
        "sha256",
        "stage",
        "used_by",
        "rebuildable",
        "deletion_class",
        "deletion_consequence",
    }
    for entry in entries:
        assert set(entry) == expected_fields
        assert entry["deletion_class"] in {
            "safe_to_delete",
            "rebuildable",
            "keep_for_audit",
            "published",
        }
        assert entry["deletion_consequence"]
    # Published files appear.
    assert any(entry["action"] == "published" for entry in entries)


def test_inventory_entries_are_sorted_for_determinism() -> None:
    entries = _coverage_inventory()
    forward = build_run_inventory(source_id=_SOURCE_ID, run_id=_RUN_ID, entries=entries)
    reversed_ = build_run_inventory(
        source_id=_SOURCE_ID, run_id=_RUN_ID, entries=tuple(reversed(entries))
    )
    assert forward.to_json_text() == reversed_.to_json_text()
    paths = [entry["path"] for entry in json.loads(forward.to_json_text())["entries"]]
    assert paths == sorted(paths)


def test_inventory_rejects_duplicate_path_action() -> None:
    entry = _inventory_entry(
        "cache/x.json", InventoryKind.CACHE, InventoryAction.CREATED, DeletionClass.REBUILDABLE
    )
    with pytest.raises(RunReportError) as excinfo:
        build_run_inventory(source_id=_SOURCE_ID, run_id=_RUN_ID, entries=(entry, entry))
    assert excinfo.value.reason == "duplicate_inventory_entry"


def test_same_path_two_actions_is_allowed() -> None:
    read = _inventory_entry(
        "input/x.mkv",
        InventoryKind.EXTERNAL_SOURCE,
        InventoryAction.READ,
        DeletionClass.KEEP_FOR_AUDIT,
    )
    modified = _inventory_entry(
        "input/x.mkv", InventoryKind.FILE, InventoryAction.MODIFIED, DeletionClass.REBUILDABLE
    )
    inventory = build_run_inventory(source_id=_SOURCE_ID, run_id=_RUN_ID, entries=(read, modified))
    assert len(inventory.entries) == 2


def test_published_content_entries_cover_projected_files_with_hashes() -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A, _PART_B)
    projection = project_publication(plan, _full_evidence(plan))
    entries = published_content_entries(projection)
    available = [artifact for artifact in projection.artifacts if artifact.sha256 is not None]
    assert len(entries) == len(available)
    for entry in entries:
        assert entry.action is InventoryAction.PUBLISHED
        assert entry.deletion_class is DeletionClass.PUBLISHED
        assert entry.sha256 is not None


def test_published_content_entries_omit_unavailable_artifacts() -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST)
    projection = project_publication(plan, ProjectionEvidence())  # nothing available
    assert published_content_entries(projection) == ()


def test_published_content_entries_omit_invalid_artifacts() -> None:
    # An invalid artifact has bytes and a hash but is not a published formal
    # output (plan §17.5); it must not appear as an action=published entry.
    plan = _plan(AsrMode.SUBTITLE_FIRST)
    evidence = ProjectionEvidence(
        content_report=PlainArtifactEvidence(content="# 报告", invalid=True),
    )
    projection = project_publication(plan, evidence)
    invalid = [a for a in projection.artifacts if a.status is ArtifactStatus.INVALID]
    assert invalid, "fixture must produce at least one invalid artifact"
    entries = published_content_entries(projection)
    assert all(entry.path != invalid[0].path for entry in entries)


def test_published_content_entries_record_carried_forward_source_run() -> None:
    # An Improvement run's carried-forward artifact records its source run id in
    # the inventory (reports), not only the manifest (criterion 3).
    carried = ProjectedArtifact(
        path="parts/p1/subtitles.enhanced.srt",
        kind=ArtifactKind.SUBTITLES,
        status=ArtifactStatus.VALID,
        content="1\ncue\n",
        sha256="deadbeef",
        timing_view=TimingView.PART_RELATIVE,
        timing_basis=None,
        provenance={
            "carried_forward_from_run": "20260816T090000Z-abcdef0123456789",
            "carried_forward_sha256": "deadbeef",
        },
    )
    projection = ProjectionResult(artifacts=(carried,), stage_version=1)
    entries = published_content_entries(projection)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.action is InventoryAction.PUBLISHED
    assert entry.used_by == ("20260816T090000Z-abcdef0123456789",)
    assert "20260816T090000Z-abcdef0123456789" in entry.purpose
    assert entry.sha256 == "deadbeef"


def test_deletable_entries_exclude_published_and_audit() -> None:
    inventory = build_run_inventory(
        source_id=_SOURCE_ID, run_id=_RUN_ID, entries=_coverage_inventory()
    )
    classes = {entry.deletion_class for entry in inventory.deletable_entries()}
    assert classes == {DeletionClass.SAFE_TO_DELETE, DeletionClass.REBUILDABLE}


# --- Processing report ------------------------------------------------------


def _processing_report(inventory_entries: tuple[InventoryEntry, ...]) -> ProcessingReport:
    inventory = build_run_inventory(source_id=_SOURCE_ID, run_id=_RUN_ID, entries=inventory_entries)
    return ProcessingReport(
        source_id=_SOURCE_ID,
        run_id=_RUN_ID,
        plan_id=_PLAN_ID,
        run_status=RunStatus.COMPLETE,
        inventory=inventory,
        inputs=(InputRecord(part_id=_PART_A, source="input/a.mkv", sha256=_PART_A),),
        tools=(ToolRecord(name="ffprobe", path="tools/ffprobe", version="6.0", purpose="探测"),),
        environment=EnvironmentInfo(
            python_version="3.12.13",
            virtualenv_path=".venv",
            lockfile_sha256="d" * 64,
        ),
        models=(
            ModelRecord(
                name="asr",
                revision="r1",
                sha256="e" * 64,
                path="models/asr/model.bin",
                size_bytes=1024,
                purpose="转写",
            ),
        ),
        parameters=(ParameterRecord(name="language", value="zh"),),
        network=(),
        resource_usage=ResourceUsage(elapsed_seconds=1.5, peak_memory_bytes=2048),
        warnings=("字幕轨可能删减",),
        review_needed=(ReviewNeededInterval(scope=_PART_A, reason="names"),),
    )


def test_processing_report_contains_every_section_and_project_stage_line() -> None:
    markdown = _processing_report(_coverage_inventory()).to_markdown()
    assert markdown.startswith("# 处理报告")
    assert PROJECT_STAGE_LINE in markdown
    for section in PROCESSING_REPORT_SECTIONS:
        assert section in markdown, f"missing section {section}"


def test_processing_report_derives_paths_and_cleanup_from_inventory() -> None:
    markdown = _processing_report(_coverage_inventory()).to_markdown()
    # External reads section lists the read-action inventory entries.
    assert "input/original.mkv" in markdown
    # Published path appears under created/modified/published.
    assert f"outputs/{_SOURCE_ID}/{_RUN_ID}/subtitles.source.srt" in markdown
    # Cleanup section names the deletable (rebuildable / safe-to-delete) entries
    # and states the no-auto-delete rule; published/audit paths are not offered.
    cleanup = markdown.split(PROCESSING_REPORT_SECTIONS[11], 1)[1]
    assert "没有 cleanup 命令" in cleanup
    assert "cache/probe/xyz.json" in cleanup
    assert "subtitles.source.srt" not in cleanup  # published output is retained


def test_processing_report_renders_placeholders_for_empty_sections() -> None:
    empty = build_run_inventory(source_id=_SOURCE_ID, run_id=_RUN_ID, entries=())
    report = ProcessingReport(
        source_id=_SOURCE_ID,
        run_id=_RUN_ID,
        plan_id=_PLAN_ID,
        run_status=RunStatus.FAILED,
        inventory=empty,
    )
    markdown = report.to_markdown()
    for section in PROCESSING_REPORT_SECTIONS:
        assert section in markdown
    assert "未使用模型" in markdown
    assert "未测量" in markdown  # resource usage unmeasured
    assert "无网络请求或下载" in markdown


# --- Diagnostics ------------------------------------------------------------


def test_events_snapshot_is_ordered_and_deterministic() -> None:
    events = _events()
    snapshot = render_events_snapshot(tuple(reversed(events)))
    payload = json.loads(snapshot)
    assert payload["schema_version"] == 1
    assert [event["sequence"] for event in payload["events"]] == [0, 1]
    assert render_events_snapshot(events) == snapshot


# --- Minimal RunBundle floor ------------------------------------------------


def _minimal_documents(plan: RunPlan, evidence: ProjectionEvidence, status: RunStatus):
    projection = project_publication(plan, evidence)
    quality = build_quality_report(
        source_id=_SOURCE_ID, run_id=_RUN_ID, run_status=status, projection=projection
    )
    inventory = build_run_inventory(
        source_id=_SOURCE_ID,
        run_id=_RUN_ID,
        entries=published_content_entries(projection) + _coverage_inventory(),
    )
    processing = ProcessingReport(
        source_id=_SOURCE_ID,
        run_id=_RUN_ID,
        plan_id=_PLAN_ID,
        run_status=status,
        inventory=inventory,
    )
    documents = assemble_minimal_run_bundle(
        quality_report=quality,
        processing_report=processing,
        inventory=inventory,
        events=_events(),
    )
    return projection, documents


def test_minimal_run_bundle_produces_the_full_audit_floor() -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST)
    _, documents = _minimal_documents(plan, _full_evidence(plan), RunStatus.COMPLETE)
    assert {document.path for document in documents} == set(MINIMAL_RUN_BUNDLE_DOCUMENTS)
    assert DIAGNOSTICS_EVENTS_PATH in {document.path for document in documents}


def test_failure_before_any_stage_still_publishes_minimal_bundle(tmp_path: Path) -> None:
    # A run that fails before any stage completes has no evidence at all.
    plan = _plan(AsrMode.SUBTITLE_FIRST)
    projection, documents = _minimal_documents(plan, ProjectionEvidence(), RunStatus.FAILED)
    layout = initialize_run_workspace(_layout(tmp_path))

    outcome = publish_run_bundle(
        layout,
        run_status=RunStatus.FAILED,
        projection=projection,
        documents=documents,
        now=_NOW,
    )

    # The six-piece floor is published and hash-verifiable.
    published = {
        path.relative_to(layout.output_dir).as_posix()
        for path in layout.output_dir.rglob("*")
        if path.is_file()
    }
    for floor in (MANIFEST_FILENAME, *MINIMAL_RUN_BUNDLE_DOCUMENTS):
        assert floor in published, f"missing floor piece {floor}"
    assert verify_published_bundle(layout.output_dir).verified
    assert outcome.verification.verified
    # A purely failed run never advances the latest pointer.
    assert outcome.latest_advanced is False
    assert not layout.latest_path.exists()


def test_repository_outputs_untouched_by_synthetic_publish(tmp_path: Path) -> None:
    # Publication is exercised only inside a synthetic project root.
    plan = _plan(AsrMode.SUBTITLE_FIRST)
    projection, documents = _minimal_documents(plan, _full_evidence(plan), RunStatus.COMPLETE)
    layout = initialize_run_workspace(_layout(tmp_path))
    publish_run_bundle(
        layout,
        run_status=RunStatus.COMPLETE,
        projection=projection,
        documents=documents,
        now=_NOW,
    )
    assert (tmp_path / "outputs" / _SOURCE_ID / _RUN_ID).is_dir()
    repo_outputs = Path(__file__).parents[2] / "outputs" / _SOURCE_ID
    assert not repo_outputs.exists()
