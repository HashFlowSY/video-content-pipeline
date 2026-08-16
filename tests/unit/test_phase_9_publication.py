from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from video_content_pipeline import publication
from video_content_pipeline.orchestration import RunLayout, initialize_run_workspace
from video_content_pipeline.planning import RunPlan, calculate_disk_headroom
from video_content_pipeline.publication import (
    MANIFEST_FILENAME,
    BundleDocument,
    LatestPointer,
    PublicationError,
    PublicationVerification,
    RunBundleManifest,
    VerificationDiscrepancy,
    assemble_staging,
    build_run_bundle_manifest,
    latest_pointer_eligible,
    publish_run_bundle,
    read_latest_pointer,
    read_run_bundle_manifest,
    verify_published_bundle,
)
from video_content_pipeline.publication_projection import (
    ArtifactStatus,
    PlainArtifactEvidence,
    ProjectionEvidence,
    ProjectionResult,
    PublicationBasis,
    TimedArtifactEvidence,
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
from video_content_pipeline.run_state import RunStatus
from video_content_pipeline.source import SourceArtifact

_PART_A = "a" * 64
_PART_B = "b" * 64
_SOURCE_ID = "c" * 64
_RUN_ID = "20260816T083000Z-0123456789abcdef"
_LATER_RUN_ID = "20260817T083000Z-0123456789abcdef"
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


def _artifact(content_hash: str) -> SourceArtifact:
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
        source_artifacts=tuple(_artifact(part) for part in (parts or (_PART_A,))),
        tools=(),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint=_CONFIG,
        run_choices=_mode_choices(mode),
    )


def _full_evidence(plan: RunPlan) -> ProjectionEvidence:
    """Evidence that makes every expected artifact available."""

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


def _empty_evidence() -> ProjectionEvidence:
    """Evidence with nothing available — every artifact is unavailable."""

    return ProjectionEvidence()


def _documents() -> tuple[BundleDocument, ...]:
    return (
        BundleDocument("processing-report.md", "# processing\n"),
        BundleDocument("run-inventory.json", "{}\n"),
        BundleDocument("quality-report.md", "# quality\n"),
        BundleDocument("quality-report.json", "{}\n"),
        BundleDocument("diagnostics/events.jsonl", '{"seq":0}\n'),
    )


def _layout(tmp_path: Path, *, run_id: str = _RUN_ID) -> RunLayout:
    layout = RunLayout(project_root=tmp_path, source_id=_SOURCE_ID, run_id=run_id)
    initialize_run_workspace(layout)
    return layout


def _projection(plan: RunPlan, evidence: ProjectionEvidence) -> ProjectionResult:
    return project_publication(plan, evidence)


# --- Manifest ---------------------------------------------------------------


def test_manifest_lists_every_projected_artifact_and_document() -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    projection = _projection(plan, _full_evidence(plan))
    documents = _documents()

    manifest = build_run_bundle_manifest(
        source_id=_SOURCE_ID,
        run_id=_RUN_ID,
        run_status=RunStatus.COMPLETE,
        projection=projection,
        documents=documents,
    )

    paths = {artifact.path for artifact in manifest.artifacts}
    assert {artifact.path for artifact in projection.artifacts} <= paths
    assert {document.path for document in documents} <= paths
    assert manifest.projection_stage_version == projection.stage_version


def test_manifest_is_sorted_by_path() -> None:
    plan = _plan(AsrMode.FULL_ASR, _PART_A, _PART_B)
    manifest = build_run_bundle_manifest(
        source_id=_SOURCE_ID,
        run_id=_RUN_ID,
        run_status=RunStatus.COMPLETE,
        projection=_projection(plan, _full_evidence(plan)),
        documents=_documents(),
    )
    paths = [artifact.path for artifact in manifest.artifacts]
    assert paths == sorted(paths)


