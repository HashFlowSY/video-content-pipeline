"""The production :class:`~video_content_pipeline.run_loop.RunComposition`.

This is the in-process composition ``vcp run`` and ``vcp resume`` wire to
production: a :data:`~video_content_pipeline.stage_dag.StageExecutor` that invokes
the sixteen expert commands' underlying functions in dependency order, chaining
their report ids and translating the confirmed plan's front-loaded choices back
into each function's own selector shapes. Nothing is ever spawned as a
subprocess; the expert commands stay untouched.

Two structural facts shape this module:

* The per-phase functions are *collection-level* — each takes a ``plan_id`` and
  processes every Part in one call — while the stage DAG's atomic unit is
  ``(stage, Part)``. So a stage's function is invoked **once**, memoized by
  stage, and its single collective outcome is returned for every Part unit of
  that stage. A collective failure fails each Part's unit; a collective decision
  pause stops at the first unit; a collective success completes them all.
* A stage's pause is mapped to a :class:`~video_content_pipeline.stage_dag.StageResult`
  using that stage's *own retained pause vocabulary verbatim* (ADR 0052): the
  ``required_decision`` the function recorded, so ``vcp resume`` can match
  ``--decision`` against the exact token the expert command would.

The heavy per-phase work cannot run offline (it needs media, models, and the
network), so this module's end-to-end behaviour is exercised in a real
environment; the offline tests prove its *logic* — parameter translation, report
chaining, and pause mapping — with the stage functions replaced by controlled
stand-ins, and the run loop it feeds is proven separately over a controlled
composition.

Scope note (ticket 10, option 1): the executor wiring and pause mapping are
complete. The evidence and report-input gatherers are deliberately conservative
— they surface only what a workspace plainly exposes and record everything else
as ``unavailable`` (the projection fabricates no placeholder). Full byte-level
reconstruction of the timed subtitle and transcript artifacts from the stage
workspaces is a deferred follow-up; until then a real run publishes the audit
floor plus whatever plainly-exposed content the workspaces carry.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from video_content_pipeline.audio_analysis import analyze_audio
from video_content_pipeline.enhancement import enhance
from video_content_pipeline.orchestration import RunLayout
from video_content_pipeline.planning import (
    PlanningError,
    RunPlan,
    confirmed_plan_matches,
    load_plan_report,
    revalidate_report,
)
from video_content_pipeline.publication_projection import (
    PlainArtifactEvidence,
    ProjectionEvidence,
)
from video_content_pipeline.run_choices import (
    audio_analysis_stage_parameters,
    enhancement_stage_parameters,
    subtitle_stage_parameters,
    transcription_stage_parameters,
    visual_text_stage_parameters,
)
from video_content_pipeline.run_loop import RunComposition, RunReportInputs
from video_content_pipeline.stage_dag import (
    StageInvalidationKey,
    StageName,
    StageResult,
    StageResultKind,
    StageUnit,
)
from video_content_pipeline.subtitle_pipeline import process_subtitles, resume_subtitles
from video_content_pipeline.text_analysis import analyze_text
from video_content_pipeline.transcription import transcribe
from video_content_pipeline.visual_text_command import run_visual_text

#: Stage return statuses that mean the stage produced usable output.
_SUCCESS_STATUSES: frozenset[str] = frozenset({"complete", "completed", "partial"})

#: Statuses a stage uses for an acquisition/adapter pause it expresses only
#: through its status (no nested ``required_decision``): a Run decision pause,
#: not a failure. The synthesized token hands off to the model-prototype session.
_ACQUISITION_PAUSE_STATUSES: frozenset[str] = frozenset(
    {"model_acquisition_required", "controlled_adapter_unavailable"}
)

#: Shared capability state that marks an audio ``blocked`` return as a model
#: acquisition pause rather than a failure.
_MODEL_ACQUISITION_CAPABILITY = "model_acquisition_required"


def _extract_required_decision(report: Mapping[str, object]) -> dict[str, object] | None:
    """Return the stage's recorded pause payload from its own report, if any.

    Checks the two places the expert commands record a resumable decision: the
    top-level ``required_decision`` (transcription, enhancement, text-analysis,
    visual-text) and the nested ``partial_analysis.required_decision`` (audio).
    """

    required = report.get("required_decision")
    if isinstance(required, Mapping) and required:
        return dict(required)
    partial = report.get("partial_analysis")
    if isinstance(partial, Mapping):
        nested = partial.get("required_decision")
        if isinstance(nested, Mapping) and nested:
            return dict(nested)
    return None


def _blocked_is_acquisition_pause(report: Mapping[str, object]) -> bool:
    """Whether a ``blocked`` audio return is really a model-acquisition pause.

    Audio analysis blocks with no formal evidence when its capability models are
    unacquired; that is a Run decision pause (model acquisition), not a stage
    failure. Any capability in the ``model_acquisition_required`` state signals it.
    """

    capabilities = report.get("capabilities")
    if not isinstance(capabilities, list):
        return False
    for capability in capabilities:
        if (
            isinstance(capability, Mapping)
            and capability.get("state") == _MODEL_ACQUISITION_CAPABILITY
        ):
            return True
    return False


def map_stage_return(status: str, report: Mapping[str, object]) -> StageResult:
    """Map a per-phase function's ``(status, report)`` to a :class:`StageResult`.

    A recorded ``required_decision`` (top-level or nested) becomes a decision
    pause carrying that payload verbatim; a success status completes the unit; an
    acquisition/adapter pause becomes a synthesized decision pause; anything else
    — including ``failed`` and a genuinely ``blocked`` stage — is a per-Part
    failure recorded with the stage's status and report id.
    """

    required = _extract_required_decision(report)
    if required is not None:
        return StageResult.decision_required(required)
    if status in _SUCCESS_STATUSES:
        return StageResult.completed({"report_id": report.get("report_id")})
    if status in _ACQUISITION_PAUSE_STATUSES or (
        status == "blocked" and _blocked_is_acquisition_pause(report)
    ):
        return StageResult.decision_required({"reason": status, "decision": status})
    return StageResult.failed({"status": status, "report_id": report.get("report_id")})


@dataclass
class StageFunctions:
    """The per-phase functions the composition invokes, as an injectable seam.

    Production uses the module defaults; the offline tests substitute controlled
    stand-ins so the composition's translation, chaining, and mapping logic is
    provable without media, a model, or the network.
    """

    process_subtitles: Callable[..., dict[str, object]] = process_subtitles
    resume_subtitles: Callable[..., dict[str, object]] = resume_subtitles
    analyze_audio: Callable[..., dict[str, object]] = analyze_audio
    transcribe: Callable[..., dict[str, object]] = transcribe
    enhance: Callable[..., dict[str, object]] = enhance
    analyze_text: Callable[..., dict[str, object]] = analyze_text
    run_visual_text: Callable[..., dict[str, object]] = run_visual_text


@dataclass
class _CompositionState:
    """The per-run mutable state the executor and gatherers share by closure."""

    layout: RunLayout
    plan: RunPlan
    functions: StageFunctions
    reports: dict[StageName, str] = field(default_factory=dict)
    results: dict[StageName, StageResult] = field(default_factory=dict)


def _record(state: _CompositionState, stage: StageName, result: StageResult) -> StageResult:
    report_id = result.detail.get("report_id")
    if result.kind is StageResultKind.COMPLETED and isinstance(report_id, str):
        state.reports[stage] = report_id
    return result


def _invoke_source_revalidation(state: _CompositionState) -> StageResult:
    """Revalidate the confirmed plan before any heavy stage, tolerantly.

    Each per-phase function revalidates its own inputs as well; this collection
    unit fails the run early on plan-confirmation drift, and is a no-op when the
    confirmed report is not on disk (nothing to compare against).
    """

    report_path = (
        state.layout.project_root / "plans" / "reports" / state.plan.report_id / "plan-report.json"
    )
    try:
        report = load_plan_report(report_path)
    except PlanningError:
        return StageResult.completed()
    if not confirmed_plan_matches(report, state.plan):
        return StageResult.failed({"reason": "plan_confirmation_drift"})
    diagnostics = revalidate_report(report, state.layout.project_root)
    if diagnostics:
        return StageResult.failed({"reason": diagnostics[0].reason})
    return StageResult.completed()


def _invoke_subtitles(state: _CompositionState) -> StageResult:
    root = state.layout.project_root
    params = subtitle_stage_parameters(state.plan.run_choices)
    result = state.functions.process_subtitles(state.plan.plan_id, root, params.decoders)
    status, report = _split(result)
    if status == "awaiting_subtitle_selection" and params.select:
        # Apply the front-loaded track selection rather than pausing for it.
        result = state.functions.resume_subtitles(
            state.plan.plan_id,
            _report_id(report),
            params.select,
            root,
            params.decoders,
        )
        status, report = _split(result)
    return _record(state, StageName.SUBTITLES, map_stage_return(status, report))


def _invoke_audio(state: _CompositionState) -> StageResult:
    subtitle_id = state.reports.get(StageName.SUBTITLES)
    if subtitle_id is None:
        return StageResult.failed({"reason": "subtitle_report_unavailable"})
    params = audio_analysis_stage_parameters(state.plan.run_choices)
    result = state.functions.analyze_audio(
        state.plan.plan_id,
        subtitle_id,
        state.layout.project_root,
        params.audio_stream,
        params.diarization_candidate,
        params.role_metadata,
    )
    status, report = _split(result)
    return _record(state, StageName.AUDIO_ANALYSIS, map_stage_return(status, report))


def _invoke_transcription(state: _CompositionState) -> StageResult:
    subtitle_id = state.reports.get(StageName.SUBTITLES)
    audio_id = state.reports.get(StageName.AUDIO_ANALYSIS)
    if subtitle_id is None or audio_id is None:
        return StageResult.failed({"reason": "upstream_report_unavailable"})
    params = transcription_stage_parameters(state.plan.run_choices)
    result = state.functions.transcribe(
        state.plan.plan_id,
        subtitle_id,
        audio_id,
        state.layout.project_root,
        upgrade_all=params.upgrade_all,
    )
    status, report = _split(result)
    return _record(state, StageName.TRANSCRIPTION, map_stage_return(status, report))


def _invoke_enhancement(state: _CompositionState) -> StageResult:
    subtitle_id = state.reports.get(StageName.SUBTITLES)
    if subtitle_id is None:
        return StageResult.failed({"reason": "subtitle_report_unavailable"})
    params = enhancement_stage_parameters(state.plan.run_choices)
    result = state.functions.enhance(
        state.plan.plan_id,
        subtitle_id,
        state.layout.project_root,
        part_selectors=params.part_selectors,
        range_selectors=params.range_selectors,
        cue_selectors=params.cue_selectors,
        audio_report_id=state.reports.get(StageName.AUDIO_ANALYSIS),
    )
    status, report = _split(result)
    return _record(state, StageName.ENHANCEMENT, map_stage_return(status, report))


def _invoke_text_analysis(state: _CompositionState) -> StageResult:
    subtitle_id = state.reports.get(StageName.SUBTITLES)
    if subtitle_id is None:
        return StageResult.failed({"reason": "subtitle_report_unavailable"})
    result = state.functions.analyze_text(
        state.plan.plan_id,
        subtitle_id,
        state.layout.project_root,
        audio_report_id=state.reports.get(StageName.AUDIO_ANALYSIS),
    )
    status, report = _split(result)
    return _record(state, StageName.TEXT_ANALYSIS, map_stage_return(status, report))


def _invoke_visual_text(state: _CompositionState) -> StageResult:
    params = visual_text_stage_parameters(state.plan.run_choices)
    result = state.functions.run_visual_text(
        state.plan.plan_id,
        state.layout.project_root,
        all_parts=params.all_parts,
        part_selectors=params.part_selectors,
        range_selectors=params.range_selectors,
        audio_report_id=state.reports.get(StageName.AUDIO_ANALYSIS),
    )
    status, report = _split(result)
    return _record(state, StageName.VISUAL_TEXT, map_stage_return(status, report))


_STAGE_INVOKERS: Mapping[StageName, Callable[[_CompositionState], StageResult]] = {
    StageName.SOURCE_REVALIDATION: _invoke_source_revalidation,
    StageName.SUBTITLES: _invoke_subtitles,
    StageName.AUDIO_ANALYSIS: _invoke_audio,
    StageName.TRANSCRIPTION: _invoke_transcription,
    StageName.ENHANCEMENT: _invoke_enhancement,
    StageName.TEXT_ANALYSIS: _invoke_text_analysis,
    StageName.VISUAL_TEXT: _invoke_visual_text,
}


def _split(result: Mapping[str, object]) -> tuple[str, Mapping[str, object]]:
    status = result.get("status")
    report = result.get("report")
    if not isinstance(status, str):
        status = "failed"
    if not isinstance(report, Mapping):
        report = {}
    return status, report


def _report_id(report: Mapping[str, object]) -> str:
    report_id = report.get("report_id")
    return report_id if isinstance(report_id, str) else ""


def _build_executor(
    state: _CompositionState,
) -> Callable[[StageUnit, StageInvalidationKey], StageResult]:
    def executor(unit: StageUnit, key: StageInvalidationKey) -> StageResult:
        stage = unit.stage
        if stage not in state.results:
            # Collection-level: invoke the stage's function once, memoized, and
            # return its collective outcome for every Part unit of this stage.
            state.results[stage] = _STAGE_INVOKERS[stage](state)
        return state.results[stage]

    return executor


def _gather_evidence(state: _CompositionState) -> ProjectionEvidence:
    """Read the plainly-exposed collection documents from the workspaces.

    Conservative by design (ticket 10 option 1): it surfaces only artifacts a
    workspace exposes as a readable path, and records everything else as absent
    so the projection marks it ``unavailable``. Timed subtitle/transcript
    reconstruction is the deferred follow-up.
    """

    correction_log = _read_enhancement_artifact(state, "correction_log")
    return ProjectionEvidence(
        correction_log=PlainArtifactEvidence(content=correction_log)
        if correction_log is not None
        else None,
    )


def _read_enhancement_artifact(state: _CompositionState, key: str) -> str | None:
    """Read one enhancement artifact by its recorded path, defensively.

    Enhancement records each artifact as an ``InputEvidence`` with a ``path``;
    this reads that file when it exists and is readable, returning ``None`` on
    any absence so a missing artifact becomes ``unavailable``, never an error.
    """

    report_id = state.reports.get(StageName.ENHANCEMENT)
    if report_id is None:
        return None
    report_path = (
        state.layout.project_root
        / "work"
        / "enhancement-reports"
        / report_id
        / "enhancement-report.json"
    )
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    artifacts = report.get("artifacts") if isinstance(report, Mapping) else None
    if not isinstance(artifacts, Mapping):
        return None
    artifact = artifacts.get(key)
    if not isinstance(artifact, Mapping):
        return None
    path = artifact.get("path")
    if not isinstance(path, str):
        return None
    try:
        return (state.layout.project_root / path).read_text(encoding="utf-8")
    except OSError:
        return None


def _gather_report_inputs(state: _CompositionState) -> RunReportInputs:
    """Gather the audit report inputs from what the stages recorded.

    Conservative (ticket 10 option 1): the published content entries the run loop
    derives from the projection are the run's inventory floor. Deeper per-stage
    gate aggregation and workspace inventory records are the deferred follow-up,
    so this returns an empty set of recorded inputs rather than fabricated ones.
    """

    return RunReportInputs()


def build_run_composition(
    layout: RunLayout,
    plan: RunPlan,
    *,
    functions: StageFunctions | None = None,
) -> RunComposition:
    """Build the production composition for a confirmed plan's run.

    The returned composition invokes the per-phase functions in process, memoized
    per stage, chains their report ids, translates the plan's front-loaded
    choices into each function's selectors, and maps each stage's own pause
    vocabulary onto the DAG's decision-pause contract. The evidence and
    report-input gatherers read the resulting workspaces conservatively.
    """

    state = _CompositionState(layout=layout, plan=plan, functions=functions or StageFunctions())
    return RunComposition(
        executor=_build_executor(state),
        evidence=lambda: _gather_evidence(state),
        report_inputs=lambda: _gather_report_inputs(state),
    )
