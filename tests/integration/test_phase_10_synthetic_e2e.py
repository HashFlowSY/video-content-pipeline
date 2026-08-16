"""Ticket 08: the first true offline end-to-end ``vcp run`` (Workstream C).

This drives the *production* run composition end to end over the ticket-03
subtitle-first synthetic fixture: the real per-phase functions, the real
identity-pinned ffmpeg/ffprobe, and the real filesystem, with deterministic
substitute model adapters (``tests/support/model_adapters``) as the only non-real
component. It proves the run reaches ``complete`` and publishes a hash-verified
RunBundle whose core content artifacts — per-Part and collection subtitles, the
source transcript, the content report, and the segments — are VALID, and that the
adapters inject no randomness (a second run publishes byte-identical content).

Only model adapters are faked; production gains no test mode. The plan is built
through the real inspection/confirmation path (real ffprobe), the subtitle,
audio, and text stages run for real, and audio analysis extracts a real analysis
derivative with the pinned ffmpeg. The one seam is the file-based controlled
adapter (ADR 0037): the audio registry candidates and the text-analysis contracts
the per-phase functions load, every bound value derived from this run's own real
inputs so the adapters are content-derived and deterministic.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.support.model_adapters import (
    CONTROLLED_DIARIZATION_CANDIDATE,
    seed_offline_model_adapters,
)
from tests.support.synthetic_fixtures import (
    FIXTURE_RECIPES,
    FixtureToolchain,
    generate_fixture,
    resolve_fixture_toolchain,
)
from video_content_pipeline.external_tools import identify_external_tool
from video_content_pipeline.heavy_task_lock import heavy_task_lock_path
from video_content_pipeline.inspection import (
    PlanInspectionEvidence,
    capture_probe_documents,
    inspect_documents,
)
from video_content_pipeline.orchestration import (
    RunLayout,
    initialize_run_workspace,
    run_id_from_run_plan,
    source_id_from_run_plan,
)
from video_content_pipeline.planning import (
    PlanState,
    RunPlan,
    confirm_run_plan,
    create_plan_report,
    persist_plan_report,
    planning_configuration_fingerprint,
)
from video_content_pipeline.publication import verify_published_bundle
from video_content_pipeline.run_choices import (
    COLLECTION_SCOPE,
    KEY_ASR_MODE,
    KEY_DIARIZATION_CANDIDATE,
    KEY_VISUAL_TEXT_ENABLED,
    STAGE_AUDIO_ANALYSIS,
    STAGE_RUN,
    AsrMode,
    ChoiceProvenance,
    RunChoice,
    RunPlanChoices,
)
from video_content_pipeline.run_composition import build_run_composition
from video_content_pipeline.run_loop import RunOutcome, execute_confirmed_run
from video_content_pipeline.run_state import RunStatus
from video_content_pipeline.source import snapshot_local_source, validate_local_source_candidate
from video_content_pipeline.subtitle_pipeline import SubtitleCandidateReport, process_subtitles

pytestmark = [pytest.mark.integration, pytest.mark.slow]

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: A fixed run start so the run identity and audit timestamps are stable.
_NOW = datetime(2026, 8, 16, 12, 0, 0, tzinfo=UTC)

#: The published artifact kinds that are core *content* (plan §7), as opposed to
#: the always-present audit documents. These are what ticket 08 requires VALID.
_CORE_CONTENT_KINDS = frozenset({"subtitles", "transcript", "content_report", "segments"})


@pytest.fixture(scope="session")
def toolchain() -> FixtureToolchain:
    return resolve_fixture_toolchain(PROJECT_ROOT)


@pytest.fixture(scope="session")
def fixture_cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("phase-10-e2e-fixtures")


def _subtitle_first_fixture(toolchain: FixtureToolchain, fixture_cache: Path) -> Path:
    recipe = next(r for r in FIXTURE_RECIPES if r.fixture_id == "subtitle-first")
    return generate_fixture(recipe, toolchain, fixture_cache).parts[0]


def _confirmed_plan(
    root: Path, fixture: Path
) -> tuple[RunPlan, tuple[PlanInspectionEvidence, ...]]:
    """Build and confirm a real subtitle-first plan over ``fixture`` (real ffprobe)."""

    candidate = validate_local_source_candidate(fixture)
    artifact = snapshot_local_source(candidate, root / "input", origin_kind="synthetic_fixture")
    ffprobe = identify_external_tool("ffprobe", _tool_path(root, "ffprobe"))
    ffmpeg = identify_external_tool("ffmpeg", _tool_path(root, "ffmpeg"))
    structural, coverage = capture_probe_documents(
        ffprobe, artifact, artifact.media_path.parent / "evidence"
    )
    evidence = PlanInspectionEvidence.from_inspection(
        artifact.source_id, inspect_documents(structural, coverage)
    )
    choices = RunPlanChoices.build(
        (
            RunChoice(
                STAGE_RUN, KEY_ASR_MODE, COLLECTION_SCOPE,
                AsrMode.SUBTITLE_FIRST.value, ChoiceProvenance.USER_CHOSEN,
            ),
            RunChoice(
                STAGE_RUN, KEY_VISUAL_TEXT_ENABLED, COLLECTION_SCOPE,
                "false", ChoiceProvenance.USER_CHOSEN,
            ),
            RunChoice(
                STAGE_AUDIO_ANALYSIS, KEY_DIARIZATION_CANDIDATE, COLLECTION_SCOPE,
                CONTROLLED_DIARIZATION_CANDIDATE, ChoiceProvenance.USER_CHOSEN,
            ),
        )
    )
    report = create_plan_report(
        state=PlanState.READY_FOR_CONFIRMATION,
        source_artifacts=(artifact,),
        tools=(ffprobe, ffmpeg),
        planned_increment_bytes=0,
        configuration_fingerprint=planning_configuration_fingerprint(root),
        inspection_evidence=(evidence,),
        run_choices=choices,
    )
    persist_plan_report(report, root / "plans")
    return confirm_run_plan(report, root, root / "plans"), (evidence,)


def _tool_path(root: Path, tool_id: str) -> Path:
    registry = json.loads((root / "config" / "tools.json").read_text(encoding="utf-8"))
    return Path(next(tool["path"] for tool in registry["tools"] if tool["id"] == tool_id))


def _prepare_project(root: Path, fixture: Path) -> RunPlan:
    """Assemble a confirmed, adapter-seeded project root ready for ``vcp run``."""

    shutil.copytree(PROJECT_ROOT / "config", root / "config")
    plan, evidence = _confirmed_plan(root, fixture)
    # A pre-pass produces the subtitle workspace the content-derived adapters bind
    # against; the in-run subtitles stage reproduces it byte for byte.
    subtitles = process_subtitles(plan.plan_id, root)
    report = SubtitleCandidateReport.from_json(
        subtitles["report"], Path(subtitles["report"]["report_path"])
    )
    seed_offline_model_adapters(root, inspection_evidence=evidence, subtitle_report=report)
    return plan


def _run(root: Path, plan: RunPlan) -> tuple[RunOutcome, RunLayout]:
    layout = initialize_run_workspace(
        RunLayout(root, source_id_from_run_plan(plan), run_id_from_run_plan(plan, _NOW))
    )
    outcome = execute_confirmed_run(
        layout=layout,
        plan=plan,
        composition=build_run_composition(layout, plan),
        lock_path=heavy_task_lock_path(root),
        now=_NOW,
    )
    return outcome, layout


def _manifest_artifacts(layout: RunLayout) -> list[Mapping[str, object]]:
    manifest = json.loads((layout.output_dir / "manifest.json").read_text(encoding="utf-8"))
    artifacts = manifest["artifacts"]
    assert isinstance(artifacts, list)
    return artifacts


def test_subtitle_first_runs_end_to_end_and_verifies(
    tmp_path: Path, toolchain: FixtureToolchain, fixture_cache: Path
) -> None:
    fixture = _subtitle_first_fixture(toolchain, fixture_cache)
    plan = _prepare_project(tmp_path, fixture)

    outcome, layout = _run(tmp_path, plan)

    # The first real end-to-end run reaches a clean terminal and publishes.
    assert outcome.status is RunStatus.COMPLETE, outcome.failure_reason
    assert outcome.publication is not None
    assert outcome.publication.latest_advanced is True

    # The published bundle hash-verifies with no discrepancies (vcp verify green).
    verification = verify_published_bundle(layout.output_dir)
    assert verification.verified is True
    assert verification.discrepancies == ()

    # Every core content artifact the mode publishes is VALID (present + hashed).
    artifacts = _manifest_artifacts(layout)
    core = [a for a in artifacts if a.get("kind") in _CORE_CONTENT_KINDS]
    assert core, "expected core content artifacts in the manifest"
    assert all(a["status"] == "valid" for a in core), core
    # The four required families are all represented and valid.
    valid_kinds = {a["kind"] for a in core if a["status"] == "valid"}
    assert _CORE_CONTENT_KINDS <= valid_kinds, valid_kinds
    # The collection subtitles/transcript and per-Part subtitles all landed.
    paths = {a["path"] for a in core}
    assert "transcript.source.md" in paths
    assert "subtitles.source.srt" in paths
    assert any(p.startswith("parts/") for p in paths)

    # Real ffmpeg ran inside the run: audio analysis extracted a real derivative.
    assert any((tmp_path / "work").rglob("*.wav")), "expected a real extracted analysis derivative"


def test_adapters_are_deterministic_double_run(
    tmp_path: Path, toolchain: FixtureToolchain, fixture_cache: Path
) -> None:
    fixture = _subtitle_first_fixture(toolchain, fixture_cache)

    def content_digest(root: Path) -> dict[str, object]:
        plan = _prepare_project(root, fixture)
        _outcome, layout = _run(root, plan)
        return {
            str(a["path"]): a["sha256"]
            for a in _manifest_artifacts(layout)
            if a.get("kind") in _CORE_CONTENT_KINDS
        }

    first = content_digest(tmp_path / "run-a")
    second = content_digest(tmp_path / "run-b")

    # Same fixture bytes through the same content-derived adapters must publish
    # byte-identical core artifacts — the adapters add no randomness.
    assert first == second
    assert first  # and there is real content to compare