def test_manifest_document_may_not_claim_the_reserved_path() -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    with pytest.raises(PublicationError) as excinfo:
        build_run_bundle_manifest(
            source_id=_SOURCE_ID,
            run_id=_RUN_ID,
            run_status=RunStatus.COMPLETE,
            projection=_projection(plan, _full_evidence(plan)),
            documents=(BundleDocument(MANIFEST_FILENAME, "{}"),),
        )
    assert excinfo.value.reason == "reserved_manifest_path"


def test_manifest_rejects_duplicate_paths() -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    with pytest.raises(PublicationError) as excinfo:
        build_run_bundle_manifest(
            source_id=_SOURCE_ID,
            run_id=_RUN_ID,
            run_status=RunStatus.COMPLETE,
            projection=_projection(plan, _full_evidence(plan)),
            documents=(
                BundleDocument("dup.txt", "one"),
                BundleDocument("dup.txt", "two"),
            ),
        )
    assert excinfo.value.reason == "duplicate_artifact_path"


def test_manifest_round_trips_through_json() -> None:
    plan = _plan(AsrMode.FULL_ASR, _PART_A)
    manifest = build_run_bundle_manifest(
        source_id=_SOURCE_ID,
        run_id=_RUN_ID,
        run_status=RunStatus.COMPLETE_WITH_WARNINGS,
        projection=_projection(plan, _full_evidence(plan)),
        documents=_documents(),
    )
    restored = RunBundleManifest.from_json(json.loads(manifest.to_text()))
    assert restored == manifest


# --- Staging ----------------------------------------------------------------


def test_staging_writes_available_artifacts_and_documents(tmp_path: Path) -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    layout = _layout(tmp_path)
    projection = _projection(plan, _full_evidence(plan))

    manifest = assemble_staging(
        layout,
        run_status=RunStatus.COMPLETE,
        projection=projection,
        documents=_documents(),
    )

    staged_manifest = layout.staging_dir / MANIFEST_FILENAME
    assert staged_manifest.is_file()
    for artifact in manifest.artifacts:
        target = layout.staging_dir / artifact.path
        if artifact.has_file:
            assert target.is_file()
        else:
            assert not target.exists()


def test_staging_never_fabricates_unavailable_files(tmp_path: Path) -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    layout = _layout(tmp_path)
    projection = _projection(plan, _empty_evidence())

    manifest = assemble_staging(
        layout,
        run_status=RunStatus.FAILED,
        projection=projection,
        documents=_documents(),
    )

    unavailable = [a for a in manifest.artifacts if a.status is ArtifactStatus.UNAVAILABLE]
    assert unavailable  # empty evidence yields unavailable content artifacts
    for artifact in unavailable:
        assert artifact.sha256 is None
        assert not (layout.staging_dir / artifact.path).exists()


def test_staging_self_check_is_bidirectional(tmp_path: Path) -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    layout = _layout(tmp_path)
    manifest = assemble_staging(
        layout,
        run_status=RunStatus.COMPLETE,
        projection=_projection(plan, _full_evidence(plan)),
        documents=_documents(),
    )
    verification = verify_published_bundle(layout.staging_dir)
    assert verification.verified
    # Manifest ↔ disk: every hashed entry has a file, and no file is unlisted.
    on_disk = {
        p.relative_to(layout.staging_dir).as_posix()
        for p in layout.staging_dir.rglob("*")
        if p.is_file()
    }
    expected = {a.path for a in manifest.artifacts if a.has_file} | {MANIFEST_FILENAME}
    assert on_disk == expected


# --- st_dev precheck --------------------------------------------------------


def test_cross_device_staging_errors_without_copying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    layout = _layout(tmp_path)
    outputs_root = tmp_path / "outputs"

    def fake_device_of(path: Path) -> int:
        # Pretend the outputs root lives on a different device than staging.
        return 1 if path == outputs_root else 2

    monkeypatch.setattr(publication, "_device_of", fake_device_of)

    with pytest.raises(PublicationError) as excinfo:
        assemble_staging(
            layout,
            run_status=RunStatus.COMPLETE,
            projection=_projection(plan, _full_evidence(plan)),
            documents=_documents(),
        )
    assert excinfo.value.reason == "cross_device_publish"
    # Nothing was staged past the precheck.
    assert not (layout.staging_dir / MANIFEST_FILENAME).exists()


