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

import hashlib
import json
import os
import platform
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from video_content_pipeline import environment
from video_content_pipeline.audio_analysis import analyze_audio
from video_content_pipeline.capabilities import candidate_eligibility
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
    PublicationBasis,
    TimedArtifactEvidence,
    expected_subtitle_bases,
    transcript_basis,
)
from video_content_pipeline.real_engine_adapter import RealEngineSelection
from video_content_pipeline.run_choices import (
    COLLECTION_SCOPE,
    AsrMode,
    audio_analysis_stage_parameters,
    enhancement_stage_parameters,
    subtitle_stage_parameters,
    transcription_stage_parameters,
    visual_text_stage_parameters,
)
from video_content_pipeline.run_loop import RunComposition, RunReportInputs
from video_content_pipeline.run_reports import (
    EnvironmentInfo,
    ModelRecord,
    ParameterRecord,
    ResourceUsage,
    ToolRecord,
)
from video_content_pipeline.run_state import RunStateError, read_journal
from video_content_pipeline.stage_dag import (
    StageInvalidationKey,
    StageName,
    StageResult,
    StageResultKind,
    StageUnit,
)
from video_content_pipeline.subtitle_pipeline import (
    CandidateState,
    SubtitleCandidate,
    SubtitleCandidateReport,
    SubtitleReportError,
    process_subtitles,
    resume_subtitles,
)
from video_content_pipeline.text_analysis import analyze_text
from video_content_pipeline.transcription import subtitle_unavailable_parts, transcribe
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


# --- Real-vs-offline adapter selection (Phase 12 ticket 06) -----------------


class AdapterKind(Enum):
    """Which adapter a model capability runs in one orchestrated run.

    ``OFFLINE`` is the controlled offline adapter ADR 0037 keeps as the
    automated-test path; ``REAL`` drives the acquired real engine (Phase 11),
    selected only for a run whose registry says the capability is really
    acquired.
    """

    OFFLINE = "offline"
    REAL = "real"


#: The real-engine capabilities each model-bearing stage can run, so run
#: composition can hand a stage the subset of its capabilities the adapter profile
#: graded real. The deterministic subtitle stage names no model and is absent;
#: enhancement re-runs ASR over named intervals, so it shares ``asr_primary``.
#: This is the single source of truth for the real capabilities — the flat set
#: below and the engine-adapter verifier map are both bound to it.
_STAGE_CAPABILITIES: Mapping[StageName, tuple[str, ...]] = {
    StageName.AUDIO_ANALYSIS: ("vad", "forced_alignment", "diarization"),
    StageName.TRANSCRIPTION: ("asr_primary", "asr_review"),
    StageName.ENHANCEMENT: ("asr_primary",),
    StageName.TEXT_ANALYSIS: ("text_semantics",),
    StageName.VISUAL_TEXT: ("ocr_primary",),
}

#: The model capabilities selection considers real, derived from the stage map so
#: the two never drift. A capability not named here always stays offline.
REAL_ENGINE_CAPABILITIES: frozenset[str] = frozenset(
    capability for capabilities in _STAGE_CAPABILITIES.values() for capability in capabilities
)


@dataclass(frozen=True)
class AdapterProfile:
    """Per-capability real-vs-offline adapter selection for one orchestrated run.

    Built by :func:`select_adapter_profile` from the model registry's metadata
    alone; a capability the profile does not name defaults to ``OFFLINE`` so the
    absence of an entry is never mistaken for a real selection.
    """

    selections: Mapping[str, AdapterKind]

    def kind(self, capability: str) -> AdapterKind:
        return self.selections.get(capability, AdapterKind.OFFLINE)

    def is_real(self, capability: str) -> bool:
        return self.kind(capability) is AdapterKind.REAL

    @property
    def real_capabilities(self) -> frozenset[str]:
        return frozenset(
            capability
            for capability, kind in self.selections.items()
            if kind is AdapterKind.REAL
        )

    @property
    def any_real(self) -> bool:
        return any(kind is AdapterKind.REAL for kind in self.selections.values())


