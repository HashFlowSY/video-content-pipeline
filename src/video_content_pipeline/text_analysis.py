"""Phase 6's immutable text-analysis workspace, CLI contract, and revalidation.

Ticket 01 established the immutable workspace, the domain records, the report
identity, and the ``controlled_adapter_unavailable`` result. Ticket 02 adds the
``vcp analyze-text`` and explicit ``vcp resume-text-analysis`` public commands
and completes input revalidation before an attempt may proceed: a confirmed
RunPlan and its ready PlanReport (including SourceArtifact hashes and hash-pinned
inspection evidence), the retained Subtitle candidate report with every selected
Primary track, the versioned subtitle and text-analysis rules, and an optional
Audio analysis report binding. Any drift blocks the attempt as ``failed``.

Ticket 03 binds the versioned generation and rendering contracts: a fully
revalidated attempt now also revalidates the versioned prompt template, output
projection schema, evidence-rule record, and Controlled offline text adapter
identity (see ``text_contracts``), records their hash evidence, and writes a
deterministic Markdown rendition of the authoritative JSON report into the
immutable workspace.

Ticket 07 makes every attempt's provenance immutable and auditable: a fully
revalidated attempt records an ``attempt_provenance`` binding its prompt and
deterministically rendered prompt, its input-cue manifest, the adapter identity,
sampling, output-schema and evidence-rule hashes, the raw-output and projection
state, and an execution-resource measurement. It also evaluates the
future-real-model 12 GiB resource envelope, retaining a resumable
``resource_envelope_exceeded`` report when a conservative estimate exceeds it.
``resume_text_analysis`` continues only such a retained
pause, from an explicit decision, as a fresh non-overwriting attempt — there is
no automatic retry. Append-only synthetic human-review records live in
``text_review``.

Ticket 08 completes the offline contract: a fully revalidated attempt whose
Controlled offline text adapter binds a hash-pinned synthetic output fixture to
these exact revalidated cues now *generates*. ``_run_controlled_generation``
projects that retained output through the versioned schema and composes it — via
``text_generation`` — into verified SemanticSegments, chapters, and a collection
summary, concluding ``complete`` or ``partial``. Without a bound fixture the
adapter cannot generate and the attempt still retains
``controlled_adapter_unavailable`` with no semantic content; an invalid whole
projection fails the attempt while its raw output stays restricted audit evidence.
The adapter is not a model asset and can never earn a real-model qualification. See
``docs/PHASE_06_SPECIFICATION.md`` and the Text Analysis Context.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
from pathlib import Path

from video_content_pipeline.capabilities import (
    MAX_MODEL_RESOURCE_BYTES,
    CandidateAssessment,
    assess_candidate,
    capability_state_from_grades,
    load_candidate_matrix,
)
from video_content_pipeline.evidence import (
    InputEvidence,
    validated_report_id,
    write_bytes_once,
    write_json_once,
    write_text_once,
)
from video_content_pipeline.planning import (
    PlanningDiagnostic,
    PlanningError,
    RunPlan,
    confirmed_plan_matches,
    load_plan_report,
    load_run_plan,
    revalidate_confirmed_inspection_evidence,
)
from video_content_pipeline.real_engine_adapter import (
    RealEngineSelection,
    dispatch_real_stage,
)
from video_content_pipeline.source import SourceArtifact, sha256_file
from video_content_pipeline.subtitle_pipeline import (
    CandidateReportState,
    CandidateState,
    SubtitleCandidate,
    SubtitleCandidateReport,
    SubtitleReportError,
    subtitle_rules_fingerprint,
)
from video_content_pipeline.text_aggregation import (
    Chapter,
    CollectionSummary,
    TextAggregationError,
)
from video_content_pipeline.text_contracts import (
    TextContractError,
    TextGenerationContracts,
    project_text_model_output,
    render_text_analysis_markdown,
    revalidate_text_generation_contracts,
)
from video_content_pipeline.text_generation import (
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_PARTIAL,
    GeneratedSegment,
    LoadedPart,
    TextGenerationError,
    UnavailablePartInfo,
    generate_analysis,
    input_cue_manifest_document,
    input_cue_manifest_sha256,
    load_controlled_generation,
    load_cue_inventory,
)
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval

# The future-real-model one-large-model rule: a conservative resource estimate
# above this envelope pauses for an explicit resource decision instead of
# silently altering the model, quantization, context, or sampling. The Controlled
# offline text adapter loads no model asset, so its resource measurement is
# ``not_applicable`` and never exceeds this envelope. The envelope is the shared
# machine ceiling defined once in :mod:`video_content_pipeline.capabilities`.
TEXT_MODEL_RESOURCE_ENVELOPE_BYTES = MAX_MODEL_RESOURCE_BYTES

# The explicit decision that continues a retained resource-envelope pause. The
# name matches the Phase 5 audio-analysis resume convention.
_RESOURCE_DECISION = "resource_configuration_changed"

# Raw adapter or model output is restricted local audit evidence: its pointer
# may be summarized in workspace diagnostics but never carries the raw content
# into a formal report or default publication.
_RESTRICTED_LOCAL_AUDIT = "local_audit_only"


class TextAnalysisReportStatus(StrEnum):
    """The recorded outcome of one text-analysis attempt.

    ``complete``/``partial``/``failed`` are the formal Text analysis report
    statuses. ``controlled_adapter_unavailable`` is the availability outcome
    recorded when no eligible offline text adapter exists, and
    ``resource_envelope_exceeded`` is the resumable decision-pause outcome
    recorded when a conservative future-real-model resource estimate exceeds the
    12 GiB envelope; both retain no SemanticSegments. ``model_acquisition_required``
    is the ``text_semantics`` capability-evaluation outcome recorded when the real
    text-semantics model is not yet an eligible, acquired engine (Phase 11 ticket
    10): a registry-only evaluation that produces no SemanticSegments, mirroring the
    transcription and visual-text capability results.
    """

    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    CONTROLLED_ADAPTER_UNAVAILABLE = "controlled_adapter_unavailable"
    RESOURCE_ENVELOPE_EXCEEDED = "resource_envelope_exceeded"
    MODEL_ACQUISITION_REQUIRED = "model_acquisition_required"


class TextAnalysisError(ValueError):
    """A rejected Phase 6 input with a machine-readable diagnostic reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


# --- text_semantics capability evaluation (Phase 11 ticket 10) ----------------
#
# ``text_semantics`` is this phase's one new capability. Like transcription's
# ``asr_*`` and visual-text's ``ocr_primary``, it is evaluated from
# ``models/registry.json`` through the shared, security-sensitive eligibility gate
# (:mod:`video_content_pipeline.capabilities`) and never downloads or runs a model.
# The Controlled offline text adapter is not a registry candidate and carries no
# pinned asset hash, so it can never grade as an eligible real model here (ADR 0037
# lineage): the capability's real-model path is satisfied only by an eligible,
# acquired registry candidate.

#: The provider-neutral text-semantics capability defined by Phase 11.
TEXT_SEMANTICS_CAPABILITY = "text_semantics"
TEXT_SEMANTICS_CAPABILITIES: tuple[str, ...] = (TEXT_SEMANTICS_CAPABILITY,)


@dataclass(frozen=True)
class TextSemanticsCapabilityAvailability:
    """The explicit availability state for the ``text_semantics`` capability."""

    capability: str
    state: str
    candidates: tuple[CandidateAssessment, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "capability": self.capability,
            "state": self.state,
            "model": None,
            "diagnostic": {
                "reason": self.state,
                "message": _capability_message(self.capability, self.state),
            },
            "candidates": [candidate.as_json() for candidate in self.candidates],
        }