# --- Atomic publish ---------------------------------------------------------


def test_publish_renames_whole_bundle_and_verifies(tmp_path: Path) -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    layout = _layout(tmp_path)
    projection = _projection(plan, _full_evidence(plan))

    outcome = publish_run_bundle(
        layout,
        run_status=RunStatus.COMPLETE,
        projection=projection,
        documents=_documents(),
        now=_NOW,
    )

    assert outcome.output_dir == layout.output_dir
    assert outcome.verification.verified
    assert (layout.output_dir / MANIFEST_FILENAME).is_file()
    # Staging is consumed by the rename — nothing left behind.
    assert not layout.staging_dir.exists()
    # The published manifest matches disk both ways.
    assert verify_published_bundle(layout.output_dir).verified


def test_publish_leaves_no_trace_when_rename_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    layout = _layout(tmp_path)

    def failing_rename(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(publication.os, "rename", failing_rename)

    with pytest.raises(PublicationError) as excinfo:
        publish_run_bundle(
            layout,
            run_status=RunStatus.COMPLETE,
            projection=_projection(plan, _full_evidence(plan)),
            documents=_documents(),
            now=_NOW,
        )
    assert excinfo.value.reason == "publish_rename_failed"
    assert not layout.output_dir.exists()  # no trace of the run under outputs/


def test_publish_never_overwrites_an_existing_run(tmp_path: Path) -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    layout = RunLayout(project_root=tmp_path, source_id=_SOURCE_ID, run_id=_RUN_ID)
    # A published bundle already exists with a sentinel file.
    layout.output_dir.mkdir(parents=True)
    sentinel = layout.output_dir / "sentinel.txt"
    sentinel.write_text("keep me", encoding="utf-8")
    layout.staging_dir.mkdir(parents=True)

    with pytest.raises(PublicationError) as excinfo:
        publish_run_bundle(
            layout,
            run_status=RunStatus.COMPLETE,
            projection=_projection(plan, _full_evidence(plan)),
            documents=_documents(),
            now=_NOW,
        )
    assert excinfo.value.reason == "run_already_published"
    assert sentinel.read_text(encoding="utf-8") == "keep me"


def test_publish_is_byte_identical_for_equal_runs(tmp_path: Path) -> None:
    plan = _plan(AsrMode.FULL_ASR, _PART_A, _PART_B)
    projection = _projection(plan, _full_evidence(plan))
    documents = _documents()

    first = _layout(tmp_path / "one")
    second = _layout(tmp_path / "two")
    publish_run_bundle(
        first, run_status=RunStatus.COMPLETE, projection=projection, documents=documents, now=_NOW
    )
    publish_run_bundle(
        second, run_status=RunStatus.COMPLETE, projection=projection, documents=documents, now=_NOW
    )

    first_manifest = (first.output_dir / MANIFEST_FILENAME).read_bytes()
    second_manifest = (second.output_dir / MANIFEST_FILENAME).read_bytes()
    assert first_manifest == second_manifest


# --- Post-publish reverification --------------------------------------------


def test_verify_detects_a_corrupted_published_file(tmp_path: Path) -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    layout = _layout(tmp_path)
    publish_run_bundle(
        layout,
        run_status=RunStatus.COMPLETE,
        projection=_projection(plan, _full_evidence(plan)),
        documents=_documents(),
        now=_NOW,
    )

    corrupted = layout.output_dir / "processing-report.md"
    corrupted.write_text("tampered", encoding="utf-8")

    verification = verify_published_bundle(layout.output_dir)
    assert not verification.verified
    assert any(d.reason == "hash_mismatch" for d in verification.discrepancies)


def test_verify_detects_an_unexpected_extra_file(tmp_path: Path) -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    layout = _layout(tmp_path)
    publish_run_bundle(
        layout,
        run_status=RunStatus.COMPLETE,
        projection=_projection(plan, _full_evidence(plan)),
        documents=_documents(),
        now=_NOW,
    )
    (layout.output_dir / "stowaway.txt").write_text("extra", encoding="utf-8")

    verification = verify_published_bundle(layout.output_dir)
    assert not verification.verified
    assert any(d.reason == "unexpected" for d in verification.discrepancies)


def test_publish_journals_and_reports_verification_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    layout = _layout(tmp_path)
    journalled: list[dict[str, object]] = []

    def fake_verify(bundle_dir: Path) -> PublicationVerification:
        return PublicationVerification(
            verified=False,
            discrepancies=(VerificationDiscrepancy("subtitles.source.srt", "hash_mismatch"),),
        )

    monkeypatch.setattr(publication, "verify_published_bundle", fake_verify)

    outcome = publish_run_bundle(
        layout,
        run_status=RunStatus.COMPLETE,
        projection=_projection(plan, _full_evidence(plan)),
        documents=_documents(),
        now=_NOW,
        journal=lambda record: journalled.append(dict(record)),
    )

    assert not outcome.verification.verified  # reported in the outcome
    assert len(journalled) == 1  # journaled through the seam
    assert journalled[0]["event"] == "publication_verification_failed"
    # A bundle that failed reverification never advances the pointer.
    assert outcome.latest_advanced is False
    assert read_latest_pointer(layout.latest_path) is None


# --- Latest pointer ---------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [RunStatus.COMPLETE, RunStatus.COMPLETE_WITH_WARNINGS],
)
def test_latest_advances_for_complete_runs(tmp_path: Path, status: RunStatus) -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    layout = _layout(tmp_path)
    outcome = publish_run_bundle(
        layout,
        run_status=status,
        projection=_projection(plan, _full_evidence(plan)),
        documents=_documents(),
        now=_NOW,
    )
    assert outcome.latest_advanced
    pointer = read_latest_pointer(layout.latest_path)
    assert pointer is not None
    assert pointer.run_id == _RUN_ID
    assert pointer.run_status is status


