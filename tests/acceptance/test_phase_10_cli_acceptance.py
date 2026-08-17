"""Ticket 10: the five-branch CLI acceptance layer (Workstream E).

This is the named acceptance layer the plan's CLI list demands. It drives the
*production* orchestration surface — ``vcp run``/``status``/``pause``/``resume``/
``cancel``/``verify``/``inventory`` plus ``vcp improve`` — through the real
:func:`~video_content_pipeline.cli.main` entry point over the five synthetic
fixture branches, with ticket 08's real composition (real per-phase functions,
the identity-pinned ffmpeg/ffprobe, the real filesystem; only the model adapters
are controlled, and only for the subtitle-bearing branches). The CLI is the real
one throughout: argument parsing, run discovery, the production
``_composition_factory``, publication, and the JSON contract. Only the project
root and the two environment gates are stood in, exactly as the Phase 9
orchestration CLI contract test does.

Per branch the sequence ``plan → run → status → verify → inventory`` is green —
``run``/``status``/``verify``/``inventory`` exit 0 through the real CLI and the
published bundle hash-verifies. The confirmed plan is built through the plan
machinery (``create_plan_report``/``confirm_run_plan``) so it can carry the
branch's front-loaded run choices, which the ``vcp plan`` command surface does not
inject; that command surface itself (``plan`` → ``plan decode`` → ``plan
confirm``) is driven end to end once in
:func:`test_plan_command_confirms_a_plan_over_a_fixture`. The run's terminal
*status* is what the offline environment can honestly reach:

* ``subtitle-first`` and ``anomalous-subtitles`` carry a usable embedded subtitle
  track, so with the controlled audio/text adapters seeded they run to
  ``complete`` and publish VALID core content (per-Part subtitles, transcript,
  content report, segments); the latest pointer advances.
* ``full-asr``, ``multi-part`` and ``visual-text`` carry no usable subtitle track,
  so the subtitles stage hands off to ASR and the run proceeds to the first heavy
  stage whose model the offline boundary forbids (audio analysis), where it
  decision-pauses. The run publishes a clean, hash-verifiable ``incomplete``
  RunBundle carrying that required decision; with no partial content the latest
  pointer does not advance. That is the correct offline terminal for a
  model-dependent branch, and it proves the run reaches, executes, and publishes
  rather than failing at the subtitles stage.

The cross-branch orchestration commands are exercised once each (for budget), all
against real runs: ``pause``/``resume`` at a real unit boundary, ``cancel`` at a
real unit boundary, and ``vcp improve`` carrying an unaffected Part forward from a
genuinely published subtitle-bearing bundle. The 16 expert commands are out of
scope — their per-phase contract tests remain their contract.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
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
from video_content_pipeline import cli
from video_content_pipeline.external_tools import identify_external_tool
from video_content_pipeline.inspection import (
    PlanInspectionEvidence,
    capture_probe_documents,
    inspect_documents,
)
from video_content_pipeline.orchestration import RunLayout
from video_content_pipeline.planning import (
    PlanState,
    RunPlan,
    confirm_run_plan,
    create_plan_report,
    persist_plan_report,
    planning_configuration_fingerprint,
)
from video_content_pipeline.publication import read_latest_pointer
from video_content_pipeline.publication_projection import (
    ProjectionEvidence,
    PublicationBasis,
    TimedArtifactEvidence,
)
from video_content_pipeline.run_choices import (
    COLLECTION_SCOPE,
    KEY_ASR_MODE,
    KEY_DIARIZATION_CANDIDATE,
    KEY_VISUAL_TEXT_ALL,
    KEY_VISUAL_TEXT_ENABLED,
    STAGE_AUDIO_ANALYSIS,
    STAGE_RUN,
    STAGE_VISUAL_TEXT,
    AsrMode,
    ChoiceProvenance,
    RunChoice,
    RunPlanChoices,
)
from video_content_pipeline.run_composition import build_run_composition
from video_content_pipeline.run_control import ControlKind, request_control
from video_content_pipeline.run_loop import RunComposition, RunReportInputs
from video_content_pipeline.run_state import RunStatus, read_run_state
from video_content_pipeline.source import snapshot_local_source, validate_local_source_candidate
from video_content_pipeline.stage_dag import StageInvalidationKey, StageResult, StageUnit
from video_content_pipeline.subtitle_pipeline import SubtitleCandidateReport, process_subtitles

pytestmark = [pytest.mark.integration, pytest.mark.slow]

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: The published artifact kinds that are core *content* (plan §7), as opposed to
#: the always-present audit documents. A completed branch publishes these VALID.
_CORE_CONTENT_KINDS = frozenset({"subtitles", "transcript", "content_report", "segments"})


@pytest.fixture(scope="session")
def toolchain() -> FixtureToolchain:
    return resolve_fixture_toolchain(PROJECT_ROOT)


@pytest.fixture(scope="session")
def fixture_cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("phase-10-cli-acceptance-fixtures")


# --- Branch descriptors -----------------------------------------------------


@dataclass(frozen=True)
class _BranchCase:
    """One fixture branch and the published terminal it reaches offline."""

    branch_id: str
    recipe_ids: tuple[str, ...]
    subtitled: bool
    expected_status: RunStatus
    expects_content: bool
    latest_advances: bool


_BRANCHES: tuple[_BranchCase, ...] = (
    _BranchCase("subtitle-first", ("subtitle-first",), True, RunStatus.COMPLETE, True, True),
    _BranchCase(
        "anomalous-subtitles", ("anomalous-subtitles",), True, RunStatus.COMPLETE, True, True
    ),
    _BranchCase("full-asr", ("full-asr",), False, RunStatus.INCOMPLETE, False, False),
    _BranchCase("multi-part", ("multi-part",), False, RunStatus.INCOMPLETE, False, False),
    _BranchCase("visual-text", ("visual-text",), False, RunStatus.INCOMPLETE, False, False),
)


def _branch_choices(branch_id: str) -> RunPlanChoices:
    """The front-loaded choices a branch's plan carries (no missing-choice pause)."""

    if branch_id in ("subtitle-first", "anomalous-subtitles"):
        return _subtitle_first_choices()
    if branch_id in ("full-asr", "multi-part"):
        return RunPlanChoices.build(
            (
                _choice(STAGE_RUN, KEY_ASR_MODE, AsrMode.FULL_ASR.value),
                _choice(STAGE_RUN, KEY_VISUAL_TEXT_ENABLED, "false"),
                _choice(
                    STAGE_AUDIO_ANALYSIS,
                    KEY_DIARIZATION_CANDIDATE,
                    CONTROLLED_DIARIZATION_CANDIDATE,
                ),
            )
        )
    # visual-text: a video-only source; visual text enabled with a collection scope.
    return RunPlanChoices.build(
        (
            _choice(STAGE_RUN, KEY_ASR_MODE, AsrMode.SUBTITLE_FIRST.value),
            _choice(STAGE_RUN, KEY_VISUAL_TEXT_ENABLED, "true"),
            _choice(STAGE_VISUAL_TEXT, KEY_VISUAL_TEXT_ALL, "true"),
        )
    )


