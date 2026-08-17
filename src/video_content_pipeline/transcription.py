"""Transcription Context: provider-neutral ASR capability contracts (Phase 7).

Ticket 01 registers the Transcription capability contract -- the provider-neutral
pair ``asr_primary`` (full transcription) and ``asr_review`` (independent
interval review) -- and evaluates candidates from ``models/registry.json`` using
the shared Phase 5 eligibility gate. It enforces the Independent-model review
requirement (a same-model retry is a recovery attempt, never independent review)
and produces the Model-acquisition-required transcription result: an immutable
outcome carrying no transcription evidence.

The whole evaluation is offline. No model is downloaded or executed; an eligible
candidate is one that *could* be acquired, so the result is
``model_acquisition_required`` until a separately authorized acquisition step
exists (see docs/PHASE_07_SPECIFICATION.md and ADR 0036/0043).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from video_content_pipeline.capabilities import (
    CandidateAssessment,
    assess_candidate,
    capability_state_from_grades,
    load_candidate_matrix,
)
from video_content_pipeline.evidence import (
    InputEvidence,
    input_evidence,
    validated_report_id,
    write_json_once,
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
from video_content_pipeline.source import SourceArtifact
from video_content_pipeline.subtitle_pipeline import (
    SubtitleCandidateReport,
    SubtitlePartState,
    SubtitleReportError,
    subtitle_rules_fingerprint,
)

# The provider-neutral ASR capability pair is defined once in the lower-level
# transcription-contracts module (its projection and fixture loaders validate
# against it) and re-exported here for the ticket-01/02 capability evaluation.
from video_content_pipeline.transcription_contracts import ASR_CAPABILITIES


class TranscriptionError(ValueError):
    """A transcription evaluation failure that names a stable diagnostic reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class AsrCapabilityAvailability:
    """The explicit availability state for one provider-neutral ASR capability."""

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
class IndependentReviewResolution:
    """Whether review can resolve to a different eligible model than the primary."""

    state: str
    primary_model_identity: str | None
    review_model_identity: str | None

    def as_json(self) -> dict[str, object]:
        return {
            "state": self.state,
            "primary_model_identity": self.primary_model_identity,
            "review_model_identity": self.review_model_identity,
        }