def select_adapter_profile(project_root: Path) -> AdapterProfile:
    """Choose, per model capability, the real engine or the controlled offline adapter.

    Pure metadata: reads only ``models/registry.json`` and never opens a model
    asset. A capability runs the real engine when the shared eligibility gate
    (:func:`~video_content_pipeline.capabilities.candidate_eligibility`) grades one
    of its schema-2 candidates ``eligible`` *and* that candidate carries no
    ``controlled_adapter`` fixture — the controlled offline adapter the ADR 0037
    automated-test path seeds. A real selection wins over an offline one whenever
    any eligible non-controlled candidate exists, whatever the candidate order.

    Everything else stays offline: an absent, unreadable, or schema-1 registry, a
    candidate carrying a controlled adapter, an ineligible candidate, or an
    unrecognised capability. So the automated suite — whose registries carry
    controlled adapters or grade ineligible — always selects offline, and a real
    acquired registry selects real. A candidate the registry lists as eligible but
    whose asset is missing on disk is still selected real here; the stage's real
    adapter then fails typed at invocation (never a download), per the ticket.
    """

    registry_path = project_root / "models" / "registry.json"
    try:
        decoded = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return AdapterProfile({})
    if not isinstance(decoded, Mapping) or decoded.get("schema_version") != 2:
        return AdapterProfile({})
    candidates = decoded.get("candidates")
    if not isinstance(candidates, list):
        return AdapterProfile({})
    selections: dict[str, AdapterKind] = {}
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        capability = candidate.get("capability")
        if capability not in REAL_ENGINE_CAPABILITIES:
            continue
        if candidate.get("controlled_adapter") is not None:
            # A controlled offline adapter fixture is always the test path; it
            # never forces a capability real, but it must not overwrite a real
            # selection another candidate of the same capability already earned.
            selections.setdefault(capability, AdapterKind.OFFLINE)
            continue
        state, _reason = candidate_eligibility(candidate, project_root)
        if state == "eligible":
            selections[capability] = AdapterKind.REAL
        else:
            selections.setdefault(capability, AdapterKind.OFFLINE)
    return AdapterProfile(dict(selections))


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
    profile: AdapterProfile = field(default_factory=lambda: AdapterProfile({}))
    reports: dict[StageName, str] = field(default_factory=dict)
    results: dict[StageName, StageResult] = field(default_factory=dict)
    #: The subtitle candidate report the subtitles stage produced, retained so
    #: transcription can read its ASR handoff and the evidence gatherer its
    #: source/readable renderings without re-locating it on disk.
    subtitle_report: SubtitleCandidateReport | None = None
    #: Source ids whose retained subtitle report hands off to ASR planning.
    subtitle_asr_handoff: tuple[str, ...] = ()


def _record(state: _CompositionState, stage: StageName, result: StageResult) -> StageResult:
    report_id = result.detail.get("report_id")
    if result.kind is StageResultKind.COMPLETED and isinstance(report_id, str):
        state.reports[stage] = report_id
    return result


def _real_engines(state: _CompositionState, stage: StageName) -> RealEngineSelection | None:
    """The real-adapter selection for ``stage``, or ``None`` when it is all-offline.

    The intersection of the stage's capabilities and the profile's real set; an
    all-offline stage (every automated-test run, and any capability the registry
    has not promoted to a real acquisition) yields ``None`` so the stage function
    keeps its controlled offline path unchanged.
    """

    selected = frozenset(_STAGE_CAPABILITIES.get(stage, ())) & state.profile.real_capabilities
    if not selected:
        return None
    return RealEngineSelection(project_root=state.layout.project_root, capabilities=selected)


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
    # The subtitle stage is deterministic (Phase 4) and names no model, so it has
    # no real-adapter path; it always runs unchanged.
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
    _record_subtitle_handoff(state, report)
    mapped = map_stage_return(status, report)
    if _is_full_asr_handoff(state, mapped):
        # A source with no usable embedded subtitle track makes the subtitles stage
        # return ``blocked``, but when every Part is instead recorded as a clean
        # full-ASR handoff the stage has not failed — it has produced exactly the
        # report transcription needs. Complete the stage (carrying its report id) so
        # the run proceeds to transcription, which applies its own full-ASR logic (a
        # subtitle-first upgrade or a full-ASR resource-confirmation pause), rather
        # than failing the whole run at the subtitles stage. A genuinely broken
        # subtitle report leaves at least one Part outside the handoff set and so
        # stays a failure.
        mapped = StageResult.completed({"report_id": report.get("report_id")})
    return _record(state, StageName.SUBTITLES, mapped)