def test_latest_never_advances_for_a_purely_failed_run(tmp_path: Path) -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    layout = _layout(tmp_path)
    outcome = publish_run_bundle(
        layout,
        run_status=RunStatus.FAILED,
        projection=_projection(plan, _empty_evidence()),
        documents=_documents(),
        now=_NOW,
    )
    assert outcome.latest_advanced is False
    assert read_latest_pointer(layout.latest_path) is None


def test_latest_advances_for_incomplete_run_with_partial_results(tmp_path: Path) -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    # Some content is available, so partial results are published.
    layout = _layout(tmp_path)
    outcome = publish_run_bundle(
        layout,
        run_status=RunStatus.INCOMPLETE,
        projection=_projection(plan, _full_evidence(plan)),
        documents=_documents(),
        now=_NOW,
    )
    assert outcome.latest_advanced


def test_latest_does_not_advance_for_incomplete_run_without_content(tmp_path: Path) -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    layout = _layout(tmp_path)
    outcome = publish_run_bundle(
        layout,
        run_status=RunStatus.INCOMPLETE,
        projection=_projection(plan, _empty_evidence()),
        documents=_documents(),
        now=_NOW,
    )
    assert outcome.latest_advanced is False
    assert read_latest_pointer(layout.latest_path) is None


def test_latest_pointer_stores_a_pointer_not_a_copy(tmp_path: Path) -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    layout = _layout(tmp_path)
    publish_run_bundle(
        layout,
        run_status=RunStatus.COMPLETE,
        projection=_projection(plan, _full_evidence(plan)),
        documents=_documents(),
        now=_NOW,
    )
    document = json.loads(layout.latest_path.read_text(encoding="utf-8"))
    assert set(document) == {"schema_version", "source_id", "run_id", "run_status", "published_at"}
    assert document["run_id"] == _RUN_ID