def _subtitle_first_choices() -> RunPlanChoices:
    return RunPlanChoices.build(
        (
            _choice(STAGE_RUN, KEY_ASR_MODE, AsrMode.SUBTITLE_FIRST.value),
            _choice(STAGE_RUN, KEY_VISUAL_TEXT_ENABLED, "false"),
            _choice(
                STAGE_AUDIO_ANALYSIS, KEY_DIARIZATION_CANDIDATE, CONTROLLED_DIARIZATION_CANDIDATE
            ),
        )
    )


def _choice(stage: str, key: str, value: str) -> RunChoice:
    return RunChoice(stage, key, COLLECTION_SCOPE, value, ChoiceProvenance.USER_CHOSEN)


# --- Project preparation (real plan machinery) ------------------------------


def _tool_path(root: Path, tool_id: str) -> Path:
    registry = json.loads((root / "config" / "tools.json").read_text(encoding="utf-8"))
    return Path(next(tool["path"] for tool in registry["tools"] if tool["id"] == tool_id))


def _fixtures(
    recipe_ids: Sequence[str], toolchain: FixtureToolchain, cache: Path
) -> tuple[Path, ...]:
    parts: list[Path] = []
    for recipe_id in recipe_ids:
        recipe = next(r for r in FIXTURE_RECIPES if r.fixture_id == recipe_id)
        parts.extend(generate_fixture(recipe, toolchain, cache).parts)
    return tuple(parts)


