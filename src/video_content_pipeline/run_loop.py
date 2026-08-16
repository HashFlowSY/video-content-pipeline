"""The non-interactive run loop that executes a confirmed plan end-to-end.

``vcp run`` composes the existing per-phase prototypes in process over the stage
DAG, then always publishes a RunBundle — a full one on success, a Minimal
RunBundle on every ordinary failure. This module is that orchestration: it owns
the run lifecycle (``planned -> queued -> running -> terminal``), turns the
stage-DAG disposition into the plan §12 terminal status, and drives the
Publication projection, the Minimal RunBundle floor, staging, and the atomic
publish. It never prompts: a front-loaded plan choice a mode needs but the plan
omits becomes a Run decision pause (``incomplete`` + a machine-readable required
decision), and an ordinary stage exception becomes a ``failed`` bundle.

The heavy per-phase work and the evidence it produces reach this loop through the
:class:`RunComposition` seam: an in-process :data:`StageExecutor` plus two
gatherers that read the workspaces the executor filled (the verified evidence the
projection selects among, and the recorded values the audit reports render). The
production composition (``run_composition``) invokes the sixteen expert commands'
underlying functions and never spawns a subprocess; the offline tests substitute
a controlled composition, so the loop's orchestration — non-interactive
execution, terminal classification, cancel-still-publishes, decision pauses, and
the guaranteed Minimal RunBundle — is provable without a model, media, or the
network. The heavy-task lock is held only around execution; publication is light
and runs after the run is terminal.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from video_content_pipeline.durable_io import utc_now
from video_content_pipeline.heavy_task_lock import (
    ProcessProbe,
    acquire_heavy_task_lock,
    heavy_task_lock_path,
)
from video_content_pipeline.orchestration import (
    RunLayout,
    initialize_run_workspace,
    run_id_from_run_plan,
    source_id_from_run_plan,
)
from video_content_pipeline.planning import RunPlan, load_run_plan
from video_content_pipeline.publication import (
    BundleDocument,
    PublicationOutcome,
    publish_run_bundle,
)
from video_content_pipeline.publication_projection import (
    PUBLICATION_PROJECTION_STAGE_VERSION,
    ProjectedArtifact,
    ProjectionEvidence,
    ProjectionResult,
    project_publication,
)
from video_content_pipeline.run_choices import COLLECTION_SCOPE, missing_required_choices
from video_content_pipeline.run_control import ControlDirective
from video_content_pipeline.run_recovery import ResumeAction, resume_run
from video_content_pipeline.run_reports import (
    EnvironmentInfo,
    GateStatus,
    InputRecord,
    InventoryEntry,
    ModelRecord,
    NetworkAccess,
    ParameterRecord,
    ProcessingReport,
    ResourceUsage,
    ReviewNeededInterval,
    StageGateReport,
    ToolRecord,
    assemble_minimal_run_bundle,
    build_quality_report,
    build_run_inventory,
    published_content_entries,
)
from video_content_pipeline.run_state import (
    RunStateWriter,
    RunStatus,
    read_journal,
)
from video_content_pipeline.stage_dag import (
    StageExecutor,
    StageRunDisposition,
    StageRunResult,
    execute_stages,
)


class RunLoopError(ValueError):
    """A run-loop failure with an auditable machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


# --- Composition seam -------------------------------------------------------


@dataclass(frozen=True)
class RunReportInputs:
    """The recorded values the audit reports render, gathered from workspaces.

    Every field defaults empty so a run that fails before any stage still yields
    a valid Minimal RunBundle. The gatherer reads only what the stages already
    recorded; nothing here is re-computed or re-analysed.
    """

    stage_reports: tuple[StageGateReport, ...] = ()
    review_needed: tuple[ReviewNeededInterval, ...] = ()
    inputs: tuple[InputRecord, ...] = ()
    tools: tuple[ToolRecord, ...] = ()
    environment: EnvironmentInfo | None = None
    models: tuple[ModelRecord, ...] = ()
    parameters: tuple[ParameterRecord, ...] = ()
    network: tuple[NetworkAccess, ...] = ()
    resource_usage: ResourceUsage = field(default_factory=ResourceUsage)
    warnings: tuple[str, ...] = ()
    inventory_entries: tuple[InventoryEntry, ...] = ()