@dataclass(frozen=True)
class TextSemanticsCapabilityReport:
    """Immutable ``text_semantics`` capability evaluation with no text evidence."""

    result: str
    capabilities: tuple[TextSemanticsCapabilityAvailability, ...]
    model_registry_evidence: InputEvidence | None

    def as_json(self) -> dict[str, object]:
        return {
            "result": self.result,
            "capabilities": [capability.as_json() for capability in self.capabilities],
            "text_analysis_evidence": None,
            "model_registry": (
                self.model_registry_evidence.as_json()
                if self.model_registry_evidence is not None
                else None
            ),
            "guarantees": {
                "model_acquisition": "not_attempted",
                "model_execution": "not_attempted",
                "network_access": "not_attempted",
                "outputs_publication": "not_attempted",
            },
        }


def evaluate_text_semantics_capability(project_root: Path) -> TextSemanticsCapabilityReport:
    """Evaluate ``text_semantics`` from the model registry, offline.

    With no eligible, acquired model available the result is always
    ``model_acquisition_required`` and no text-analysis evidence is produced; the
    per-capability state and candidate grades carry the detail a later, separately
    authorized acquisition and execution step consumes. Same semantics as the audio
    ``asr_*`` and visual-text ``ocr_primary`` capabilities: a credential-gated
    candidate keeps the capability ``model_credential_gated``, an over-envelope or
    evidence-incomplete candidate is ``model_ineligible``, and an eligible candidate
    (or no candidate at all) means acquisition is the remaining step.
    """

    registry_path = project_root / "models" / "registry.json"
    if not registry_path.exists():
        return _text_semantics_report(
            tuple(
                TextSemanticsCapabilityAvailability(
                    capability,
                    TextAnalysisReportStatus.MODEL_ACQUISITION_REQUIRED.value,
                    (),
                )
                for capability in TEXT_SEMANTICS_CAPABILITIES
            ),
            registry_evidence=None,
        )

    grouped = load_candidate_matrix(
        registry_path,
        TEXT_SEMANTICS_CAPABILITIES,
        invalid_error=lambda message: TextAnalysisError("model_registry_invalid", message),
    )
    capabilities = tuple(
        _text_semantics_availability(capability, grouped[capability], project_root)
        for capability in TEXT_SEMANTICS_CAPABILITIES
    )
    return _text_semantics_report(capabilities, registry_evidence=_input_evidence(registry_path))


def _text_semantics_report(
    capabilities: tuple[TextSemanticsCapabilityAvailability, ...],
    *,
    registry_evidence: InputEvidence | None,
) -> TextSemanticsCapabilityReport:
    # A registry-only evaluation never acquires or executes: the result is always
    # ``model_acquisition_required`` (mirroring the ASR/OCR capability reports).
    return TextSemanticsCapabilityReport(
        result=TextAnalysisReportStatus.MODEL_ACQUISITION_REQUIRED.value,
        capabilities=capabilities,
        model_registry_evidence=registry_evidence,
    )


def _text_semantics_availability(
    capability: str, candidates: list[Mapping[str, object]], project_root: Path
) -> TextSemanticsCapabilityAvailability:
    # The asset-level model identity assess_candidate binds for an eligible
    # candidate is exposed only when eligible; the finer model identity binds later
    # at the Text-model output projection (ADR 0036), never at the offline adapter.
    assessments = tuple(
        assess_candidate(candidate, capability, project_root) for candidate in candidates
    )
    state = capability_state_from_grades(
        (assessment.state, assessment.reason) for assessment in assessments
    )
    return TextSemanticsCapabilityAvailability(capability, state, assessments)


def _capability_message(capability: str, state: str) -> str:
    messages = {
        "model_acquisition_required": (
            f"No acquired offline model is available for {capability}; acquisition is required "
            "before text-semantics generation."
        ),
        "model_credential_gated": (
            f"A {capability} candidate requires credentials and is blocked."
        ),
        "model_ineligible": (
            f"No registered {capability} candidate satisfies the eligibility gates."
        ),
    }
    return messages[state]


@dataclass(frozen=True)
class RestrictedRawOutput:
    """Raw adapter or model output retained as restricted local audit evidence.

    It is excluded from formal content and from default publication; any export
    requires separate explicit authorization.
    """

    path: Path
    sha256: str
    byte_count: int

    def as_json(self) -> dict[str, object]:
        return {
            "path": self.path.as_posix(),
            "sha256": self.sha256,
            "byte_count": self.byte_count,
            "restriction": _RESTRICTED_LOCAL_AUDIT,
        }


def record_restricted_raw_output(
    workspace_path: Path, name: str, raw_output: bytes
) -> RestrictedRawOutput:
    """Retain a raw adapter or model output as restricted local audit evidence.

    The bytes are written once into the immutable workspace's restricted area and
    returned as a hash-only pointer. The pointer never carries the raw content, so
    it can be summarized in workspace diagnostics without leaking generated text
    into a formal report or default publication.
    """

    restricted_path = workspace_path / "restricted" / f"{name}.raw"
    write_bytes_once(
        restricted_path,
        raw_output,
        conflict_error=lambda message: TextAnalysisError("text_analysis_report_conflict", message),
    )
    return RestrictedRawOutput(
        path=restricted_path,
        sha256=sha256(raw_output).hexdigest(),
        byte_count=len(raw_output),
    )


@dataclass(frozen=True)
class AttemptProvenance:
    """The immutable identity record linking one attempt to its inputs and rules.

    It binds the prompt template and its deterministically rendered prompt, the
    input-cue manifest, the Controlled offline text adapter identity, the sampling
    configuration, the output-schema and evidence-rule hashes, the raw-output and
    projection state, and the execution-resource measurement. A resume records the
    prior report it continued; identity-bound inputs are never changed.
    """

    attempt_id: str
    resumed_from_report_id: str | None
    resumption_decision: str | None
    prompt: dict[str, object] | None
    input_cue_manifest: dict[str, object] | None
    adapter_identity: dict[str, object] | None
    sampling: dict[str, object] | None
    output_schema_identity: dict[str, object] | None
    evidence_rules_identity: dict[str, object] | None
    raw_output: dict[str, object]
    projection: dict[str, object]
    resource_measurement: dict[str, object]

    def as_json(self) -> dict[str, object]:
        return {
            "attempt_id": self.attempt_id,
            "resumed_from_report_id": self.resumed_from_report_id,
            "resumption_decision": self.resumption_decision,
            "prompt": self.prompt,
            "input_cue_manifest": self.input_cue_manifest,
            "adapter_identity": self.adapter_identity,
            "sampling": self.sampling,
            "output_schema_identity": self.output_schema_identity,
            "evidence_rules_identity": self.evidence_rules_identity,
            "raw_output": self.raw_output,
            "projection": self.projection,
            "resource_measurement": self.resource_measurement,
        }


@dataclass(frozen=True)
class _ControlledGenerationOutcome:
    """The internal result of one Controlled offline text-generation attempt.

    It gathers the fields an attempt derives after revalidation so the caller binds
    them by name rather than by position, mirroring the frozen-record style used for
    every outward report structure.
    """

    status: TextAnalysisReportStatus
    diagnostics: tuple[PlanningDiagnostic, ...]
    adapter_state: ControlledTextAdapterState
    segments: tuple[GeneratedSegment, ...]
    chapters: tuple[Chapter, ...]
    collection_summary: CollectionSummary | None
    unsupported_item_count: int
    restricted_raw_output: tuple[RestrictedRawOutput, ...]
    raw_output_state: str
    projection_state: dict[str, object]


@dataclass(frozen=True)
class ControlledTextAdapterState:
    """The availability outcome for the Controlled offline text adapter."""

    state: str
    diagnostic: PlanningDiagnostic | None

    def as_json(self) -> dict[str, object]:
        return {
            "state": self.state,
            "model": None,
            "diagnostic": self.diagnostic.as_json() if self.diagnostic is not None else None,
        }