def _confirm_plan(
    root: Path, fixtures: Sequence[Path], choices: RunPlanChoices
) -> tuple[RunPlan, tuple[PlanInspectionEvidence, ...]]:
    """Build and confirm a real plan over ``fixtures`` (real ffprobe inspection)."""

    ffprobe = identify_external_tool("ffprobe", _tool_path(root, "ffprobe"))
    ffmpeg = identify_external_tool("ffmpeg", _tool_path(root, "ffmpeg"))
    artifacts = []
    evidence = []
    for fixture in fixtures:
        candidate = validate_local_source_candidate(fixture)
        artifact = snapshot_local_source(candidate, root / "input", origin_kind="synthetic_fixture")
        structural, coverage = capture_probe_documents(
            ffprobe, artifact, artifact.media_path.parent / "evidence"
        )
        artifacts.append(artifact)
        evidence.append(
            PlanInspectionEvidence.from_inspection(
                artifact.source_id, inspect_documents(structural, coverage)
            )
        )
    report = create_plan_report(
        state=PlanState.READY_FOR_CONFIRMATION,
        source_artifacts=tuple(artifacts),
        tools=(ffprobe, ffmpeg),
        planned_increment_bytes=0,
        configuration_fingerprint=planning_configuration_fingerprint(root),
        inspection_evidence=tuple(evidence),
        run_choices=choices,
    )
    persist_plan_report(report, root / "plans")
    return confirm_run_plan(report, root, root / "plans"), tuple(evidence)


def _prepare(
    root: Path,
    recipe_ids: Sequence[str],
    choices: RunPlanChoices,
    *,
    subtitled: bool,
    toolchain: FixtureToolchain,
    cache: Path,
) -> RunPlan:
    """Assemble a confirmed, adapter-seeded project root ready for ``vcp run``."""

    shutil.copytree(PROJECT_ROOT / "config", root / "config")
    plan, evidence = _confirm_plan(root, _fixtures(recipe_ids, toolchain, cache), choices)
    if subtitled:
        _seed_adapters(root, plan, evidence)
    return plan


def _seed_adapters(root: Path, plan: RunPlan, evidence: Sequence[PlanInspectionEvidence]) -> None:
    """Seed the content-derived audio/text adapters from a real subtitle pre-pass.

    The in-run subtitles stage reproduces this workspace byte for byte, so the
    controlled adapters the audio and text stages load are bound to the run's own
    real inputs (ADR 0037 lineage; the ticket-08 mechanism).
    """

    subtitles = process_subtitles(plan.plan_id, root)
    report = SubtitleCandidateReport.from_json(
        subtitles["report"], Path(subtitles["report"]["report_path"])
    )
    seed_offline_model_adapters(root, inspection_evidence=evidence, subtitle_report=report)


# --- Real CLI driving -------------------------------------------------------