@dataclass(frozen=True)
class TranscriptionCapabilityReport:
    """Immutable ASR-capability evaluation with no transcription evidence."""

    result: str
    capabilities: tuple[AsrCapabilityAvailability, ...]
    independent_review: IndependentReviewResolution
    model_registry_evidence: InputEvidence | None

    def as_json(self) -> dict[str, object]:
        return {
            "result": self.result,
            "capabilities": [capability.as_json() for capability in self.capabilities],
            "independent_review": self.independent_review.as_json(),
            "transcription_evidence": None,
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


def evaluate_asr_capabilities(project_root: Path) -> TranscriptionCapabilityReport:
    """Evaluate ``asr_primary`` and ``asr_review`` from the model registry, offline.

    With no eligible, acquired model available, the result is always
    ``model_acquisition_required`` and no transcription evidence is produced; the
    per-capability states and the Independent-model review resolution carry the
    detail a later acquisition step will consume.
    """

    registry_path = project_root / "models" / "registry.json"
    if not registry_path.exists():
        return _report(
            tuple(
                AsrCapabilityAvailability(capability, "model_acquisition_required", ())
                for capability in ASR_CAPABILITIES
            ),
            registry_evidence=None,
        )

    grouped = load_candidate_matrix(
        registry_path,
        ASR_CAPABILITIES,
        invalid_error=lambda message: TranscriptionError("model_registry_invalid", message),
    )
    capabilities = tuple(
        _availability(capability, grouped[capability], project_root)
        for capability in ASR_CAPABILITIES
    )
    return _report(capabilities, registry_evidence=input_evidence(registry_path))


def _availability(
    capability: str, candidates: list[Mapping[str, object]], project_root: Path
) -> AsrCapabilityAvailability:
    # The asset-level model identity assess_candidate binds for an eligible
    # candidate is what the Independent-model review requirement compares; the
    # finer identity the spec names binds later at the model-output projection
    # (ADR 0036, tickets 03+).
    assessments = tuple(
        assess_candidate(candidate, capability, project_root) for candidate in candidates
    )
    state = capability_state_from_grades(
        (assessment.state, assessment.reason) for assessment in assessments
    )
    return AsrCapabilityAvailability(capability, state, assessments)


def _resolve_independent_review(
    capabilities: tuple[AsrCapabilityAvailability, ...],
) -> IndependentReviewResolution:
    by_capability = {capability.capability: capability for capability in capabilities}
    primary_identities = _eligible_identities(by_capability["asr_primary"])
    review_identities = _eligible_identities(by_capability["asr_review"])
    if not primary_identities:
        return IndependentReviewResolution("no_eligible_primary", None, None)
    if not review_identities:
        return IndependentReviewResolution("no_eligible_review", None, None)
    for primary in primary_identities:
        for review in review_identities:
            if review != primary:
                return IndependentReviewResolution("available", primary, review)
    # Every eligible review shares the primary's model identity: a same-model
    # retry is a recovery attempt, never independent review.
    return IndependentReviewResolution(
        "review_same_model_as_primary",
        primary_identities[0],
        review_identities[0],
    )


def _eligible_identities(availability: AsrCapabilityAvailability) -> list[str]:
    identities: list[str] = []
    for assessment in availability.candidates:
        if (
            assessment.state == "eligible"
            and assessment.model_identity is not None
            and assessment.model_identity not in identities
        ):
            identities.append(assessment.model_identity)
    return identities


def _report(
    capabilities: tuple[AsrCapabilityAvailability, ...],
    *,
    registry_evidence: InputEvidence | None,
) -> TranscriptionCapabilityReport:
    return TranscriptionCapabilityReport(
        result="model_acquisition_required",
        capabilities=capabilities,
        independent_review=_resolve_independent_review(capabilities),
        model_registry_evidence=registry_evidence,
    )


def _capability_message(capability: str, state: str) -> str:
    messages = {
        "model_acquisition_required": (
            f"No acquired offline model is available for {capability}; acquisition is required "
            "before transcription."
        ),
        "model_credential_gated": (
            f"An {capability} candidate requires credentials and is blocked."
        ),
        "model_ineligible": (
            f"No registered {capability} candidate satisfies the eligibility gates."
        ),
    }
    return messages[state]


# --- Ticket 02: immutable transcription workspace and the transcribe CLI ----
#
# ``transcribe`` establishes a new immutable workspace from exactly revalidated
# retained inputs and stops before any ASR execution: no model is acquired or
# run, no ``outputs/`` is written, and no user media is read. It enforces the
# Explicit transcription command boundary -- a subtitle-priority run never
# triggers ASR automatically -- and records the pauses that precede execution.
# ASR execution, projection, gates, detection, and arbitration land in later
# tickets; ``complete``/``partial`` are the executed-run statuses those tickets
# populate, so ticket 02 reaches only the pre-execution states below.

# The explicit decision that continues a retained Full-ASR resource confirmation
# pause before a subtitle-unavailable full ASR run may execute.
FULL_ASR_RESOURCE_CONFIRMATION_DECISION = "full_asr_resource_plan_confirmed"

# The explicit decision that continues a retained Transcription resource-envelope
# pause. The name matches the Phase 5 audio-analysis resume convention.
RESOURCE_ENVELOPE_DECISION = "resource_configuration_changed"

_SUBTITLE_UNAVAILABLE_HANDOFF = "subtitle_unavailable_requires_asr_plan"
_EXPLICIT_UPGRADE = "explicit_whole_selection_upgrade"


class TranscriptionReportStatus(StrEnum):
    """The recorded outcome of one transcription attempt.

    ``complete``/``partial``/``failed`` are the formal statuses; ``complete`` and
    ``partial`` describe an executed run and are populated once ASR execution
    lands in later tickets. ``failed`` retains any revalidation drift or unmet
    precondition. The remaining values are the pre-execution recorded states:
    ``awaiting_full_asr_resource_confirmation`` and ``resource_envelope_exceeded``
    are resumable decision pauses, and ``model_acquisition_required`` is the
    terminal outcome when no acquired offline ASR model exists.
    """

    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    AWAITING_FULL_ASR_RESOURCE_CONFIRMATION = "awaiting_full_asr_resource_confirmation"
    RESOURCE_ENVELOPE_EXCEEDED = "resource_envelope_exceeded"
    MODEL_ACQUISITION_REQUIRED = "model_acquisition_required"


@dataclass(frozen=True)
class SourceArtifactBinding:
    """One revalidated SourceArtifact identity carried by the confirmed RunPlan."""

    source_id: str
    sha256: str
    byte_count: int

    def as_json(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "sha256": self.sha256,
            "byte_count": self.byte_count,
        }


@dataclass(frozen=True)
class AudioReportBinding:
    """The required Audio analysis report binding for one transcription attempt.

    Its absence (before the audio report is revalidated) is recorded as
    ``not_available``; a revalidated report is ``bound`` with its input identities.
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


@dataclass(frozen=True)
class TranscriptionStartPrecondition:
    """Why this attempt is an authorized full-ASR start.

    The basis is either a retained ``subtitle_unavailable_requires_asr_plan``
    handoff or an explicit whole-selection upgrade; a subtitle-priority run with
    neither is rejected before any execution.
    """

    basis: str
    source_ids: tuple[str, ...]

    def as_json(self) -> dict[str, object]:
        return {"basis": self.basis, "source_ids": list(self.source_ids)}


@dataclass(frozen=True)
class TranscriptionRevalidation:
    """The auditable outcome of transcription input revalidation before execution."""

    run_plan_confirmed: bool
    subtitle_rules_fingerprint: str | None
    source_artifacts: tuple[SourceArtifactBinding, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "run_plan_confirmed": self.run_plan_confirmed,
            "subtitle_rules_fingerprint": self.subtitle_rules_fingerprint,
            "source_artifacts": [binding.as_json() for binding in self.source_artifacts],
        }


@dataclass(frozen=True)
class TranscriptionReport:
    """Immutable machine-readable result of one transcription attempt."""

    report_id: str
    plan_id: str
    subtitle_report_id: str
    audio_report_id: str
    status: TranscriptionReportStatus
    workspace_path: Path
    report_path: Path
    run_plan_evidence: InputEvidence | None
    subtitle_report_evidence: InputEvidence | None
    audio_report_evidence: InputEvidence | None
    model_registry_evidence: InputEvidence | None
    resumed_from_report: InputEvidence | None
    resumed_from_report_id: str | None
    resumption_decision: str | None
    start_precondition: TranscriptionStartPrecondition | None
    revalidation: TranscriptionRevalidation
    audio_analysis: AudioReportBinding
    capabilities: tuple[AsrCapabilityAvailability, ...]
    independent_review: IndependentReviewResolution | None
    required_decision: dict[str, object] | None
    diagnostics: tuple[PlanningDiagnostic, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "report_id": self.report_id,
            "plan_id": self.plan_id,
            "subtitle_report_id": self.subtitle_report_id,
            "audio_report_id": self.audio_report_id,
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
                "audio_analysis_report": (
                    self.audio_report_evidence.as_json()
                    if self.audio_report_evidence is not None
                    else None
                ),
                "model_registry": (
                    self.model_registry_evidence.as_json()
                    if self.model_registry_evidence is not None
                    else None
                ),
                "resumed_from_report": (
                    self.resumed_from_report.as_json()
                    if self.resumed_from_report is not None
                    else None
                ),
                "resumed_from_report_id": self.resumed_from_report_id,
                "resumption_decision": self.resumption_decision,
            },
            "start_precondition": (
                self.start_precondition.as_json() if self.start_precondition is not None else None
            ),
            "revalidation": self.revalidation.as_json(),
            "audio_analysis": self.audio_analysis.as_json(),
            "audio_completeness": "not_verified",
            "capabilities": [capability.as_json() for capability in self.capabilities],
            "independent_review": (
                self.independent_review.as_json() if self.independent_review is not None else None
            ),
            "required_decision": self.required_decision,
            "diagnostics": [diagnostic.as_json() for diagnostic in self.diagnostics],
            "guarantees": {
                "model_acquisition": "not_attempted",
                "model_execution": "not_attempted",
                "network_access": "not_attempted",
                "outputs_publication": "not_attempted",
            },
        }


def transcribe(
    plan_id: str,
    subtitle_report_id: str,
    audio_report_id: str,
    project_root: Path,
    *,
    upgrade_all: bool = False,
    resumed_from_report: InputEvidence | None = None,
    resumed_from_report_id: str | None = None,
    resumption_decision: str | None = None,
    real_engines: RealEngineSelection | None = None,
) -> dict[str, object]:
    """Create one immutable transcription report from fully revalidated inputs.

    Every bound input identity -- the confirmed RunPlan and its SourceArtifact
    hashes, the retained subtitle report and its rules, and the required Audio
    analysis report -- is revalidated before the attempt proceeds; any drift
    retains a ``failed`` report. A revalidated attempt must satisfy the start
    precondition (a retained ``subtitle_unavailable_requires_asr_plan`` handoff or
    an explicit ``upgrade_all`` demand) or it is rejected; a subtitle-priority run
    never triggers ASR automatically. Before any execution the attempt records its
    pauses: a conservative resource estimate over the 12 GiB envelope retains a
    resumable ``resource_envelope_exceeded`` report, a subtitle-unavailable source
    retains a resumable ``awaiting_full_asr_resource_confirmation`` report, and
    otherwise -- no acquired offline ASR model existing yet -- the attempt retains
    ``model_acquisition_required`` with no transcription evidence. A resume passes
    ``resumption_decision``; each attempt owns a fresh workspace and never
    overwrites prior evidence, so there is no automatic retry.

    ``real_engines`` is run composition's real-adapter selection (Phase 12 ticket
    06): ``None`` on every automated-test run (the controlled offline path below),
    and the acquired real ASR engines when set, reached through
    :func:`~video_content_pipeline.real_engine_adapter.dispatch_real_stage`.
    """

    if real_engines is not None:
        return dispatch_real_stage(real_engines, stage="transcription")
    report_id = uuid.uuid4().hex
    workspace_path = project_root / "work" / "transcription-reports" / report_id
    report_path = workspace_path / "transcription-report.json"
    run_plan_evidence: InputEvidence | None = None
    subtitle_report_evidence: InputEvidence | None = None
    audio_report_evidence: InputEvidence | None = None
    model_registry_evidence: InputEvidence | None = None
    report_plan_id = plan_id
    report_subtitle_id = subtitle_report_id
    report_audio_id = audio_report_id
    status = TranscriptionReportStatus.FAILED
    run_plan_confirmed = False
    subtitle_rules_value: str | None = None
    source_bindings: tuple[SourceArtifactBinding, ...] = ()
    audio_binding = AudioReportBinding("not_available")
    start_precondition: TranscriptionStartPrecondition | None = None
    capabilities: tuple[AsrCapabilityAvailability, ...] = ()
    independent_review: IndependentReviewResolution | None = None
    required_decision: dict[str, object] | None = None
    diagnostics: tuple[PlanningDiagnostic, ...] = ()

    try:
        plan_path = project_root / "plans" / plan_id / "run-plan.json"
        plan = load_run_plan(plan_path)
        if plan.plan_id != plan_id:
            raise TranscriptionError(
                "run_plan_not_confirmed", "RunPlan identity does not match the requested plan ID."
            )
        confirmed_report = load_plan_report(
            project_root / "plans" / "reports" / plan.report_id / "plan-report.json"
        )
        if not confirmed_plan_matches(confirmed_report, plan):
            raise TranscriptionError(
                "run_plan_not_confirmed", "RunPlan evidence does not match a confirmed PlanReport."
            )
        revalidate_confirmed_inspection_evidence(
            confirmed_report,
            plan,
            drift_error=lambda: TranscriptionError(
                "inspection_evidence_changed",
                "PlanReport inspection evidence no longer matches the confirmed RunPlan.",
            ),
        )
        run_plan_confirmed = True
        source_bindings = _source_artifact_bindings(plan)
        expected_subtitle_id = _validated_subtitle_report_id(subtitle_report_id)
        subtitle_path = _subtitle_report_path(
            project_root, plan.source_artifacts, expected_subtitle_id
        )
        subtitle_report = _load_subtitle_report(subtitle_path)
        if (
            subtitle_report.report_id != expected_subtitle_id
            or subtitle_report.plan_id != plan.plan_id
        ):
            raise TranscriptionError(
                "subtitle_report_mismatch",
                "Subtitle candidate report does not belong to this RunPlan.",
            )
        subtitle_rules_value = _revalidate_subtitle_rules(subtitle_report, project_root)
        audio_report_evidence, audio_binding = _bind_audio_report(
            project_root, audio_report_id, plan.plan_id, subtitle_report.report_id
        )
        run_plan_evidence = input_evidence(plan_path)
        subtitle_report_evidence = input_evidence(subtitle_path)
        report_plan_id = plan.plan_id
        report_subtitle_id = subtitle_report.report_id
        report_audio_id = audio_binding.report_id or audio_report_id
        unavailable = subtitle_unavailable_parts(subtitle_report)
        subtitle_unavailable = bool(unavailable)
        if subtitle_unavailable:
            start_precondition = TranscriptionStartPrecondition(
                _SUBTITLE_UNAVAILABLE_HANDOFF, unavailable
            )
        elif upgrade_all:
            start_precondition = TranscriptionStartPrecondition(
                _EXPLICIT_UPGRADE,
                tuple(artifact.source_id for artifact in plan.source_artifacts),
            )
        else:
            raise TranscriptionError(
                "transcription_precondition_unmet",
                "A subtitle-priority run never triggers ASR automatically; transcribe requires "
                "a retained subtitle_unavailable_requires_asr_plan handoff or --upgrade-all.",
            )
        capability_report = evaluate_asr_capabilities(project_root)
        capabilities = capability_report.capabilities
        independent_review = capability_report.independent_review
        model_registry_evidence = capability_report.model_registry_evidence
        envelope_pause = transcription_resource_envelope_pause(capability_report)
        confirmation_granted = resumption_decision == FULL_ASR_RESOURCE_CONFIRMATION_DECISION
        if envelope_pause is not None:
            status = TranscriptionReportStatus.RESOURCE_ENVELOPE_EXCEEDED
            required_decision = {
                "reason": "resource_envelope_exceeded",
                "decision": RESOURCE_ENVELOPE_DECISION,
            }
            diagnostics = (
                PlanningDiagnostic(
                    "resource_envelope_exceeded",
                    "A conservative ASR resource estimate exceeds the 12 GiB envelope; "
                    "reconfigure rather than silently change model, quantization, or batch.",
                ),
            )
        elif subtitle_unavailable and not confirmation_granted:
            status = TranscriptionReportStatus.AWAITING_FULL_ASR_RESOURCE_CONFIRMATION
            required_decision = {
                "reason": "full_asr_resource_confirmation",
                "decision": FULL_ASR_RESOURCE_CONFIRMATION_DECISION,
            }
            diagnostics = (
                PlanningDiagnostic(
                    "full_asr_resource_confirmation",
                    "A subtitle-unavailable full ASR run must confirm its resource plan before "
                    "execution.",
                ),
            )
        else:
            status = TranscriptionReportStatus.MODEL_ACQUISITION_REQUIRED
            diagnostics = (
                PlanningDiagnostic(
                    "model_acquisition_required",
                    "No acquired offline ASR model is available; acquisition is required before "
                    "transcription.",
                ),
            )
    except (
        TranscriptionError,
        PlanningError,
        SubtitleReportError,
        OSError,
        ValueError,
    ) as error:
        status = TranscriptionReportStatus.FAILED
        run_plan_confirmed = False
        source_bindings = ()
        audio_report_evidence = None
        model_registry_evidence = None
        audio_binding = AudioReportBinding("not_available")
        start_precondition = None
        capabilities = ()
        independent_review = None
        required_decision = None
        diagnostics = (
            PlanningDiagnostic(
                getattr(error, "reason", "transcription_input_invalid"),
                str(error),
            ),
        )

    report = TranscriptionReport(
        report_id=report_id,
        plan_id=report_plan_id,
        subtitle_report_id=report_subtitle_id,
        audio_report_id=report_audio_id,
        status=status,
        workspace_path=workspace_path,
        report_path=report_path,
        run_plan_evidence=run_plan_evidence,
        subtitle_report_evidence=subtitle_report_evidence,
        audio_report_evidence=audio_report_evidence,
        model_registry_evidence=model_registry_evidence,
        resumed_from_report=resumed_from_report,
        resumed_from_report_id=resumed_from_report_id,
        resumption_decision=resumption_decision,
        start_precondition=start_precondition,
        revalidation=TranscriptionRevalidation(
            run_plan_confirmed=run_plan_confirmed,
            subtitle_rules_fingerprint=subtitle_rules_value,
            source_artifacts=source_bindings,
        ),
        audio_analysis=audio_binding,
        capabilities=capabilities,
        independent_review=independent_review,
        required_decision=required_decision,
        diagnostics=diagnostics,
    )
    _write_json_once(report_path, report.as_json())
    return {"status": report.status.value, "report": report.as_json()}


def resume_transcription(
    report_id: str,
    decision: str | None,
    project_root: Path,
) -> dict[str, object]:
    """Resume one retained transcription decision pause from an explicit decision.

    Resumption never auto-resumes and never changes identity-bound inputs: it
    requires an explicit report ID and an explicit user decision, and it may
    continue only a retained report whose decision pause it recognizes -- the
    Full-ASR resource confirmation pause (continued with
    ``full_asr_resource_plan_confirmed``) or the Transcription resource-envelope
    pause (continued with ``resource_configuration_changed``). A resume starts a
    fresh attempt from the retained plan, subtitle, and audio identities; it never
    overwrites the paused report, so there is no automatic retry.
    """

    prior_path = _transcription_report_path(project_root, report_id)
    try:
        prior_document = json.loads(prior_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TranscriptionError(
            "transcription_report_invalid", "Transcription report cannot be read."
        ) from error
    if not isinstance(prior_document, Mapping) or prior_document.get("report_id") != report_id:
        raise TranscriptionError("transcription_report_invalid", "Transcription report is invalid.")
    if decision is None:
        raise TranscriptionError(
            "transcription_resume_invalid", "Resume requires an explicit user decision."
        )
    pause_reason = _resumable_pause_reason(prior_document)
    if pause_reason is None:
        raise TranscriptionError(
            "transcription_resume_invalid",
            "Only a retained transcription decision pause can be resumed.",
        )
    if (
        pause_reason == "full_asr_resource_confirmation"
        and decision != FULL_ASR_RESOURCE_CONFIRMATION_DECISION
    ):
        raise TranscriptionError(
            "transcription_resume_invalid",
            "A Full-ASR resource confirmation pause requires "
            "--decision full_asr_resource_plan_confirmed.",
        )
    if pause_reason == "resource_envelope_exceeded" and decision != RESOURCE_ENVELOPE_DECISION:
        raise TranscriptionError(
            "transcription_resume_invalid",
            "A resource-envelope pause requires --decision resource_configuration_changed.",
        )
    plan_id, subtitle_report_id, audio_report_id, upgrade_all = _resumed_identities(prior_document)
    return transcribe(
        plan_id,
        subtitle_report_id,
        audio_report_id,
        project_root,
        upgrade_all=upgrade_all,
        resumed_from_report=input_evidence(prior_path),
        resumed_from_report_id=report_id,
        resumption_decision=decision,
    )


def subtitle_unavailable_parts(report: SubtitleCandidateReport) -> tuple[str, ...]:
    """Return the source IDs whose retained subtitle report hands off to ASR planning.

    A Part authorizes the full-ASR path only when it is in the
    ``subtitle_unavailable_requires_asr_plan`` state *and* carries the retained
    handoff diagnostic; the state alone is not treated as authorization.
    """

    return tuple(
        part.source_id
        for part in report.part_reports
        if part.state is SubtitlePartState.SUBTITLE_UNAVAILABLE_REQUIRES_ASR_PLAN
        and part.asr_planning_handoff is not None
        and part.asr_planning_handoff.reason == _SUBTITLE_UNAVAILABLE_HANDOFF
    )


def transcription_resource_envelope_pause(
    report: TranscriptionCapabilityReport,
) -> dict[str, object] | None:
    """Return the Transcription resource-envelope pause detail, if any candidate exceeds it.

    A candidate graded ``resource_envelope_exceeded`` by the shared 12 GiB
    eligibility gate means the conservative estimate exceeds the envelope; the
    attempt pauses rather than silently choosing a smaller model or quantization.
    """

    for capability in report.capabilities:
        for candidate in capability.candidates:
            if candidate.reason == "resource_envelope_exceeded":
                return {
                    "capability": capability.capability,
                    "candidate_id": candidate.candidate_id,
                    "reason": "resource_envelope_exceeded",
                    "resource_high_bytes": candidate.eligibility_evidence.get(
                        "resource_high_bytes"
                    ),
                }
    return None


def _source_artifact_bindings(plan: RunPlan) -> tuple[SourceArtifactBinding, ...]:
    return tuple(
        SourceArtifactBinding(artifact.source_id, artifact.sha256, artifact.byte_count)
        for artifact in plan.source_artifacts
    )


def _revalidate_subtitle_rules(report: SubtitleCandidateReport, project_root: Path) -> str:
    """Reject subtitle-rule drift between report creation and this attempt."""

    current = subtitle_rules_fingerprint(project_root)
    if report.subtitle_rules_fingerprint != current:
        raise TranscriptionError(
            "subtitle_rules_changed",
            "Subtitle rules no longer match the retained candidate report.",
        )
    return current


def _bind_audio_report(
    project_root: Path, audio_report_id: str, plan_id: str, subtitle_report_id: str
) -> tuple[InputEvidence, AudioReportBinding]:
    """Bind and revalidate the required Audio analysis report's input identities."""

    validated_id = validated_report_id(
        audio_report_id,
        invalid_error=lambda: TranscriptionError(
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
        raise TranscriptionError(
            "audio_report_invalid", "Audio analysis report cannot be read."
        ) from error
    if not isinstance(decoded, Mapping) or decoded.get("report_id") != validated_id:
        raise TranscriptionError("audio_report_invalid", "Audio analysis report is invalid.")
    if decoded.get("plan_id") != plan_id or decoded.get("subtitle_report_id") != subtitle_report_id:
        raise TranscriptionError(
            "audio_report_mismatch",
            "Audio analysis report is not bound to this RunPlan and subtitle report.",
        )
    return input_evidence(audio_path), AudioReportBinding(
        "bound",
        report_id=validated_id,
        plan_id=plan_id,
        subtitle_report_id=subtitle_report_id,
    )


def _resumable_pause_reason(report: Mapping[str, object]) -> str | None:
    """Return the resumable decision-pause reason of a retained report, if any."""

    required_decision = report.get("required_decision")
    if not isinstance(required_decision, Mapping):
        return None
    reason = required_decision.get("reason")
    status = report.get("status")
    if (
        status == TranscriptionReportStatus.AWAITING_FULL_ASR_RESOURCE_CONFIRMATION.value
        and reason == "full_asr_resource_confirmation"
    ):
        return "full_asr_resource_confirmation"
    if (
        status == TranscriptionReportStatus.RESOURCE_ENVELOPE_EXCEEDED.value
        and reason == "resource_envelope_exceeded"
    ):
        return "resource_envelope_exceeded"
    return None


def _resumed_identities(report: Mapping[str, object]) -> tuple[str, str, str, bool]:
    """Read the identity-bound plan, subtitle, and audio inputs from a paused report."""

    plan_id = report.get("plan_id")
    subtitle_report_id = report.get("subtitle_report_id")
    audio_report_id = report.get("audio_report_id")
    if not (
        isinstance(plan_id, str)
        and isinstance(subtitle_report_id, str)
        and isinstance(audio_report_id, str)
    ):
        raise TranscriptionError(
            "transcription_report_invalid", "Paused report omits its identity-bound inputs."
        )
    precondition = report.get("start_precondition")
    upgrade_all = (
        isinstance(precondition, Mapping) and precondition.get("basis") == _EXPLICIT_UPGRADE
    )
    return plan_id, subtitle_report_id, audio_report_id, upgrade_all


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
        raise TranscriptionError(
            "subtitle_report_invalid", "Subtitle candidate report cannot be read."
        ) from error
    return SubtitleCandidateReport.from_json(decoded, path)


def _validated_subtitle_report_id(value: str) -> str:
    return validated_report_id(
        value,
        invalid_error=lambda: TranscriptionError(
            "subtitle_report_invalid", "Subtitle candidate report ID must be a UUID."
        ),
    )


def _transcription_report_path(project_root: Path, report_id: str) -> Path:
    validated_id = validated_report_id(
        report_id,
        invalid_error=lambda: TranscriptionError(
            "transcription_report_invalid", "Transcription report ID must be a UUID."
        ),
    )
    return (
        project_root / "work" / "transcription-reports" / validated_id / "transcription-report.json"
    )


def _write_json_once(path: Path, payload: object) -> None:
    write_json_once(
        path,
        payload,
        conflict_error=lambda message: TranscriptionError("transcription_report_conflict", message),
    )