def test_latest_pointer_does_not_regress_to_an_older_run(tmp_path: Path) -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    projection = _projection(plan, _full_evidence(plan))
    documents = _documents()

    newer = _layout(tmp_path, run_id=_LATER_RUN_ID)
    publish_run_bundle(
        newer, run_status=RunStatus.COMPLETE, projection=projection, documents=documents, now=_NOW
    )
    older = _layout(tmp_path, run_id=_RUN_ID)
    outcome = publish_run_bundle(
        older, run_status=RunStatus.COMPLETE, projection=projection, documents=documents, now=_NOW
    )

    assert outcome.latest_advanced is False
    pointer = read_latest_pointer(older.latest_path)
    assert pointer is not None
    assert pointer.run_id == _LATER_RUN_ID  # the newer run keeps the pointer


def test_read_latest_pointer_is_none_when_absent(tmp_path: Path) -> None:
    assert read_latest_pointer(tmp_path / "outputs" / _SOURCE_ID / "latest.json") is None


# --- Eligibility (pure) -----------------------------------------------------


def test_eligibility_requires_verification() -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    projection = _projection(plan, _full_evidence(plan))
    unverified = PublicationVerification(verified=False)
    assert not latest_pointer_eligible(RunStatus.COMPLETE, projection, unverified)


def test_eligibility_of_terminal_statuses() -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    full = _projection(plan, _full_evidence(plan))
    empty = _projection(plan, _empty_evidence())
    ok = PublicationVerification(verified=True)

    assert latest_pointer_eligible(RunStatus.COMPLETE, empty, ok)
    assert latest_pointer_eligible(RunStatus.COMPLETE_WITH_WARNINGS, empty, ok)
    assert not latest_pointer_eligible(RunStatus.FAILED, full, ok)
    assert latest_pointer_eligible(RunStatus.INCOMPLETE, full, ok)
    assert not latest_pointer_eligible(RunStatus.INCOMPLETE, empty, ok)
    assert latest_pointer_eligible(RunStatus.CANCELLED, full, ok)


def test_latest_pointer_naive_timestamp_is_rejected(tmp_path: Path) -> None:
    plan = _plan(AsrMode.SUBTITLE_FIRST, _PART_A)
    layout = _layout(tmp_path)
    with pytest.raises(PublicationError) as excinfo:
        publish_run_bundle(
            layout,
            run_status=RunStatus.COMPLETE,
            projection=_projection(plan, _full_evidence(plan)),
            documents=_documents(),
            now=datetime(2026, 8, 16, 9, 0, 0),  # naive
        )
    assert excinfo.value.reason == "naive_timestamp"


# --- Manifest reading -------------------------------------------------------


def test_read_run_bundle_manifest_round_trips(tmp_path: Path) -> None:
    plan = _plan(AsrMode.FULL_ASR, _PART_A)
    layout = _layout(tmp_path)
    outcome = publish_run_bundle(
        layout,
        run_status=RunStatus.COMPLETE,
        projection=_projection(plan, _full_evidence(plan)),
        documents=_documents(),
        now=_NOW,
    )
    read_back = read_run_bundle_manifest(layout.output_dir)
    assert read_back == outcome.manifest


def test_read_run_bundle_manifest_missing_errors(tmp_path: Path) -> None:
    with pytest.raises(PublicationError) as excinfo:
        read_run_bundle_manifest(tmp_path)
    assert excinfo.value.reason == "manifest_missing"


def test_corrupt_latest_pointer_reports_latest_invalid(tmp_path: Path) -> None:
    # A defective latest.json must surface the pointer's own reason, not the
    # manifest's — the reason is the auditable discriminator.
    path = tmp_path / "latest.json"
    path.write_text(json.dumps({"schema_version": 1, "run_id": "x"}), encoding="utf-8")
    with pytest.raises(PublicationError) as excinfo:
        read_latest_pointer(path)
    assert excinfo.value.reason == "latest_invalid"


def test_latest_pointer_json_round_trips() -> None:
    pointer = LatestPointer(
        source_id=_SOURCE_ID,
        run_id=_RUN_ID,
        run_status=RunStatus.COMPLETE,
        published_at=_NOW.isoformat(),
    )
    restored = LatestPointer.from_json(pointer.as_json())
    assert restored == pointer