def _is_full_asr_handoff(state: _CompositionState, mapped: StageResult) -> bool:
    """Whether a failed subtitle mapping is really an all-Parts full-ASR handoff.

    True only when the mapping failed, the subtitle report parsed, and *every* Part
    of the plan is in the report's ASR handoff set (``subtitle_unavailable_parts``,
    recorded by :func:`_record_subtitle_handoff`). Requiring all Parts keeps a
    genuine subtitle failure — where some Part is broken rather than merely
    subtitle-unavailable — a failure.
    """

    if mapped.kind is not StageResultKind.FAILED or state.subtitle_report is None:
        return False
    part_ids = {artifact.source_id for artifact in state.plan.source_artifacts}
    return bool(part_ids) and part_ids <= set(state.subtitle_asr_handoff)


def _record_subtitle_handoff(state: _CompositionState, report: Mapping[str, object]) -> None:
    """Retain which Parts the subtitle report hands off to ASR planning.

    Read from the report the stage just produced, using the subtitle context's
    own ``subtitle_unavailable_parts`` rule so transcription later matches the
    expert command's notion of a full-ASR handoff exactly. A report that cannot
    be parsed leaves the handoff empty; transcription's own revalidation stays the
    authority on a genuinely broken report.
    """

    report_path = report.get("report_path")
    if not isinstance(report_path, str):
        return
    try:
        parsed = SubtitleCandidateReport.from_json(report, Path(report_path))
    except (SubtitleReportError, ValueError):
        return
    state.subtitle_report = parsed
    state.subtitle_asr_handoff = subtitle_unavailable_parts(parsed)


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
        real_engines=_real_engines(state, StageName.AUDIO_ANALYSIS),
    )
    status, report = _split(result)
    return _record(state, StageName.AUDIO_ANALYSIS, map_stage_return(status, report))


