"""Ticket 07: the exhaustive orchestration fault matrix — the phase's failure proof.

This is the centerpiece of Phase 10. It drives one micro run scenario (a single
Part, the subtitle-first branch, a trivial completing fake executor — the matrix
targets orchestration *persistence*, not stage internals) through the real
:func:`~video_content_pipeline.run_loop.execute_confirmed_run` and, for every
recoverable wreck, the real
:func:`~video_content_pipeline.run_loop.resume_and_finalize`. No model, no media,
no network — the only test seam is the ticket-02 :class:`DurableIoInterceptor`,
which redirects the four ``durable_io`` primitives on the five orchestration
modules that import them.

The matrix is built in two moves:

* **Golden run** — one pass with a plan-less interceptor counts every durable
  write in order. That count *N* is recomputed each run and asserted against a
  recorded constant (:data:`RECORDED_DURABLE_WRITE_COUNT`); a new persistence
  call site therefore changes *N* and fails the golden-run test loudly. Updating
  the constant is the review act.

* **N × 3 replay** — for every write position ``k`` in ``1..N`` and every
  :class:`FaultClass` (process death / exhausted disk / torn write), the scenario
  is replayed failing the ``k``-th durable write, and
  :func:`_assert_cell_invariants` checks all five invariants:

  (a) ``vcp status`` (:func:`diagnose_run`) classifies the wreck read-only — it
      returns a diagnosis or raises one controlled, machine-readable orchestration
      reason (a crash mid lock-claim leaves an unreadable lock, which the lock
      layer deliberately refuses to auto-steal), and either way state, journal,
      and lock bytes are untouched.
  (b) The run ends in exactly one safe shape: already terminal at the fault, or
      driven to a terminal status by ``vcp resume`` (a published, verifiable
      bundle), or failed in-loop into a Minimal RunBundle — never a wedged third
      outcome. An *exhausted disk* the process is expected to survive becomes a
      published ``failed`` bundle; a write the process cannot make (process death,
      a torn write, or a full disk on the run's own state/journal — an unwritable
      state file *is* a crash) stops the process and leaves recovery to resume.
  (c) ``outputs/`` is only ever absent or a fully valid bundle, and ``latest.json``
      only ever absent or a valid pointer — the atomic publish rename and the
      atomic pointer replace make a partial or corrupt published artifact
      impossible in every cell.
  (d) A resume never re-executes a unit whose ``completed`` checkpoint is already
      on disk (revalidate-and-adopt, ADR 0052).
  (e) A resumed crash leaves no torn artifact behind: no ``run-state.json.tmp``
      survives, the journal reads back strictly, and the recovery is journaled.

Two production gaps this matrix exposed were fixed alongside it (both keep the
project's "every failure carries a machine-readable reason the CLI catches"
contract): a garbage (non-UTF-8) control request leaked a bare
``UnicodeDecodeError`` instead of ``control_request_unreadable``, and a full disk
during publication escaped as a bare ``OSError`` instead of a ``PublicationError``.
The control-file corruption cells (:func:`test_corrupt_control_file_halts_safely`)
and the explicit ENOSPC-during-publish / torn-latest-pointer cells pin both.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.support.fault_injection import (
    DURABLE_IO_FUNCTIONS,
    DurableIoInterceptor,
    FaultClass,
    InjectionPlan,
    SimulatedProcessDeath,
    corrupt_with_garbage,
    truncate_file,
)
from video_content_pipeline import (
    heavy_task_lock as heavy_task_lock_module,
)
from video_content_pipeline import (
    publication as publication_module,
)
from video_content_pipeline import (
    run_control as run_control_module,
)
from video_content_pipeline import (
    run_recovery as run_recovery_module,
)
from video_content_pipeline import (
    run_state as run_state_module,
)
from video_content_pipeline.heavy_task_lock import (
    HeavyTaskLockError,
    ProcessIdentity,
    heavy_task_lock_path,
)
from video_content_pipeline.orchestration import (
    RunLayout,
    initialize_run_workspace,
    run_id_from_run_plan,
    source_id_from_run_plan,
)
from video_content_pipeline.planning import RunPlan, calculate_disk_headroom
from video_content_pipeline.publication import (
    PublicationError,
    read_latest_pointer,
    verify_published_bundle,
)
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
from video_content_pipeline.run_control import ControlKind, request_control
from video_content_pipeline.run_loop import (
    RunComposition,
    RunOutcome,
    RunReportInputs,
    execute_confirmed_run,
    resume_and_finalize,
)
from video_content_pipeline.run_recovery import (
    ResumeCase,
    RunRecoveryError,
    diagnose_run,
)
from video_content_pipeline.run_state import (
    EventKind,
    RunStateError,
    RunStatus,
    read_journal,
    read_run_state,
)
from video_content_pipeline.source import SourceArtifact
from video_content_pipeline.stage_dag import (
    StageInvalidationKey,
    StageResult,
    StageUnit,
    UnitStatus,
    plan_stage_units,
    read_recorded_units,
)

pytestmark = [pytest.mark.faultmatrix, pytest.mark.integration, pytest.mark.slow]

#: The number of durable writes one micro run performs, recomputed by the golden
#: run and asserted against here. A new persistence call site changes it and
#: fails the golden-run test — updating this constant is the review act that
#: admits the new write site into the matrix.
RECORDED_DURABLE_WRITE_COUNT = 23

#: The five orchestration modules that import a ``durable_io`` primitive; the
#: interceptor redirects only those names, so a module using just one primitive
#: is otherwise untouched.
_TARGET_MODULES = (
    run_state_module,
    run_recovery_module,
    run_control_module,
    heavy_task_lock_module,
    publication_module,
)

#: Run statuses that end a run; resume must refuse these as ``not_resumable``.
_TERMINAL_STATUSES = frozenset(
    {
        RunStatus.COMPLETE,
        RunStatus.COMPLETE_WITH_WARNINGS,
        RunStatus.INCOMPLETE,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }
)

_PART = "a" * 64
_PLAN_ID = "plan0123456789abcdef0123"
_CONFIG = "cfg" + "0" * 61
_NOW = datetime(2026, 8, 16, 8, 45, 0, tzinfo=UTC)
_RESUMER = ProcessIdentity(pid=900, start_time="s900")


# --- Scenario builders (mirrors the offline run-loop harness) ----------------


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
        plan_id=_PLAN_ID,
        report_id="0" * 32,
        source_artifacts=(
            SourceArtifact(
                source_id=_PART,
                sha256=_PART,
                byte_count=1,
                media_path=Path("input") / _PART / "media",
            ),
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
    publishes a real bundle (not only the audit-document floor)."""

    return ProjectionEvidence(content_report=PlainArtifactEvidence(content="# 内容报告\n"))