@dataclass(frozen=True)
class RunComposition:
    """The in-process composition the run loop drives (the executor seam).

    ``executor`` runs one stage unit in process and reports its outcome;
    ``evidence`` and ``report_inputs`` are called after execution to read back the
    verified evidence and recorded report values from the workspaces the executor
    filled. The production factory builds one over the per-phase functions; the
    offline tests build one from controlled values.
    """

    executor: StageExecutor
    evidence: Callable[[], ProjectionEvidence] = ProjectionEvidence
    report_inputs: Callable[[], RunReportInputs] = RunReportInputs


#: A factory that builds the composition once the layout and confirmed plan are
#: known — the seam ``vcp run`` and ``vcp resume`` wire to production, and the
#: offline tests replace with a controlled composition.
CompositionFactory = Callable[[RunLayout, RunPlan], RunComposition]


# --- Run outcome ------------------------------------------------------------


@dataclass(frozen=True)
class RunOutcome:
    """The terminal result of a run, machine-readable for the CLI boundary."""

    layout: RunLayout
    status: RunStatus
    disposition: StageRunDisposition | None
    required_decision: Mapping[str, object] | None = None
    publication: PublicationOutcome | None = None
    stage_result: StageRunResult | None = None
    failure_reason: str | None = None
    accepted_decision: str | None = None

    def to_document(self) -> dict[str, object]:
        document: dict[str, object] = {
            "status": "ok",
            "run_id": self.layout.run_id,
            "source_id": self.layout.source_id,
            "run_status": self.status.value,
            "published": self.publication is not None,
        }
        if self.disposition is not None:
            document["disposition"] = self.disposition.value
        if self.accepted_decision is not None:
            document["accepted_decision"] = self.accepted_decision
        if self.required_decision is not None:
            document["required_decision"] = dict(self.required_decision)
        if self.stage_result is not None:
            document["failed_scopes"] = sorted(self.stage_result.failed_scopes)
        if self.failure_reason is not None:
            document["failure_reason"] = self.failure_reason
        if self.publication is not None:
            document["output_dir"] = str(self.publication.output_dir)
            document["latest_advanced"] = self.publication.latest_advanced
            document["verified"] = self.publication.verification.verified
            if not self.publication.verification.verified:
                document["verification_discrepancies"] = [
                    {"path": d.path, "reason": d.reason}
                    for d in self.publication.verification.discrepancies
                ]
        return document


# --- Terminal-status classification -----------------------------------------


def _has_warnings(report_inputs: RunReportInputs) -> bool:
    if report_inputs.warnings:
        return True
    return any(
        outcome.status is GateStatus.WARNING
        for report in report_inputs.stage_reports
        for outcome in report.outcomes
    )


def classify_completed_run(
    result: StageRunResult, plan: RunPlan, report_inputs: RunReportInputs
) -> RunStatus:
    """Map a completed DAG pass to its plan §12 terminal status.

    A collection-level failure or every Part failing yields ``failed`` (nothing
    usable was produced); some — but not all — Parts failing yields
    ``incomplete`` (partial results publish); a clean pass yields ``complete``,
    or ``complete_with_warnings`` when a gate recorded a warning.
    """

    part_scopes = {artifact.source_id for artifact in plan.source_artifacts}
    failed = result.failed_scopes
    if failed:
        if COLLECTION_SCOPE in failed or (part_scopes and part_scopes <= failed):
            return RunStatus.FAILED
        return RunStatus.INCOMPLETE
    if _has_warnings(report_inputs):
        return RunStatus.COMPLETE_WITH_WARNINGS
    return RunStatus.COMPLETE


# --- Projection and publication ---------------------------------------------


def _project(plan: RunPlan, evidence: ProjectionEvidence) -> ProjectionResult:
    """Project the plan's evidence, tolerating a plan with no fixed ASR mode.

    A run that decision-pauses for a missing front-loaded choice never fixed a
    mode, so there is no artifact set to project; it still publishes a Minimal
    RunBundle whose manifest lists only the audit documents.
    """

    if plan.run_choices.asr_mode() is None:
        return ProjectionResult(artifacts=(), stage_version=PUBLICATION_PROJECTION_STAGE_VERSION)
    return project_publication(plan, evidence)


