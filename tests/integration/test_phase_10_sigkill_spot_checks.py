"""Ticket 09 (tier 2): real SIGKILL power-loss spot checks (Workstream D).

The deterministic matrix (ticket 07) freezes the durable-write seam in process; this
is the end-to-end complement — a *genuinely killed* run recovered through the CLI.
A separate process (:mod:`tests.support.kill_harness`) drives the real
:func:`~video_content_pipeline.run_loop.execute_confirmed_run` over real
``durable_io`` and the real OS process probe, then blocks at a chosen fault point;
this test sends it ``SIGKILL`` and then recovers it by driving the real
:mod:`video_content_pipeline.cli` entry point — ``vcp status --run`` and
``vcp resume --run`` — over the synthetic project root, exactly as the Phase 9 CLI
crash-recovery test does, but with the crashed state produced by a real dead
process rather than a wedged writer.

This extends the Phase 9 CLI kill test
(``test_status_diagnoses_a_crashed_run_and_resume_recovers_it``) from wedged-state
simulation to a real dead process, its real orphaned heavy-task lock, and a real
stale-lock steal detected by the live CLI against the killed process's recorded
identity. Two moments are covered: mid-stage and mid-publish (the finalization
window before the terminal transition commits — the only publish-window crash a
resume can still drive to a published bundle, per ticket 07's boundary note).

The composition the production factory would build needs media, a model, and the
network, so — as in the Phase 9 contract test — the CLI's ``_composition_factory``
and environment gates are replaced with controlled stand-ins; everything else
(argument parsing, run discovery, diagnosis, resume, publication, the JSON
contract) is the real CLI.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.support import kill_harness
from video_content_pipeline import cli
from video_content_pipeline.orchestration import RunLayout
from video_content_pipeline.publication_projection import PlainArtifactEvidence, ProjectionEvidence
from video_content_pipeline.run_loop import RunComposition, RunReportInputs
from video_content_pipeline.run_state import RunStatus, read_run_state
from video_content_pipeline.stage_dag import (
    StageInvalidationKey,
    StageResult,
    StageUnit,
    UnitStatus,
    read_recorded_units,
)

pytestmark = [pytest.mark.slow, pytest.mark.integration]

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: How long to wait for the child to reach its block point before giving up.
_READY_TIMEOUT_SECONDS = 30.0


@contextmanager
def _killed_run(root: Path, block: str) -> Iterator[None]:
    """Spawn the harness, wait until it blocks at ``block``, SIGKILL it, and reap it.

    Yields once the child is confirmed dead by ``SIGKILL`` (returncode ``-SIGKILL``),
    leaving its real crashed run state and orphaned lock on disk under ``root``.
    """

    ready = root / "ready.marker"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(PROJECT_ROOT), env.get("PYTHONPATH", "")]).rstrip(
        os.pathsep
    )
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tests.support.kill_harness",
            "run",
            "--root",
            str(root),
            "--block",
            block,
            "--ready",
            str(ready),
        ],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + _READY_TIMEOUT_SECONDS
        while not ready.exists():
            if child.poll() is not None:
                stderr = child.stderr.read().decode() if child.stderr else ""
                raise AssertionError(
                    f"harness exited before blocking ({child.returncode}):\n{stderr}"
                )
            if time.monotonic() > deadline:
                raise AssertionError("harness never reached its block point")
            time.sleep(0.05)
        os.kill(child.pid, signal.SIGKILL)
        assert child.wait(timeout=_READY_TIMEOUT_SECONDS) == -signal.SIGKILL
        yield
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=_READY_TIMEOUT_SECONDS)


def _configure_cli(root: Path, monkeypatch: pytest.MonkeyPatch, executed: list[StageUnit]) -> None:
    """Point the real CLI at ``root`` with a controlled, unit-recording composition.

    Mirrors the Phase 9 contract test's ``_configure``: the environment gates and
    the composition factory are the only stand-ins; ``status`` and ``resume``
    dispatch, discovery, diagnosis, and publication are the production CLI.
    """

    ev = ProjectionEvidence(content_report=PlainArtifactEvidence(content="# 内容报告\n"))

    def executor(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
        executed.append(unit)
        return StageResult.completed()

    composition = RunComposition(
        executor=executor, evidence=lambda: ev, report_inputs=RunReportInputs
    )
    monkeypatch.setattr(cli, "assert_runtime_policy", lambda: None)
    monkeypatch.setattr(cli, "assert_project_venv", lambda: object())
    monkeypatch.setattr(cli, "_project_root", lambda: root)
    monkeypatch.setattr(cli, "_composition_factory", lambda layout, plan: composition)


def _invoke(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, object]]:
    code = cli.main(argv)
    return code, json.loads(capsys.readouterr().out)


def _completed_units(layout: RunLayout) -> set[StageUnit]:
    recorded = read_recorded_units(read_run_state(layout.state_path))
    return {unit for unit, record in recorded.items() if record.status is UnitStatus.COMPLETED}


def _recover_via_cli(
    layout: RunLayout, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> list[StageUnit]:
    """Drive ``vcp status`` then ``vcp resume`` over the killed run; return re-executed units."""

    executed: list[StageUnit] = []
    _configure_cli(layout.project_root, monkeypatch, executed)

    # `vcp status --run` classifies the wreck as a crashed, stale-running run and
    # never mutates the on-disk state.
    before = layout.state_path.read_bytes()
    code, status = _invoke(["status", "--run", layout.run_id], capsys)
    assert code == 0
    assert status["run"]["resume_case"] == "crashed"
    assert status["run"]["stale_running"] is True
    assert layout.state_path.read_bytes() == before

    # `vcp resume --run` steals the dead process's stale lock and drives the run to a
    # published, hash-verified terminal bundle.
    code, resumed = _invoke(["resume", "--run", layout.run_id], capsys)
    assert code == 0
    assert resumed["run_status"] == "complete"
    assert resumed["published"] is True
    assert resumed["verified"] is True
    assert read_run_state(layout.state_path).status is RunStatus.COMPLETE
    assert (layout.output_dir / "manifest.json").is_file()
    return executed


def test_sigkill_mid_stage_recovers_via_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A run SIGKILLed mid-stage is diagnosed crashed by ``vcp status`` and recovered
    by ``vcp resume``, re-executing only the units with no durable checkpoint."""

    layout = kill_harness.build_layout(tmp_path)

    with _killed_run(tmp_path, "stage"):
        completed_before = _completed_units(layout)
        # The kill landed mid-DAG: some units checkpointed, the run state still running.
        assert completed_before
        assert read_run_state(layout.state_path).status is RunStatus.RUNNING

        executed = _recover_via_cli(layout, monkeypatch, capsys)

    # No unit with a durable ``completed`` checkpoint was re-run (revalidate-and-adopt).
    assert set(executed).isdisjoint(completed_before)
    assert executed, "the interrupted and later units must be re-executed on resume"


def test_sigkill_mid_publish_recovers_via_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A run SIGKILLed in the publish window — after every unit checkpointed but
    before the terminal transition committed — is diagnosed crashed and recovered by
    ``vcp resume`` without re-executing any already-completed unit."""

    layout = kill_harness.build_layout(tmp_path)

    with _killed_run(tmp_path, "publish"):
        completed_before = _completed_units(layout)
        # Every unit had checkpointed before the crash; the state is still running.
        assert completed_before
        assert read_run_state(layout.state_path).status is RunStatus.RUNNING

        executed = _recover_via_cli(layout, monkeypatch, capsys)

    # All units were adoptable — resume re-executed none of them, only finalized.
    assert executed == []
