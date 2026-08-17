"""Phase 12 ticket 06: pause / resume / cancel across Model runtime subprocess stages.

A run's model-bearing stages (audio analysis, transcription, ...) execute their
real engines out-of-process through the Model runtime subprocess (ADR 0055). A
control request is observed only at the boundary *between* stage units (ADR 0053),
never mid-subprocess, so a running child is never interrupted: the run pauses
cleanly after the current stage completes and checkpoints, and ``vcp resume``
starts a fresh process that adopts every completed stage and never re-runs it.

These exercises drive the run loop over a controlled composition — no model, no
media, no network — whose executor stands in for the subprocess-model stages and
counts its own invocations, so the boundary contract is proven without loading a
real engine. They complement the Phase 10 acceptance drills (pause/resume/cancel
over the real offline composition) with the ticket-06 assertion that a *completed
subprocess stage is not re-executed on resume*.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

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
    KEY_VISUAL_TEXT_ENABLED,
    STAGE_RUN,
    AsrMode,
    ChoiceProvenance,
    RunChoice,
    RunPlanChoices,
)
from video_content_pipeline.run_control import ControlDirective
from video_content_pipeline.run_loop import (
    RunComposition,
    execute_confirmed_run,
    resume_and_finalize,
)
from video_content_pipeline.run_state import RunStatus, read_run_state
from video_content_pipeline.source import SourceArtifact
from video_content_pipeline.stage_dag import (
    StageInvalidationKey,
    StageName,
    StageResult,
    StageRunDisposition,
    StageUnit,
)

_PLAN_ID = "plan0123456789abcdef0123"
_NOW = datetime(2026, 8, 16, 8, 45, 0, tzinfo=UTC)
_RESUMER = ProcessIdentity(pid=900, start_time="s900")

#: The stages that stand in for Model runtime subprocess stages in these drills.
_SUBPROCESS_STAGES = frozenset({StageName.AUDIO_ANALYSIS, StageName.TRANSCRIPTION})


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


def _plan() -> RunPlan:
    return RunPlan(
        plan_id=_PLAN_ID,
        report_id="0" * 32,
        source_artifacts=(
            SourceArtifact(
                source_id="a" * 64, sha256="a" * 64, byte_count=1, media_path=Path("input/a/m")
            ),
        ),
        tools=(),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint="cfg" + "0" * 61,
        run_choices=RunPlanChoices.build(
            (
                RunChoice(
                    STAGE_RUN, KEY_ASR_MODE, COLLECTION_SCOPE,
                    AsrMode.FULL_ASR.value, ChoiceProvenance.USER_CHOSEN,
                ),
                RunChoice(
                    STAGE_RUN, KEY_VISUAL_TEXT_ENABLED, COLLECTION_SCOPE,
                    "false", ChoiceProvenance.USER_CHOSEN,
                ),
            )
        ),
    )


def _layout(tmp_path: Path, plan: RunPlan) -> RunLayout:
    return initialize_run_workspace(
        RunLayout(tmp_path, source_id_from_run_plan(plan), run_id_from_run_plan(plan, _NOW))
    )


def _composition(
    executor: Callable[[StageUnit, StageInvalidationKey], StageResult],
) -> RunComposition:
    return RunComposition(
        executor=executor,
        evidence=lambda: ProjectionEvidence(
            content_report=PlainArtifactEvidence(content="# 报告\n")
        ),
    )


def _counting_executor(
    counts: dict[StageName, int], *, on_audio_done: Callable[[], None] = lambda: None
) -> Callable[[StageUnit, StageInvalidationKey], StageResult]:
    def executor(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
        counts[unit.stage] = counts.get(unit.stage, 0) + 1
        if unit.stage is StageName.AUDIO_ANALYSIS:
            on_audio_done()
        return StageResult.completed()

    return executor


def test_pause_after_a_subprocess_stage_resumes_without_re_running_it(tmp_path: Path) -> None:
    plan = _plan()
    layout = _layout(tmp_path, plan)

    # Pause at the boundary immediately after the audio (subprocess-model) stage
    # completes and checkpoints — never mid-subprocess.
    audio_done = {"flag": False}
    run_counts: dict[StageName, int] = {}

    def mark_audio_done() -> None:
        audio_done["flag"] = True

    paused = execute_confirmed_run(
        layout=layout,
        plan=plan,
        composition=_composition(
            _counting_executor(run_counts, on_audio_done=mark_audio_done)
        ),
        lock_path=heavy_task_lock_path(tmp_path),
        clock=_clock(),
        now=_NOW,
        on_boundary=lambda: (
            ControlDirective.PAUSE if audio_done["flag"] else ControlDirective.CONTINUE
        ),
    )
    assert paused.status is RunStatus.PAUSED
    assert paused.publication is None  # a paused run never publishes
    assert not layout.output_dir.exists()
    # The subprocess stages up to the boundary ran exactly once and checkpointed.
    assert run_counts[StageName.AUDIO_ANALYSIS] == 1
    assert StageName.TRANSCRIPTION not in run_counts  # never reached before the pause

    # Resume: a fresh process (fresh executor) drives the run to a published,
    # hash-verified terminal, and the completed subprocess stages are adopted —
    # their executor is never invoked again.
    resume_counts: dict[StageName, int] = {}
    outcome = resume_and_finalize(
        layout=layout,
        plan=plan,
        composition=_composition(_counting_executor(resume_counts)),
        lock_path=heavy_task_lock_path(tmp_path),
        probe=_FakeProbe(_RESUMER, {_RESUMER}),
        clock=_clock(),
        now=_NOW,
        on_boundary=lambda: ControlDirective.CONTINUE,
    )
    assert outcome.status is RunStatus.COMPLETE
    assert outcome.publication is not None
    assert read_run_state(layout.state_path).status is RunStatus.COMPLETE
    assert verify_published_bundle(layout.output_dir).verified is True
    # No completed stage is re-run on resume — the ticket's no-re-run guarantee.
    for stage in _SUBPROCESS_STAGES & set(run_counts):
        assert stage not in resume_counts, f"{stage} was re-executed on resume"
    assert resume_counts[StageName.TRANSCRIPTION] == 1  # the first not-yet-done stage runs once


def test_cancel_after_a_subprocess_stage_publishes(tmp_path: Path) -> None:
    plan = _plan()
    layout = _layout(tmp_path, plan)
    audio_done = {"flag": False}
    counts: dict[StageName, int] = {}

    def mark_audio_done() -> None:
        audio_done["flag"] = True

    outcome = execute_confirmed_run(
        layout=layout,
        plan=plan,
        composition=_composition(_counting_executor(counts, on_audio_done=mark_audio_done)),
        lock_path=heavy_task_lock_path(tmp_path),
        clock=_clock(),
        now=_NOW,
        on_boundary=lambda: (
            ControlDirective.CANCEL if audio_done["flag"] else ControlDirective.CONTINUE
        ),
    )
    # Cancel is a terminal transition that still publishes a bundle (unlike pause).
    assert outcome.disposition is StageRunDisposition.CANCELLED
    assert outcome.status is RunStatus.CANCELLED
    assert outcome.publication is not None
    assert read_run_state(layout.state_path).status is RunStatus.CANCELLED
    assert counts[StageName.AUDIO_ANALYSIS] == 1