def _merge_carried_forward(
    projection: ProjectionResult, carried_forward: Sequence[ProjectedArtifact]
) -> ProjectionResult:
    """Fold an Improvement run's carried-forward artifacts into its projection.

    The re-projection is authoritative for every path it produced with real
    content (the affected Parts and the recomputed collection artifacts); a
    carried-forward artifact fills a path the re-projection left ``unavailable`` or
    never produced — an unaffected Part's retained output (ADR 0046 at run level,
    the ADR 0052 improvement exception). Merged artifacts keep the projection's
    sorted-path order so the manifest stays byte-identical across equal runs.
    """

    if not carried_forward:
        return projection
    by_path: dict[str, ProjectedArtifact] = {
        artifact.path: artifact for artifact in projection.artifacts
    }
    for artifact in carried_forward:
        existing = by_path.get(artifact.path)
        if existing is None or existing.content is None:
            by_path[artifact.path] = artifact
    merged = tuple(sorted(by_path.values(), key=lambda artifact: artifact.path))
    return ProjectionResult(artifacts=merged, stage_version=projection.stage_version)


def _bundle_documents(
    *,
    layout: RunLayout,
    plan: RunPlan,
    run_status: RunStatus,
    projection: ProjectionResult,
    report_inputs: RunReportInputs,
) -> Sequence[BundleDocument]:
    """Render the five Minimal RunBundle audit documents from recorded values."""

    quality_report = build_quality_report(
        source_id=layout.source_id,
        run_id=layout.run_id,
        run_status=run_status,
        projection=projection,
        stage_reports=report_inputs.stage_reports,
        review_needed=report_inputs.review_needed,
    )
    inventory = build_run_inventory(
        source_id=layout.source_id,
        run_id=layout.run_id,
        entries=(*report_inputs.inventory_entries, *published_content_entries(projection)),
    )
    processing_report = ProcessingReport(
        source_id=layout.source_id,
        run_id=layout.run_id,
        plan_id=plan.plan_id,
        run_status=run_status,
        inventory=inventory,
        inputs=report_inputs.inputs,
        tools=report_inputs.tools,
        environment=report_inputs.environment,
        models=report_inputs.models,
        parameters=report_inputs.parameters,
        network=report_inputs.network,
        resource_usage=report_inputs.resource_usage,
        warnings=report_inputs.warnings,
        review_needed=report_inputs.review_needed,
    )
    return assemble_minimal_run_bundle(
        quality_report=quality_report,
        processing_report=processing_report,
        inventory=inventory,
        events=read_journal(layout.journal_path),
    )


def finalize_and_publish(
    *,
    writer: RunStateWriter,
    layout: RunLayout,
    plan: RunPlan,
    composition: RunComposition,
    run_status: RunStatus,
    now: datetime,
    carried_forward: Sequence[ProjectedArtifact] = (),
) -> PublicationOutcome:
    """Project, assemble the Minimal RunBundle floor, and atomically publish.

    Called once the run has reached a terminal status other than ``paused``. The
    projection selects among the composition's gathered evidence; an Improvement
    run's carried-forward artifacts (:func:`_merge_carried_forward`) are folded in
    so an unaffected Part's retained output publishes without re-analysis. The
    audit documents render the composition's recorded values; a publication
    verification failure is journaled through the single writer and returned in
    the outcome. The heavy-task lock is not required here — the run is terminal
    and publication does no heavy work.
    """

    projection = _merge_carried_forward(_project(plan, composition.evidence()), carried_forward)
    documents = _bundle_documents(
        layout=layout,
        plan=plan,
        run_status=run_status,
        projection=projection,
        report_inputs=composition.report_inputs(),
    )
    return publish_run_bundle(
        layout,
        run_status=run_status,
        projection=projection,
        documents=documents,
        plan_id=plan.plan_id,
        now=now,
        journal=writer.record_publication_verification_failure,
    )


# --- Fresh run --------------------------------------------------------------


def _terminal_outcome(
    *,
    writer: RunStateWriter,
    layout: RunLayout,
    plan: RunPlan,
    composition: RunComposition,
    result: StageRunResult,
    now: datetime,
    carried_forward: Sequence[ProjectedArtifact] = (),
) -> RunOutcome:
    """Drive a completed execution pass to its terminal status and publish.

    ``execute_stages`` has already transitioned a paused, cancelled, or
    decision-paused run; only a ``completed_all`` pass still needs the final
    ``running -> terminal`` transition. ``paused`` never publishes (a later
    ``vcp resume`` continues it); every other terminal status publishes a bundle.
    """

    disposition = result.disposition
    if disposition is StageRunDisposition.PAUSED:
        return RunOutcome(
            layout=layout,
            status=writer.state.status,
            disposition=disposition,
            stage_result=result,
        )
    if disposition is StageRunDisposition.DECISION_REQUIRED:
        run_status = RunStatus.INCOMPLETE
    elif disposition is StageRunDisposition.CANCELLED:
        run_status = RunStatus.CANCELLED
    else:
        run_status = classify_completed_run(result, plan, composition.report_inputs())
        writer.transition_to(run_status)
    publication = finalize_and_publish(
        writer=writer,
        layout=layout,
        plan=plan,
        composition=composition,
        run_status=run_status,
        now=now,
        carried_forward=carried_forward,
    )
    return RunOutcome(
        layout=layout,
        status=run_status,
        disposition=disposition,
        required_decision=result.required_decision,
        publication=publication,
        stage_result=result,
    )