@dataclass(frozen=True)
class SelectedPrimaryTrack:
    """One revalidated Primary subtitle track bound to a Part's SourceArtifact."""

    source_id: str
    stream_index: int
    sha256: str

    def as_json(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "stream_index": self.stream_index,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class RevalidationEvidence:
    """The auditable outcome of complete text-analysis input revalidation."""

    run_plan_confirmed: bool
    subtitle_rules_fingerprint: str | None
    text_analysis_rules_fingerprint: str | None
    selected_primary_tracks: tuple[SelectedPrimaryTrack, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "run_plan_confirmed": self.run_plan_confirmed,
            "subtitle_rules_fingerprint": self.subtitle_rules_fingerprint,
            "text_analysis_rules_fingerprint": self.text_analysis_rules_fingerprint,
            "selected_primary_tracks": [track.as_json() for track in self.selected_primary_tracks],
        }


@dataclass(frozen=True)
class AudioAnalysisBinding:
    """The optional Audio analysis context binding for one text-analysis attempt.

    Its absence is recorded as ``not_available``; the report always keeps
    ``audio_completeness=not_verified`` regardless of any bound audio evidence.
    """

    state: str
    report_id: str | None = None
    plan_id: str | None = None
    subtitle_report_id: str | None = None

    def as_json(self) -> dict[str, object]:
        if self.state != "bound":
            return {"state": self.state}
        return {
            "state": self.state,
            "report_id": self.report_id,
            "plan_id": self.plan_id,
            "subtitle_report_id": self.subtitle_report_id,
        }


# The OCR-not-requested record. Vocabulary is owned by the Visual-Text Context
# (see CONTEXT-MAP.md and docs/contexts/visual-text/CONTEXT.md), but a plain
# text-analysis run is precisely the "run that never enables visual-text": it
# extracts no frames, runs no detection, and produces no visual facts, and any
# on-screen picture-only content is left recorded as unanalyzed visual content
# rather than implied as covered (Phase 8 spec, User Story 1). The strings are
# reproduced here rather than imported so the Phase 6 core keeps no dependency on
# the Phase 8 visual-text module.
_OCR_NOT_REQUESTED_RECORD: dict[str, object] = {
    "ocr": "not_requested",
    "frame_extraction": "not_attempted",
    "detection": "not_attempted",
    "visual_facts": [],
    "picture_only_content": "unanalyzed_visual_content",
}


@dataclass(frozen=True)
class TextAnalysisReport:
    """Immutable machine-readable result of one text-analysis attempt."""

    report_id: str
    plan_id: str
    subtitle_report_id: str
    status: TextAnalysisReportStatus
    workspace_path: Path
    report_path: Path
    run_plan_evidence: InputEvidence | None
    subtitle_report_evidence: InputEvidence | None
    text_analysis_rules_evidence: InputEvidence | None
    audio_analysis_report_evidence: InputEvidence | None
    resumed_from_report: InputEvidence | None
    resumption_decision: str | None
    controlled_text_adapter: ControlledTextAdapterState
    audio_analysis: AudioAnalysisBinding
    revalidation: RevalidationEvidence
    text_generation_contracts: TextGenerationContracts | None
    attempt_provenance: AttemptProvenance
    required_decision: dict[str, object] | None
    rendered_report: dict[str, object] | None
    restricted_raw_output: tuple[RestrictedRawOutput, ...]
    segments: tuple[GeneratedSegment, ...]
    chapters: tuple[Chapter, ...]
    collection_summary: CollectionSummary | None
    unsupported_item_count: int
    diagnostics: tuple[PlanningDiagnostic, ...]
    # Populated only by a real run (Phase 12 ticket 08): the text_semantics
    # stage-execution record with its measured subprocess peak. Empty on the
    # controlled-adapter path, so the offline document is unchanged.
    stage_execution: tuple[dict[str, object], ...] = ()

    def as_json(self) -> dict[str, object]:
        return {
            "report_id": self.report_id,
            "plan_id": self.plan_id,
            "subtitle_report_id": self.subtitle_report_id,
            "status": self.status.value,
            "workspace_path": self.workspace_path.as_posix(),
            "report_path": self.report_path.as_posix(),
            "input_evidence": {
                "run_plan": (
                    self.run_plan_evidence.as_json() if self.run_plan_evidence is not None else None
                ),
                "subtitle_candidate_report": (
                    self.subtitle_report_evidence.as_json()
                    if self.subtitle_report_evidence is not None
                    else None
                ),
                "text_analysis_rules": (
                    self.text_analysis_rules_evidence.as_json()
                    if self.text_analysis_rules_evidence is not None
                    else None
                ),
                "audio_analysis_report": (
                    self.audio_analysis_report_evidence.as_json()
                    if self.audio_analysis_report_evidence is not None
                    else None
                ),
                "resumed_from_report": (
                    self.resumed_from_report.as_json()
                    if self.resumed_from_report is not None
                    else None
                ),
                "resumption_decision": self.resumption_decision,
            },
            "controlled_text_adapter": self.controlled_text_adapter.as_json(),
            "audio_analysis": self.audio_analysis.as_json(),
            "audio_completeness": "not_verified",
            "revalidation": self.revalidation.as_json(),
            "text_generation_contracts": (
                self.text_generation_contracts.as_json()
                if self.text_generation_contracts is not None
                else None
            ),
            "attempt_provenance": self.attempt_provenance.as_json(),
            "required_decision": self.required_decision,
            "rendered_report": self.rendered_report,
            "segments": [segment.as_json() for segment in self.segments],
            "chapters": [chapter.as_json() for chapter in self.chapters],
            "collection_summary": (
                self.collection_summary.as_json() if self.collection_summary is not None else None
            ),
            "unsupported_item_count": self.unsupported_item_count,
            "restricted_raw_output": [output.as_json() for output in self.restricted_raw_output],
            "diagnostics": [diagnostic.as_json() for diagnostic in self.diagnostics],
            "stage_execution": list(self.stage_execution),
            # A run that never enables visual-text records the OCR-not-requested
            # record so absence is explicit rather than implied.
            "visual_text": dict(_OCR_NOT_REQUESTED_RECORD),
            "guarantees": {
                "asr_or_ocr": "not_attempted",
                "external_knowledge": "not_used",
                "model_acquisition": "not_attempted",
                # The Controlled offline text adapter reads a hash-pinned synthetic
                # fixture; it loads and runs no model asset, so real-model execution
                # is never attempted in this phase.
                "model_execution": "not_attempted",
                "network_access": "not_attempted",
                "outputs_publication": "not_attempted",
                "run_plan_mutation": "not_attempted",
                "subtitle_artifact_mutation": "not_attempted",
                "translation": "not_attempted",
                "user_media_access": "not_attempted",
            },
        }


_ADAPTER_UNAVAILABLE_MESSAGE = (
    "No Controlled offline text adapter is available; no semantic content was generated."
)


def _adapter_unavailable_diagnostic() -> PlanningDiagnostic:
    return PlanningDiagnostic(
        TextAnalysisReportStatus.CONTROLLED_ADAPTER_UNAVAILABLE.value,
        _ADAPTER_UNAVAILABLE_MESSAGE,
    )


def text_analysis_rules_fingerprint(project_root: Path) -> str:
    """Validate and fingerprint the versioned, project-owned Phase 6 text rules.

    The rules bundle the cue, prompt-template, output-schema, evidence-rule, and
    Controlled offline text adapter identity versions. Ticket 02 revalidates the
    whole-file identity; later tickets interpret the individual versioned fields.
    """

    rules_path = project_root / "config" / "text-analysis-rules.json"
    try:
        raw_rules = rules_path.read_bytes()
        decoded = json.loads(raw_rules)
    except (OSError, json.JSONDecodeError) as error:
        raise TextAnalysisError(
            "text_analysis_rules_invalid", "Text analysis rules cannot be read."
        ) from error
    if not isinstance(decoded, Mapping) or decoded.get("schema_version") != 1:
        raise TextAnalysisError(
            "text_analysis_rules_invalid", "Text analysis rules have an invalid schema."
        )
    return sha256(raw_rules).hexdigest()


def analyze_text(
    plan_id: str,
    subtitle_report_id: str,
    project_root: Path,
    audio_report_id: str | None = None,
    resumed_from_report: InputEvidence | None = None,
    resumed_from_report_id: str | None = None,
    resumption_decision: str | None = None,
    real_engines: RealEngineSelection | None = None,
) -> dict[str, object]:
    """Create one immutable text-analysis report from fully revalidated inputs.

    Every bound input identity is revalidated before an attempt proceeds; any
    drift retains a ``failed`` report. A fully revalidated attempt records its
    generation-attempt provenance and then evaluates the future-real-model 12 GiB
    resource envelope: a conservative estimate above the envelope retains a
    resumable ``resource_envelope_exceeded`` report, and otherwise the attempt —
    which has no Controlled offline text adapter that generates yet — retains a
    ``controlled_adapter_unavailable`` report with no semantic content. A resume
    passes ``resumed_from_report`` and
    ``resumption_decision``; each attempt owns a fresh workspace and never
    overwrites prior evidence, so there is no automatic retry.

    ``real_engines`` is run composition's real-adapter selection (Phase 12 ticket
    06): ``None`` on every automated-test run (the controlled offline path below),
    and the acquired real text_semantics engine when set, reached through
    :func:`~video_content_pipeline.real_engine_adapter.dispatch_real_stage`.
    """

    if real_engines is not None:
        return dispatch_real_stage(real_engines, stage="text_analysis")
    report_id = uuid.uuid4().hex
    workspace_path = project_root / "work" / "text-analysis-reports" / report_id
    report_path = workspace_path / "text-analysis-report.json"
    run_plan_evidence: InputEvidence | None = None
    subtitle_report_evidence: InputEvidence | None = None
    text_analysis_rules_evidence: InputEvidence | None = None
    audio_analysis_report_evidence: InputEvidence | None = None
    diagnostics: tuple[PlanningDiagnostic, ...] = ()
    status = TextAnalysisReportStatus.FAILED
    report_plan_id = plan_id
    report_subtitle_id = subtitle_report_id
    run_plan_confirmed = False
    subtitle_rules_value: str | None = None
    text_rules_value: str | None = None
    selected_primary_tracks: tuple[SelectedPrimaryTrack, ...] = ()
    audio_binding = AudioAnalysisBinding("not_available")
    contracts: TextGenerationContracts | None = None
    required_decision: dict[str, object] | None = None
    resource_measurement: dict[str, object] = _incomplete_resource_measurement()
    segments: tuple[GeneratedSegment, ...] = ()
    chapters: tuple[Chapter, ...] = ()
    collection_summary: CollectionSummary | None = None
    unsupported_item_count = 0
    restricted_raw_output: tuple[RestrictedRawOutput, ...] = ()
    raw_output_state = "not_generated"
    projection_state: dict[str, object] = {"state": "not_projected"}
    adapter_state = ControlledTextAdapterState(
        state=TextAnalysisReportStatus.CONTROLLED_ADAPTER_UNAVAILABLE.value,
        diagnostic=_adapter_unavailable_diagnostic(),
    )

    try:
        plan_path = project_root / "plans" / plan_id / "run-plan.json"
        plan = load_run_plan(plan_path)
        if plan.plan_id != plan_id:
            raise TextAnalysisError(
                "run_plan_not_confirmed", "RunPlan identity does not match the requested plan ID."
            )
        confirmed_report = load_plan_report(
            project_root / "plans" / "reports" / plan.report_id / "plan-report.json"
        )
        if not confirmed_plan_matches(confirmed_report, plan):
            raise TextAnalysisError(
                "run_plan_not_confirmed", "RunPlan evidence does not match a confirmed PlanReport."
            )
        revalidate_confirmed_inspection_evidence(
            confirmed_report,
            plan,
            drift_error=lambda: TextAnalysisError(
                "inspection_evidence_changed",
                "PlanReport inspection evidence no longer matches the confirmed RunPlan.",
            ),
        )
        run_plan_confirmed = True
        expected_subtitle_id = _validated_report_id(subtitle_report_id)
        subtitle_path = _subtitle_report_path(
            project_root, plan.source_artifacts, expected_subtitle_id
        )
        subtitle_report = _load_subtitle_report(subtitle_path)
        if (
            subtitle_report.report_id != expected_subtitle_id
            or subtitle_report.plan_id != plan.plan_id
        ):
            raise TextAnalysisError(
                "subtitle_report_mismatch",
                "Subtitle candidate report does not belong to this RunPlan.",
            )
        subtitle_rules_value = _revalidate_subtitle_rules(subtitle_report, project_root)
        selected_primary_tracks = _selected_primary_tracks(plan, subtitle_report)
        text_rules_value = text_analysis_rules_fingerprint(project_root)
        contracts = revalidate_text_generation_contracts(project_root)
        if audio_report_id is not None:
            audio_analysis_report_evidence, audio_binding = _bind_audio_report(
                project_root, audio_report_id, plan.plan_id, subtitle_report.report_id
            )
        run_plan_evidence = _input_evidence(plan_path)
        subtitle_report_evidence = _input_evidence(subtitle_path)
        text_analysis_rules_evidence = _input_evidence(
            project_root / "config" / "text-analysis-rules.json"
        )
        report_plan_id = plan.plan_id
        report_subtitle_id = subtitle_report.report_id
        conservative_high_bytes = _resource_plan_high_bytes(project_root)
        resource_measurement = _resource_measurement(conservative_high_bytes)
        if resource_measurement["state"] == "resource_envelope_exceeded":
            status = TextAnalysisReportStatus.RESOURCE_ENVELOPE_EXCEEDED
            required_decision = {
                "reason": "resource_envelope_exceeded",
                "decision": _RESOURCE_DECISION,
            }
            diagnostics = (
                PlanningDiagnostic(
                    "resource_envelope_exceeded",
                    "A conservative text-model resource estimate exceeds the 12 GiB envelope.",
                ),
            )
        else:
            outcome = _run_controlled_generation(
                contracts=contracts,
                plan=plan,
                subtitle_report=subtitle_report,
                selected_primary_tracks=selected_primary_tracks,
                project_root=project_root,
                workspace_path=workspace_path,
            )
            status = outcome.status
            diagnostics = outcome.diagnostics
            adapter_state = outcome.adapter_state
            segments = outcome.segments
            chapters = outcome.chapters
            collection_summary = outcome.collection_summary
            unsupported_item_count = outcome.unsupported_item_count
            restricted_raw_output = outcome.restricted_raw_output
            raw_output_state = outcome.raw_output_state
            projection_state = outcome.projection_state
    except (
        TextAnalysisError,
        TextContractError,
        TextGenerationError,
        TextAggregationError,
        PlanningError,
        SubtitleReportError,
        OSError,
        ValueError,
    ) as error:
        status = TextAnalysisReportStatus.FAILED
        run_plan_confirmed = False
        selected_primary_tracks = ()
        audio_analysis_report_evidence = None
        audio_binding = AudioAnalysisBinding("not_available")
        contracts = None
        required_decision = None
        resource_measurement = _incomplete_resource_measurement()
        segments = ()
        chapters = ()
        collection_summary = None
        unsupported_item_count = 0
        restricted_raw_output = ()
        raw_output_state = "not_generated"
        projection_state = {"state": "not_projected"}
        adapter_state = ControlledTextAdapterState(
            state=TextAnalysisReportStatus.CONTROLLED_ADAPTER_UNAVAILABLE.value,
            diagnostic=_adapter_unavailable_diagnostic(),
        )
        diagnostics = (
            PlanningDiagnostic(
                getattr(error, "reason", "text_analysis_input_invalid"),
                str(error),
            ),
        )

    provenance = _build_attempt_provenance(
        attempt_id=report_id,
        resumed_from_report_id=resumed_from_report_id,
        resumption_decision=resumption_decision,
        contracts=contracts,
        selected_primary_tracks=selected_primary_tracks,
        resource_measurement=resource_measurement,
        restricted_raw_output=restricted_raw_output,
        raw_output_state=raw_output_state,
        projection_state=projection_state,
        workspace_path=workspace_path,
    )
    report = TextAnalysisReport(
        report_id=report_id,
        plan_id=report_plan_id,
        subtitle_report_id=report_subtitle_id,
        status=status,
        workspace_path=workspace_path,
        report_path=report_path,
        run_plan_evidence=run_plan_evidence,
        subtitle_report_evidence=subtitle_report_evidence,
        text_analysis_rules_evidence=text_analysis_rules_evidence,
        audio_analysis_report_evidence=audio_analysis_report_evidence,
        resumed_from_report=resumed_from_report,
        resumption_decision=resumption_decision,
        controlled_text_adapter=adapter_state,
        audio_analysis=audio_binding,
        revalidation=RevalidationEvidence(
            run_plan_confirmed=run_plan_confirmed,
            subtitle_rules_fingerprint=subtitle_rules_value,
            text_analysis_rules_fingerprint=text_rules_value,
            selected_primary_tracks=selected_primary_tracks,
        ),
        text_generation_contracts=contracts,
        attempt_provenance=provenance,
        required_decision=required_decision,
        rendered_report=None,
        restricted_raw_output=restricted_raw_output,
        segments=segments,
        chapters=chapters,
        collection_summary=collection_summary,
        unsupported_item_count=unsupported_item_count,
        diagnostics=diagnostics,
    )
    report = _render_and_bind_markdown(report)
    _write_json_once(report_path, report.as_json())
    return {"status": report.status.value, "report": report.as_json()}


def _render_and_bind_markdown(report: TextAnalysisReport) -> TextAnalysisReport:
    """Render the deterministic Markdown rendition and bind its version and hash.

    The renderer reads only verified report content, never the ``rendered_report``
    provenance it produces, so the Markdown hash is stable and the JSON report
    stays authoritative. The rendition is written into the immutable workspace.
    """

    rendition = render_text_analysis_markdown(report.as_json())
    markdown_path = report.workspace_path / "text-analysis-report.md"
    if markdown_path.exists():
        if markdown_path.read_text(encoding="utf-8") != rendition.text:
            raise TextAnalysisError(
                "text_analysis_report_conflict",
                f"Immutable Markdown rendition differs: {markdown_path}",
            )
    else:
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(rendition.text, encoding="utf-8")
    rendered_report = dict(rendition.as_json())
    rendered_report["path"] = markdown_path.as_posix()
    return replace(report, rendered_report=rendered_report)


def _build_attempt_provenance(
    *,
    attempt_id: str,
    resumed_from_report_id: str | None,
    resumption_decision: str | None,
    contracts: TextGenerationContracts | None,
    selected_primary_tracks: tuple[SelectedPrimaryTrack, ...],
    resource_measurement: dict[str, object],
    restricted_raw_output: tuple[RestrictedRawOutput, ...],
    raw_output_state: str = "not_generated",
    projection_state: dict[str, object] | None = None,
    workspace_path: Path,
) -> AttemptProvenance:
    """Compose the immutable provenance record for one attempt.

    When the contracts revalidated, the rendered prompt and input-cue manifest are
    written into the immutable workspace and bound by hash so a future real-model
    boundary can prove exactly which prompt, adapter, sampling, schema, and
    evidence rules produced a candidate. A failed attempt records only the
    identities it managed to bind. ``raw_output_state`` and ``projection_state``
    record whether the Controlled offline text adapter generated and whether its
    output projected; the raw output itself stays restricted local audit evidence
    referenced only by hash.
    """

    prompt: dict[str, object] | None = None
    input_cue_manifest: dict[str, object] | None = None
    adapter_identity: dict[str, object] | None = None
    sampling: dict[str, object] | None = None
    output_schema_identity: dict[str, object] | None = None
    evidence_rules_identity: dict[str, object] | None = None
    if contracts is not None:
        adapter = contracts.controlled_adapter
        adapter_identity = {
            "version": adapter.version,
            "implementation_version": adapter.document.get("implementation_version"),
            "sha256": adapter.evidence.sha256,
        }
        sampling = _sampling_identity(adapter.document)
        output_schema_identity = {
            "version": contracts.output_schema.version,
            "sha256": contracts.output_schema.evidence.sha256,
        }
        evidence_rules_identity = {
            "version": contracts.evidence_rules.version,
            "sha256": contracts.evidence_rules.evidence.sha256,
        }
        manifest_document = input_cue_manifest_document(
            _selected_track_tuples(selected_primary_tracks)
        )
        manifest_path = workspace_path / "provenance" / "input-cue-manifest.json"
        _write_json_once(manifest_path, manifest_document)
        manifest_evidence = _input_evidence(manifest_path)
        input_cue_manifest = {
            **manifest_evidence.as_json(),
            "track_count": len(selected_primary_tracks),
        }
        rendered = _render_prompt(contracts, selected_primary_tracks)
        rendered_path = workspace_path / "provenance" / "rendered-prompt.txt"
        _write_text_once(rendered_path, rendered)
        prompt = {
            "version": contracts.prompt_template.version,
            "sha256": contracts.prompt_template.evidence.sha256,
            "rendered_prompt": _input_evidence(rendered_path).as_json(),
        }
    return AttemptProvenance(
        attempt_id=attempt_id,
        resumed_from_report_id=resumed_from_report_id,
        resumption_decision=resumption_decision,
        prompt=prompt,
        input_cue_manifest=input_cue_manifest,
        adapter_identity=adapter_identity,
        sampling=sampling,
        output_schema_identity=output_schema_identity,
        evidence_rules_identity=evidence_rules_identity,
        raw_output={
            "state": raw_output_state,
            "restriction": _RESTRICTED_LOCAL_AUDIT,
            "artifacts": [output.as_json() for output in restricted_raw_output],
        },
        projection=(
            dict(projection_state) if projection_state is not None else {"state": "not_projected"}
        ),
        resource_measurement=resource_measurement,
    )


def _sampling_identity(adapter_document: Mapping[str, object]) -> dict[str, object]:
    configuration = adapter_document.get("sampling_configuration")
    if not isinstance(configuration, Mapping):
        configuration = {}
    encoded = json.dumps(dict(configuration), sort_keys=True).encode("utf-8")
    return {"sha256": sha256(encoded).hexdigest(), "configuration": dict(configuration)}


@dataclass(frozen=True)
class _RealTextOutcome:
    """The real text-semantics run's outcome, shaped for the report fields."""

    status: TextAnalysisReportStatus
    segments: tuple[GeneratedSegment, ...]
    chapters: tuple[Chapter, ...]
    collection_summary: CollectionSummary | None
    unsupported_item_count: int
    restricted_raw_output: tuple[RestrictedRawOutput, ...]
    diagnostics: tuple[PlanningDiagnostic, ...]
    stage_execution: tuple[dict[str, object], ...]


def _text_semantics_stage_execution(
    candidate_id: str,
    resource_high_bytes: int | None,
    peak_memory_bytes: int,
    workspace_path: Path,
) -> dict[str, object]:
    """Record the text_semantics stage execution with its measured child peak.

    The engine runs the model in its own Model runtime subprocess (ADR 0055), so the
    peak is the honest child high-water mark and released/0 is truthful on exit. An
    over-envelope peak fails closed to ``release_unverified`` as every stage does.
    """

    record: dict[str, object] = {
        "capability": "text_semantics",
        "candidate_id": candidate_id,
        "state": "completed",
        "resource_measurement": {"peak_bytes": peak_memory_bytes},
        "unload_evidence": {"state": "released", "resident_bytes": 0},
    }
    if (
        resource_high_bytes is None
        or peak_memory_bytes < 0
        or peak_memory_bytes > resource_high_bytes
    ):
        record["state"] = "release_unverified"
        return record
    measurement_path = (
        workspace_path / "stage-execution" / "text_semantics" / candidate_id / "resource.json"
    )
    write_json_once(
        measurement_path,
        {"peak_bytes": peak_memory_bytes},
        conflict_error=lambda message: TextAnalysisError("text_analysis_report_invalid", message),
    )
    record["resource_measurement"] = _input_evidence(measurement_path).as_json()
    return record


def build_text_semantics_analysis(
    project_root: Path,
    workspace_path: Path,
    contracts: TextGenerationContracts,
    parts: Sequence[tuple[str, int, Path]],
    unavailable: Sequence[UnavailablePartInfo],
    text_candidate: Mapping[str, object],
    *,
    command: Sequence[str] | None = None,
) -> _RealTextOutcome:
    """Run the real Qwen3-4B text-semantics engine over the revalidated cue inventories.

    ``parts`` are ``(part_id, stream_index, source_candidate_path)`` for every Part
    with recognized text -- the embedded Primary subtitle track, or, in the full-ASR
    branch, the published transcript candidate (same schema). Loads each Part's cue
    identities and verbatim texts, runs the self-isolated engine once, and maps its
    result onto the report's segment/chapter/summary contract plus a measured
    text_semantics stage-execution record.
    """

    from video_content_pipeline.text_generation import load_part_with_cue_texts
    from video_content_pipeline.text_semantics_engine import generate_text_semantics

    candidate_id = text_candidate.get("candidate_id")
    if not isinstance(candidate_id, str):
        raise TextAnalysisError("model_output_invalid", "text_semantics candidate id is missing.")
    available: list[LoadedPart] = []
    cue_texts: dict[str, str] = {}
    representative: tuple[str, int] | None = None
    for part_id, stream_index, candidate_path in parts:
        part, texts = load_part_with_cue_texts(
            candidate_path, part_id=part_id, stream_index=stream_index
        )
        available.append(part)
        cue_texts.update(texts)
        if representative is None:
            representative = (part_id, stream_index)
    if representative is None:
        raise TextAnalysisError(
            "text_analysis_input_invalid", "A real text-semantics run needs at least one Part."
        )
    result = generate_text_semantics(
        project_root,
        workspace_path,
        contracts,
        source_id=representative[0],
        stream_index=representative[1],
        available=tuple(available),
        cue_texts=cue_texts,
        unavailable=tuple(unavailable),
        command=command,
    )
    status = (
        TextAnalysisReportStatus.FAILED
        if result.status not in {"complete", "partial", "failed"}
        else TextAnalysisReportStatus(result.status)
    )
    high_bytes = _text_resource_high_bytes(text_candidate)
    stage_execution = (
        _text_semantics_stage_execution(
            candidate_id, high_bytes, result.peak_memory_bytes, workspace_path
        ),
    )
    return _RealTextOutcome(
        status=status,
        segments=result.segments,
        chapters=result.chapters,
        collection_summary=result.collection_summary,
        unsupported_item_count=result.unsupported_item_count,
        restricted_raw_output=(result.restricted_raw_output,),
        diagnostics=result.diagnostics,
        stage_execution=stage_execution,
    )


def _text_resource_high_bytes(candidate: Mapping[str, object]) -> int | None:
    evidence = candidate.get("eligibility_evidence")
    high_bytes = evidence.get("resource_high_bytes") if isinstance(evidence, Mapping) else None
    return high_bytes if isinstance(high_bytes, int) and not isinstance(high_bytes, bool) else None


def _run_controlled_generation(
    *,
    contracts: TextGenerationContracts,
    plan: RunPlan,
    subtitle_report: SubtitleCandidateReport,
    selected_primary_tracks: tuple[SelectedPrimaryTrack, ...],
    project_root: Path,
    workspace_path: Path,
) -> _ControlledGenerationOutcome:
    """Attempt Controlled offline text generation for a fully revalidated attempt.

    Without a bound generation fixture the Controlled offline text adapter cannot
    generate, so the attempt retains ``controlled_adapter_unavailable`` with no
    semantic content (backward compatible with an availability-only attempt). With
    one, the fixture must be bound to exactly these revalidated cues; its retained
    output is projected through the versioned schema, and an invalid whole
    projection fails the complete attempt while its raw output stays restricted
    audit evidence. A valid projection is composed into verified segments,
    chapters, and a collection summary by ``text_generation``.
    """

    manifest_document = input_cue_manifest_document(_selected_track_tuples(selected_primary_tracks))
    manifest_sha = input_cue_manifest_sha256(manifest_document)
    controlled = load_controlled_generation(
        contracts.controlled_adapter.document, project_root, manifest_sha
    )
    if controlled is None:
        return _ControlledGenerationOutcome(
            status=TextAnalysisReportStatus.CONTROLLED_ADAPTER_UNAVAILABLE,
            diagnostics=(_adapter_unavailable_diagnostic(),),
            adapter_state=ControlledTextAdapterState(
                state=TextAnalysisReportStatus.CONTROLLED_ADAPTER_UNAVAILABLE.value,
                diagnostic=_adapter_unavailable_diagnostic(),
            ),
            segments=(),
            chapters=(),
            collection_summary=None,
            unsupported_item_count=0,
            restricted_raw_output=(),
            raw_output_state="not_generated",
            projection_state={"state": "not_projected"},
        )
    if controlled.input_fixture_sha256 != manifest_sha:
        raise TextAnalysisError(
            "controlled_generation_input_mismatch",
            "Controlled adapter fixture is not bound to these revalidated cues.",
        )
    raw_pointer = record_restricted_raw_output(
        workspace_path, "controlled-generation", controlled.raw_output
    )
    restricted_raw_output = (raw_pointer,)
    projection = project_text_model_output(
        _decode_generation_output(controlled.raw_output), contracts
    )
    if projection.projection is None:
        diagnostic = projection.diagnostic or PlanningDiagnostic(
            "model_output_invalid", "The controlled generation output is invalid."
        )
        return _ControlledGenerationOutcome(
            status=TextAnalysisReportStatus.FAILED,
            diagnostics=(diagnostic,),
            adapter_state=ControlledTextAdapterState(
                state="controlled_generation_invalid", diagnostic=diagnostic
            ),
            segments=(),
            chapters=(),
            collection_summary=None,
            unsupported_item_count=0,
            restricted_raw_output=restricted_raw_output,
            raw_output_state="generated",
            projection_state={"state": projection.state},
        )
    available, unavailable = _generation_parts(plan, subtitle_report, selected_primary_tracks)
    result = projection.projection.get("result")
    analysis = generate_analysis(
        available, unavailable, result if isinstance(result, Mapping) else {}
    )
    return _ControlledGenerationOutcome(
        status=_map_generation_status(analysis.status),
        diagnostics=analysis.diagnostics,
        adapter_state=ControlledTextAdapterState(
            state="controlled_generation_complete", diagnostic=None
        ),
        segments=analysis.segments,
        chapters=analysis.chapters,
        collection_summary=analysis.collection_summary,
        unsupported_item_count=analysis.unsupported_item_count,
        restricted_raw_output=restricted_raw_output,
        raw_output_state="generated",
        projection_state={
            "state": "projected",
            "output_schema_version": contracts.output_schema.version,
        },
    )


def _generation_parts(
    plan: RunPlan,
    subtitle_report: SubtitleCandidateReport,
    selected_primary_tracks: tuple[SelectedPrimaryTrack, ...],
) -> tuple[tuple[LoadedPart, ...], tuple[UnavailablePartInfo, ...]]:
    """Derive authoritative per-Part cue inventories and unavailable-Part ranges.

    Each selected Primary track becomes an available Part whose ordered cue
    identities are loaded from its retained ``source-candidate.json``. Every plan
    SourceArtifact without a selected Primary track is a ``text_content=unavailable``
    Part whose retained CollectionVirtualTime range is derived from the candidate's
    retained raw-PTS cue intervals, so the collection can declare its omission
    without inventing content.
    """

    candidates_by_key = {
        (candidate.source_id, candidate.stream_index): candidate
        for candidate in subtitle_report.candidates
    }
    available: list[LoadedPart] = []
    covered: set[str] = set()
    for track in selected_primary_tracks:
        candidate = candidates_by_key.get((track.source_id, track.stream_index))
        if candidate is None or candidate.source_candidate_path is None:
            raise TextAnalysisError(
                "subtitle_track_changed",
                "A selected Primary subtitle track lost its retained cue evidence.",
            )
        available.append(
            load_cue_inventory(
                Path(candidate.source_candidate_path),
                part_id=track.source_id,
                stream_index=track.stream_index,
            )
        )
        covered.add(track.source_id)

    unavailable: list[UnavailablePartInfo] = []
    for artifact in plan.source_artifacts:
        if artifact.source_id in covered:
            continue
        part_candidates = [
            candidate
            for candidate in subtitle_report.candidates
            if candidate.source_id == artifact.source_id
        ]
        unavailable.append(_unavailable_part(artifact.source_id, part_candidates))
    return tuple(available), tuple(unavailable)


def _unavailable_part(source_id: str, candidates: list[SubtitleCandidate]) -> UnavailablePartInfo:
    """Build one ``text_content=unavailable`` Part with its retained omitted range."""

    intervals = [
        interval for candidate in candidates for interval in candidate.raw_pts_cue_intervals
    ]
    if intervals:
        start = min(interval.start for interval in intervals)
        end = max(interval.end for interval in intervals)
        virtual_time_range = HalfOpenInterval(start, end)
    else:
        # No retained timing evidence for this Part; declare a minimal placeholder
        # range so the omission is still visible without inventing cue content.
        virtual_time_range = HalfOpenInterval(ExactTime(0), ExactTime(1))
    reason = "no_valid_primary_track"
    for candidate in candidates:
        if candidate.diagnostic is not None:
            reason = candidate.diagnostic.reason
            break
    return UnavailablePartInfo(
        part_id=source_id, reason=reason, virtual_time_range=virtual_time_range
    )


def _decode_generation_output(raw_output: bytes) -> object:
    """Decode restricted raw generation bytes for projection, or a rejecting sentinel."""

    try:
        return json.loads(raw_output)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _selected_track_tuples(
    selected_primary_tracks: tuple[SelectedPrimaryTrack, ...],
) -> list[tuple[str, int, str]]:
    """Project the selected Primary tracks into the input-cue manifest identity form."""

    return [
        (track.source_id, track.stream_index, track.sha256) for track in selected_primary_tracks
    ]


def _map_generation_status(status: str) -> TextAnalysisReportStatus:
    return {
        STATUS_COMPLETE: TextAnalysisReportStatus.COMPLETE,
        STATUS_PARTIAL: TextAnalysisReportStatus.PARTIAL,
        STATUS_FAILED: TextAnalysisReportStatus.FAILED,
    }[status]


def _render_prompt(
    contracts: TextGenerationContracts, selected_primary_tracks: tuple[SelectedPrimaryTrack, ...]
) -> str:
    """Deterministically render the prompt bound to one attempt.

    The rendition concatenates the versioned prompt-template sections and the
    input-cue manifest so its hash is a stable fingerprint of exactly what an
    adapter would be shown. It is restricted workspace evidence, not a formal
    report artifact.
    """

    lines = [f"# prompt-template {contracts.prompt_template.version}"]
    sections = contracts.prompt_template.document.get("sections")
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, Mapping):
                continue
            role = section.get("role")
            section_id = section.get("id")
            text = section.get("text")
            lines.append(f"[{role}:{section_id}] {text}")
    lines.append("# input-cue-manifest")
    for track in selected_primary_tracks:
        lines.append(f"- {track.source_id} stream {track.stream_index} sha256 {track.sha256}")
    return "\n".join(lines) + "\n"


