"""Ticket 09 (tier 1): representative per-stage fault injection (Workstream D).

Ticket 07's matrix exhausts the *persistence* layer with a trivially completing
executor; this file exhausts the complementary axis — the *stage* layer. For every
stage in the DAG it injects a fault through the executor seam (the same seam the
production composition fills) and pins the stage-scoped consequence the run loop is
contracted to produce:

* A raised exception from any stage aborts the whole run into a published,
  hash-verifiable ``failed`` Minimal RunBundle (:func:`_fail_and_publish`), with the
  exception's reason carried on the outcome — proven for *every* DAG stage.
* An explicit per-Part ``FAILED`` result collapses only that Part's own downstream
  units to ``blocked`` while sibling Parts finish, and the run classifies
  ``incomplete`` (partial results still publish).
* An explicit ``FAILED`` at the single collection-level stage blocks every Part and
  the run classifies ``failed`` (nothing usable was produced).
* A recorded gate ``warning`` on an otherwise-clean pass classifies the run
  ``complete_with_warnings``.

Everything runs in process against the real
:func:`~video_content_pipeline.run_loop.execute_confirmed_run`, real
``durable_io``, and the real publication path; only the executor and the gathered
report inputs are controlled. No model, media, or network.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from video_content_pipeline.heavy_task_lock import ProcessIdentity, heavy_task_lock_path
from video_content_pipeline.orchestration import (
    RunLayout,
    initialize_run_workspace,
    run_id_from_run_plan,
    source_id_from_run_plan,
)
from video_content_pipeline.planning import RunPlan, calculate_disk_headroom
from video_content_pipeline.publication import verify_published_bundle
from video_content_pipeline.publication_projection import (
    PlainArtifactEvidence,
    ProjectionEvidence,
)
from video_content_pipeline.run_choices import (
    COLLECTION_SCOPE,
    KEY_ASR_MODE,
    KEY_ENHANCEMENT_PART,
    KEY_VISUAL_TEXT_ALL,
    KEY_VISUAL_TEXT_ENABLED,
    STAGE_ENHANCEMENT,
    STAGE_RUN,
    STAGE_VISUAL_TEXT,
    AsrMode,
    ChoiceProvenance,
    RunChoice,
    RunPlanChoices,
)
from video_content_pipeline.run_loop import (
    RunComposition,
    RunOutcome,
    RunReportInputs,
    execute_confirmed_run,
)
from video_content_pipeline.run_reports import GateOutcome, GateStatus, StageGateReport
from video_content_pipeline.run_state import RunStatus, read_run_state
from video_content_pipeline.source import SourceArtifact
from video_content_pipeline.stage_dag import (
    StageInvalidationKey,
    StageName,
    StageResult,
    StageScope,
    StageUnit,
    UnitStatus,
    plan_stage_units,
    read_recorded_units,
    stage_scope,
)

pytestmark = [pytest.mark.integration]

_PLAN_ID = "plan0123456789abcdef0123"
_CONFIG = "cfg" + "0" * 61
_NOW = datetime(2026, 8, 16, 9, 15, 0, tzinfo=UTC)
_RUNNER = ProcessIdentity(pid=700, start_time="s700")

#: Two distinct Part source-ids, so per-Part isolation has a sibling to spare.
_PART_A = "a" * 64
_PART_B = "b" * 64

#: The per-Part stages, split by the ASR mode that selects each. TRANSCRIPTION and
#: ENHANCEMENT are mutually exclusive; VISUAL_TEXT only appears when enabled — so
#: covering all seven stages needs both a transcription plan and an enhancement one.
_TRANSCRIBE_PART_STAGES = (
    StageName.SUBTITLES,
    StageName.AUDIO_ANALYSIS,
    StageName.TRANSCRIPTION,
    StageName.TEXT_ANALYSIS,
    StageName.VISUAL_TEXT,
)
_ENHANCE_ONLY_PART_STAGES = (StageName.ENHANCEMENT,)


# --- Offline harness (no lock contention: a fixed live probe) ----------------


class _FakeProbe:
    def __init__(self, identity: ProcessIdentity) -> None:
        self._identity = identity

    def identify(self) -> ProcessIdentity:
        return self._identity

    def is_running(self, identity: ProcessIdentity) -> bool:
        return identity == self._identity


def _clock() -> Callable[[], datetime]:
    step = {"n": 0}

    def tick() -> datetime:
        moment = datetime(2026, 8, 16, 9, 0, step["n"] % 60, tzinfo=UTC)
        step["n"] += 1
        return moment

    return tick


def _plan(*, mode: AsrMode, visual_text: bool, parts: tuple[str, ...]) -> RunPlan:
    selections = [
        RunChoice(
            stage=STAGE_RUN,
            key=KEY_ASR_MODE,
            scope=COLLECTION_SCOPE,
            value=mode.value,
            provenance=ChoiceProvenance.USER_CHOSEN,
        ),
        RunChoice(
            stage=STAGE_RUN,
            key=KEY_VISUAL_TEXT_ENABLED,
            scope=COLLECTION_SCOPE,
            value="true" if visual_text else "false",
            provenance=ChoiceProvenance.USER_CHOSEN,
        ),
    ]
    # Front-loaded scope choices each mode genuinely needs (else the run decision-
    # pauses at ``incomplete`` before any stage executes — see missing_required_choices).
    if visual_text:
        selections.append(
            RunChoice(
                stage=STAGE_VISUAL_TEXT,
                key=KEY_VISUAL_TEXT_ALL,
                scope=COLLECTION_SCOPE,
                value="true",
                provenance=ChoiceProvenance.USER_CHOSEN,
            )
        )
    if mode is AsrMode.ENHANCEMENT:
        selections.append(
            RunChoice(
                stage=STAGE_ENHANCEMENT,
                key=KEY_ENHANCEMENT_PART,
                scope=COLLECTION_SCOPE,
                value="true",
                provenance=ChoiceProvenance.USER_CHOSEN,
            )
        )
    choices = RunPlanChoices.build(tuple(selections))
    return RunPlan(
        plan_id=_PLAN_ID,
        report_id="0" * 32,
        source_artifacts=tuple(
            SourceArtifact(
                source_id=part,
                sha256=part,
                byte_count=1,
                media_path=Path("input") / part / "media",
            )
            for part in parts
        ),
        tools=(),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint=_CONFIG,
        run_choices=choices,
    )


def _layout(root: Path, plan: RunPlan) -> RunLayout:
    return initialize_run_workspace(
        RunLayout(root, source_id_from_run_plan(plan), run_id_from_run_plan(plan, _NOW))
    )


def _evidence() -> ProjectionEvidence:
    """Evidence yielding one VALID published content artifact, so a clean run
    publishes a real bundle rather than only the audit-document floor."""

    return ProjectionEvidence(content_report=PlainArtifactEvidence(content="# 内容报告\n"))


def _composition(
    executor: Callable[[StageUnit, StageInvalidationKey], StageResult],
    *,
    report_inputs: RunReportInputs | None = None,
) -> RunComposition:
    inputs = report_inputs if report_inputs is not None else RunReportInputs()
    evidence = _evidence()
    return RunComposition(
        executor=executor, evidence=lambda: evidence, report_inputs=lambda: inputs
    )


def _run(layout: RunLayout, plan: RunPlan, composition: RunComposition) -> RunOutcome:
    return execute_confirmed_run(
        layout=layout,
        plan=plan,
        composition=composition,
        lock_path=heavy_task_lock_path(layout.project_root),
        probe=_FakeProbe(_RUNNER),
        clock=_clock(),
        now=_NOW,
    )


def _recorded(layout: RunLayout) -> dict[StageUnit, UnitStatus]:
    recorded = read_recorded_units(read_run_state(layout.state_path))
    return {unit: record.status for unit, record in recorded.items()}


class _InjectedStageFault(RuntimeError):
    """A reason-carrying stage exception, mirroring the codebase's error idiom.

    The run loop reads ``error.reason`` (:func:`run_loop._fail_and_publish`) to
    record the failure, so a domain-style exception with a ``reason`` attribute is
    what a real stage raises; a bare ``RuntimeError`` would surface its class name.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _raise_at(target: StageUnit) -> Callable[[StageUnit, StageInvalidationKey], StageResult]:
    """An executor that completes every unit but raises a stage error at ``target``."""

    def executor(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
        if unit == target:
            raise _InjectedStageFault("injected_stage_fault")
        return StageResult.completed()

    return executor


def _predecessors_of(plan: RunPlan, target: StageUnit) -> list[StageUnit]:
    """The units the DAG runs strictly before ``target`` in execution order."""

    units = plan_stage_units(plan)
    return list(units[: units.index(target)])


def _fail_at(target: StageUnit) -> Callable[[StageUnit, StageInvalidationKey], StageResult]:
    """An executor that completes every unit but returns FAILED at ``target``."""

    def executor(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
        if unit == target:
            return StageResult.failed({"reason": "injected_part_failure"})
        return StageResult.completed()

    return executor


def _downstream_of(plan: RunPlan, target: StageUnit) -> set[StageUnit]:
    """The units the DAG would run strictly after ``target`` within its own Part."""

    units = plan_stage_units(plan)
    index = units.index(target)
    return {
        unit for unit in units[index + 1 :] if not unit.is_collection and unit.scope == target.scope
    }


# --- Every DAG stage: a raised exception aborts into a published failed bundle -


def _stage_plans() -> list[tuple[StageName, RunPlan]]:
    """One (stage, plan) pair per DAG stage, choosing a plan that includes it."""

    transcribe = _plan(mode=AsrMode.SUBTITLE_FIRST, visual_text=True, parts=(_PART_A,))
    enhance = _plan(mode=AsrMode.ENHANCEMENT, visual_text=False, parts=(_PART_A,))
    pairs: list[tuple[StageName, RunPlan]] = [
        (unit.stage, transcribe) for unit in plan_stage_units(transcribe)
    ]
    pairs.append((StageName.ENHANCEMENT, enhance))
    return pairs


def _stage_plan_ids() -> list[str]:
    return [stage.value for stage, _ in _stage_plans()]


@pytest.mark.parametrize("stage,plan", _stage_plans(), ids=_stage_plan_ids())
def test_stage_exception_fails_run_and_publishes(
    tmp_path: Path, stage: StageName, plan: RunPlan
) -> None:
    """A raised exception at *any* stage aborts the run into a published, verifiable
    ``failed`` bundle whose outcome carries the stage's reason — every DAG stage."""

    scope = COLLECTION_SCOPE if stage_scope(stage) is StageScope.COLLECTION else _PART_A
    target = StageUnit(stage, scope)
    layout = _layout(tmp_path, plan)

    outcome = _run(layout, plan, _composition(_raise_at(target)))

    assert outcome.status is RunStatus.FAILED
    assert outcome.failure_reason == "injected_stage_fault"
    # The failed run still published an auditable Minimal RunBundle that verifies.
    assert outcome.publication is not None
    assert verify_published_bundle(layout.output_dir).verified is True
    assert read_run_state(layout.state_path).status is RunStatus.FAILED

    # The abort is anchored to *this* stage: a raised exception leaves no checkpoint
    # for the target unit, while every predecessor completed durably before it — so
    # the crash fired at ``target`` and neither earlier nor later.
    recorded = _recorded(layout)
    assert recorded.get(target) is not UnitStatus.COMPLETED
    for predecessor in _predecessors_of(plan, target):
        assert recorded[predecessor] is UnitStatus.COMPLETED


# --- Per-Part FAILED isolates that Part's downstream as blocked ---------------


@pytest.mark.parametrize(
    "stage",
    _TRANSCRIBE_PART_STAGES + _ENHANCE_ONLY_PART_STAGES,
    ids=lambda stage: stage.value,
)
def test_per_part_failure_isolates_downstream(tmp_path: Path, stage: StageName) -> None:
    """A per-Part ``FAILED`` blocks only that Part's own later units; the sibling
    Part completes and the run classifies ``incomplete`` with a published bundle."""

    if stage is StageName.ENHANCEMENT:
        plan = _plan(mode=AsrMode.ENHANCEMENT, visual_text=False, parts=(_PART_A, _PART_B))
    else:
        plan = _plan(mode=AsrMode.SUBTITLE_FIRST, visual_text=True, parts=(_PART_A, _PART_B))

    target = StageUnit(stage, _PART_A)
    layout = _layout(tmp_path, plan)

    outcome = _run(layout, plan, _composition(_fail_at(target)))

    # Some Parts succeeded, some failed → the run is incomplete, and still publishes.
    assert outcome.status is RunStatus.INCOMPLETE
    assert outcome.publication is not None
    assert verify_published_bundle(layout.output_dir).verified is True

    recorded = _recorded(layout)
    downstream = _downstream_of(plan, target)
    assert recorded[target] is UnitStatus.FAILED
    # The failed Part's own downstream is blocked, never executed.
    assert all(recorded[unit] is UnitStatus.BLOCKED for unit in downstream)
    # Every unit of the sibling Part completed — isolation did not leak across Parts.
    sibling = {unit for unit in plan_stage_units(plan) if unit.scope == _PART_B}
    assert sibling and all(recorded[unit] is UnitStatus.COMPLETED for unit in sibling)


# --- Collection-stage FAILED fails the whole run -----------------------------


def test_collection_failure_fails_whole_run(tmp_path: Path) -> None:
    """A ``FAILED`` at the single collection-level stage blocks every Part and the
    run classifies ``failed`` — a failed bundle is still published and verifies."""

    plan = _plan(mode=AsrMode.SUBTITLE_FIRST, visual_text=True, parts=(_PART_A, _PART_B))
    target = StageUnit(StageName.SOURCE_REVALIDATION, COLLECTION_SCOPE)
    assert target.is_collection
    layout = _layout(tmp_path, plan)

    outcome = _run(layout, plan, _composition(_fail_at(target)))

    assert outcome.status is RunStatus.FAILED
    assert outcome.publication is not None
    assert verify_published_bundle(layout.output_dir).verified is True

    recorded = _recorded(layout)
    assert recorded[target] is UnitStatus.FAILED
    # Every per-Part unit was blocked by the collection failure; none executed.
    part_units = [unit for unit in plan_stage_units(plan) if not unit.is_collection]
    assert part_units and all(recorded[unit] is UnitStatus.BLOCKED for unit in part_units)


# --- A recorded gate warning propagates to classification --------------------


def test_gate_warning_propagates_to_classification(tmp_path: Path) -> None:
    """A clean pass carrying a recorded gate ``warning`` classifies the run
    ``complete_with_warnings`` (plan §12) and publishes a verifiable bundle."""

    plan = _plan(mode=AsrMode.SUBTITLE_FIRST, visual_text=False, parts=(_PART_A,))
    layout = _layout(tmp_path, plan)
    report_inputs = RunReportInputs(
        stage_reports=(
            StageGateReport(
                stage=StageName.SUBTITLES.value,
                scope=_PART_A,
                outcomes=(
                    GateOutcome(
                        gate="subtitle_coverage",
                        status=GateStatus.WARNING,
                        detail="coverage below target",
                    ),
                ),
            ),
        )
    )

    def complete(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
        return StageResult.completed()

    outcome = _run(layout, plan, _composition(complete, report_inputs=report_inputs))

    assert outcome.status is RunStatus.COMPLETE_WITH_WARNINGS
    assert outcome.publication is not None
    assert verify_published_bundle(layout.output_dir).verified is True
    recorded = _recorded(layout)
    assert all(status is UnitStatus.COMPLETED for status in recorded.values())