def _composition(recorder: list[StageUnit] | None = None) -> RunComposition:
    def executor(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
        if recorder is not None:
            recorder.append(unit)
        return StageResult.completed()

    evidence = _evidence()
    return RunComposition(
        executor=executor, evidence=lambda: evidence, report_inputs=RunReportInputs
    )


@contextlib.contextmanager
def _intercepting(interceptor: DurableIoInterceptor) -> Iterator[DurableIoInterceptor]:
    """Redirect the durable-write seam to ``interceptor`` for the block, restoring
    the real functions on the way out (so no patch leaks between cells)."""

    saved = [
        (module, name, getattr(module, name))
        for module in _TARGET_MODULES
        for name in DURABLE_IO_FUNCTIONS
        if hasattr(module, name)
    ]
    try:
        interceptor.install(
            lambda module, name, value: setattr(module, name, value), *_TARGET_MODULES
        )
        yield interceptor
    finally:
        for module, name, value in saved:
            setattr(module, name, value)


def _run(
    layout: RunLayout,
    plan: RunPlan,
    interceptor: DurableIoInterceptor,
    *,
    recorder: list[StageUnit] | None = None,
) -> tuple[RunOutcome | None, BaseException | None]:
    """Execute the confirmed run under ``interceptor``; return (outcome, raised)."""

    with _intercepting(interceptor):
        try:
            outcome = execute_confirmed_run(
                layout=layout,
                plan=plan,
                composition=_composition(recorder),
                lock_path=heavy_task_lock_path(layout.project_root),
                probe=_FakeProbe(_RESUMER, {_RESUMER}),
                clock=_clock(),
                now=_NOW,
            )
        except BaseException as error:  # noqa: BLE001 - a fault legitimately kills the run
            return None, error
    return outcome, None


def _state(layout: RunLayout) -> RunStatus | None:
    try:
        return read_run_state(layout.state_path).status
    except RunStateError:
        return None


def _state_tmp(layout: RunLayout) -> Path:
    return layout.state_path.with_name(layout.state_path.name + ".tmp")


def _completed_units(layout: RunLayout) -> set[StageUnit]:
    recorded = read_recorded_units(read_run_state(layout.state_path))
    return {unit for unit, record in recorded.items() if record.status is UnitStatus.COMPLETED}


def _assert_outputs_safe(layout: RunLayout) -> None:
    """Invariant (c): outputs and the latest pointer are never partial/corrupt."""

    if layout.output_dir.exists():
        assert verify_published_bundle(layout.output_dir).verified, "published bundle is corrupt"
    # ``read_latest_pointer`` raises only on a *present but corrupt* pointer; a
    # torn atomic replace never writes one, so this must not raise.
    read_latest_pointer(layout.latest_path)


# --- Golden run --------------------------------------------------------------


def test_golden_run_enumerates_the_recorded_write_count(tmp_path: Path) -> None:
    """The golden run counts every durable write and pins N (recorded-N assertion)."""

    plan = _plan()
    layout = _layout(tmp_path, plan)
    interceptor = DurableIoInterceptor()
    outcome, raised = _run(layout, plan, interceptor)

    assert raised is None
    assert outcome is not None and outcome.status is RunStatus.COMPLETE
    assert interceptor.call_count == RECORDED_DURABLE_WRITE_COUNT, (
        "the run's durable-write count changed; a new persistence call site must be "
        "reviewed and RECORDED_DURABLE_WRITE_COUNT updated to admit it into the matrix"
    )
    # Sanity: a clean golden run really did publish a verifiable bundle.
    assert verify_published_bundle(layout.output_dir).verified is True


# --- The N × 3 matrix --------------------------------------------------------


def _assert_cell_invariants(
    layout: RunLayout,
    plan: RunPlan,
    outcome: RunOutcome | None,
    raised: BaseException | None,
) -> None:
    """Assert the five invariants for one already-executed fault cell."""

    # A crash before the very first durable write lands leaves no run at all —
    # nothing to diagnose or recover, and certainly nothing published.
    if _state(layout) is None:
        assert outcome is None and raised is not None
        assert not layout.output_dir.exists()
        return

    # (c) outputs are safe no matter what the fault did.
    _assert_outputs_safe(layout)

    # (a) `vcp status` is read-only and either classifies or refuses with one
    # controlled machine-readable reason (an unreadable lock is not auto-stolen).
    before_state = layout.state_path.read_bytes()
    before_journal = layout.journal_path.read_bytes() if layout.journal_path.exists() else None
    lock_path = heavy_task_lock_path(layout.project_root)
    before_lock = lock_path.read_bytes() if lock_path.exists() else None
    diagnosis = None
    try:
        diagnosis = diagnose_run(
            layout, lock_path=lock_path, probe=_FakeProbe(_RESUMER, {_RESUMER})
        )
    except HeavyTaskLockError as error:
        assert error.reason == "heavy_task_lock_unreadable"
    assert layout.state_path.read_bytes() == before_state, "status mutated run state"
    after_journal = layout.journal_path.read_bytes() if layout.journal_path.exists() else None
    assert after_journal == before_journal, "status mutated the journal"
    after_lock = lock_path.read_bytes() if lock_path.exists() else None
    assert after_lock == before_lock, "status mutated the lock"

    if diagnosis is None:
        # The only way to reach here is the unreadable-lock refusal above; the run
        # never started executing (it crashed claiming the lock), so nothing is
        # published and a human must clear the corrupt lock before any resume.
        assert not layout.output_dir.exists()
        return

    # (b) Non-recoverable diagnoses are already in a safe resting shape.
    # Invariants (d) and (e) are resume-properties — a run that is not resumed
    # never re-executes a unit and never runs artifact repair — so they are
    # vacuous here and asserted only on the resumable branch below; (a) and (c)
    # have already been checked above for this cell.
    if diagnosis.case in (ResumeCase.NOT_RESUMABLE, ResumeCase.DECISION_PAUSE):
        state = read_run_state(layout.state_path).status
        if outcome is not None:
            # The run loop returned: it either completed cleanly before the fault,
            # or caught a survivable fault (ENOSPC) into a published bundle.
            assert state in _TERMINAL_STATUSES
            assert outcome.publication is not None
            assert verify_published_bundle(layout.output_dir).verified is True
        else:
            # The process died. State is either terminal (crash during the final
            # publish, after the terminal transition committed) or a pre-running
            # status the run never advanced past — never a wedged running state.
            assert state not in (RunStatus.RUNNING, RunStatus.PAUSING)
        with pytest.raises(RunRecoveryError):
            _resume(layout, plan)
        return

    # (b)+(d)+(e) A crashed/paused run resumes to a clean terminal bundle.
    assert diagnosis.case in (ResumeCase.CRASHED, ResumeCase.PAUSED)
    completed_before = _completed_units(layout)
    resumed: list[StageUnit] = []
    resume_outcome = _resume(layout, plan, recorder=resumed)

    assert resume_outcome.status is RunStatus.COMPLETE
    assert resume_outcome.publication is not None
    assert read_run_state(layout.state_path).status is RunStatus.COMPLETE
    _assert_outputs_safe(layout)
    assert verify_published_bundle(layout.output_dir).verified is True

    # (d) No unit with a durable ``completed`` checkpoint is re-executed.
    assert set(resumed).isdisjoint(completed_before)
    final = read_recorded_units(read_run_state(layout.state_path))
    assert set(final) == set(plan_stage_units(plan))
    assert all(record.status is UnitStatus.COMPLETED for record in final.values())

    # (e) Recovery repaired every torn artifact and journaled the recovery.
    assert not _state_tmp(layout).exists(), "a torn run-state temp survived resume"
    events = read_journal(layout.journal_path)  # strict read: torn tails were repaired
    assert any(event.kind is EventKind.RECOVERY for event in events)


def _resume(
    layout: RunLayout, plan: RunPlan, *, recorder: list[StageUnit] | None = None
) -> RunOutcome:
    clean = DurableIoInterceptor()
    with _intercepting(clean):
        return resume_and_finalize(
            layout=layout,
            plan=plan,
            composition=_composition(recorder),
            lock_path=heavy_task_lock_path(layout.project_root),
            probe=_FakeProbe(_RESUMER, {_RESUMER}),
            clock=_clock(),
            now=_NOW,
        )


@pytest.mark.parametrize("fault", list(FaultClass))
@pytest.mark.parametrize("fail_at", range(1, RECORDED_DURABLE_WRITE_COUNT + 1))
def test_fault_matrix_cell(tmp_path: Path, fail_at: int, fault: FaultClass) -> None:
    """Fail the ``fail_at``-th durable write with ``fault`` and prove all five
    invariants hold — exhaustively over every write position and Fault class."""

    plan = _plan()
    layout = _layout(tmp_path, plan)
    interceptor = DurableIoInterceptor(InjectionPlan(fail_at=fail_at, fault=fault))
    outcome, raised = _run(layout, plan, interceptor)

    # Exactly one of "returned" or "raised" — and a raise is only ever the death
    # freeze (a killed process) or a typed orchestration error the CLI catches.
    assert (outcome is None) != (raised is None)
    if raised is not None:
        assert isinstance(
            raised, SimulatedProcessDeath | OSError | PublicationError | RunStateError
        )

    _assert_cell_invariants(layout, plan, outcome, raised)


# --- Control-file corruption cells (Phase 9 deferral) ------------------------


@pytest.mark.parametrize("corrupt", ["garbage", "truncated"])
def test_corrupt_control_file_halts_safely(tmp_path: Path, corrupt: str) -> None:
    """A garbage or truncated control request observed at a boundary halts the run
    into a published Minimal RunBundle with the typed ``control_request_unreadable``
    reason — never a misread request or a bare decode error, and never corrupt
    output."""

    plan = _plan()
    layout = _layout(tmp_path, plan)
    control_path = request_control(layout, ControlKind.PAUSE)
    if corrupt == "garbage":
        corrupt_with_garbage(control_path)  # non-UTF-8 bytes
    else:
        truncate_file(control_path, keep_bytes=5)  # a torn JSON prefix

    outcome, raised = _run(layout, plan, DurableIoInterceptor())

    assert raised is None
    assert outcome is not None
    assert outcome.status is RunStatus.FAILED
    assert outcome.failure_reason == "control_request_unreadable"
    assert outcome.publication is not None
    assert verify_published_bundle(layout.output_dir).verified is True


# --- Explicit publish-window cells (called out by the ticket) ----------------


def _golden_call_sequence(tmp_path: Path) -> list[str]:
    plan = _plan()
    layout = _layout(tmp_path, plan)
    interceptor = DurableIoInterceptor()
    _run(layout, plan, interceptor)
    return list(interceptor.calls)


def test_enospc_during_publish_staging_leaves_no_partial_bundle(tmp_path: Path) -> None:
    """A full disk while staging the bundle surfaces the typed ``staging_write_failed``
    reason and leaves ``outputs/`` absent — the atomic rename never ran, so nothing
    partial is ever visible."""

    calls = _golden_call_sequence(tmp_path / "golden")
    # The publish staging writes are the run of ``durable_write`` calls that ends
    # just before the post-rename directory fsync.
    fsync_index = calls.index("fsync_directory") + 1  # 1-based
    staging_write = fsync_index - 1  # the last staged file, squarely inside publish
    assert calls[staging_write - 1] == "durable_write"

    plan = _plan()
    layout = _layout(tmp_path / "run", plan)
    interceptor = DurableIoInterceptor(
        InjectionPlan(fail_at=staging_write, fault=FaultClass.EXHAUSTED_DISK)
    )
    outcome, raised = _run(layout, plan, interceptor)

    assert outcome is None
    assert isinstance(raised, PublicationError)
    assert raised.reason == "staging_write_failed"
    assert not layout.output_dir.exists()
    # The run committed its terminal status before publishing; the fault stranded
    # only the bundle, never a partial artifact.
    assert read_run_state(layout.state_path).status is RunStatus.COMPLETE


def test_torn_latest_pointer_never_publishes_a_corrupt_pointer(tmp_path: Path) -> None:
    """A torn write of ``latest.json`` (the final durable write) leaves the pointer
    absent or intact — never half-written — while the bundle itself stays valid."""

    calls = _golden_call_sequence(tmp_path / "golden")
    # The latest-pointer replace is the final durable write, after the fsync.
    assert calls[-1] == "atomic_replace"
    latest_write = len(calls)  # 1-based index of the last call

    plan = _plan()
    layout = _layout(tmp_path / "run", plan)
    interceptor = DurableIoInterceptor(
        InjectionPlan(fail_at=latest_write, fault=FaultClass.TORN_WRITE)
    )
    outcome, raised = _run(layout, plan, interceptor)

    assert outcome is None
    assert isinstance(raised, SimulatedProcessDeath)
    # The bundle published atomically before the pointer write; it is fully valid.
    assert verify_published_bundle(layout.output_dir).verified is True
    # The pointer is never torn: it is absent (never written) here, and readable
    # without error either way.
    assert read_latest_pointer(layout.latest_path) is None
    assert not layout.latest_path.exists()