def _resource_plan_high_bytes(project_root: Path) -> int | None:
    """Return the optional future-real-model conservative resource estimate.

    The versioned rules bundle may declare a ``resource_plan`` for a future real
    model. Its conservative high estimate, when present, gates the one-large-model
    resource envelope. The Controlled offline text adapter declares none, so the
    envelope never trips for controlled verification.
    """

    rules_path = project_root / "config" / "text-analysis-rules.json"
    try:
        decoded = json.loads(rules_path.read_bytes())
    except (OSError, json.JSONDecodeError) as error:
        raise TextAnalysisError(
            "text_analysis_rules_invalid", "Text analysis rules cannot be read."
        ) from error
    plan = decoded.get("resource_plan") if isinstance(decoded, Mapping) else None
    if plan is None:
        return None
    if not isinstance(plan, Mapping):
        raise TextAnalysisError(
            "text_analysis_rules_invalid", "Text analysis rules have an invalid resource plan."
        )
    high_bytes = plan.get("conservative_high_bytes")
    if not isinstance(high_bytes, int) or isinstance(high_bytes, bool) or high_bytes < 0:
        raise TextAnalysisError(
            "text_analysis_rules_invalid",
            "A resource plan requires a non-negative conservative_high_bytes.",
        )
    return high_bytes