def _configure_cli(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the real CLI at ``root`` with the production composition factory.

    Only the project root and the two environment gates are stood in — the
    ``_composition_factory`` stays production, so ``vcp run`` builds the real
    composition. Mirrors the Phase 9 orchestration CLI contract test's setup.
    """

    monkeypatch.setattr(cli, "assert_runtime_policy", lambda: None)
    monkeypatch.setattr(cli, "assert_project_venv", lambda: object())
    monkeypatch.setattr(cli, "_project_root", lambda: root)


def _invoke(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, object]]:
    code = cli.main(argv)
    return code, json.loads(capsys.readouterr().out)


def _manifest_artifacts(output_dir: Path) -> list[Mapping[str, object]]:
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    artifacts = manifest["artifacts"]
    assert isinstance(artifacts, list)
    return artifacts


def _tree_snapshot(directory: Path, *, base: Path) -> dict[str, bytes]:
    """Every file under ``directory`` as ``{path-relative-to-base: bytes}``."""

    if not directory.is_dir():
        return {}
    return {
        path.relative_to(base).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }


# --- Per-branch acceptance --------------------------------------------------


@pytest.mark.parametrize("case", _BRANCHES, ids=[case.branch_id for case in _BRANCHES])
def test_branch_runs_through_cli_to_a_published_bundle(
    case: _BranchCase,
    tmp_path: Path,
    toolchain: FixtureToolchain,
    fixture_cache: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``plan → run → status → verify → inventory`` is green for every branch.

    The confirmed plan is built through the real plan machinery (real ffprobe
    inspection, ``confirm_run_plan``); the run and every downstream command go
    through the real CLI over the production composition. Each branch reaches the
    published terminal the offline boundary allows, and the published bundle
    hash-verifies with a structurally valid inventory.
    """

    plan = _prepare(
        tmp_path,
        case.recipe_ids,
        _branch_choices(case.branch_id),
        subtitled=case.subtitled,
        toolchain=toolchain,
        cache=fixture_cache,
    )
    _configure_cli(tmp_path, monkeypatch)

    # vcp run: drives the production composition to its terminal and publishes.
    code, run_doc = _invoke(["run", "--plan", plan.plan_id, "--json"], capsys)
    assert code == 0, run_doc
    assert run_doc["run_status"] == case.expected_status.value, run_doc
    assert run_doc["published"] is True
    assert run_doc["verified"] is True
    assert run_doc["latest_advanced"] is case.latest_advances
    run_id = str(run_doc["run_id"])
    output_dir = Path(str(run_doc["output_dir"]))

    # vcp status: reports the run without mutating its persisted state.
    before = (tmp_path / "work" / run_doc["source_id"] / run_id / "run-state.json").read_bytes()
    code, status_doc = _invoke(["status", "--run", run_id, "--json"], capsys)
    assert code == 0
    assert status_doc["run"]["run_id"] == run_id
    assert status_doc["run"]["status"] == case.expected_status.value
    assert (
        tmp_path / "work" / run_doc["source_id"] / run_id / "run-state.json"
    ).read_bytes() == before

    # vcp verify: the hash layer plus the inventory-structure check are both green.
    code, verify_doc = _invoke(["verify", "--run", run_id, "--json"], capsys)
    assert code == 0
    assert verify_doc["verified"] is True
    assert verify_doc["hash_verified"] is True
    assert verify_doc["inventory_valid"] is True
    assert verify_doc["discrepancies"] == []

    # vcp inventory: renders the published inventory (plan §18.2 record shape).
    code, inventory_doc = _invoke(["inventory", "--run", run_id, "--json"], capsys)
    assert code == 0
    assert inventory_doc["inventory"]["schema_version"] == 1
    assert isinstance(inventory_doc["inventory"]["entries"], list)

    _assert_branch_content(case, run_doc, output_dir, tmp_path)


def _assert_branch_content(
    case: _BranchCase, run_doc: Mapping[str, object], output_dir: Path, root: Path
) -> None:
    """Assert the branch's published content and the standing latest-pointer rule."""

    artifacts = _manifest_artifacts(output_dir)
    if case.expects_content:
        core = [a for a in artifacts if a.get("kind") in _CORE_CONTENT_KINDS]
        valid_kinds = {a["kind"] for a in core if a["status"] == "valid"}
        assert _CORE_CONTENT_KINDS <= valid_kinds, valid_kinds
        # A real extracted analysis derivative proves ffmpeg ran inside the run.
        assert any((root / "work").rglob("*.wav")), "expected a real analysis derivative"
    else:
        # A model-dependent branch decision-pauses past the subtitles stage and
        # publishes the audit floor with no VALID content — proof it reached and
        # executed rather than failing at subtitles.
        assert run_doc["disposition"] == "decision_required", run_doc
        assert run_doc.get("required_decision"), run_doc
        content = [a for a in artifacts if a.get("kind") in _CORE_CONTENT_KINDS]
        assert all(a["status"] != "valid" for a in content), content
        # The standing guarantee: a run with no published partial content (a failed
        # or content-free decision pause) never advances the latest pointer.
        source_id = output_dir.parent.name
        pointer = read_latest_pointer(root / "outputs" / source_id / "latest.json")
        assert pointer is None or pointer.run_id != output_dir.name


# --- RunBundle processing-report provenance (Phase 12 ticket 05) -------------


def _section(report: str, header: str) -> str:
    """Return the lines of one processing-report section, header exclusive.

    Slices from ``header`` to the next ``## `` heading, so an assertion reads a
    single section's body without matching a value that happens to recur
    elsewhere in the report.
    """

    assert header in report, f"missing section {header!r}"
    tail = report.split(header, 1)[1]
    return tail.split("\n## ", 1)[0]


def test_processing_report_carries_full_provenance(
    tmp_path: Path,
    toolchain: FixtureToolchain,
    fixture_cache: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A completed offline golden run publishes a fully-provenanced report.

    Phase 12 ticket 05: the ``subtitle-first`` branch runs the controlled audio
    and text adapters to ``complete`` through the real CLI, and its published
    ``processing-report.md`` must carry non-empty models, tools, environment,
    parameters, and measured resource-usage sections — the provenance that binds a
    Coverage-ledger entry to the model stack that produced the outputs. Model
    entries are consistent with the (seeded) registry, and the resource figures
    are the run's own measurements, not placeholders.
    """

    plan = _prepare(
        tmp_path,
        ("subtitle-first",),
        _subtitle_first_choices(),
        subtitled=True,
        toolchain=toolchain,
        cache=fixture_cache,
    )
    _configure_cli(tmp_path, monkeypatch)

    code, run_doc = _invoke(["run", "--plan", plan.plan_id, "--json"], capsys)
    assert code == 0, run_doc
    assert run_doc["run_status"] == RunStatus.COMPLETE.value, run_doc
    output_dir = Path(str(run_doc["output_dir"]))
    report = (output_dir / "processing-report.md").read_text(encoding="utf-8")

    # Models: every controlled audio engine the run executed, described from the
    # registry (name/revision/sha256/size/purpose). The registry is the seeded
    # fixture registry, so the values assert consistency with it.
    models = _section(report, "## 模型")
    assert "未使用模型" not in models
    for candidate_id, asset in (
        ("controlled-vad", "a" * 64),
        ("controlled-alignment", "b" * 64),
        ("controlled-diarization", "c" * 64),
    ):
        assert f"offline/{candidate_id}" in models, models
        assert asset in models, models
    assert "revision `phase-10-fixture-r1`" in models
    assert "4096 字节" in models
    assert "Controlled offline" in models

    # Tools: the plan's pinned ffmpeg/ffprobe identities.
    tools = _section(report, "## 工具")
    assert "ffmpeg" in tools and "ffprobe" in tools

    # Environment: the interpreter identity that ran the pipeline.
    env = _section(report, "## 运行环境")
    assert "Python 版本：" in env
    assert "锁文件哈希：" in env
    assert "运行环境未记录" not in env

    # Parameters: the front-loaded run choices plus the configuration fingerprint.
    parameters = _section(report, "## 关键参数、提示词、语言与质量配置")
    assert "configuration_fingerprint：" in parameters
    assert f"{STAGE_RUN}.{KEY_ASR_MODE}：{AsrMode.SUBTITLE_FIRST.value}" in parameters
    assert "无关键参数记录" not in parameters

    # Resource usage: real measurements. Peak memory is the controlled adapter's
    # recorded 512-byte model-runtime peak; disk delta and elapsed are measured,
    # never the "未测量" placeholder.
    resources = _section(report, "## 实际耗时、峰值内存与磁盘变化")
    assert "峰值内存：512 字节" in resources
    assert "峰值内存：未测量" not in resources
    assert "磁盘变化：未测量" not in resources
    assert "实际耗时：未测量" not in resources


def test_processing_report_omits_models_when_none_executed(
    tmp_path: Path,
    toolchain: FixtureToolchain,
    fixture_cache: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A run that executed no model honestly omits it — no padding (ticket 05).

    The ``full-asr`` branch decision-pauses at audio analysis (its model is not
    acquired offline), so no capability stage ever executed a model. The published
    report's models section must say so rather than listing a registry model the
    run did not actually run.
    """

    plan = _prepare(
        tmp_path,
        ("full-asr",),
        _branch_choices("full-asr"),
        subtitled=False,
        toolchain=toolchain,
        cache=fixture_cache,
    )
    _configure_cli(tmp_path, monkeypatch)

    code, run_doc = _invoke(["run", "--plan", plan.plan_id, "--json"], capsys)
    assert code == 0, run_doc
    assert run_doc["run_status"] == RunStatus.INCOMPLETE.value, run_doc
    output_dir = Path(str(run_doc["output_dir"]))
    report = (output_dir / "processing-report.md").read_text(encoding="utf-8")

    assert "未使用模型。" in _section(report, "## 模型")
    # Tools and environment are still recorded — they are properties of the run,
    # not of a model that executed.
    assert "ffmpeg" in _section(report, "## 工具")
    assert "Python 版本：" in _section(report, "## 运行环境")


# --- vcp plan: the plan command surface -------------------------------------


def test_plan_command_confirms_a_plan_over_a_fixture(
    tmp_path: Path,
    toolchain: FixtureToolchain,
    fixture_cache: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The real ``vcp plan`` command surface: ``plan → plan decode → plan confirm``.

    The branch tests confirm plans through the plan machinery so they can carry
    front-loaded run choices (which the ``vcp plan`` CLI does not inject); this
    closes the sequence by driving the ``plan`` command itself end to end over the
    subtitle-first fixture — real intake, real ffprobe inspection, a real ffmpeg
    decode validation, and ``confirm_run_plan`` — through the CLI's own contract.
    """

    shutil.copytree(PROJECT_ROOT / "config", tmp_path / "config")
    (fixture,) = _fixtures(("subtitle-first",), toolchain, fixture_cache)
    _configure_cli(tmp_path, monkeypatch)

    code, awaiting = _invoke(["plan", str(fixture)], capsys)
    assert code == 0, awaiting
    assert awaiting["status"] == "awaiting_decode_confirmation"
    awaiting_id = str(awaiting["report"]["report_id"])

    code, ready = _invoke(["plan", "decode", awaiting_id], capsys)
    assert code == 0, ready
    assert ready["status"] == "ready_for_confirmation"
    ready_id = str(ready["report"]["report_id"])

    code, confirmed = _invoke(["plan", "confirm", ready_id], capsys)
    assert code == 0, confirmed
    assert confirmed["status"] == "confirmed"
    plan_id = str(confirmed["plan"]["plan_id"])
    assert (tmp_path / "plans" / plan_id / "run-plan.json").is_file()


# --- pause / resume at a real unit boundary ---------------------------------


def _real_composition_that_requests(
    kind: ControlKind, fired: list[bool]
) -> Callable[[RunLayout, RunPlan], RunComposition]:
    """A production-composition factory that requests a control at the first boundary.

    It builds the real composition and wraps only its executor: after the first
    unit runs, it records a real ``control/<kind>.json`` request (exactly what
    ``vcp pause``/``vcp cancel`` write) so the run loop honours it at that unit
    boundary. ``fired`` guards it to a single request, so a later ``vcp resume``
    rebuilds through this same factory without re-triggering the pause.
    """

    def factory(layout: RunLayout, plan: RunPlan) -> RunComposition:
        real = build_run_composition(layout, plan)
        real_executor = real.executor

        def executor(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
            result = real_executor(unit, key)
            if not fired:
                request_control(layout, kind)
                fired.append(True)
            return result

        return RunComposition(
            executor=executor, evidence=real.evidence, report_inputs=real.report_inputs
        )

    return factory


def test_pause_and_resume_at_a_unit_boundary(
    tmp_path: Path,
    toolchain: FixtureToolchain,
    fixture_cache: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``vcp run`` honours a pause at a real unit boundary; ``vcp resume`` finishes.

    The pause is requested from within the first executed unit (the real effect of
    ``vcp pause``), so the run loop pauses at that boundary without publishing;
    ``vcp resume`` then drives the real subtitle-first composition to a published,
    hash-verified ``complete`` bundle.
    """

    plan = _prepare(
        tmp_path,
        ("subtitle-first",),
        _subtitle_first_choices(),
        subtitled=True,
        toolchain=toolchain,
        cache=fixture_cache,
    )
    _configure_cli(tmp_path, monkeypatch)
    fired: list[bool] = []
    monkeypatch.setattr(
        cli, "_composition_factory", _real_composition_that_requests(ControlKind.PAUSE, fired)
    )

    # The run pauses cleanly at the boundary — paused runs never publish.
    code, run_doc = _invoke(["run", "--plan", plan.plan_id, "--json"], capsys)
    assert code == 0, run_doc
    assert run_doc["run_status"] == RunStatus.PAUSED.value
    assert run_doc["published"] is False
    run_id = str(run_doc["run_id"])
    outputs = tmp_path / "outputs"
    assert not outputs.exists() or not any(outputs.rglob("manifest.json"))
    assert (
        read_run_state(tmp_path / "work" / run_doc["source_id"] / run_id / "run-state.json").status
        is RunStatus.PAUSED
    )

    # vcp resume continues the same run to a published, verified terminal.
    code, resume_doc = _invoke(["resume", "--run", run_id, "--json"], capsys)
    assert code == 0, resume_doc
    assert resume_doc["run_status"] == RunStatus.COMPLETE.value
    assert resume_doc["published"] is True
    assert resume_doc["verified"] is True
    assert len(fired) == 1, "the boundary pause fired exactly once"


def test_cancel_at_a_unit_boundary_publishes(
    tmp_path: Path,
    toolchain: FixtureToolchain,
    fixture_cache: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``vcp run`` honours a cancel at a real unit boundary and still publishes.

    A cancel is a terminal transition that publishes a bundle (unlike pause); with
    no content produced before the boundary the published bundle is the audit floor
    and the latest pointer does not advance.
    """

    plan = _prepare(
        tmp_path,
        ("subtitle-first",),
        _subtitle_first_choices(),
        subtitled=True,
        toolchain=toolchain,
        cache=fixture_cache,
    )
    _configure_cli(tmp_path, monkeypatch)
    fired: list[bool] = []
    monkeypatch.setattr(
        cli, "_composition_factory", _real_composition_that_requests(ControlKind.CANCEL, fired)
    )

    code, run_doc = _invoke(["run", "--plan", plan.plan_id, "--json"], capsys)
    assert code == 0, run_doc
    assert run_doc["run_status"] == RunStatus.CANCELLED.value
    assert run_doc["published"] is True
    assert run_doc["verified"] is True
    assert run_doc["latest_advanced"] is False
    assert len(fired) == 1


def test_control_and_status_commands_never_write_outputs(
    tmp_path: Path,
    toolchain: FixtureToolchain,
    fixture_cache: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``status``/``verify``/``inventory``/``pause``/``cancel`` never publish.

    Run a real branch to a published terminal, then invoke every non-publication
    command and assert they leave ``outputs/`` and the run state byte-identical —
    ``pause``/``cancel`` write only their control request. The requests are never
    observed (the run is already terminal), so they cannot perturb the bundle.
    """

    plan = _prepare(
        tmp_path,
        ("subtitle-first",),
        _subtitle_first_choices(),
        subtitled=True,
        toolchain=toolchain,
        cache=fixture_cache,
    )
    _configure_cli(tmp_path, monkeypatch)
    _, run_doc = _invoke(["run", "--plan", plan.plan_id, "--json"], capsys)
    run_id = str(run_doc["run_id"])
    state_path = tmp_path / "work" / run_doc["source_id"] / run_id / "run-state.json"

    outputs_before = _tree_snapshot(tmp_path / "outputs", base=tmp_path)
    state_before = state_path.read_bytes()

    for argv, requested in (
        (["status", "--run", run_id, "--json"], None),
        (["verify", "--run", run_id, "--json"], None),
        (["inventory", "--run", run_id, "--json"], None),
        (["pause", "--run", run_id, "--json"], "pause"),
        (["cancel", "--run", run_id, "--json"], "cancel"),
    ):
        code, doc = _invoke(argv, capsys)
        assert code == 0, doc
        if requested is not None:
            assert doc["requested"] == requested
            assert Path(str(doc["control_request_path"])).is_file()

    after = _tree_snapshot(tmp_path / "outputs", base=tmp_path)
    assert after == outputs_before, "no non-publication command may write outputs/"
    assert state_path.read_bytes() == state_before, "no command may mutate run state"


# --- vcp improve: carry-forward against a real published bundle --------------


def _stub_enhancement_factory(
    affected: tuple[str, ...],
) -> Callable[[RunLayout, RunPlan], RunComposition]:
    """A stub improvement composition producing enhanced content for affected Parts.

    The improvement run's re-analysis is stubbed so the carried-forward mechanism
    is observable: the re-projection produces content only for the affected Part,
    leaving each unaffected Part's path to be filled from the *real* published
    source bundle (:func:`~video_content_pipeline.run_loop._merge_carried_forward`).
    A production improvement run would re-run the subtitle stage for every Part and
    so reproduce the unaffected Part in place, which would hide — not break — the
    carry-forward; this isolates it against the genuine published bytes.
    """

    evidence = ProjectionEvidence(
        part_subtitles={
            (part, PublicationBasis.ENHANCED): TimedArtifactEvidence(original=f"1\n{part}\n")
            for part in affected
        }
    )

    def factory(layout: RunLayout, plan: RunPlan) -> RunComposition:
        return RunComposition(
            executor=lambda unit, key: StageResult.completed(),
            evidence=lambda: evidence,
            report_inputs=RunReportInputs,
        )

    return factory


def test_improve_carries_an_unaffected_part_forward_from_a_published_bundle(
    tmp_path: Path,
    toolchain: FixtureToolchain,
    fixture_cache: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``vcp improve`` carries an unaffected Part forward from a real published bundle.

    A genuinely published two-Part subtitle-bearing bundle is produced by the real
    composition (``vcp run``). ``vcp improve --asr <part-a>`` then re-enhances only
    Part A; Part B's per-Part artifacts must be carried forward byte-identically
    from the published source bundle, tagged with the source run id, and the source
    bundle must be left untouched.
    """

    # A real, published two-Part subtitle-bearing source (both Parts VALID).
    plan = _prepare(
        tmp_path,
        ("subtitle-first", "anomalous-subtitles"),
        _subtitle_first_choices(),
        subtitled=True,
        toolchain=toolchain,
        cache=fixture_cache,
    )
    _configure_cli(tmp_path, monkeypatch)
    _, source_doc = _invoke(["run", "--plan", plan.plan_id, "--json"], capsys)
    assert source_doc["published"] is True and source_doc["verified"] is True
    source_run_id = str(source_doc["run_id"])
    source_dir = Path(str(source_doc["output_dir"]))
    source_snapshot = _tree_snapshot(source_dir, base=source_dir)

    part_a, part_b = (artifact.source_id for artifact in plan.source_artifacts)
    carried_paths = _valid_part_paths(source_dir, part_b)
    assert carried_paths, "the source must publish VALID content for the carried Part"

    # Improve Part A; the re-analysis is stubbed so carry-forward is observable.
    monkeypatch.setattr(cli, "_composition_factory", _stub_enhancement_factory((part_a,)))
    code, improve_doc = _invoke(
        ["improve", "--from-run", source_run_id, "--asr", part_a, "--json"], capsys
    )
    assert code == 0, improve_doc
    assert improve_doc["published"] is True
    assert improve_doc["verified"] is True
    assert improve_doc["source_run_id"] == source_run_id
    improve_dir = Path(str(improve_doc["output_dir"]))

    # Part B is carried forward byte-identically and tagged with the source run.
    by_path = {a["path"]: a for a in _manifest_artifacts(improve_dir)}
    for path in carried_paths:
        assert path in by_path, f"carried Part path {path} missing from improvement bundle"
        provenance = by_path[path].get("provenance")
        assert isinstance(provenance, Mapping)
        assert provenance.get("carried_forward_from_run") == source_run_id, by_path[path]
        assert (improve_dir / path).read_bytes() == source_snapshot[path]

    # Part A was re-projected, not carried.
    for path in _valid_part_paths(improve_dir, part_a):
        provenance = by_path[path].get("provenance")
        if isinstance(provenance, Mapping):
            assert "carried_forward_from_run" not in provenance, path

    # The source bundle is untouched.
    assert _tree_snapshot(source_dir, base=source_dir) == source_snapshot


def _valid_part_paths(bundle_dir: Path, part_id: str) -> tuple[str, ...]:
    """The VALID per-Part artifact paths for ``part_id`` in a published bundle."""

    prefix = f"parts/{part_id}/"
    return tuple(
        sorted(
            str(a["path"])
            for a in _manifest_artifacts(bundle_dir)
            if a["status"] == "valid" and str(a["path"]).startswith(prefix)
        )
    )