def _fail_and_publish(
    *,
    writer: RunStateWriter,
    layout: RunLayout,
    plan: RunPlan,
    composition: RunComposition,
    reason: str,
    now: datetime,
    carried_forward: Sequence[ProjectedArtifact] = (),
) -> RunOutcome:
    """Record a ``failed`` run and publish its Minimal RunBundle.

    An ordinary stage exception is not a crash: it is caught here, recorded as a
    clean ``running -> failed`` transition, and still yields an auditable bundle.
    """

    if writer.state.status is RunStatus.RUNNING:
        writer.transition_to(RunStatus.FAILED)
    publication = finalize_and_publish(
        writer=writer,
        layout=layout,
        plan=plan,
        composition=composition,
        run_status=RunStatus.FAILED,
        now=now,
        carried_forward=carried_forward,
    )
    return RunOutcome(
        layout=layout,
        status=RunStatus.FAILED,
        disposition=None,
        publication=publication,
        failure_reason=reason,
    )


def execute_confirmed_run(
    *,
    layout: RunLayout,
    plan: RunPlan,
    composition: RunComposition,
    lock_path: Path,
    probe: ProcessProbe | None = None,
    clock: Callable[[], datetime] = utc_now,
    now: datetime | None = None,
    on_boundary: Callable[[], ControlDirective] | None = None,
    carried_forward: Sequence[ProjectedArtifact] = (),
) -> RunOutcome:
    """Execute a confirmed plan non-interactively over an initialized workspace.

    Creates the run state at ``planned``, front-loads the plan choices (a missing
    required choice becomes a Run decision pause, never a prompt), then serializes
    on the heavy-task lock: ``queued`` is the transient wait and a second heavy
    run fails fast with :class:`~video_content_pipeline.heavy_task_lock.HeavyTaskLockHeld`.
    Under the lock it drives ``queued -> running``, executes the DAG, and finalizes
    to a terminal status with a published bundle (an ordinary exception yields a
    ``failed`` bundle). ``paused`` and ``cancelled`` are honoured at unit
    boundaries; only ``paused`` skips publication.

    ``carried_forward`` is the Improvement run's retained artifacts from a named
    published RunBundle (:mod:`~video_content_pipeline.improve`); it is empty for
    an ordinary run and folded into the projection at publication time.
    """

    published_now = now if now is not None else clock()
    writer = RunStateWriter.create(layout, plan_id=plan.plan_id, clock=clock)

    gaps = missing_required_choices(plan.run_choices)
    writer.transition_to(RunStatus.QUEUED)
    with acquire_heavy_task_lock(lock_path, run_id=layout.run_id, probe=probe, clock=clock):
        writer.transition_to(RunStatus.RUNNING)
        if gaps:
            required = {
                "reason": "front_loaded_choice_missing",
                "decision": "provide_front_loaded_choices",
                "gaps": [gap.as_json() for gap in gaps],
            }
            writer.record_decision_pause(required)
            publication = finalize_and_publish(
                writer=writer,
                layout=layout,
                plan=plan,
                composition=composition,
                run_status=RunStatus.INCOMPLETE,
                now=published_now,
                carried_forward=carried_forward,
            )
            return RunOutcome(
                layout=layout,
                status=RunStatus.INCOMPLETE,
                disposition=StageRunDisposition.DECISION_REQUIRED,
                required_decision=required,
                publication=publication,
            )
        try:
            result = execute_stages(
                writer=writer,
                layout=layout,
                plan=plan,
                executor=composition.executor,
                on_boundary=on_boundary,
            )
        except Exception as error:  # noqa: BLE001 - ordinary failure still publishes
            return _fail_and_publish(
                writer=writer,
                layout=layout,
                plan=plan,
                composition=composition,
                reason=getattr(error, "reason", type(error).__name__),
                now=published_now,
                carried_forward=carried_forward,
            )
        return _terminal_outcome(
            writer=writer,
            layout=layout,
            plan=plan,
            composition=composition,
            result=result,
            now=published_now,
            carried_forward=carried_forward,
        )


