"""Ticket 09 (tier 2): a subprocess run driver that blocks at a chosen moment.

The deterministic fault matrix (ticket 07) freezes writes *in process*; these are
the complementary *real power-loss* spot checks. This module is executed as a
separate process (``python -m tests.support.kill_harness run ...``) so a parent
test can send it a genuine ``SIGKILL`` — not a simulated wedged state — and then
prove crash recovery through the same functions the CLI's ``status``/``resume``
commands call.

The driver runs the *real* :func:`~video_content_pipeline.run_loop.execute_confirmed_run`
over the real ``durable_io`` and the real
:class:`~video_content_pipeline.heavy_task_lock.SystemProcessProbe`, so the on-disk
run state and the heavy-task lock it leaves behind carry this process's real
identity — exactly what a resuming process must detect as dead. Only the executor
is controlled (a trivially completing one; stage internals are ticket 08's proof),
and it blocks forever at one deterministic point:

* ``--block stage`` blocks inside the executor at :data:`STAGE_BLOCK_INDEX`, so the
  run dies *mid-stage* with the run state ``running`` and the earlier units already
  checkpointed.
* ``--block publish`` blocks at the finalization boundary — after every unit is
  checkpointed but *before* the terminal-status transition is committed — so the
  run dies inside the publish window while the state is still ``running`` (the only
  publish-window crash a resume can still recover, per ticket 07's boundary note).

Both write a ``ready`` marker just before blocking; the parent polls for it, then
kills. The run identity is fixed by the constants below so the parent reconstructs
the identical plan and layout to resume.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from video_content_pipeline import run_loop as run_loop_module
from video_content_pipeline.heavy_task_lock import heavy_task_lock_path
from video_content_pipeline.orchestration import (
    RunLayout,
    initialize_run_workspace,
    run_id_from_run_plan,
    source_id_from_run_plan,
)
from video_content_pipeline.planning import RunPlan, calculate_disk_headroom
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
from video_content_pipeline.run_loop import (
    RunComposition,
    RunReportInputs,
    execute_confirmed_run,
)
from video_content_pipeline.source import SourceArtifact
from video_content_pipeline.stage_dag import (
    StageInvalidationKey,
    StageResult,
    StageUnit,
    plan_stage_units,
)

#: A fixed run identity so the parent test rebuilds the identical plan and layout.
PLAN_ID = "plan0123456789abcdef0123"
CONFIG_FINGERPRINT = "cfg" + "0" * 61
RUN_START = datetime(2026, 8, 16, 10, 0, 0, tzinfo=UTC)
PART = "c" * 64

#: The unit index the ``stage`` block halts at. The subtitle-first single-Part DAG
#: is [source_revalidation, subtitles, audio_analysis, transcription, text_analysis];
#: blocking at index 2 leaves the first two units durably checkpointed, so a resume
#: has both completed units to adopt and later units to re-execute.
STAGE_BLOCK_INDEX = 2


def build_plan() -> RunPlan:
    """The fixed single-Part, subtitle-first plan both processes reconstruct."""

    choices = RunPlanChoices.build(
        (
            RunChoice(
                stage=STAGE_RUN,
                key=KEY_ASR_MODE,
                scope=COLLECTION_SCOPE,
                value=AsrMode.SUBTITLE_FIRST.value,
                provenance=ChoiceProvenance.USER_CHOSEN,
            ),
            RunChoice(
                stage=STAGE_RUN,
                key=KEY_VISUAL_TEXT_ENABLED,
                scope=COLLECTION_SCOPE,
                value="false",
                provenance=ChoiceProvenance.USER_CHOSEN,
            ),
        )
    )
    return RunPlan(
        plan_id=PLAN_ID,
        report_id="0" * 32,
        source_artifacts=(
            SourceArtifact(
                source_id=PART,
                sha256=PART,
                byte_count=1,
                media_path=Path("input") / PART / "media",
            ),
        ),
        tools=(),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint=CONFIG_FINGERPRINT,
        run_choices=choices,
    )


def build_layout(root: Path) -> RunLayout:
    """The run layout for ``root`` under the fixed run identity (no workspace I/O)."""

    plan = build_plan()
    return RunLayout(root, source_id_from_run_plan(plan), run_id_from_run_plan(plan, RUN_START))


def evidence() -> ProjectionEvidence:
    """One VALID content artifact, so a recovered run publishes a real bundle."""

    return ProjectionEvidence(content_report=PlainArtifactEvidence(content="# 内容报告\n"))


def write_plan(root: Path) -> RunPlan:
    """Persist the fixed plan so a later ``vcp resume`` process can load it.

    ``vcp resume`` reads ``plans/<plan-id>/run-plan.json`` (see
    :func:`~video_content_pipeline.run_loop.load_confirmed_plan`); the run itself
    is passed the plan in memory, but CLI recovery must find it on disk.
    """

    plan = build_plan()
    plan_dir = root / "plans" / plan.plan_id
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "run-plan.json").write_text(json.dumps(plan.as_json(), indent=2), encoding="utf-8")
    return plan


def _block_forever(ready_path: Path) -> None:
    """Signal readiness, then block until the parent's SIGKILL lands."""

    ready_path.write_text("ready\n", encoding="utf-8")
    while True:  # pragma: no cover - the process is killed here, never returns
        time.sleep(3600)


def _blocking_composition(block: str, ready_path: Path) -> RunComposition:
    ev = evidence()
    if block == "stage":
        target = plan_stage_units(build_plan())[STAGE_BLOCK_INDEX]

        def executor(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
            if unit == target:
                _block_forever(ready_path)
            return StageResult.completed()

        return RunComposition(executor=executor, evidence=lambda: ev, report_inputs=RunReportInputs)

    # ``publish``: every unit completes; the block is at the finalization boundary,
    # before the terminal transition commits, so the state is still ``running`` when
    # the kill lands. ``classify_completed_run`` is the last call before that
    # transition, so wrapping it is the only in-``running`` seam after the DAG. The
    # rebind is a bare module-attribute swap rather than the ``monkeypatch.setattr``
    # seam the in-process kit uses (fault_injection.py): it needs no restore because
    # this is a throwaway subprocess about to be SIGKILLed, and it cannot leak — the
    # process never returns from ``_block_forever``.
    real_classify = run_loop_module.classify_completed_run

    def blocking_classify(*args: object, **kwargs: object) -> object:
        _block_forever(ready_path)
        return real_classify(*args, **kwargs)  # pragma: no cover - never reached

    run_loop_module.classify_completed_run = blocking_classify  # type: ignore[assignment]
    return RunComposition(
        executor=lambda unit, key: StageResult.completed(),
        evidence=lambda: ev,
        report_inputs=RunReportInputs,
    )


def run_until_killed(root: Path, block: str, ready_path: Path) -> None:
    """Execute the real run, blocking (until SIGKILL) at the chosen fault point."""

    plan = write_plan(root)
    layout = initialize_run_workspace(build_layout(root))
    execute_confirmed_run(
        layout=layout,
        plan=plan,
        composition=_blocking_composition(block, ready_path),
        lock_path=heavy_task_lock_path(root),
        now=RUN_START,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kill_harness")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--root", required=True)
    run.add_argument("--block", required=True, choices=("stage", "publish"))
    run.add_argument("--ready", required=True)
    arguments = parser.parse_args(argv)
    if arguments.command == "run":
        run_until_killed(Path(arguments.root), arguments.block, Path(arguments.ready))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