def _invoke_transcription(state: _CompositionState) -> StageResult:
    subtitle_id = state.reports.get(StageName.SUBTITLES)
    audio_id = state.reports.get(StageName.AUDIO_ANALYSIS)
    if subtitle_id is None or audio_id is None:
        return StageResult.failed({"reason": "upstream_report_unavailable"})
    params = transcription_stage_parameters(state.plan.run_choices)
    if (
        state.plan.run_choices.asr_mode() is AsrMode.SUBTITLE_FIRST
        and not params.upgrade_all
        and state.subtitle_report is not None
        and not state.subtitle_asr_handoff
    ):
        # A subtitle-priority run never triggers ASR automatically (the
        # transcription context's own contract). When the subtitle report parsed
        # cleanly, every Part kept a usable subtitle (no ASR handoff), and no
        # explicit upgrade was requested, there is nothing to transcribe: complete
        # the stage as a no-op rather than tripping transcribe's precondition
        # guard, which would fail the run. A full-ASR run, an upgrade, or an
        # unparsed report all fall through to transcribe's own revalidation.
        return _record(state, StageName.TRANSCRIPTION, StageResult.completed())
    result = state.functions.transcribe(
        state.plan.plan_id,
        subtitle_id,
        audio_id,
        state.layout.project_root,
        upgrade_all=params.upgrade_all,
        real_engines=_real_engines(state, StageName.TRANSCRIPTION),
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
        real_engines=_real_engines(state, StageName.ENHANCEMENT),
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
        real_engines=_real_engines(state, StageName.TEXT_ANALYSIS),
        transcription_report_id=state.reports.get(StageName.TRANSCRIPTION),
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
        real_engines=_real_engines(state, StageName.VISUAL_TEXT),
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
    """Read the published core artifacts' content from the stage workspaces.

    Phase 10 ticket 08 extends the Phase 9 floor exactly far enough for the
    end-to-end bundle's core content artifacts to be VALID, and no further. Each
    reader surfaces only what a workspace plainly exposes as readable bytes and
    returns ``None`` on any absence, so a missing artifact becomes ``unavailable``
    (no file, hash-verifiable bundle) rather than an error. The extensions are:

    * subtitles — the source-preserving and readable renderings the subtitle
      stage already wrote, published per Part and (for a single-Part collection)
      at the collection root in the mode's declared bases;
    * transcript — a plain rendering of the source cues in the mode's transcript
      basis;
    * content-report and segments — the text-analysis stage's rendered report and
      its verified segments;
    * correction-log — unchanged from Phase 9 (enhancement only).
    """

    mode = state.plan.run_choices.asr_mode()
    part_subtitles: dict[tuple[str, PublicationBasis], TimedArtifactEvidence] = {}
    collection_subtitles: dict[PublicationBasis, TimedArtifactEvidence] = {}
    transcript: TimedArtifactEvidence | None = None
    if mode is not None and state.subtitle_report is not None:
        part_subtitles, collection_subtitles = _gather_subtitle_evidence(state, mode)
        transcript = _gather_transcript_evidence(state, mode)
    content_report = _read_text_content_report(state)
    segments = _read_text_segments(state)
    correction_log = _read_enhancement_artifact(state, "correction_log")
    return ProjectionEvidence(
        part_subtitles=part_subtitles,
        collection_subtitles=collection_subtitles,
        collection_transcript=transcript,
        content_report=PlainArtifactEvidence(content=content_report)
        if content_report is not None
        else None,
        segments=PlainArtifactEvidence(content=segments) if segments is not None else None,
        correction_log=PlainArtifactEvidence(content=correction_log)
        if correction_log is not None
        else None,
    )


#: The subtitle candidate rendering each publication basis publishes. Only the
#: bases a mode declares (``expected_subtitle_bases``) are ever gathered.
_BASIS_RENDERINGS: Mapping[PublicationBasis, str] = {
    PublicationBasis.SOURCE: "source_srt_path",
    PublicationBasis.READABLE: "readable_vtt_path",
}


def _gather_subtitle_evidence(
    state: _CompositionState, mode: AsrMode
) -> tuple[
    dict[tuple[str, PublicationBasis], TimedArtifactEvidence],
    dict[PublicationBasis, TimedArtifactEvidence],
]:
    """Gather per-Part and collection subtitle renderings in the mode's bases.

    A candidate's source-preserving and readable exports are read verbatim. The
    collection-level rendering is only emitted for a single-Part collection, where
    CollectionVirtualTime coincides with the Part's PartRelativeTime; a genuine
    multi-Part virtual splice is a later ticket, so it stays ``unavailable``.
    """

    report = state.subtitle_report
    assert report is not None
    bases = tuple(basis for basis in expected_subtitle_bases(mode) if basis in _BASIS_RENDERINGS)
    part_subtitles: dict[tuple[str, PublicationBasis], TimedArtifactEvidence] = {}
    valid_by_source = _valid_candidates_by_source(report)
    for source_id, candidate in valid_by_source.items():
        for basis in bases:
            content = _read_optional_text(getattr(candidate, _BASIS_RENDERINGS[basis], None))
            if content is not None:
                part_subtitles[(source_id, basis)] = TimedArtifactEvidence(original=content)
    collection_subtitles: dict[PublicationBasis, TimedArtifactEvidence] = {}
    if len(valid_by_source) == 1:
        (only_source,) = tuple(valid_by_source)
        for basis in bases:
            evidence = part_subtitles.get((only_source, basis))
            if evidence is not None:
                collection_subtitles[basis] = evidence
    return part_subtitles, collection_subtitles


def _gather_transcript_evidence(
    state: _CompositionState, mode: AsrMode
) -> TimedArtifactEvidence | None:
    """Render a plain transcript of the source cues in the mode's transcript basis.

    Only the source basis is reconstructed (a subtitle-priority run's transcript
    is its cues as prose); any other basis stays ``unavailable`` until the stage
    that would produce it is wired. Emitted only for a single-Part collection, for
    the same CollectionVirtualTime reason as the collection subtitles.
    """

    if transcript_basis(mode) is not PublicationBasis.SOURCE:
        return None
    report = state.subtitle_report
    assert report is not None
    valid = _valid_candidates_by_source(report)
    if len(valid) != 1:
        return None
    ((_source_id, candidate),) = valid.items()
    lines = _read_source_cue_lines(state, candidate)
    if lines is None:
        return None
    return TimedArtifactEvidence(original="".join(f"{line}\n" for line in lines))


def _valid_candidates_by_source(
    report: SubtitleCandidateReport,
) -> dict[str, SubtitleCandidate]:
    """Return the first VALID candidate per source id, in report order."""

    chosen: dict[str, SubtitleCandidate] = {}
    for candidate in report.candidates:
        if candidate.state is CandidateState.VALID:
            chosen.setdefault(candidate.source_id, candidate)
    return chosen


def _read_source_cue_lines(
    state: _CompositionState, candidate: SubtitleCandidate
) -> list[str] | None:
    """Read a candidate's retained source cues as their ordered text lines."""

    path = _read_candidate_path(state, candidate.source_candidate_path)
    if path is None:
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    cues = document.get("cues") if isinstance(document, Mapping) else None
    if not isinstance(cues, list):
        return None
    lines: list[str] = []
    for cue in cues:
        text = cue.get("text") if isinstance(cue, Mapping) else None
        if isinstance(text, str):
            lines.append(text)
    return lines


def _read_text_content_report(state: _CompositionState) -> str | None:
    """Render the published content report from the text stage's verified segments.

    Deliberately rendered from the segments rather than reusing the stage's own
    ``text-analysis-report.md``: that internal audit document echoes the run's
    plan id, which would make an otherwise content-only artifact vary by install
    location. This rendering carries only segment content, so identical inputs
    yield identical bytes.
    """

    segments = _text_segments(state)
    if segments is None:
        return None
    lines = ["# 内容报告", ""]
    for segment in segments:
        title = segment.get("title") if isinstance(segment, Mapping) else None
        ordinal = segment.get("ordinal") if isinstance(segment, Mapping) else None
        text = title.get("text") if isinstance(title, Mapping) else None
        heading = text if isinstance(text, str) and text else f"段落 {ordinal}"
        lines.append(f"## {heading}")
    return "\n".join(lines) + "\n"


def _read_text_segments(state: _CompositionState) -> str | None:
    """Serialize the text-analysis report's verified segments as ``segments.json``.

    The segments are the run's own verified evidence, re-rendered deterministically
    (sorted keys) so byte-identical inputs yield a byte-identical artifact.
    """

    segments = _text_segments(state)
    if segments is None:
        return None
    return json.dumps({"schema_version": 1, "segments": segments}, sort_keys=True) + "\n"


def _text_segments(state: _CompositionState) -> list[object] | None:
    """Read the verified segments list from the text-analysis report, if present."""

    workspace = _text_analysis_workspace(state)
    if workspace is None:
        return None
    try:
        document = json.loads((workspace / "text-analysis-report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    segments = document.get("segments") if isinstance(document, Mapping) else None
    return segments if isinstance(segments, list) else None


def _text_analysis_workspace(state: _CompositionState) -> Path | None:
    report_id = state.reports.get(StageName.TEXT_ANALYSIS)
    if report_id is None:
        return None
    return state.layout.project_root / "work" / "text-analysis-reports" / report_id


def _read_candidate_path(state: _CompositionState, recorded: object) -> Path | None:
    if not isinstance(recorded, str):
        return None
    path = Path(recorded)
    resolved = path if path.is_absolute() else state.layout.project_root / path
    return resolved if resolved.is_file() else None


def _read_optional_text(recorded: object) -> str | None:
    if not isinstance(recorded, str):
        return None
    try:
        return Path(recorded).read_text(encoding="utf-8")
    except OSError:
        return None


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


#: The stage report each model-bearing stage writes, relative to
#: ``work/`` and keyed by the report id the executor chained. Only stages that
#: can name a model appear here; the deterministic subtitle stage never does.
#: The map is the single place the provenance gatherer looks, so a stage that did
#: not complete (no chained report id) contributes nothing — an honest omission,
#: never a padded placeholder (ticket 05).
_STAGE_REPORT_FILES: Mapping[StageName, tuple[str, str]] = {
    StageName.AUDIO_ANALYSIS: ("audio-analysis-reports", "audio-analysis-report.json"),
    StageName.TRANSCRIPTION: ("transcription-reports", "transcription-report.json"),
    StageName.ENHANCEMENT: ("enhancement-reports", "enhancement-report.json"),
    StageName.TEXT_ANALYSIS: ("text-analysis-reports", "text-analysis-report.json"),
    StageName.VISUAL_TEXT: ("visual-text-reports", "visual-report.json"),
}

#: A readable purpose per approved external tool, so the processing report's tool
#: section says what each binary was for. Unknown ids fall back to a neutral
#: label rather than inventing a specific one.
_TOOL_PURPOSES: Mapping[str, str] = {
    "ffmpeg": "音频/视频解码与派生提取",
    "ffprobe": "媒体结构与覆盖探测",
    "yt-dlp": "URL 素材获取",
}


def _read_stage_reports(state: _CompositionState) -> list[Mapping[str, object]]:
    """Read back every completed stage's own report document, defensively.

    Only stages the executor chained a report id for (``state.reports``) and that
    can name a model are read; a missing or unparsable report is skipped so the
    audit floor is always produced. This is the sole seam the provenance
    gatherers read a stage's recorded model identity and resource evidence from.
    """

    documents: list[Mapping[str, object]] = []
    for stage, report_id in state.reports.items():
        located = _STAGE_REPORT_FILES.get(stage)
        if located is None:
            continue
        directory, filename = located
        report_path = state.layout.project_root / "work" / directory / report_id / filename
        try:
            document = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(document, Mapping):
            documents.append(document)
    return documents


def _completed_executions(
    reports: Sequence[Mapping[str, object]],
) -> Iterator[Mapping[str, object]]:
    """Yield every ``stage_execution`` entry a stage recorded as ``completed``.

    A stage records one ``stage_execution`` entry per model it actually ran; a
    ``completed`` entry names the registry candidate whose engine produced output
    and references that engine's measured resource use. Reading this — rather than
    a capability *assessment* list, which grades eligible-but-unused candidates
    too — is what keeps the report to the engines the run actually selected
    (ticket 05: "no padding").

    Today only audio analysis records ``stage_execution`` (the offline path runs
    no other model); transcription, text, and visual-text gain their executed-model
    and subprocess-peak evidence when the orchestrated run invokes the real engines
    (Phase 12 ticket 06). This one seam is where that evidence flows in, so both the
    models section and the peak-memory measurement extend the moment those stages
    record it — no other change needed here.
    """

    for report in reports:
        executions = report.get("stage_execution")
        if not isinstance(executions, list):
            continue
        for execution in executions:
            if isinstance(execution, Mapping) and execution.get("state") == "completed":
                yield execution


def _executed_candidate_ids(reports: Sequence[Mapping[str, object]]) -> list[str]:
    """Collect the registry candidate ids the stages recorded as executed."""

    return [
        str(execution["candidate_id"])
        for execution in _completed_executions(reports)
        if isinstance(execution.get("candidate_id"), str)
    ]


def _load_registry_candidates(project_root: Path) -> dict[str, Mapping[str, object]]:
    """Index the model registry's candidates by id, tolerating any read failure.

    The gatherer never invents model facts: a candidate the run executed is
    described from its own registry entry, so a missing or malformed registry
    simply yields no model records rather than an error.
    """

    registry_path = project_root / "models" / "registry.json"
    try:
        decoded = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    candidates = decoded.get("candidates") if isinstance(decoded, Mapping) else None
    if not isinstance(candidates, list):
        return {}
    indexed: dict[str, Mapping[str, object]] = {}
    for candidate in candidates:
        candidate_id = candidate.get("candidate_id") if isinstance(candidate, Mapping) else None
        if isinstance(candidate_id, str):
            indexed[candidate_id] = candidate
    return indexed


def _candidate_size_bytes(candidate: Mapping[str, object]) -> int:
    """Return the model's on-disk size from the registry entry, or 0 if absent.

    Prefers the recorded ``total_size_bytes``; falls back to summing the pinned
    file manifest so a registry that only carries the manifest still reports a
    real size.
    """

    total = candidate.get("total_size_bytes")
    if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
        return total
    manifest = candidate.get("file_manifest")
    if isinstance(manifest, list):
        summed = 0
        for entry in manifest:
            size = entry.get("size") if isinstance(entry, Mapping) else None
            if isinstance(size, int) and not isinstance(size, bool) and size >= 0:
                summed += size
        return summed
    return 0


def _str_field(candidate: Mapping[str, object], key: str, default: str = "") -> str:
    """Read a string registry field, falling back to ``default`` when absent."""

    value = candidate.get(key)
    return value if isinstance(value, str) else default


def _model_record(candidate: Mapping[str, object]) -> ModelRecord:
    """Summarise one executed registry candidate for the readable report."""

    return ModelRecord(
        name=_str_field(candidate, "model_id", _str_field(candidate, "candidate_id")),
        revision=_str_field(candidate, "revision"),
        sha256=_str_field(candidate, "asset_sha256"),
        path=_str_field(candidate, "local_path"),
        size_bytes=_candidate_size_bytes(candidate),
        purpose=_str_field(candidate, "purpose"),
    )


def _gather_models(
    state: _CompositionState, reports: Sequence[Mapping[str, object]]
) -> tuple[ModelRecord, ...]:
    """Describe every model the run executed from its registry entry.

    The engines the run selected are read from the completed stages' execution
    records (:func:`_completed_executions`) and described from
    ``models/registry.json``; a candidate the registry does not carry, or a run
    that executed no model, yields no record. Deduplicated and name-sorted for a
    byte-stable report.
    """

    registry = _load_registry_candidates(state.layout.project_root)
    seen: set[str] = set()
    records: list[ModelRecord] = []
    for candidate_id in _executed_candidate_ids(reports):
        if candidate_id in seen:
            continue
        candidate = registry.get(candidate_id)
        if candidate is None:
            continue
        seen.add(candidate_id)
        records.append(_model_record(candidate))
    return tuple(sorted(records, key=lambda record: (record.name, record.sha256)))


def _gather_tools(plan: RunPlan) -> tuple[ToolRecord, ...]:
    """Describe the external tools the confirmed plan pinned for the run."""

    return tuple(
        ToolRecord(
            name=tool.tool_id,
            path=tool.path.as_posix(),
            version=tool.version,
            purpose=_TOOL_PURPOSES.get(tool.tool_id, "外部工具"),
        )
        for tool in plan.tools
    )


def _gather_environment() -> EnvironmentInfo:
    """Capture the interpreter identity that actually executed the run.

    Python version and virtual-environment come from the running interpreter, and
    the lockfile digest from the repository ``uv.lock`` — the environment the run
    used, independent of where its ``work/`` tree happened to live. An unreadable
    lockfile records an empty digest rather than failing the audit floor.
    """

    lockfile_path = environment.project_root() / "uv.lock"
    try:
        lockfile_sha256 = hashlib.sha256(lockfile_path.read_bytes()).hexdigest()
    except OSError:
        lockfile_sha256 = ""
    return EnvironmentInfo(
        python_version=platform.python_version(),
        virtualenv_path=os.environ.get("VIRTUAL_ENV") or sys.prefix,
        lockfile_sha256=lockfile_sha256,
    )


def _gather_parameters(plan: RunPlan) -> tuple[ParameterRecord, ...]:
    """Record the run-affecting parameters fixed at plan confirmation.

    Every front-loaded run choice becomes one parameter line, plus the plan's
    configuration fingerprint, so the readable report states exactly what shaped
    the run. Collection-scoped choices drop the redundant scope suffix.
    """

    parameters = [
        ParameterRecord(name="configuration_fingerprint", value=plan.configuration_fingerprint)
    ]
    for choice in plan.run_choices.choices:
        scope = "" if choice.scope == COLLECTION_SCOPE else f" [{choice.scope}]"
        parameters.append(
            ParameterRecord(name=f"{choice.stage}.{choice.key}{scope}", value=choice.value)
        )
    return tuple(parameters)


def _measured_peak_memory_bytes(reports: Sequence[Mapping[str, object]]) -> int | None:
    """Return the largest recorded model-runtime peak across executed stages.

    Each completed ``stage_execution`` entry references a resource-measurement
    document whose ``peak_bytes`` is the subprocess (or controlled-adapter) peak
    for that model; the run's peak is the largest recorded, since stages load one
    model at a time. This reads the same executed-stage seam as the models
    section, so it covers exactly the stages that recorded a measurement — audio
    today, and the remaining engines once ticket 06 records theirs (see
    :func:`_completed_executions`). ``None`` when no stage recorded a measurement.
    """

    peaks = [
        peak
        for execution in _completed_executions(reports)
        if (peak := _read_peak_bytes(execution.get("resource_measurement"))) is not None
    ]
    return max(peaks) if peaks else None


def _read_peak_bytes(measurement: object) -> int | None:
    """Read ``peak_bytes`` from a resource-measurement evidence reference."""

    if not isinstance(measurement, Mapping):
        return None
    path = measurement.get("path")
    if not isinstance(path, str):
        return None
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    peak = document.get("peak_bytes") if isinstance(document, Mapping) else None
    if isinstance(peak, int) and not isinstance(peak, bool) and peak >= 0:
        return peak
    return None


def _elapsed_seconds(state: _CompositionState) -> float | None:
    """Measure the run's wall-clock span from its own append-only journal.

    The first and last recorded event bracket the run; their timestamp delta is
    the elapsed time. ``None`` when fewer than two events exist or a timestamp
    cannot be parsed, so a placeholder is never fabricated.
    """

    try:
        events = read_journal(state.layout.journal_path)
    except RunStateError:
        return None
    if len(events) < 2:
        return None
    ordered = sorted(events, key=lambda event: event.sequence)
    try:
        first = datetime.fromisoformat(ordered[0].at)
        last = datetime.fromisoformat(ordered[-1].at)
    except ValueError:
        return None
    return max(0.0, (last - first).total_seconds())


def _disk_delta_bytes(state: _CompositionState) -> int | None:
    """Sum the bytes the run wrote into its own ``work/`` subtree.

    A run creates its work directory from scratch, so the total size of that
    subtree is the disk the run itself added. ``None`` when the directory is
    absent (a run that failed before creating it).
    """

    work_dir = state.layout.work_dir
    if not work_dir.is_dir():
        return None
    total = 0
    for path in work_dir.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def _gather_resource_usage(
    state: _CompositionState, reports: Sequence[Mapping[str, object]]
) -> ResourceUsage:
    """Measure the run's elapsed time, peak model memory, and disk footprint."""

    return ResourceUsage(
        elapsed_seconds=_elapsed_seconds(state),
        peak_memory_bytes=_measured_peak_memory_bytes(reports),
        disk_delta_bytes=_disk_delta_bytes(state),
    )


def _gather_report_inputs(state: _CompositionState) -> RunReportInputs:
    """Gather the audit report inputs from what the stages recorded.

    Reads the completed stages' own reports for the models they executed and the
    resource evidence they measured, the confirmed plan for tools and parameters,
    and the running interpreter for the environment identity — the full RunBundle
    provenance (ticket 05). Every read is defensive: a missing or malformed source
    contributes nothing rather than failing the always-published audit floor, and
    the published-content inventory floor still comes from the run loop's
    projection. Gate aggregation and workspace inventory records remain the
    deferred follow-up.
    """

    reports = _read_stage_reports(state)
    return RunReportInputs(
        models=_gather_models(state, reports),
        tools=_gather_tools(state.plan),
        environment=_gather_environment(),
        parameters=_gather_parameters(state.plan),
        resource_usage=_gather_resource_usage(state, reports),
    )


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

    Before execution it selects, per capability, the real engine or the controlled
    offline adapter (:func:`select_adapter_profile`) from the run's registry
    metadata alone — no model is loaded here. Each model-bearing stage is handed
    the subset of its capabilities graded real; a stage with none keeps its offline
    path. The automated suite always selects offline (Phase 12 ticket 06).
    """

    profile = select_adapter_profile(layout.project_root)
    state = _CompositionState(
        layout=layout, plan=plan, functions=functions or StageFunctions(), profile=profile
    )
    return RunComposition(
        executor=_build_executor(state),
        evidence=lambda: _gather_evidence(state),
        report_inputs=lambda: _gather_report_inputs(state),
    )
