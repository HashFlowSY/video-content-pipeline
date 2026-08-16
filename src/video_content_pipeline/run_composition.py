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
from pathlib import Path

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
    PublicationBasis,
    TimedArtifactEvidence,
    expected_subtitle_bases,
    transcript_basis,
)
from video_content_pipeline.run_choices import (
    AsrMode,
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