def _resource_measurement(conservative_high_bytes: int | None) -> dict[str, object]:
    if (
        conservative_high_bytes is not None
        and conservative_high_bytes > TEXT_MODEL_RESOURCE_ENVELOPE_BYTES
    ):
        return {
            "state": "resource_envelope_exceeded",
            "reason": "conservative_estimate_exceeds_envelope",
            "conservative_high_bytes": conservative_high_bytes,
            "envelope_limit_bytes": TEXT_MODEL_RESOURCE_ENVELOPE_BYTES,
            "peak_bytes": None,
            "unload_evidence": None,
        }
    return {
        "state": "not_applicable",
        "reason": "controlled_offline_adapter",
        "conservative_high_bytes": conservative_high_bytes,
        "envelope_limit_bytes": TEXT_MODEL_RESOURCE_ENVELOPE_BYTES,
        "peak_bytes": None,
        "unload_evidence": None,
    }


def _incomplete_resource_measurement() -> dict[str, object]:
    return {
        "state": "not_applicable",
        "reason": "attempt_incomplete",
        "conservative_high_bytes": None,
        "envelope_limit_bytes": TEXT_MODEL_RESOURCE_ENVELOPE_BYTES,
        "peak_bytes": None,
        "unload_evidence": None,
    }


def resume_text_analysis(
    report_id: str,
    decision: str | None,
    project_root: Path,
) -> dict[str, object]:
    """Resume one retained text-analysis decision pause from an explicit decision.

    Resumption never auto-resumes and never changes identity-bound inputs: it
    requires an explicit report ID and an explicit user decision, and it may
    continue only a retained ``partial`` report whose decision pause it recognizes.
    The single resumable decision pause in this phase is the future-real-model
    ``resource_envelope_exceeded`` pause, continued with
    ``resource_configuration_changed``. A resume starts a fresh attempt from the
    retained plan and subtitle identities — it never overwrites the paused report,
    so there is no automatic retry.
    """

    prior_path = _text_analysis_report_path(project_root, report_id)
    try:
        prior_document = json.loads(prior_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TextAnalysisError(
            "text_analysis_report_invalid", "Text analysis report cannot be read."
        ) from error
    if not isinstance(prior_document, Mapping) or prior_document.get("report_id") != report_id:
        raise TextAnalysisError("text_analysis_report_invalid", "Text analysis report is invalid.")
    if decision is None:
        raise TextAnalysisError(
            "text_analysis_resume_invalid", "Resume requires an explicit user decision."
        )
    pause_reason = _resumable_pause_reason(prior_document)
    if pause_reason is None:
        raise TextAnalysisError(
            "text_analysis_resume_invalid",
            "Only a retained Phase 6 decision pause can be resumed.",
        )
    if pause_reason == "resource_envelope_exceeded" and decision != _RESOURCE_DECISION:
        raise TextAnalysisError(
            "text_analysis_resume_invalid",
            "A resource-envelope pause requires --decision resource_configuration_changed.",
        )
    plan_id, subtitle_report_id, audio_report_id = _resumed_identities(prior_document)
    return analyze_text(
        plan_id,
        subtitle_report_id,
        project_root,
        audio_report_id,
        resumed_from_report=_input_evidence(prior_path),
        resumed_from_report_id=report_id,
        resumption_decision=decision,
    )


def _resumable_pause_reason(report: Mapping[str, object]) -> str | None:
    """Return the resumable decision-pause reason of a retained report, if any."""

    if report.get("status") != TextAnalysisReportStatus.RESOURCE_ENVELOPE_EXCEEDED.value:
        return None
    required_decision = report.get("required_decision")
    if not isinstance(required_decision, Mapping):
        return None
    if required_decision.get("reason") == "resource_envelope_exceeded":
        return "resource_envelope_exceeded"
    return None


def _resumed_identities(report: Mapping[str, object]) -> tuple[str, str, str | None]:
    """Read the identity-bound plan and subtitle inputs from a paused report."""

    plan_id = report.get("plan_id")
    subtitle_report_id = report.get("subtitle_report_id")
    if not isinstance(plan_id, str) or not isinstance(subtitle_report_id, str):
        raise TextAnalysisError(
            "text_analysis_report_invalid", "Paused report omits its identity-bound inputs."
        )
    audio_report_id: str | None = None
    input_evidence = report.get("input_evidence")
    if isinstance(input_evidence, Mapping):
        audio_binding = report.get("audio_analysis")
        if isinstance(audio_binding, Mapping) and audio_binding.get("state") == "bound":
            bound_id = audio_binding.get("report_id")
            audio_report_id = bound_id if isinstance(bound_id, str) else None
    return plan_id, subtitle_report_id, audio_report_id


def _write_text_once(path: Path, text: str) -> None:
    write_text_once(
        path,
        text,
        conflict_error=lambda message: TextAnalysisError("text_analysis_report_conflict", message),
    )


def _revalidate_subtitle_rules(report: SubtitleCandidateReport, project_root: Path) -> str:
    """Reject subtitle-rule drift between report creation and this attempt."""

    current = subtitle_rules_fingerprint(project_root)
    if report.subtitle_rules_fingerprint != current:
        raise TextAnalysisError(
            "subtitle_rules_changed",
            "Subtitle rules no longer match the retained candidate report.",
        )
    return current


def _selected_primary_tracks(
    plan: RunPlan, report: SubtitleCandidateReport
) -> tuple[SelectedPrimaryTrack, ...]:
    """Revalidate the retained Primary subtitle track for every resolved Part."""

    if report.state not in {CandidateReportState.COMPLETED, CandidateReportState.PARTIAL}:
        raise TextAnalysisError(
            "subtitle_selection_unresolved",
            "Subtitle candidate report is not fully resolved for text analysis.",
        )
    selections = {selection.source_id: selection.stream_index for selection in report.selections}
    tracks: list[SelectedPrimaryTrack] = []
    for artifact in plan.source_artifacts:
        valid = [
            candidate
            for candidate in report.candidates
            if candidate.source_id == artifact.source_id and candidate.state is CandidateState.VALID
        ]
        if not valid:
            # A Part without a valid Primary subtitle track is text_content=unavailable;
            # later tickets record that omission during collection aggregation.
            continue
        if len(valid) == 1:
            selected = valid[0]
        else:
            chosen_index = selections.get(artifact.source_id)
            match = next(
                (candidate for candidate in valid if candidate.stream_index == chosen_index),
                None,
            )
            if match is None:
                raise TextAnalysisError(
                    "subtitle_selection_unresolved",
                    "A Part has multiple valid subtitle tracks without a retained selection.",
                )
            selected = match
        if selected.source_candidate_path is None or selected.source_candidate_sha256 is None:
            raise TextAnalysisError(
                "subtitle_track_changed",
                "A selected Primary subtitle track has incomplete retained evidence.",
            )
        try:
            actual_sha256, _ = sha256_file(Path(selected.source_candidate_path))
        except OSError as error:
            raise TextAnalysisError(
                "subtitle_track_changed",
                "A selected Primary subtitle track can no longer be read.",
            ) from error
        if actual_sha256 != selected.source_candidate_sha256:
            raise TextAnalysisError(
                "subtitle_track_changed",
                "A selected Primary subtitle track hash no longer matches.",
            )
        tracks.append(
            SelectedPrimaryTrack(
                source_id=selected.source_id,
                stream_index=selected.stream_index,
                sha256=selected.source_candidate_sha256,
            )
        )
    return tuple(tracks)


def _bind_audio_report(
    project_root: Path, audio_report_id: str, plan_id: str, subtitle_report_id: str
) -> tuple[InputEvidence, AudioAnalysisBinding]:
    """Bind an optional Audio analysis report and revalidate its input identities."""

    validated_id = validated_report_id(
        audio_report_id,
        invalid_error=lambda: TextAnalysisError(
            "audio_report_invalid", "Audio analysis report ID must be a UUID."
        ),
    )
    audio_path = (
        project_root
        / "work"
        / "audio-analysis-reports"
        / validated_id
        / "audio-analysis-report.json"
    )
    try:
        decoded = json.loads(audio_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TextAnalysisError(
            "audio_report_invalid", "Audio analysis report cannot be read."
        ) from error
    if not isinstance(decoded, Mapping) or decoded.get("report_id") != validated_id:
        raise TextAnalysisError("audio_report_invalid", "Audio analysis report is invalid.")
    if decoded.get("plan_id") != plan_id or decoded.get("subtitle_report_id") != subtitle_report_id:
        raise TextAnalysisError(
            "audio_report_mismatch",
            "Audio analysis report is not bound to this RunPlan and subtitle report.",
        )
    return _input_evidence(audio_path), AudioAnalysisBinding(
        "bound",
        report_id=validated_id,
        plan_id=plan_id,
        subtitle_report_id=subtitle_report_id,
    )


def _text_analysis_report_path(project_root: Path, report_id: str) -> Path:
    validated_id = validated_report_id(
        report_id,
        invalid_error=lambda: TextAnalysisError(
            "text_analysis_report_invalid", "Text analysis report ID must be a UUID."
        ),
    )
    return (
        project_root / "work" / "text-analysis-reports" / validated_id / "text-analysis-report.json"
    )


def _validated_report_id(value: str) -> str:
    return validated_report_id(
        value,
        invalid_error=lambda: TextAnalysisError(
            "subtitle_report_invalid", "Subtitle candidate report ID must be a UUID."
        ),
    )


def _subtitle_report_path(
    project_root: Path, source_artifacts: tuple[SourceArtifact, ...], report_id: str
) -> Path:
    if len(source_artifacts) == 1:
        return (
            project_root
            / "work"
            / source_artifacts[0].source_id
            / report_id
            / "candidate-report.json"
        )
    return project_root / "work" / "subtitle-reports" / report_id / "report.json"


def _load_subtitle_report(path: Path) -> SubtitleCandidateReport:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TextAnalysisError(
            "subtitle_report_invalid", "Subtitle candidate report cannot be read."
        ) from error
    return SubtitleCandidateReport.from_json(decoded, path)


def _input_evidence(path: Path) -> InputEvidence:
    digest, byte_count = sha256_file(path)
    return InputEvidence(path, digest, byte_count)


def _write_json_once(path: Path, payload: object) -> None:
    write_json_once(
        path,
        payload,
        conflict_error=lambda message: TextAnalysisError("text_analysis_report_conflict", message),
    )
