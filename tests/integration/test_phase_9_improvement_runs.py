"""Ticket 11 acceptance: improvement runs, proven offline.

These exercises drive :func:`~video_content_pipeline.improve.start_improvement_run`
(and the ``vcp improve`` CLI boundary) over a synthetic project root with a
controlled :class:`~video_content_pipeline.run_loop.RunComposition` — no model, no
media, no network. They prove the improvement contract: the source bundle's
hashes are revalidated and read from (never a workspace); a new plan and run id
are created while the prior bundle stays byte-identical; the unaffected Parts are
carried forward with their source run id and artifact hashes recorded in the new
manifest; the scope grammar maps to the enhancement scope; and the new run
publishes through the standard staging/atomic-publish path with the standard
latest-pointer eligibility.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from video_content_pipeline import cli
from video_content_pipeline.heavy_task_lock import ProcessIdentity
from video_content_pipeline.improve import ImproveError, start_improvement_run
from video_content_pipeline.orchestration import (
    RunLayout,
    run_id_from_run_plan,
    source_id_from_run_plan,
)
from video_content_pipeline.planning import RunPlan, calculate_disk_headroom
from video_content_pipeline.publication import read_latest_pointer, read_run_bundle_manifest
from video_content_pipeline.publication_projection import (
    ProjectionEvidence,
    PublicationBasis,
    TimedArtifactEvidence,
)
from video_content_pipeline.run_choices import (
    COLLECTION_SCOPE,
    KEY_ASR_MODE,
    KEY_ENHANCEMENT_PART,
    KEY_VISUAL_TEXT_ENABLED,
    STAGE_ENHANCEMENT,
    STAGE_RUN,
    AsrMode,
    ChoiceProvenance,
    RunChoice,
    RunPlanChoices,
)
from video_content_pipeline.run_loop import RunComposition, RunReportInputs, start_run
from video_content_pipeline.run_state import RunStatus
from video_content_pipeline.source import SourceArtifact
from video_content_pipeline.stage_dag import (
    StageInvalidationKey,
    StageResult,
    StageUnit,
)

_PART_A = "a" * 64
_PART_B = "b" * 64
_ME = ProcessIdentity(pid=700, start_time="s700")
_NOW = datetime(2026, 8, 16, 8, 0, 0, tzinfo=UTC)
_LATER = datetime(2026, 8, 16, 12, 0, 0, tzinfo=UTC)


class _FakeProbe:
    def __init__(self, identity: ProcessIdentity, live: set[ProcessIdentity]) -> None:
        self._identity = identity
        self._live = set(live)

    def identify(self) -> ProcessIdentity:
        return self._identity

    def is_running(self, identity: ProcessIdentity) -> bool:
        return identity in self._live


def _clock() -> Callable[[], datetime]:
    step = {"n": 0}

    def tick() -> datetime:
        moment = datetime(2026, 8, 16, 8, 30, step["n"] % 60, tzinfo=UTC)
        step["n"] += 1
        return moment

    return tick


def _enhancement_plan(part_ids: tuple[str, ...]) -> RunPlan:
    choices = [
        RunChoice(
            STAGE_RUN,
            KEY_ASR_MODE,
            COLLECTION_SCOPE,
            AsrMode.ENHANCEMENT.value,
            ChoiceProvenance.USER_CHOSEN,
        ),
        RunChoice(
            STAGE_RUN,
            KEY_VISUAL_TEXT_ENABLED,
            COLLECTION_SCOPE,
            "false",
            ChoiceProvenance.USER_CHOSEN,
        ),
    ]
    choices.extend(
        RunChoice(
            STAGE_ENHANCEMENT,
            KEY_ENHANCEMENT_PART,
            COLLECTION_SCOPE,
            part,
            ChoiceProvenance.USER_CHOSEN,
        )
        for part in part_ids
    )
    return RunPlan(
        plan_id="src0123456789abcdef0123x",
        report_id="0" * 32,
        source_artifacts=tuple(
            SourceArtifact(
                source_id=part, sha256=part, byte_count=1, media_path=Path("input") / part / "m"
            )
            for part in part_ids
        ),
        tools=(),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint="cfg" + "0" * 61,
        run_choices=RunPlanChoices.build(tuple(choices)),
    )


def _evidence(parts: tuple[str, ...]) -> ProjectionEvidence:
    """Per-Part enhanced subtitles for each named Part (VALID content)."""

    return ProjectionEvidence(
        part_subtitles={
            (part, PublicationBasis.ENHANCED): TimedArtifactEvidence(original=f"1\n{part}\n")
            for part in parts
        }
    )


def _completed(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
    return StageResult.completed()


def _composition(evidence: ProjectionEvidence) -> RunComposition:
    return RunComposition(
        executor=_completed, evidence=lambda: evidence, report_inputs=RunReportInputs
    )


def _write_plan(project_root: Path, plan: RunPlan) -> None:
    plan_dir = project_root / "plans" / plan.plan_id
    plan_dir.mkdir(parents=True)
    (plan_dir / "run-plan.json").write_text(json.dumps(plan.as_json(), indent=2), encoding="utf-8")


def _bundle_snapshot(bundle_dir: Path) -> dict[str, bytes]:
    return {
        path.relative_to(bundle_dir).as_posix(): path.read_bytes()
        for path in bundle_dir.rglob("*")
        if path.is_file()
    }


def _publish_source(project_root: Path, part_ids: tuple[str, ...]) -> tuple[RunLayout, RunPlan]:
    """Publish a source enhancement-mode bundle with per-Part content for reuse."""

    plan = _enhancement_plan(part_ids)
    _write_plan(project_root, plan)
    outcome = start_run(
        project_root,
        plan.plan_id,
        composition_factory=lambda layout, run_plan: _composition(_evidence(part_ids)),
        run_start=_NOW,
        probe=_FakeProbe(_ME, {_ME}),
        clock=_clock(),
        now=_NOW,
    )
    assert outcome.status is RunStatus.COMPLETE
    source_id = source_id_from_run_plan(plan)
    run_id = run_id_from_run_plan(plan, _NOW)
    return RunLayout(project_root, source_id, run_id), plan


def _improve(project_root: Path, source_run_id: str, scope: str, affected: tuple[str, ...]):
    # The improvement run re-enhances only the affected Parts.
    return start_improvement_run(
        project_root,
        source_run_id,
        scope,
        composition_factory=lambda layout, plan: _composition(_evidence(affected)),
        run_start=_LATER,
        probe=_FakeProbe(_ME, {_ME}),
        clock=_clock(),
        now=_LATER,
    )


# --- The happy path ---------------------------------------------------------


def test_improve_carries_forward_unaffected_part_and_publishes(tmp_path: Path) -> None:
    source_layout, source_plan = _publish_source(tmp_path, (_PART_A, _PART_B))
    before = _bundle_snapshot(source_layout.output_dir)
    assert f"parts/{_PART_A}/subtitles.enhanced.srt" in before

    outcome = _improve(tmp_path, source_layout.run_id, _PART_B, (_PART_B,))

    # A new plan and a new run id, both distinct from the source.
    assert outcome.status is RunStatus.COMPLETE
    assert outcome.publication is not None
    assert outcome.layout.run_id != source_layout.run_id
    assert outcome.layout.source_id == source_layout.source_id
    new_manifest = read_run_bundle_manifest(outcome.layout.output_dir)
    assert new_manifest.plan_id != source_plan.plan_id

    # The unaffected Part A carries forward with recorded source-run provenance.
    carried = {
        artifact.path: artifact
        for artifact in new_manifest.artifacts
        if artifact.provenance.get("carried_forward_from_run")
    }
    part_a_srt = f"parts/{_PART_A}/subtitles.enhanced.srt"
    assert part_a_srt in carried
    assert carried[part_a_srt].provenance["carried_forward_from_run"] == source_layout.run_id
    assert carried[part_a_srt].provenance["carried_forward_sha256"] == carried[part_a_srt].sha256
    # The affected Part B is re-projected, not carried forward.
    part_b_srt = f"parts/{_PART_B}/subtitles.enhanced.srt"
    assert new_manifest.artifacts  # sanity
    assert not any(
        a.path == part_b_srt and a.provenance.get("carried_forward_from_run")
        for a in new_manifest.artifacts
    )

    # The carried-forward file is byte-identical to the source and re-hashes clean.
    assert (outcome.layout.output_dir / part_a_srt).read_bytes() == before[part_a_srt]
    assert outcome.publication.verification.verified is True

    # The reports — not only the manifest — record the carried-forward source run
    # id and the artifact hash (criterion 3: manifest *and reports*).
    inventory = json.loads(
        (outcome.layout.output_dir / "run-inventory.json").read_text(encoding="utf-8")
    )
    carried_entries = [
        entry
        for entry in inventory["entries"]
        if entry["path"] == part_a_srt and entry["action"] == "published"
    ]
    assert len(carried_entries) == 1
    entry = carried_entries[0]
    assert source_layout.run_id in entry["purpose"]
    assert source_layout.run_id in entry["used_by"]
    assert entry["sha256"] == carried[part_a_srt].sha256
    processing_report = (outcome.layout.output_dir / "processing-report.md").read_text(
        encoding="utf-8"
    )
    assert source_layout.run_id in processing_report

    # The prior bundle is untouched; latest advances to the newer improvement run.
    assert _bundle_snapshot(source_layout.output_dir) == before
    pointer = read_latest_pointer(source_layout.latest_path)
    assert pointer is not None and pointer.run_id == outcome.layout.run_id


def test_building_the_plan_leaves_the_source_bundle_and_pointer_untouched(tmp_path: Path) -> None:
    from video_content_pipeline.improve import build_improvement_plan
    from video_content_pipeline.planning import load_run_plan

    source_layout, source_plan = _publish_source(tmp_path, (_PART_A, _PART_B))
    bundle_before = _bundle_snapshot(source_layout.output_dir)
    pointer_before = source_layout.latest_path.read_bytes()

    plan, affected = build_improvement_plan(
        load_run_plan(tmp_path / "plans" / source_plan.plan_id / "run-plan.json"),
        source_layout.run_id,
        _PART_B,
        tmp_path / "plans",
    )

    # Deriving and persisting the new plan touches neither the prior bundle nor
    # its latest pointer — only the standard publish may advance the pointer.
    assert plan.plan_id != source_plan.plan_id
    assert affected == frozenset({_PART_B})
    assert _bundle_snapshot(source_layout.output_dir) == bundle_before
    assert source_layout.latest_path.read_bytes() == pointer_before


def test_improve_all_reenhances_every_part_with_no_carry_forward(tmp_path: Path) -> None:
    source_layout, _ = _publish_source(tmp_path, (_PART_A, _PART_B))
    outcome = _improve(tmp_path, source_layout.run_id, "all", (_PART_A, _PART_B))

    assert outcome.status is RunStatus.COMPLETE
    manifest = read_run_bundle_manifest(outcome.layout.output_dir)
    assert not any(a.provenance.get("carried_forward_from_run") for a in manifest.artifacts)


# --- Reads only from a revalidated bundle -----------------------------------


def test_improve_refuses_a_tampered_source_bundle(tmp_path: Path) -> None:
    source_layout, _ = _publish_source(tmp_path, (_PART_A, _PART_B))
    tampered = source_layout.output_dir / f"parts/{_PART_A}/subtitles.enhanced.srt"
    tampered.write_text("tampered\n", encoding="utf-8")

    with pytest.raises(ImproveError) as caught:
        _improve(tmp_path, source_layout.run_id, _PART_B, (_PART_B,))
    assert caught.value.reason == "source_bundle_unverified"


def test_improve_unknown_source_run_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ImproveError) as caught:
        _improve(tmp_path, "20260816T090000Z-deadbeefdeadbeef", "all", (_PART_A,))
    assert caught.value.reason == "source_run_not_found"


# --- The CLI boundary -------------------------------------------------------


def test_improve_cli_publishes_and_reports_source_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source_layout, _ = _publish_source(tmp_path, (_PART_A, _PART_B))
    monkeypatch.setattr(cli, "assert_runtime_policy", lambda: None)
    monkeypatch.setattr(cli, "assert_project_venv", lambda: object())
    monkeypatch.setattr(cli, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(
        cli, "_composition_factory", lambda layout, plan: _composition(_evidence((_PART_B,)))
    )

    code = cli.main(["improve", "--from-run", source_layout.run_id, "--asr", _PART_B])
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["status"] == "ok"
    assert output["run_status"] == "complete"
    assert output["published"] is True
    assert output["source_run_id"] == source_layout.run_id
    assert output["run_id"] != source_layout.run_id


def test_improve_cli_unknown_run_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "assert_runtime_policy", lambda: None)
    monkeypatch.setattr(cli, "assert_project_venv", lambda: object())
    monkeypatch.setattr(cli, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(
        cli, "_composition_factory", lambda layout, plan: _composition(_evidence(()))
    )

    code = cli.main(["improve", "--from-run", "20260816T090000Z-deadbeefdeadbeef", "--asr", "all"])
    output = json.loads(capsys.readouterr().out)
    assert code == 2
    assert output["reason"] == "source_run_not_found"