def start_run(
    project_root: Path,
    plan_id: str,
    *,
    composition_factory: CompositionFactory,
    run_start: datetime,
    lock_path: Path | None = None,
    probe: ProcessProbe | None = None,
    clock: Callable[[], datetime] = utc_now,
    now: datetime | None = None,
    on_boundary: Callable[[], ControlDirective] | None = None,
) -> RunOutcome:
    """Load a confirmed plan by id and execute it as a fresh run.

    Derives the run identity and layout from the plan (ADR 0051 guards a
    published run from being overwritten), initializes the run workspace, builds
    the composition, and runs. Raises :class:`RunLoopError` when the plan id is
    unknown; identity and workspace errors surface their own reasons.
    """

    plan = load_confirmed_plan(project_root, plan_id)
    source_id = source_id_from_run_plan(plan)
    run_id = run_id_from_run_plan(plan, run_start)
    layout = initialize_run_workspace(RunLayout(project_root, source_id, run_id))
    composition = composition_factory(layout, plan)
    return execute_confirmed_run(
        layout=layout,
        plan=plan,
        composition=composition,
        lock_path=lock_path if lock_path is not None else heavy_task_lock_path(project_root),
        probe=probe,
        clock=clock,
        now=now if now is not None else run_start,
        on_boundary=on_boundary,
    )


def load_confirmed_plan(project_root: Path, plan_id: str) -> RunPlan:
    """Load a confirmed RunPlan by its immutable id (the one plan-path authority).

    Both the fresh-run path and ``vcp resume`` reach a plan through here, so the
    ``plans/<plan-id>/run-plan.json`` layout lives in exactly one place.
    """

    if not plan_id:
        raise RunLoopError("plan_id_missing", "vcp run requires a confirmed plan id.")
    return load_run_plan(project_root / "plans" / plan_id / "run-plan.json")


def resume_and_finalize(
    *,
    layout: RunLayout,
    plan: RunPlan,
    composition: RunComposition,
    lock_path: Path,
    decision: str | None = None,
    probe: ProcessProbe | None = None,
    clock: Callable[[], datetime] = utc_now,
    now: datetime | None = None,
    on_boundary: Callable[[], ControlDirective] | None = None,
) -> RunOutcome:
    """Resume an on-disk run and publish it if resuming drove it to a terminal.

    Delegates the three-case resume contract to
    :func:`~video_content_pipeline.run_recovery.resume_run`, then completes the
    run-loop half ticket 06 deferred: a validate-and-handoff decision resume
    publishes nothing (the run's bundle was published when it first paused); a
    resumed ``paused`` run that pauses again publishes nothing; and a resumed run
    that reaches a terminal disposition is driven to its terminal status and
    publishes its bundle exactly as a fresh run would. The heavy-task lock is held
    only inside ``resume_run``'s execution; publication runs after it releases,
    against a reopened writer.
    """

    published_now = now if now is not None else clock()
    outcome = resume_run(
        layout=layout,
        plan=plan,
        executor=composition.executor,
        lock_path=lock_path,
        decision=decision,
        probe=probe,
        clock=clock,
        on_boundary=on_boundary,
    )
    if outcome.action is ResumeAction.DECISION_ACCEPTED:
        return RunOutcome(
            layout=layout,
            status=RunStatus.INCOMPLETE,
            disposition=StageRunDisposition.DECISION_REQUIRED,
            required_decision=outcome.diagnosis.required_decision,
            accepted_decision=outcome.accepted_decision,
        )
    result = outcome.stage_result
    if result is None:  # pragma: no cover - EXECUTED always carries a stage result
        raise RunLoopError("resume_incomplete", "A resumed run produced no stage result.")
    if result.disposition is StageRunDisposition.PAUSED:
        return RunOutcome(
            layout=layout,
            status=RunStatus.PAUSED,
            disposition=StageRunDisposition.PAUSED,
            stage_result=result,
        )
    writer = RunStateWriter.reopen(layout, clock=clock)
    return _terminal_outcome(
        writer=writer,
        layout=layout,
        plan=plan,
        composition=composition,
        result=result,
        now=published_now,
    )
