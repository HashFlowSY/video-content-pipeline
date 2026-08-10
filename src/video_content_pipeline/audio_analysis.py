"""Phase 5's no-model audio-analysis CLI contract."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from video_content_pipeline.coverage import StreamCoverage
from video_content_pipeline.inspection import PlanInspectionEvidence
from video_content_pipeline.planning import (
    PlanningDiagnostic,
    PlanningError,
    PlanReport,
    PlanState,
    RunPlan,
    load_plan_report,
    load_run_plan,
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
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval


class AudioAnalysisReportState(StrEnum):
    """The current minimum Phase 5 report can only be blocked by missing models."""

    BLOCKED = "blocked"
    PARTIAL = "partial"


class AudioAnalysisError(ValueError):
    """A rejected Phase 5 input with a machine-readable diagnostic reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class VoiceActivityState(StrEnum):
    """The only formal audio states permitted by calibrated VAD evidence."""

    SPEECH_LIKELY = "speech_likely"
    NON_SPEECH = "non_speech"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class AlignmentCue:
    """One immutable Primary subtitle cue eligible only for timing adoption."""

    source_ordinal: int
    text: str
    interval: HalfOpenInterval


@dataclass(frozen=True)
class AlignmentProposal:
    """A controlled aligner proposal for one existing subtitle cue identity."""

    source_ordinal: int
    text: str
    interval: HalfOpenInterval
    confidence: float


@dataclass(frozen=True)
class AlignmentCandidateEvidence:
    """The retained adoption outcome for one Primary subtitle cue."""

    source_ordinal: int
    original_interval: HalfOpenInterval
    proposed_interval: HalfOpenInterval
    interval: HalfOpenInterval
    adopted: bool
    reason: str
    global_reason: str | None
    vad_indeterminate_risk: bool


@dataclass(frozen=True)
class AdoptedAlignmentTimingView:
    """An immutable timing derivative that leaves source subtitle evidence unchanged."""

    state: str
    source_id: str
    language: str
    cues: tuple[AlignmentCue, ...]
    candidates: tuple[AlignmentCandidateEvidence, ...]
    diagnostic: str | None = None


@dataclass(frozen=True)
class ProjectedAlignmentPart:
    """One complete controlled-adapter alignment projection for a selected Part."""

    language: str
    proposals: tuple[AlignmentProposal, ...]


@dataclass(frozen=True)
class VoiceActivityCandidateSegment:
    """One exact, projected VAD classification before coverage partitioning."""

    interval: HalfOpenInterval
    state: VoiceActivityState


@dataclass(frozen=True)
class VoiceActivityInterval:
    """One interval in the complete formal VAD partition for usable audio."""

    interval: HalfOpenInterval
    state: VoiceActivityState


@dataclass(frozen=True)
class VadRiskEvidence:
    """A retained interval whose report prominence is independent of its existence."""

    interval: HalfOpenInterval
    elevated: bool


@dataclass(frozen=True)
class LongSilenceEvidence:
    """A duration-qualified continuous calibrated non-speech interval."""

    interval: HalfOpenInterval


@dataclass(frozen=True)
class VadPartEvidence:
    """Audio-only VAD evidence and separately derived subtitle-coverage risks."""

    source_id: str
    stream_index: int
    voice_activity_intervals: tuple[VoiceActivityInterval, ...]
    uncovered_speech_risks: tuple[VadRiskEvidence, ...]
    audio_state_indeterminate: tuple[VadRiskEvidence, ...]
    long_silences: tuple[LongSilenceEvidence, ...]


@dataclass(frozen=True)
class SpeakerTurnCandidate:
    """One anonymous diarization group before VAD and calibration gates."""

    cluster_id: str
    interval: HalfOpenInterval
    confidence: float


@dataclass(frozen=True)
class RoleCandidateProposal:
    """One adapter-proposed role backed by an exact subtitle-text citation."""

    cluster_id: str
    role: str
    source_ordinal: int
    text: str


@dataclass(frozen=True)
class UserRoleMetadata:
    """One explicit user role assignment for an anonymous diarization group."""

    source_id: str
    cluster_id: str
    role: str


@dataclass(frozen=True)
class ProjectedDiarizationPart:
    """One complete controlled-adapter diarization projection for a selected Part."""

    turns: tuple[SpeakerTurnCandidate, ...]
    role_candidates: tuple[RoleCandidateProposal, ...]


@dataclass(frozen=True)
class AnalysisAudioStreamSelection:
    """One Phase 5 audio stream selected from retained inspection evidence."""

    source_id: str
    stream_index: int
    codec: str
    language: str | None
    disposition: dict[str, object]
    structural_evidence_sha256: str
    coverage_evidence_sha256: str

    def as_json(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "stream_index": self.stream_index,
            "codec": self.codec,
            "language": self.language,
            "disposition": self.disposition,
            "structural_evidence_sha256": self.structural_evidence_sha256,
            "coverage_evidence_sha256": self.coverage_evidence_sha256,
        }


@dataclass(frozen=True)
class InputEvidence:
    """Hash-recorded read-only evidence for a required retained input."""

    path: Path
    sha256: str
    byte_count: int

    def as_json(self) -> dict[str, object]:
        return {
            "path": self.path.as_posix(),
            "sha256": self.sha256,
            "byte_count": self.byte_count,
        }


@dataclass(frozen=True)
class CapabilityAvailability:
    """The explicit no-model state for one provider-neutral Phase 5 capability."""

    capability: str
    state: str
    candidates: tuple[dict[str, object], ...] = ()

    def as_json(self) -> dict[str, object]:
        return {
            "capability": self.capability,
            "state": self.state,
            "model": None,
            "diagnostic": {
                "reason": self.state,
                "message": _capability_message(self.capability, self.state),
            },
            "candidates": list(self.candidates),
        }


@dataclass(frozen=True)
class AudioAnalysisReport:
    """Immutable machine-readable result of one no-model analysis attempt."""

    report_id: str
    plan_id: str
    subtitle_report_id: str
    state: AudioAnalysisReportState
    workspace_path: Path
    report_path: Path
    run_plan_evidence: InputEvidence | None
    subtitle_report_evidence: InputEvidence | None
    model_registry_evidence: InputEvidence | None
    resumed_from_report: InputEvidence | None
    capabilities: tuple[CapabilityAvailability, ...]
    analysis_audio_streams: tuple[AnalysisAudioStreamSelection, ...]
    formal_evidence: tuple[dict[str, object], ...]
    processing_authorization: dict[str, object]
    diagnostics: tuple[PlanningDiagnostic, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "report_id": self.report_id,
            "plan_id": self.plan_id,
            "subtitle_report_id": self.subtitle_report_id,
            "state": self.state.value,
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
            },
            "capabilities": [capability.as_json() for capability in self.capabilities],
            "analysis_audio_streams": [
                selection.as_json() for selection in self.analysis_audio_streams
            ],
            "formal_evidence": list(self.formal_evidence),
            "diagnostics": [diagnostic.as_json() for diagnostic in self.diagnostics],
            "processing_authorization": self.processing_authorization,
            "guarantees": {
                "asr": "not_attempted",
                "model_acquisition": "not_attempted",
                "model_execution": "not_attempted",
                "network_access": "not_attempted",
                "outputs_publication": "not_attempted",
                "phase_4_artifact_mutation": "not_attempted",
                "run_plan_mutation": "not_attempted",
            },
        }


_CAPABILITIES = ("vad", "forced_alignment", "diarization")
_CAPABILITY_STATES = {
    "model_acquisition_required",
    "model_credential_gated",
    "model_ineligible",
    "model_unavailable",
}


def analyze_audio(
    plan_id: str,
    subtitle_report_id: str,
    project_root: Path,
    requested_audio_streams: tuple[str, ...] = (),
    requested_diarization_candidate: str | None = None,
    requested_role_metadata: tuple[str, ...] = (),
    resumed_from_report: InputEvidence | None = None,
) -> dict[str, object]:
    """Retain the Phase 5 no-model result without media processing or a model runtime."""

    report_id = uuid.uuid4().hex
    workspace_path = project_root / "work" / "audio-analysis-reports" / report_id
    report_path = workspace_path / "audio-analysis-report.json"
    run_plan_evidence: InputEvidence | None = None
    subtitle_report_evidence: InputEvidence | None = None
    model_registry_evidence: InputEvidence | None = None
    diagnostics: tuple[PlanningDiagnostic, ...] = ()
    capabilities: tuple[CapabilityAvailability, ...] = ()
    analysis_audio_streams: tuple[AnalysisAudioStreamSelection, ...] = ()
    formal_evidence: tuple[dict[str, object], ...] = ()
    processing_authorization: dict[str, object] = {
        "state": "not_started",
        "reason": "model_availability_only",
    }
    report_plan_id = plan_id
    report_subtitle_id = subtitle_report_id

    try:
        plan_path = project_root / "plans" / plan_id / "run-plan.json"
        plan = load_run_plan(plan_path)
        if plan.plan_id != plan_id:
            raise AudioAnalysisError(
                "run_plan_not_confirmed", "RunPlan identity does not match the requested plan ID."
            )
        confirmed_report = load_plan_report(
            project_root / "plans" / "reports" / plan.report_id / "plan-report.json"
        )
        if not _matches_confirmed_plan(confirmed_report, plan):
            raise AudioAnalysisError(
                "run_plan_not_confirmed", "RunPlan evidence does not match a confirmed PlanReport."
            )
        expected_subtitle_id = _validated_report_id(subtitle_report_id)
        subtitle_path = _subtitle_report_path(
            project_root, plan.source_artifacts, expected_subtitle_id
        )
        subtitle_report = _load_subtitle_report(subtitle_path)
        if (
            subtitle_report.report_id != expected_subtitle_id
            or subtitle_report.plan_id != plan.plan_id
        ):
            raise AudioAnalysisError(
                "subtitle_report_mismatch",
                "Subtitle candidate report does not belong to this RunPlan.",
            )
        run_plan_evidence = _input_evidence(plan_path)
        subtitle_report_evidence = _input_evidence(subtitle_path)
        registry_path = project_root / "models" / "registry.json"
        if registry_path.exists():
            model_registry_evidence = _input_evidence(registry_path)
        capabilities = _capabilities_from_registry(project_root, workspace_path)
        vad_candidate = _qualified_vad_candidate(capabilities)
        alignment_candidate = _qualified_alignment_candidate(capabilities)
        if vad_candidate is not None:
            _revalidate_analysis_inputs(plan, subtitle_report, project_root)
            analysis_audio_streams = _select_audio_streams(
                confirmed_report.inspection_evidence, requested_audio_streams
            )
            vad_evidence = _derive_vad_evidence(
                vad_candidate,
                analysis_audio_streams,
                confirmed_report.inspection_evidence,
                subtitle_report,
                plan,
                project_root,
            )
            evidence: list[dict[str, object]] = [vad_evidence]
            formal_evidence = tuple(evidence)
            processing_authorization = {"state": "approved", "reason": "vad_revalidated"}
            if alignment_candidate is not None:
                evidence.append(
                    _derive_alignment_evidence(
                        alignment_candidate,
                        analysis_audio_streams,
                        confirmed_report.inspection_evidence,
                        subtitle_report,
                        plan,
                        vad_evidence,
                        project_root,
                        workspace_path,
                    )
                )
                formal_evidence = tuple(evidence)
            diarization_candidate = _qualified_diarization_candidate(
                capabilities, requested_diarization_candidate
            )
            if diarization_candidate is not None:
                evidence.append(
                    _derive_diarization_evidence(
                        diarization_candidate,
                        analysis_audio_streams,
                        confirmed_report.inspection_evidence,
                        subtitle_report,
                        plan,
                        vad_evidence,
                        project_root,
                        workspace_path,
                        _parse_role_metadata_requests(requested_role_metadata),
                    )
                )
                formal_evidence = tuple(evidence)
        report_plan_id = plan.plan_id
        report_subtitle_id = subtitle_report.report_id
    except (AudioAnalysisError, PlanningError, SubtitleReportError, OSError, ValueError) as error:
        diagnostics = (
            PlanningDiagnostic(
                getattr(error, "reason", "audio_analysis_input_invalid"),
                str(error),
            ),
        )

    report = AudioAnalysisReport(
        report_id=report_id,
        plan_id=report_plan_id,
        subtitle_report_id=report_subtitle_id,
        state=(
            AudioAnalysisReportState.PARTIAL
            if formal_evidence
            else AudioAnalysisReportState.BLOCKED
        ),
        workspace_path=workspace_path,
        report_path=report_path,
        run_plan_evidence=run_plan_evidence,
        subtitle_report_evidence=subtitle_report_evidence,
        model_registry_evidence=model_registry_evidence,
        resumed_from_report=resumed_from_report,
        capabilities=capabilities,
        analysis_audio_streams=analysis_audio_streams,
        formal_evidence=formal_evidence,
        processing_authorization=processing_authorization,
        diagnostics=diagnostics,
    )
    _write_json_once(report_path, report.as_json())
    return {"status": report.state.value, "report": report.as_json()}


def resume_audio_analysis(
    report_id: str,
    requested_diarization_candidate: str,
    project_root: Path,
    requested_role_metadata: tuple[str, ...] = (),
) -> dict[str, object]:
    """Resume an explicit diarization-selection pause from retained report evidence."""

    prior_path = _analysis_report_path(project_root, report_id)
    try:
        prior_document = json.loads(prior_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AudioAnalysisError(
            "analysis_report_invalid", "Audio analysis report cannot be read."
        ) from error
    if not isinstance(prior_document, Mapping):
        raise AudioAnalysisError("analysis_report_invalid", "Audio analysis report is invalid.")
    if (
        prior_document.get("report_id") != report_id
        or prior_document.get("state") != AudioAnalysisReportState.PARTIAL.value
        or not _has_diarization_selection_pause(prior_document)
    ):
        raise AudioAnalysisError(
            "analysis_resume_invalid",
            "Only a retained diarization-selection pause can be resumed.",
        )
    plan_id = prior_document.get("plan_id")
    subtitle_report_id = prior_document.get("subtitle_report_id")
    if not isinstance(plan_id, str) or not isinstance(subtitle_report_id, str):
        raise AudioAnalysisError(
            "analysis_report_invalid", "Audio analysis report identity is invalid."
        )
    input_evidence = prior_document.get("input_evidence")
    if not isinstance(input_evidence, Mapping):
        raise AudioAnalysisError(
            "analysis_report_invalid", "Audio analysis report inputs are invalid."
        )
    for key in ("run_plan", "subtitle_candidate_report", "model_registry"):
        evidence = input_evidence.get(key)
        if evidence is not None:
            if not isinstance(evidence, Mapping):
                raise AudioAnalysisError(
                    "analysis_report_invalid", "Audio analysis report inputs are invalid."
                )
            _retained_evidence_path(evidence, project_root)
    streams = prior_document.get("analysis_audio_streams")
    if not isinstance(streams, list) or not streams:
        raise AudioAnalysisError(
            "analysis_report_invalid", "Audio analysis stream evidence is invalid."
        )
    requested_audio_streams: list[str] = []
    for stream in streams:
        if not isinstance(stream, Mapping):
            raise AudioAnalysisError(
                "analysis_report_invalid", "Audio analysis stream evidence is invalid."
            )
        source_id = stream.get("source_id")
        stream_index = stream.get("stream_index")
        if not isinstance(source_id, str) or not isinstance(stream_index, int):
            raise AudioAnalysisError(
                "analysis_report_invalid", "Audio analysis stream evidence is invalid."
            )
        requested_audio_streams.append(f"{source_id}={stream_index}")
    return analyze_audio(
        plan_id,
        subtitle_report_id,
        project_root,
        tuple(requested_audio_streams),
        requested_diarization_candidate,
        requested_role_metadata,
        _input_evidence(prior_path),
    )


def _analysis_report_path(project_root: Path, report_id: str) -> Path:
    try:
        validated_id = uuid.UUID(hex=report_id).hex
    except ValueError as error:
        raise AudioAnalysisError(
            "analysis_report_invalid", "Audio analysis report ID must be a UUID."
        ) from error
    return (
        project_root
        / "work"
        / "audio-analysis-reports"
        / validated_id
        / "audio-analysis-report.json"
    )


def _has_diarization_selection_pause(report: Mapping[str, object]) -> bool:
    diagnostics = report.get("diagnostics")
    return isinstance(diagnostics, list) and any(
        isinstance(diagnostic, Mapping)
        and diagnostic.get("reason") == "diarization_model_selection_required"
        for diagnostic in diagnostics
    )


def _matches_confirmed_plan(confirmed_report: PlanReport, plan: RunPlan) -> bool:
    return (
        confirmed_report.state is PlanState.READY_FOR_CONFIRMATION
        and confirmed_report.source_artifacts == plan.source_artifacts
        and confirmed_report.tools == plan.tools
        and confirmed_report.disk_headroom == plan.disk_headroom
        and confirmed_report.configuration_fingerprint == plan.configuration_fingerprint
        and confirmed_report.url_authorizations == plan.url_authorizations
    )


_CANDIDATE_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_MAX_MODEL_RESOURCE_BYTES = 24 * 1024**3
_IDENTITY_FIELDS = (
    "asset_sha256",
    "backend",
    "backend_version",
    "precision",
    "device_class",
    "rules_fingerprint",
)


def _capabilities_from_registry(
    project_root: Path, workspace_path: Path
) -> tuple[CapabilityAvailability, ...]:
    registry_path = project_root / "models" / "registry.json"
    if not registry_path.exists():
        return tuple(
            CapabilityAvailability(capability, "model_acquisition_required")
            for capability in _CAPABILITIES
        )
    try:
        decoded = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AudioAnalysisError(
            "model_registry_invalid", "Model registry cannot be read."
        ) from error
    if not isinstance(decoded, Mapping):
        raise AudioAnalysisError("model_registry_invalid", "Model registry has an invalid schema.")
    schema_version = decoded.get("schema_version")
    if schema_version == 2:
        return _capabilities_from_candidate_matrix(decoded, project_root, workspace_path)
    if schema_version != 1:
        raise AudioAnalysisError("model_registry_invalid", "Model registry has an invalid schema.")
    models = decoded.get("models")
    if not isinstance(models, list):
        raise AudioAnalysisError("model_registry_invalid", "Model registry needs a models list.")
    states_by_capability: dict[str, str] = {}
    for model in models:
        if not isinstance(model, Mapping):
            raise AudioAnalysisError(
                "model_registry_invalid", "Model registry entry must be an object."
            )
        capability = model.get("capability")
        state = model.get("status")
        if capability not in _CAPABILITIES or not isinstance(state, str):
            raise AudioAnalysisError("model_registry_invalid", "Model registry entry is invalid.")
        if state not in _CAPABILITY_STATES:
            raise AudioAnalysisError(
                "model_registry_invalid", "Model registry status is unsupported."
            )
        if capability in states_by_capability:
            raise AudioAnalysisError(
                "model_registry_invalid", "Model registry has duplicate capability entries."
            )
        states_by_capability[capability] = state
    return tuple(
        CapabilityAvailability(
            capability,
            states_by_capability.get(capability, "model_acquisition_required"),
        )
        for capability in _CAPABILITIES
    )


def _capabilities_from_candidate_matrix(
    registry: Mapping[str, object], project_root: Path, workspace_path: Path
) -> tuple[CapabilityAvailability, ...]:
    candidates = registry.get("candidates")
    if not isinstance(candidates, list):
        raise AudioAnalysisError(
            "model_registry_invalid", "Model registry needs a candidates list."
        )
    candidates_by_capability: dict[str, list[dict[str, object]]] = {
        capability: [] for capability in _CAPABILITIES
    }
    candidate_ids: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise AudioAnalysisError("model_registry_invalid", "Model candidate must be an object.")
        candidate_id = candidate.get("candidate_id")
        capability = candidate.get("capability")
        if (
            not isinstance(candidate_id, str)
            or _CANDIDATE_ID_PATTERN.fullmatch(candidate_id) is None
            or capability not in _CAPABILITIES
        ):
            raise AudioAnalysisError(
                "model_registry_invalid", "Model candidate identity is invalid."
            )
        if candidate_id in candidate_ids:
            raise AudioAnalysisError(
                "model_registry_invalid", "Model candidate IDs must be unique."
            )
        candidate_ids.add(candidate_id)
        candidates_by_capability[capability].append(
            _evaluate_candidate(candidate, candidate_id, capability, project_root, workspace_path)
        )
    return tuple(
        CapabilityAvailability(
            capability,
            _capability_state_from_candidates(capability, candidates_by_capability[capability]),
            tuple(candidates_by_capability[capability]),
        )
        for capability in _CAPABILITIES
    )


def _capability_state_from_candidates(capability: str, candidates: list[dict[str, object]]) -> str:
    if capability == "diarization" and not any(
        candidate["state"] == "eligible" for candidate in candidates
    ):
        return "model_acquisition_required"
    if any(candidate["state"] == "eligible" for candidate in candidates):
        return "model_acquisition_required"
    if any(candidate["reason"] == "model_credential_gated" for candidate in candidates):
        return "model_credential_gated"
    if candidates:
        return "model_ineligible"
    return "model_acquisition_required"


def _evaluate_candidate(
    candidate: Mapping[str, object],
    candidate_id: str,
    capability: str,
    project_root: Path,
    workspace_path: Path,
) -> dict[str, object]:
    state, reason = _candidate_eligibility(candidate, project_root)
    result: dict[str, object] = {
        "candidate_id": candidate_id,
        "capability": capability,
        "state": state,
        "reason": reason,
        "eligibility_evidence": _candidate_eligibility_evidence(candidate, project_root),
        "adapter": None,
        "calibration": {"state": "not_evaluated", "record": None, "profile": None},
        "formal_evidence": [],
    }
    if state != "eligible":
        return result
    adapter = candidate.get("controlled_adapter")
    if adapter is None:
        result["calibration"] = {
            "state": "calibration_required",
            "record": None,
            "profile": None,
        }
        return result
    adapter_result = _retain_controlled_adapter(
        adapter, candidate, candidate_id, capability, workspace_path
    )
    result["adapter"] = adapter_result
    if adapter_result["state"] != "projected":
        return result
    result["calibration"] = _evaluate_calibration(
        candidate.get("calibration_evaluation"),
        candidate,
        candidate_id,
        capability,
        adapter_result,
        project_root,
        workspace_path,
    )
    return result


def _candidate_eligibility_evidence(
    candidate: Mapping[str, object], project_root: Path
) -> dict[str, object]:
    resource_estimate = candidate.get("resource_estimate")
    dependency_plan = _project_local_file(project_root, candidate.get("dependency_plan"))
    return {
        "official_source": candidate.get("official_source"),
        "license_approved": candidate.get("license_approved"),
        "revision": candidate.get("revision"),
        "asset_sha256": candidate.get("asset_sha256"),
        "offline_runtime": candidate.get("offline_runtime"),
        "credential_required": candidate.get("credential_required"),
        "telemetry": candidate.get("telemetry"),
        "dependency_plan": _input_evidence(dependency_plan).as_json()
        if dependency_plan is not None
        else None,
        "resource_high_bytes": (
            resource_estimate.get("high_bytes") if isinstance(resource_estimate, Mapping) else None
        ),
    }


def _candidate_eligibility(
    candidate: Mapping[str, object], project_root: Path
) -> tuple[str, str | None]:
    if candidate.get("credential_required") is True:
        return "blocked", "model_credential_gated"
    source = candidate.get("official_source")
    asset_sha256 = candidate.get("asset_sha256")
    resource_estimate = candidate.get("resource_estimate")
    required = (
        isinstance(source, Mapping)
        and isinstance(source.get("url"), str)
        and source["url"].startswith("https://")
        and source.get("approved") is True
        and candidate.get("license_approved") is True
        and isinstance(candidate.get("revision"), str)
        and bool(candidate.get("revision"))
        and isinstance(asset_sha256, str)
        and _SHA256_PATTERN.fullmatch(asset_sha256) is not None
        and candidate.get("offline_runtime") is True
        and candidate.get("credential_required") is False
        and candidate.get("telemetry") is False
        and _project_local_file(project_root, candidate.get("dependency_plan")) is not None
        and isinstance(resource_estimate, Mapping)
        and isinstance(resource_estimate.get("high_bytes"), int)
        and not isinstance(resource_estimate.get("high_bytes"), bool)
        and 0 <= resource_estimate["high_bytes"] <= _MAX_MODEL_RESOURCE_BYTES
    )
    if required:
        return "eligible", None
    return "unsupported", "model_candidate_evidence_incomplete"


def _retain_controlled_adapter(
    adapter: object,
    candidate: Mapping[str, object],
    candidate_id: str,
    capability: str,
    workspace_path: Path,
) -> dict[str, object]:
    if not isinstance(adapter, Mapping) or not isinstance(adapter.get("adapter_version"), str):
        return {
            "state": "model_output_invalid",
            "raw_output": None,
            "projection": None,
            "diagnostic": _diagnostic_json(
                "controlled_adapter_invalid", "Adapter metadata is invalid."
            ),
        }
    if "raw_output" not in adapter:
        return {
            "state": "model_output_invalid",
            "raw_output": None,
            "projection": None,
            "diagnostic": _diagnostic_json("model_output_invalid", "Raw native output is missing."),
        }
    candidate_path = workspace_path / "capabilities" / capability / candidate_id
    try:
        raw_path = candidate_path / "raw-native-output.json"
        _write_json_once(raw_path, adapter["raw_output"])
        raw_evidence = _input_evidence(raw_path).as_json()
        adapter_version_path = candidate_path / "adapter-version.json"
        _write_json_once(adapter_version_path, {"adapter_version": adapter["adapter_version"]})
        adapter_version_evidence = _input_evidence(adapter_version_path).as_json()
    except (OSError, TypeError, ValueError) as error:
        return {
            "state": "model_output_invalid",
            "raw_output": None,
            "projection": None,
            "diagnostic": _diagnostic_json("model_output_invalid", str(error)),
        }
    projection = adapter.get("projection")
    if not _is_complete_projection(projection, capability, candidate):
        if projection is not None:
            _write_json_once(candidate_path / "model-output-projection-invalid.json", projection)
        return {
            "state": "model_output_invalid",
            "adapter_version": adapter_version_evidence,
            "raw_output": raw_evidence,
            "projection": None,
            "diagnostic": _diagnostic_json(
                "model_output_invalid",
                "Model-output projection is incomplete or does not match the exact "
                "execution identity.",
            ),
        }
    projection_path = candidate_path / "model-output-projection.json"
    _write_json_once(projection_path, projection)
    return {
        "state": "projected",
        "adapter_version": adapter_version_evidence,
        "raw_output": raw_evidence,
        "projection": _input_evidence(projection_path).as_json(),
        "diagnostic": None,
    }


def _is_complete_projection(
    projection: object, capability: str, candidate: Mapping[str, object]
) -> bool:
    if not isinstance(projection, Mapping):
        return False
    identity = projection.get("model_identity")
    if (
        projection.get("schema_version") != 1
        or projection.get("capability") != capability
        or not isinstance(identity, Mapping)
        or not isinstance(projection.get("result"), Mapping)
    ):
        return False
    return all(
        isinstance(identity.get(field), str)
        and bool(identity.get(field))
        and (field != "asset_sha256" or identity[field] == candidate.get("asset_sha256"))
        for field in _IDENTITY_FIELDS
    )


def _evaluate_calibration(
    evaluation: object,
    candidate: Mapping[str, object],
    candidate_id: str,
    capability: str,
    adapter_result: Mapping[str, object],
    project_root: Path,
    workspace_path: Path,
) -> dict[str, object]:
    if evaluation is None:
        return {"state": "calibration_required", "record": None, "profile": None}
    record_path = (
        workspace_path / "capabilities" / capability / candidate_id / "calibration-evaluation.json"
    )
    try:
        if not isinstance(evaluation, Mapping) or evaluation.get("schema_version") != 1:
            raise AudioAnalysisError(
                "calibration_failed", "Calibration evaluation schema is invalid."
            )
        fixture = evaluation.get("reference_fixture")
        if not isinstance(fixture, Mapping):
            raise AudioAnalysisError(
                "calibration_failed", "Calibration reference fixture is missing."
            )
        fixture_path = _project_local_path(project_root, fixture.get("path"))
        expected_fixture_sha256 = fixture.get("sha256")
        if (
            not isinstance(expected_fixture_sha256, str)
            or _SHA256_PATTERN.fullmatch(expected_fixture_sha256) is None
            or not fixture_path.is_file()
            or _input_evidence(fixture_path).sha256 != expected_fixture_sha256
        ):
            raise AudioAnalysisError(
                "calibration_failed", "Calibration reference fixture is not hash-pinned."
            )
        reference_data = json.loads(fixture_path.read_text(encoding="utf-8"))
        expected_projection = (
            reference_data.get("expected_projection")
            if isinstance(reference_data, Mapping)
            else None
        )
        thresholds = (
            reference_data.get("thresholds") if isinstance(reference_data, Mapping) else None
        )
        if (
            not isinstance(expected_projection, Mapping)
            or not isinstance(thresholds, Mapping)
            or not thresholds
            or not isinstance(evaluation.get("evaluator_version"), str)
            or not evaluation["evaluator_version"]
        ):
            raise AudioAnalysisError("calibration_failed", "Calibration evaluation is incomplete.")
        projection_evidence = adapter_result.get("projection")
        if not isinstance(projection_evidence, Mapping):
            raise AudioAnalysisError(
                "calibration_failed", "Adapter projection evidence is missing."
            )
        projection_path = Path(str(projection_evidence["path"]))
        projection = json.loads(projection_path.read_text(encoding="utf-8"))
        passed = _canonical_json(expected_projection) == _canonical_json(projection)
        expected_results_path = record_path.with_name("expected-calibration-results.json")
        _write_json_once(expected_results_path, expected_projection)
        expected_results_evidence = _input_evidence(expected_results_path).as_json()
        record: dict[str, object] = {
            "schema_version": 1,
            "state": "qualified" if passed else "calibration_failed",
            "candidate_id": candidate_id,
            "capability": capability,
            "model_identity": projection["model_identity"],
            "reference_fixture": _input_evidence(fixture_path).as_json(),
            "candidate_output": projection_evidence,
            "expected_results": expected_results_evidence,
            "thresholds": dict(thresholds),
            "false_accepts": 0 if passed else 1,
            "false_rejects": 0 if passed else 1,
            "evaluator_version": evaluation["evaluator_version"],
        }
        _write_json_once(record_path, record)
        record_evidence = _input_evidence(record_path).as_json()
        if not passed:
            return {"state": "calibration_failed", "record": record_evidence, "profile": None}
        profile_path = record_path.with_name("calibration-profile.json")
        profile = {
            "schema_version": 1,
            "candidate_id": candidate_id,
            "capability": capability,
            "model_identity": projection["model_identity"],
            "calibration_evaluation": record_evidence,
            "qualification_scope": "synthetic_verification_only",
        }
        _write_json_once(profile_path, profile)
        return {
            "state": "qualified",
            "record": record_evidence,
            "profile": _input_evidence(profile_path).as_json(),
        }
    except (AudioAnalysisError, OSError, TypeError, ValueError, KeyError) as error:
        failed_record = {
            "schema_version": 1,
            "state": "calibration_failed",
            "candidate_id": candidate_id,
            "capability": capability,
            "diagnostic": _diagnostic_json(
                getattr(error, "reason", "calibration_failed"), str(error)
            ),
        }
        _write_json_once(record_path, failed_record)
        return {
            "state": "calibration_failed",
            "record": _input_evidence(record_path).as_json(),
            "profile": None,
        }


def _project_local_path(project_root: Path, value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise AudioAnalysisError("calibration_failed", "Calibration fixture path is invalid.")
    path = (project_root / value).resolve()
    if not path.is_relative_to(project_root.resolve()):
        raise AudioAnalysisError(
            "calibration_failed", "Calibration fixture must stay inside the project."
        )
    return path


def _project_local_file(project_root: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    path = (project_root / value).resolve()
    if not path.is_relative_to(project_root.resolve()) or not path.is_file():
        return None
    return path


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_json(value: object) -> str:
    from hashlib import sha256

    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _diagnostic_json(reason: str, message: str) -> dict[str, str]:
    return {"reason": reason, "message": message}


def _capability_message(capability: str, state: str) -> str:
    messages = {
        "model_acquisition_required": (
            f"No explicitly approved, identity-pinned offline model is available for {capability}."
        ),
        "model_credential_gated": (
            f"A {capability} model candidate requires credentials and is blocked."
        ),
        "model_ineligible": f"No registered {capability} model satisfies the eligibility gates.",
        "model_unavailable": f"A registered {capability} model is unavailable offline.",
    }
    return messages[state]


def _validated_report_id(value: str) -> str:
    try:
        return uuid.UUID(hex=value).hex
    except ValueError as error:
        raise AudioAnalysisError(
            "subtitle_report_invalid", "Subtitle candidate report ID must be a UUID."
        ) from error


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
        raise AudioAnalysisError(
            "subtitle_report_invalid", "Subtitle candidate report cannot be read."
        ) from error
    return SubtitleCandidateReport.from_json(decoded, path)


def _input_evidence(path: Path) -> InputEvidence:
    digest, byte_count = sha256_file(path)
    return InputEvidence(path, digest, byte_count)


def _write_json_once(path: Path, payload: object) -> None:
    encoded = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != encoded:
            raise AudioAnalysisError(
                "audio_analysis_report_conflict", f"Immutable record differs: {path}"
            )
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded, encoding="utf-8")


def derive_vad_part_evidence(
    *,
    source_id: str,
    stream_index: int,
    audio_coverage: StreamCoverage,
    candidate_segments: tuple[VoiceActivityCandidateSegment, ...],
    caption_intervals: tuple[HalfOpenInterval, ...],
    uncovered_speech_threshold: ExactTime,
    long_silence_threshold: ExactTime,
) -> VadPartEvidence:
    """Partition known audio, retaining VAD uncertainty and caption-gap risks separately."""

    if uncovered_speech_threshold <= ExactTime(0) or long_silence_threshold <= ExactTime(0):
        raise AudioAnalysisError(
            "vad_threshold_invalid", "VAD duration thresholds must be positive."
        )
    usable_intervals = _usable_audio_intervals(audio_coverage)
    _validate_candidate_segments(candidate_segments, usable_intervals)
    partition = _partition_usable_audio(usable_intervals, candidate_segments)
    caption_union = _union_intervals(caption_intervals)

    uncovered_speech_risks = tuple(
        VadRiskEvidence(interval, interval.end - interval.start > uncovered_speech_threshold)
        for activity in partition
        if activity.state is VoiceActivityState.SPEECH_LIKELY
        for interval in _subtract_intervals(activity.interval, caption_union)
    )
    indeterminate_intervals = [
        interval
        for activity in partition
        if activity.state is VoiceActivityState.INDETERMINATE
        for interval in _subtract_intervals(activity.interval, caption_union)
    ]
    indeterminate_intervals.extend(
        interval
        for gap in audio_coverage.gaps
        for interval in _subtract_intervals(gap, caption_union)
    )
    audio_state_indeterminate = tuple(
        VadRiskEvidence(interval, False)
        for interval in sorted(indeterminate_intervals, key=lambda item: (item.start, item.end))
    )
    long_silences = tuple(
        LongSilenceEvidence(activity.interval)
        for activity in partition
        if activity.state is VoiceActivityState.NON_SPEECH
        and activity.interval.end - activity.interval.start > long_silence_threshold
    )
    return VadPartEvidence(
        source_id=source_id,
        stream_index=stream_index,
        voice_activity_intervals=partition,
        uncovered_speech_risks=uncovered_speech_risks,
        audio_state_indeterminate=audio_state_indeterminate,
        long_silences=long_silences,
    )


def derive_adopted_alignment_timing_view(
    *,
    source_id: str,
    language: str,
    source_cues: tuple[AlignmentCue, ...],
    proposals: tuple[AlignmentProposal, ...],
    usable_audio_intervals: tuple[HalfOpenInterval, ...],
    voice_activity_intervals: tuple[VoiceActivityInterval, ...],
    minimum_confidence: float,
    duration_rules: Mapping[str, tuple[ExactTime, ExactTime]],
) -> AdoptedAlignmentTimingView:
    """Adopt only calibrated candidate times for an unchanged Primary subtitle track."""

    if not source_id or not language or not 0 <= minimum_confidence <= 1:
        raise AudioAnalysisError(
            "alignment_input_invalid", "Alignment identity and confidence threshold are invalid."
        )
    _validate_alignment_text_contract(source_cues, proposals)
    duration_rule = duration_rules.get(language)
    if duration_rule is None:
        raise AudioAnalysisError(
            "alignment_duration_rule_missing",
            "Alignment calibration lacks a duration rule for the subtitle language.",
        )
    minimum_duration, maximum_duration = duration_rule
    if minimum_duration <= ExactTime(0) or maximum_duration < minimum_duration:
        raise AudioAnalysisError(
            "alignment_duration_rule_invalid", "Alignment duration rule is invalid."
        )
    _validate_alignment_audio_evidence(usable_audio_intervals, voice_activity_intervals)

    proposals_by_ordinal = {proposal.source_ordinal: proposal for proposal in proposals}
    candidates: list[AlignmentCandidateEvidence] = []
    mixed_cues: list[AlignmentCue] = []
    for cue in source_cues:
        proposal = proposals_by_ordinal[cue.source_ordinal]
        reason, indeterminate_risk = _alignment_candidate_reason(
            proposal,
            usable_audio_intervals,
            voice_activity_intervals,
            minimum_confidence,
            minimum_duration,
            maximum_duration,
        )
        adopted = reason == "adopted"
        interval = proposal.interval if adopted else cue.interval
        candidates.append(
            AlignmentCandidateEvidence(
                source_ordinal=cue.source_ordinal,
                original_interval=cue.interval,
                proposed_interval=proposal.interval,
                interval=interval,
                adopted=adopted,
                reason=reason,
                global_reason=None,
                vad_indeterminate_risk=indeterminate_risk,
            )
        )
        mixed_cues.append(AlignmentCue(cue.source_ordinal, cue.text, interval))

    if not _has_stable_alignment_order(mixed_cues):
        untrusted_candidates = tuple(
            AlignmentCandidateEvidence(
                source_ordinal=candidate.source_ordinal,
                original_interval=candidate.original_interval,
                proposed_interval=candidate.proposed_interval,
                interval=candidate.original_interval,
                adopted=False,
                reason=candidate.reason,
                global_reason="alignment_untrusted",
                vad_indeterminate_risk=candidate.vad_indeterminate_risk,
            )
            for candidate in candidates
        )
        return AdoptedAlignmentTimingView(
            state="alignment_untrusted",
            source_id=source_id,
            language=language,
            cues=source_cues,
            candidates=untrusted_candidates,
            diagnostic="source_order_invalid",
        )
    return AdoptedAlignmentTimingView(
        state="adopted",
        source_id=source_id,
        language=language,
        cues=tuple(mixed_cues),
        candidates=tuple(candidates),
    )


def _validate_alignment_text_contract(
    source_cues: tuple[AlignmentCue, ...], proposals: tuple[AlignmentProposal, ...]
) -> None:
    source_by_ordinal = {cue.source_ordinal: cue for cue in source_cues}
    proposal_by_ordinal = {proposal.source_ordinal: proposal for proposal in proposals}
    if (
        not source_cues
        or len(source_by_ordinal) != len(source_cues)
        or len(proposal_by_ordinal) != len(proposals)
        or set(source_by_ordinal) != set(proposal_by_ordinal)
        or any(
            source_by_ordinal[ordinal].text != proposal.text
            for ordinal, proposal in proposal_by_ordinal.items()
        )
    ):
        raise AudioAnalysisError(
            "alignment_text_contract_violation",
            "Alignment may only propose times for the existing Primary subtitle cue identities.",
        )


def _validate_alignment_audio_evidence(
    usable_audio_intervals: tuple[HalfOpenInterval, ...],
    voice_activity_intervals: tuple[VoiceActivityInterval, ...],
) -> None:
    if not usable_audio_intervals:
        raise AudioAnalysisError(
            "alignment_audio_coverage_missing", "Alignment requires usable audio coverage evidence."
        )
    previous_end: ExactTime | None = None
    for interval in usable_audio_intervals:
        if previous_end is not None and interval.start < previous_end:
            raise AudioAnalysisError(
                "alignment_audio_coverage_invalid", "Usable audio coverage intervals overlap."
            )
        previous_end = interval.end
    for activity in voice_activity_intervals:
        if not any(_contains(coverage, activity.interval) for coverage in usable_audio_intervals):
            raise AudioAnalysisError(
                "alignment_vad_invalid", "VAD evidence falls outside usable audio coverage."
            )


def _alignment_candidate_reason(
    proposal: AlignmentProposal,
    usable_audio_intervals: tuple[HalfOpenInterval, ...],
    voice_activity_intervals: tuple[VoiceActivityInterval, ...],
    minimum_confidence: float,
    minimum_duration: ExactTime,
    maximum_duration: ExactTime,
) -> tuple[str, bool]:
    if proposal.confidence < minimum_confidence or proposal.confidence > 1:
        return "alignment_low_confidence", False
    duration = proposal.interval.end - proposal.interval.start
    if duration < minimum_duration or duration > maximum_duration:
        return "alignment_duration_implausible", False
    if not any(_contains(coverage, proposal.interval) for coverage in usable_audio_intervals):
        return "alignment_out_of_coverage", False
    if any(
        activity.state is VoiceActivityState.NON_SPEECH
        and proposal.interval.overlaps(activity.interval)
        for activity in voice_activity_intervals
    ):
        return "alignment_vad_conflict", False
    indeterminate_risk = any(
        activity.state is VoiceActivityState.INDETERMINATE
        and proposal.interval.overlaps(activity.interval)
        for activity in voice_activity_intervals
    )
    return "adopted", indeterminate_risk


def _has_stable_alignment_order(cues: list[AlignmentCue]) -> bool:
    return all(
        earlier.source_ordinal < later.source_ordinal
        and earlier.interval.start <= later.interval.start
        for earlier, later in zip(cues, cues[1:], strict=False)
    )


def _usable_audio_intervals(audio_coverage: StreamCoverage) -> tuple[HalfOpenInterval, ...]:
    if audio_coverage.coverage is None or audio_coverage.diagnostics:
        raise AudioAnalysisError(
            "audio_coverage_indeterminate", "VAD requires determinate usable audio coverage."
        )
    coverage = audio_coverage.coverage
    gaps = tuple(sorted(audio_coverage.gaps, key=lambda interval: (interval.start, interval.end)))
    previous_end = coverage.start
    usable: list[HalfOpenInterval] = []
    for gap in gaps:
        if gap.start < coverage.start or coverage.end < gap.end or gap.start < previous_end:
            raise AudioAnalysisError("audio_coverage_invalid", "Audio coverage gaps are invalid.")
        if previous_end < gap.start:
            usable.append(HalfOpenInterval(previous_end, gap.start))
        previous_end = gap.end
    if previous_end < coverage.end:
        usable.append(HalfOpenInterval(previous_end, coverage.end))
    if not usable:
        raise AudioAnalysisError(
            "audio_coverage_indeterminate", "Audio coverage contains no usable decoded interval."
        )
    return tuple(usable)


def _validate_candidate_segments(
    segments: tuple[VoiceActivityCandidateSegment, ...],
    usable_intervals: tuple[HalfOpenInterval, ...],
) -> None:
    ordered = sorted(segments, key=lambda segment: (segment.interval.start, segment.interval.end))
    previous: HalfOpenInterval | None = None
    for segment in ordered:
        if not any(_contains(usable, segment.interval) for usable in usable_intervals):
            raise AudioAnalysisError(
                "model_output_invalid",
                "VAD segment must be contained within known usable audio coverage.",
            )
        if previous is not None and segment.interval.start < previous.end:
            raise AudioAnalysisError("model_output_invalid", "VAD segments must not overlap.")
        previous = segment.interval


def _partition_usable_audio(
    usable_intervals: tuple[HalfOpenInterval, ...],
    segments: tuple[VoiceActivityCandidateSegment, ...],
) -> tuple[VoiceActivityInterval, ...]:
    partition: list[VoiceActivityInterval] = []
    ordered_segments = tuple(sorted(segments, key=lambda segment: segment.interval.start))
    for usable in usable_intervals:
        cursor = usable.start
        for segment in ordered_segments:
            if segment.interval.end <= usable.start or usable.end <= segment.interval.start:
                continue
            if cursor < segment.interval.start:
                _append_activity(
                    partition,
                    VoiceActivityInterval(
                        HalfOpenInterval(cursor, segment.interval.start),
                        VoiceActivityState.INDETERMINATE,
                    ),
                )
            _append_activity(partition, VoiceActivityInterval(segment.interval, segment.state))
            cursor = segment.interval.end
        if cursor < usable.end:
            _append_activity(
                partition,
                VoiceActivityInterval(
                    HalfOpenInterval(cursor, usable.end), VoiceActivityState.INDETERMINATE
                ),
            )
    return tuple(partition)


def _append_activity(
    intervals: list[VoiceActivityInterval], activity: VoiceActivityInterval
) -> None:
    if (
        intervals
        and intervals[-1].state is activity.state
        and intervals[-1].interval.end == activity.interval.start
    ):
        intervals[-1] = VoiceActivityInterval(
            HalfOpenInterval(intervals[-1].interval.start, activity.interval.end), activity.state
        )
        return
    intervals.append(activity)


def _contains(container: HalfOpenInterval, value: HalfOpenInterval) -> bool:
    return container.start <= value.start and value.end <= container.end


def _union_intervals(intervals: tuple[HalfOpenInterval, ...]) -> tuple[HalfOpenInterval, ...]:
    if not intervals:
        return ()
    merged: list[HalfOpenInterval] = []
    for interval in sorted(intervals, key=lambda value: (value.start, value.end)):
        if not merged or merged[-1].end < interval.start:
            merged.append(interval)
            continue
        merged[-1] = HalfOpenInterval(merged[-1].start, max(merged[-1].end, interval.end))
    return tuple(merged)


def _subtract_intervals(
    interval: HalfOpenInterval, exclusions: tuple[HalfOpenInterval, ...]
) -> tuple[HalfOpenInterval, ...]:
    remaining: list[HalfOpenInterval] = []
    cursor = interval.start
    for exclusion in exclusions:
        if exclusion.end <= cursor or interval.end <= exclusion.start:
            continue
        if cursor < exclusion.start:
            remaining.append(HalfOpenInterval(cursor, min(exclusion.start, interval.end)))
        if cursor < exclusion.end:
            cursor = max(cursor, exclusion.end)
        if interval.end <= cursor:
            break
    if cursor < interval.end:
        remaining.append(HalfOpenInterval(cursor, interval.end))
    return tuple(remaining)


def _qualified_vad_candidate(
    capabilities: tuple[CapabilityAvailability, ...],
) -> dict[str, object] | None:
    vad_capability = next(
        (capability for capability in capabilities if capability.capability == "vad"), None
    )
    if vad_capability is None:
        return None
    qualified: list[dict[str, object]] = []
    for candidate in vad_capability.candidates:
        if _candidate_is_qualified(candidate):
            qualified.append(candidate)
    if len(qualified) > 1:
        raise AudioAnalysisError(
            "vad_model_selection_required",
            "Multiple calibrated VAD candidates require an explicit future selection.",
        )
    return qualified[0] if qualified else None


def _qualified_alignment_candidate(
    capabilities: tuple[CapabilityAvailability, ...],
) -> dict[str, object] | None:
    alignment_capability = next(
        (capability for capability in capabilities if capability.capability == "forced_alignment"),
        None,
    )
    if alignment_capability is None:
        return None
    qualified: list[dict[str, object]] = []
    for candidate in alignment_capability.candidates:
        if _candidate_is_qualified(candidate):
            qualified.append(candidate)
    if len(qualified) > 1:
        raise AudioAnalysisError(
            "alignment_model_selection_required",
            "Multiple calibrated alignment candidates require an explicit future selection.",
        )
    return qualified[0] if qualified else None


def _qualified_diarization_candidate(
    capabilities: tuple[CapabilityAvailability, ...],
    requested_candidate_id: str | None,
) -> dict[str, object] | None:
    diarization_capability = next(
        (capability for capability in capabilities if capability.capability == "diarization"),
        None,
    )
    if diarization_capability is None:
        return None
    qualified = [
        candidate
        for candidate in diarization_capability.candidates
        if _candidate_is_qualified(candidate)
    ]
    if not qualified:
        return None
    if requested_candidate_id is None:
        raise AudioAnalysisError(
            "diarization_model_selection_required",
            "A calibrated diarization candidate requires explicit user selection.",
        )
    selected = [
        candidate
        for candidate in qualified
        if candidate.get("candidate_id") == requested_candidate_id
    ]
    if len(selected) != 1:
        raise AudioAnalysisError(
            "diarization_model_selection_invalid",
            "The selected diarization candidate is not calibrated and eligible.",
        )
    return selected[0]


def _candidate_is_qualified(candidate: Mapping[str, object]) -> bool:
    calibration = candidate.get("calibration")
    adapter = candidate.get("adapter")
    return (
        candidate.get("state") == "eligible"
        and isinstance(calibration, Mapping)
        and calibration.get("state") == "qualified"
        and isinstance(adapter, Mapping)
        and adapter.get("state") == "projected"
    )


def _select_audio_streams(
    inspection_evidence: tuple[PlanInspectionEvidence, ...], requested: tuple[str, ...]
) -> tuple[AnalysisAudioStreamSelection, ...]:
    requested_by_source = _parse_audio_stream_requests(requested)
    selections: list[AnalysisAudioStreamSelection] = []
    known_source_ids: set[str] = set()
    for evidence in inspection_evidence:
        if evidence.source_id in known_source_ids:
            raise AudioAnalysisError(
                "audio_stream_selection_invalid", "Inspection evidence repeats a Part identity."
            )
        known_source_ids.add(evidence.source_id)
        available = _available_audio_streams(evidence)
        requested_stream = requested_by_source.pop(evidence.source_id, None)
        if requested_stream is None:
            if len(available) != 1:
                raise AudioAnalysisError(
                    "audio_stream_selection_required",
                    f"Part {evidence.source_id} needs one explicit audio stream selection.",
                )
            requested_stream = available[0]
        if requested_stream not in available:
            raise AudioAnalysisError(
                "audio_stream_selection_invalid",
                f"Part {evidence.source_id} has no selected usable audio stream.",
            )
        selections.append(_audio_stream_selection(evidence, requested_stream))
    if requested_by_source:
        raise AudioAnalysisError(
            "audio_stream_selection_invalid", "Audio stream selection references an unknown Part."
        )
    if not selections:
        raise AudioAnalysisError(
            "audio_stream_selection_required",
            "No retained Part has selected usable audio evidence.",
        )
    return tuple(selections)


def _parse_audio_stream_requests(values: tuple[str, ...]) -> dict[str, int]:
    selections: dict[str, int] = {}
    for value in values:
        source_id, separator, stream_text = value.rpartition("=")
        if not separator or not source_id:
            raise AudioAnalysisError(
                "audio_stream_selection_invalid",
                "Audio stream selections must use PART_ID=STREAM_INDEX.",
            )
        try:
            stream_index = int(stream_text)
        except ValueError as error:
            raise AudioAnalysisError(
                "audio_stream_selection_invalid",
                "Audio stream indexes must be non-negative integers.",
            ) from error
        if stream_index < 0 or source_id in selections:
            raise AudioAnalysisError(
                "audio_stream_selection_invalid",
                "Audio stream selections must be unique and non-negative.",
            )
        selections[source_id] = stream_index
    return selections


def _parse_role_metadata_requests(values: tuple[str, ...]) -> tuple[UserRoleMetadata, ...]:
    metadata: list[UserRoleMetadata] = []
    seen: set[tuple[str, str]] = set()
    for value in values:
        source_id, first_separator, remainder = value.partition("=")
        cluster_id, second_separator, role = remainder.partition("=")
        key = (source_id, cluster_id)
        if (
            not first_separator
            or not second_separator
            or not source_id
            or not cluster_id
            or not role
            or key in seen
        ):
            raise AudioAnalysisError(
                "role_metadata_invalid",
                "Role metadata must use unique PART_ID=CLUSTER_ID=ROLE assignments.",
            )
        seen.add(key)
        metadata.append(UserRoleMetadata(source_id, cluster_id, role))
    return tuple(metadata)


def _available_audio_streams(evidence: PlanInspectionEvidence) -> tuple[int, ...]:
    document = evidence.structural_document
    if document is None:
        raise AudioAnalysisError(
            "audio_stream_selection_required", "Part lacks retained structural audio evidence."
        )
    try:
        raw_document = json.loads(document.raw_json)
    except json.JSONDecodeError as error:
        raise AudioAnalysisError(
            "audio_stream_selection_required", "Part structural evidence is not valid JSON."
        ) from error
    streams = raw_document.get("streams") if isinstance(raw_document, Mapping) else None
    if not isinstance(streams, list):
        raise AudioAnalysisError(
            "audio_stream_selection_required", "Part structural evidence has no stream list."
        )
    coverage_by_stream = dict(evidence.coverage_by_stream)
    available: list[int] = []
    for stream in streams:
        if not isinstance(stream, Mapping) or stream.get("codec_type") != "audio":
            continue
        stream_index = stream.get("index")
        if not isinstance(stream_index, int) or isinstance(stream_index, bool):
            continue
        coverage = coverage_by_stream.get(stream_index)
        if coverage is not None and coverage.coverage is not None and not coverage.diagnostics:
            available.append(stream_index)
    return tuple(sorted(available))


def _audio_stream_selection(
    evidence: PlanInspectionEvidence, stream_index: int
) -> AnalysisAudioStreamSelection:
    document = evidence.structural_document
    if document is None:
        raise AudioAnalysisError(
            "audio_stream_selection_invalid", "Structural evidence is missing."
        )
    try:
        decoded = json.loads(document.raw_json)
    except json.JSONDecodeError as error:
        raise AudioAnalysisError(
            "audio_stream_selection_invalid", "Structural evidence is invalid."
        ) from error
    streams = decoded.get("streams") if isinstance(decoded, Mapping) else None
    if not isinstance(streams, list):
        raise AudioAnalysisError(
            "audio_stream_selection_invalid", "Structural stream evidence is missing."
        )
    stream = next(
        (
            item
            for item in streams
            if isinstance(item, Mapping)
            and item.get("index") == stream_index
            and item.get("codec_type") == "audio"
        ),
        None,
    )
    coverage = dict(evidence.coverage_by_stream).get(stream_index)
    if not isinstance(stream, Mapping) or coverage is None:
        raise AudioAnalysisError(
            "audio_stream_selection_invalid", "Audio stream evidence is missing."
        )
    tags = stream.get("tags")
    disposition = stream.get("disposition")
    return AnalysisAudioStreamSelection(
        source_id=evidence.source_id,
        stream_index=stream_index,
        codec=str(stream.get("codec_name", stream.get("codec_type"))),
        language=(
            tags.get("language")
            if isinstance(tags, Mapping) and isinstance(tags.get("language"), str)
            else None
        ),
        disposition=dict(disposition) if isinstance(disposition, Mapping) else {},
        structural_evidence_sha256=_sha256_json(document.raw_json),
        coverage_evidence_sha256=_sha256_json(_stream_coverage_as_json(coverage)),
    )


def _derive_vad_evidence(
    candidate: Mapping[str, object],
    selections: tuple[AnalysisAudioStreamSelection, ...],
    inspection_evidence: tuple[PlanInspectionEvidence, ...],
    subtitle_report: SubtitleCandidateReport,
    plan: RunPlan,
    project_root: Path,
) -> dict[str, object]:
    candidate_id = candidate.get("candidate_id")
    if not isinstance(candidate_id, str):
        raise AudioAnalysisError("model_output_invalid", "VAD candidate identity is missing.")
    calibration = candidate.get("calibration")
    if not isinstance(calibration, Mapping):
        raise AudioAnalysisError("calibration_failed", "VAD calibration evidence is missing.")
    profile = calibration.get("profile")
    if not isinstance(profile, Mapping):
        raise AudioAnalysisError("calibration_failed", "VAD calibration profile is missing.")
    profile_path = _retained_evidence_path(profile, project_root)
    try:
        profile_document = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AudioAnalysisError(
            "calibration_failed", "VAD calibration profile cannot be read."
        ) from error
    synthetic_only = (
        isinstance(profile_document, Mapping)
        and profile_document.get("qualification_scope") == "synthetic_verification_only"
    )
    if synthetic_only and any(
        artifact.origin_kind != "synthetic_fixture" for artifact in plan.source_artifacts
    ):
        raise AudioAnalysisError(
            "calibration_scope_insufficient",
            "Synthetic-only VAD calibration cannot publish evidence for a non-synthetic source.",
        )
    thresholds = _vad_thresholds(candidate, project_root)
    candidate_segments = _projected_vad_segments(candidate, selections, project_root)
    coverage_by_source = {
        evidence.source_id: dict(evidence.coverage_by_stream) for evidence in inspection_evidence
    }
    parts: list[dict[str, object]] = []
    for selection in selections:
        coverage = coverage_by_source.get(selection.source_id, {}).get(selection.stream_index)
        if coverage is None:
            raise AudioAnalysisError(
                "audio_coverage_indeterminate",
                "Selected audio stream lacks retained coverage evidence.",
            )
        evidence = derive_vad_part_evidence(
            source_id=selection.source_id,
            stream_index=selection.stream_index,
            audio_coverage=coverage,
            candidate_segments=candidate_segments[(selection.source_id, selection.stream_index)],
            caption_intervals=_primary_caption_intervals(subtitle_report, selection.source_id),
            uncovered_speech_threshold=thresholds["uncovered_speech_duration"],
            long_silence_threshold=thresholds["long_silence_duration"],
        )
        parts.append(_vad_part_evidence_as_json(evidence))
    return {
        "capability": "vad",
        "candidate_id": candidate_id,
        "calibration_profile": calibration.get("profile"),
        "parts": parts,
    }


def _derive_alignment_evidence(
    candidate: Mapping[str, object],
    selections: tuple[AnalysisAudioStreamSelection, ...],
    inspection_evidence: tuple[PlanInspectionEvidence, ...],
    subtitle_report: SubtitleCandidateReport,
    plan: RunPlan,
    vad_evidence: Mapping[str, object],
    project_root: Path,
    workspace_path: Path,
) -> dict[str, object]:
    candidate_id = candidate.get("candidate_id")
    calibration = candidate.get("calibration")
    if not isinstance(candidate_id, str) or not isinstance(calibration, Mapping):
        raise AudioAnalysisError("model_output_invalid", "Alignment candidate evidence is missing.")
    _require_synthetic_calibration_scope(calibration, plan, project_root, "alignment")
    minimum_confidence, duration_rules = _alignment_thresholds(candidate, project_root)
    projections_by_source = _projected_alignment_proposals(candidate, selections, project_root)
    expected_sources = _primary_alignment_source_ids(subtitle_report, selections)
    if set(projections_by_source) != expected_sources:
        raise AudioAnalysisError(
            "model_output_invalid",
            "Alignment projection must provide exactly every selected Part with a Primary "
            "subtitle track.",
        )
    vad_by_source = _vad_intervals_by_source(vad_evidence)
    coverage_by_source = {
        evidence.source_id: dict(evidence.coverage_by_stream) for evidence in inspection_evidence
    }
    selection_by_source = {selection.source_id: selection for selection in selections}
    parts: list[dict[str, object]] = []
    for source_id, projected_part in projections_by_source.items():
        selection = selection_by_source.get(source_id)
        if selection is None:
            raise AudioAnalysisError(
                "model_output_invalid", "Alignment projection Part is not selected."
            )
        audio_coverage = coverage_by_source.get(source_id, {}).get(selection.stream_index)
        if audio_coverage is None:
            raise AudioAnalysisError(
                "audio_coverage_indeterminate",
                "Selected audio stream lacks retained coverage evidence.",
            )
        source_cues, source_evidence = _primary_alignment_cues(
            subtitle_report, source_id, project_root
        )
        view_path = (
            workspace_path
            / "alignment"
            / candidate_id
            / source_id
            / "adopted-alignment-timing-view.json"
        )
        view = derive_adopted_alignment_timing_view(
            source_id=source_id,
            language=projected_part.language,
            source_cues=source_cues,
            proposals=projected_part.proposals,
            usable_audio_intervals=_usable_audio_intervals(audio_coverage),
            voice_activity_intervals=vad_by_source[source_id],
            minimum_confidence=minimum_confidence,
            duration_rules=duration_rules,
        )
        view_document = _alignment_view_as_json(view, source_evidence)
        if view.state == "alignment_untrusted":
            fingerprint = _alignment_failure_fingerprint(
                plan, source_id, source_evidence, candidate_id, calibration, view
            )
            view_document["failure_fingerprint"] = fingerprint
            prior_reports = _matching_alignment_failure_reports(project_root, fingerprint)
            if prior_reports:
                _write_json_once(view_path, view_document)
                diagnosis_path = view_path.with_name("alignment-diagnosis.json")
                _write_json_once(
                    diagnosis_path,
                    {
                        "schema_version": 1,
                        "state": "alignment_diagnosis_required",
                        "failure_fingerprint": fingerprint,
                        "evidence_reports": [path.as_posix() for path in prior_reports],
                        "current_timing_view": _input_evidence(view_path).as_json(),
                        "diagnosis": "root_cause_inconclusive",
                    },
                )
                parts.append(
                    {
                        "source_id": source_id,
                        "audio_stream_index": selection.stream_index,
                        "timing_view": _input_evidence(view_path).as_json(),
                        "state": "alignment_diagnosis_required",
                        "diagnostic": _input_evidence(diagnosis_path).as_json(),
                    }
                )
                continue
        _write_json_once(view_path, view_document)
        parts.append(
            {
                "source_id": source_id,
                "audio_stream_index": selection.stream_index,
                "timing_view": _input_evidence(view_path).as_json(),
                "state": view.state,
                "diagnostic": view.diagnostic,
            }
        )
    return {
        "capability": "forced_alignment",
        "candidate_id": candidate_id,
        "calibration_profile": calibration.get("profile"),
        "parts": parts,
    }


def _derive_diarization_evidence(
    candidate: Mapping[str, object],
    selections: tuple[AnalysisAudioStreamSelection, ...],
    inspection_evidence: tuple[PlanInspectionEvidence, ...],
    subtitle_report: SubtitleCandidateReport,
    plan: RunPlan,
    vad_evidence: Mapping[str, object],
    project_root: Path,
    workspace_path: Path,
    user_role_metadata: tuple[UserRoleMetadata, ...],
) -> dict[str, object]:
    candidate_id = candidate.get("candidate_id")
    calibration = candidate.get("calibration")
    if not isinstance(candidate_id, str) or not isinstance(calibration, Mapping):
        raise AudioAnalysisError(
            "model_output_invalid", "Diarization candidate evidence is missing."
        )
    _require_synthetic_calibration_scope(calibration, plan, project_root, "diarization")
    minimum_confidence = _diarization_minimum_confidence(candidate, project_root)
    projections_by_source = _projected_diarization_parts(candidate, selections, project_root)
    vad_by_source = _vad_intervals_by_source(vad_evidence)
    coverage_by_source = {
        evidence.source_id: dict(evidence.coverage_by_stream) for evidence in inspection_evidence
    }
    selection_by_source = {selection.source_id: selection for selection in selections}
    selected_source_ids = set(selection_by_source)
    if any(item.source_id not in selected_source_ids for item in user_role_metadata):
        raise AudioAnalysisError(
            "role_metadata_invalid", "Role metadata references a Part without selected audio."
        )
    metadata_evidence = _retain_user_role_metadata(
        plan, candidate_id, user_role_metadata, workspace_path
    )
    metadata_by_source: dict[str, tuple[UserRoleMetadata, ...]] = {
        source_id: tuple(item for item in user_role_metadata if item.source_id == source_id)
        for source_id in selected_source_ids
    }
    part_labels = {
        selection.source_id: f"part-{part_number:02d}"
        for part_number, selection in enumerate(selections, start=1)
    }
    parts: list[dict[str, object]] = []
    for source_id, projected_part in projections_by_source.items():
        selection = selection_by_source.get(source_id)
        if selection is None:
            raise AudioAnalysisError(
                "audio_coverage_indeterminate",
                "Selected audio stream lacks retained coverage evidence.",
            )
        audio_coverage = coverage_by_source.get(source_id, {}).get(selection.stream_index)
        if audio_coverage is None:
            raise AudioAnalysisError(
                "audio_coverage_indeterminate",
                "Selected audio stream lacks retained coverage evidence.",
            )
        source_cues = _speaker_role_cues(subtitle_report, source_id, project_root)
        parts.append(
            _speaker_turn_part_evidence_as_json(
                source_id,
                part_labels[source_id],
                selection.stream_index,
                projected_part,
                _usable_audio_intervals(audio_coverage),
                vad_by_source[source_id],
                minimum_confidence,
                source_cues,
                metadata_by_source[source_id],
                metadata_evidence,
            )
        )
    return {
        "capability": "diarization",
        "candidate_id": candidate_id,
        "calibration_profile": calibration.get("profile"),
        "parts": parts,
    }


def _retain_user_role_metadata(
    plan: RunPlan,
    candidate_id: str,
    metadata: tuple[UserRoleMetadata, ...],
    workspace_path: Path,
) -> InputEvidence | None:
    if not metadata:
        return None
    path = workspace_path / "diarization" / candidate_id / "user-role-metadata.json"
    _write_json_once(
        path,
        {
            "schema_version": 1,
            "plan_id": plan.plan_id,
            "candidate_id": candidate_id,
            "records": [
                {
                    "entry_id": _sha256_json(
                        {
                            "source_id": item.source_id,
                            "cluster_id": item.cluster_id,
                            "role": item.role,
                        }
                    ),
                    "source_id": item.source_id,
                    "cluster_id": item.cluster_id,
                    "role": item.role,
                }
                for item in metadata
            ],
        },
    )
    return _input_evidence(path)


def _diarization_minimum_confidence(candidate: Mapping[str, object], project_root: Path) -> float:
    calibration = candidate.get("calibration")
    if not isinstance(calibration, Mapping) or not isinstance(calibration.get("record"), Mapping):
        raise AudioAnalysisError(
            "diarization_calibration_required", "Diarization calibration record is missing."
        )
    record_path = _retained_evidence_path(calibration["record"], project_root)
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        thresholds = record.get("thresholds") if isinstance(record, Mapping) else None
        confidence = (
            thresholds.get("minimum_confidence") if isinstance(thresholds, Mapping) else None
        )
        if (
            not isinstance(confidence, float | int)
            or isinstance(confidence, bool)
            or not 0 <= confidence <= 1
        ):
            raise ValueError
        return float(confidence)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise AudioAnalysisError(
            "diarization_calibration_required", "Diarization calibration thresholds are invalid."
        ) from error


def _projected_diarization_parts(
    candidate: Mapping[str, object],
    selections: tuple[AnalysisAudioStreamSelection, ...],
    project_root: Path,
) -> dict[str, ProjectedDiarizationPart]:
    adapter = candidate.get("adapter")
    if not isinstance(adapter, Mapping) or not isinstance(adapter.get("projection"), Mapping):
        raise AudioAnalysisError(
            "model_output_invalid", "Diarization projection evidence is missing."
        )
    projection_path = _retained_evidence_path(adapter["projection"], project_root)
    try:
        document = json.loads(projection_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AudioAnalysisError(
            "model_output_invalid", "Diarization projection cannot be read."
        ) from error
    result = document.get("result") if isinstance(document, Mapping) else None
    parts = result.get("parts") if isinstance(result, Mapping) else None
    if not isinstance(parts, list):
        raise AudioAnalysisError(
            "model_output_invalid", "Diarization projection must provide every selected Part."
        )
    selections_by_key = {
        (selection.source_id, selection.stream_index): selection for selection in selections
    }
    projected: dict[str, ProjectedDiarizationPart] = {}
    for part in parts:
        if not isinstance(part, Mapping):
            raise AudioAnalysisError(
                "model_output_invalid", "Diarization projected Part is invalid."
            )
        source_id = part.get("source_id")
        stream_index = part.get("stream_index")
        if (
            not isinstance(source_id, str)
            or not isinstance(stream_index, int)
            or isinstance(stream_index, bool)
            or (source_id, stream_index) not in selections_by_key
            or source_id in projected
        ):
            raise AudioAnalysisError(
                "model_output_invalid", "Diarization projection Part identity is invalid."
            )
        _validate_diarization_time_mapping(
            part.get("source_time_mapping"),
            selections_by_key[(source_id, stream_index)],
            project_root,
        )
        turns = part.get("turns")
        role_candidates = part.get("role_candidates", [])
        if not isinstance(turns, list) or not isinstance(role_candidates, list):
            raise AudioAnalysisError(
                "model_output_invalid", "Diarization projected Part is incomplete."
            )
        projected[source_id] = ProjectedDiarizationPart(
            tuple(_speaker_turn_candidate(value) for value in turns),
            tuple(_role_candidate_proposal(value) for value in role_candidates),
        )
    if set(projected) != {selection.source_id for selection in selections}:
        raise AudioAnalysisError(
            "model_output_invalid", "Diarization projection omitted a selected Part."
        )
    return projected


def _validate_diarization_time_mapping(
    value: object, selection: AnalysisAudioStreamSelection, project_root: Path
) -> None:
    if not isinstance(value, Mapping):
        raise AudioAnalysisError(
            "model_output_invalid", "Diarization source-time mapping is missing."
        )
    derivative = value.get("derivative_evidence")
    if (
        value.get("schema_version") != 1
        or value.get("coordinate") != "raw_pts_identity"
        or value.get("structural_evidence_sha256") != selection.structural_evidence_sha256
        or value.get("coverage_evidence_sha256") != selection.coverage_evidence_sha256
        or not isinstance(derivative, Mapping)
    ):
        raise AudioAnalysisError(
            "model_output_invalid", "Diarization source-time mapping is invalid."
        )
    _retained_evidence_path(derivative, project_root)


def _speaker_turn_candidate(value: object) -> SpeakerTurnCandidate:
    if not isinstance(value, Mapping):
        raise AudioAnalysisError("model_output_invalid", "Diarization turn must be an object.")
    cluster_id = value.get("cluster_id")
    confidence = value.get("confidence")
    try:
        interval = HalfOpenInterval(
            _exact_time_from_json(value.get("start")), _exact_time_from_json(value.get("end"))
        )
    except (TypeError, ValueError) as error:
        raise AudioAnalysisError(
            "model_output_invalid", "Diarization turn interval is invalid."
        ) from error
    if (
        not isinstance(cluster_id, str)
        or not cluster_id
        or not isinstance(confidence, float | int)
        or isinstance(confidence, bool)
        or not 0 <= confidence <= 1
    ):
        raise AudioAnalysisError("model_output_invalid", "Diarization turn is invalid.")
    return SpeakerTurnCandidate(cluster_id, interval, float(confidence))


def _role_candidate_proposal(value: object) -> RoleCandidateProposal:
    if not isinstance(value, Mapping):
        raise AudioAnalysisError("model_output_invalid", "Role candidate must be an object.")
    citation = value.get("subtitle_text")
    cluster_id = value.get("cluster_id")
    role = value.get("role")
    source_ordinal = citation.get("source_ordinal") if isinstance(citation, Mapping) else None
    text = citation.get("text") if isinstance(citation, Mapping) else None
    if (
        not isinstance(cluster_id, str)
        or not cluster_id
        or not isinstance(role, str)
        or not role
        or not isinstance(source_ordinal, int)
        or isinstance(source_ordinal, bool)
        or not isinstance(text, str)
        or not text
    ):
        raise AudioAnalysisError(
            "model_output_invalid", "Role candidates require an exact subtitle-text citation."
        )
    return RoleCandidateProposal(cluster_id, role, source_ordinal, text)


def _speaker_role_cues(
    subtitle_report: SubtitleCandidateReport, source_id: str, project_root: Path
) -> dict[int, str]:
    if _primary_subtitle_candidate(subtitle_report, source_id) is None:
        return {}
    cues, _ = _primary_alignment_cues(subtitle_report, source_id, project_root)
    return {cue.source_ordinal: cue.text for cue in cues}


def _speaker_turn_part_evidence_as_json(
    source_id: str,
    part_label: str,
    stream_index: int,
    projected_part: ProjectedDiarizationPart,
    usable_audio_intervals: tuple[HalfOpenInterval, ...],
    voice_activity_intervals: tuple[VoiceActivityInterval, ...],
    minimum_confidence: float,
    source_cues: Mapping[int, str],
    user_role_metadata: tuple[UserRoleMetadata, ...],
    metadata_evidence: InputEvidence | None,
) -> dict[str, object]:
    published: list[SpeakerTurnCandidate] = []
    conflicts: list[dict[str, object]] = []
    labels_by_cluster = {
        cluster_id: f"{part_label}:speaker-{ordinal:02d}"
        for ordinal, cluster_id in enumerate(
            sorted({turn.cluster_id for turn in projected_part.turns}), start=1
        )
    }
    for turn in projected_part.turns:
        if not any(
            usable.start <= turn.interval.start and turn.interval.end <= usable.end
            for usable in usable_audio_intervals
        ):
            raise AudioAnalysisError(
                "model_output_invalid", "Diarization turn falls outside usable audio coverage."
            )
        conflict_states = sorted(
            {
                activity.state.value
                for activity in voice_activity_intervals
                if activity.state is not VoiceActivityState.SPEECH_LIKELY
                and _intervals_overlap(turn.interval, activity.interval)
            }
        )
        if conflict_states:
            conflicts.append(
                {
                    "candidate_speaker_label": labels_by_cluster[turn.cluster_id],
                    "raw_pts_interval": _interval_as_json(turn.interval),
                    "confidence": turn.confidence,
                    "reason": "diarization_vad_conflict",
                    "vad_states": conflict_states,
                }
            )
        elif turn.confidence >= minimum_confidence:
            published.append(turn)
    role_candidates: list[dict[str, object]] = []
    for proposal in projected_part.role_candidates:
        speaker_label = labels_by_cluster.get(proposal.cluster_id)
        if speaker_label is None:
            continue
        if source_cues.get(proposal.source_ordinal) != proposal.text:
            raise AudioAnalysisError(
                "model_output_invalid", "Role candidate subtitle citation is not retained evidence."
            )
        role_candidates.append(
            {
                "speaker_label": speaker_label,
                "role": proposal.role,
                "evidence": {
                    "kind": "subtitle_text",
                    "source_ordinal": proposal.source_ordinal,
                    "text": proposal.text,
                },
            }
        )
    if user_role_metadata and metadata_evidence is None:
        raise AudioAnalysisError("role_metadata_invalid", "User metadata evidence is unavailable.")
    for metadata in user_role_metadata:
        speaker_label = labels_by_cluster.get(metadata.cluster_id)
        if speaker_label is None:
            raise AudioAnalysisError(
                "role_metadata_invalid", "User metadata references an unknown diarization group."
            )
        role_candidates.append(
            {
                "speaker_label": speaker_label,
                "role": metadata.role,
                "evidence": {
                    "kind": "user_metadata",
                    "record": metadata_evidence.as_json()
                    if metadata_evidence is not None
                    else None,
                    "entry_id": _sha256_json(
                        {
                            "source_id": source_id,
                            "cluster_id": metadata.cluster_id,
                            "role": metadata.role,
                        }
                    ),
                },
            }
        )
    return {
        "source_id": source_id,
        "audio_stream_index": stream_index,
        "speaker_turns": [
            {
                "speaker_label": labels_by_cluster[turn.cluster_id],
                "raw_pts_interval": _interval_as_json(turn.interval),
                "confidence": turn.confidence,
            }
            for turn in published
        ],
        "diarization_vad_conflicts": conflicts,
        "role_candidates": role_candidates,
    }


def _intervals_overlap(left: HalfOpenInterval, right: HalfOpenInterval) -> bool:
    return left.start < right.end and right.start < left.end


def _require_synthetic_calibration_scope(
    calibration: Mapping[str, object], plan: RunPlan, project_root: Path, capability: str
) -> None:
    profile = calibration.get("profile")
    if not isinstance(profile, Mapping):
        raise AudioAnalysisError(
            "calibration_failed", f"{capability} calibration profile is missing."
        )
    profile_path = _retained_evidence_path(profile, project_root)
    try:
        document = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AudioAnalysisError(
            "calibration_failed", f"{capability} calibration profile cannot be read."
        ) from error
    if (
        isinstance(document, Mapping)
        and document.get("qualification_scope") == "synthetic_verification_only"
        and any(artifact.origin_kind != "synthetic_fixture" for artifact in plan.source_artifacts)
    ):
        raise AudioAnalysisError(
            "calibration_scope_insufficient",
            f"Synthetic-only {capability} calibration cannot publish evidence for a "
            "non-synthetic source.",
        )


def _alignment_thresholds(
    candidate: Mapping[str, object], project_root: Path
) -> tuple[float, dict[str, tuple[ExactTime, ExactTime]]]:
    calibration = candidate.get("calibration")
    if not isinstance(calibration, Mapping) or not isinstance(calibration.get("record"), Mapping):
        raise AudioAnalysisError(
            "alignment_calibration_required", "Alignment calibration record is missing."
        )
    record_path = _retained_evidence_path(calibration["record"], project_root)
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        thresholds = record.get("thresholds") if isinstance(record, Mapping) else None
        confidence = (
            thresholds.get("minimum_confidence") if isinstance(thresholds, Mapping) else None
        )
        rules = thresholds.get("duration_rules") if isinstance(thresholds, Mapping) else None
        if (
            not isinstance(confidence, float | int)
            or isinstance(confidence, bool)
            or not 0 <= confidence <= 1
            or not isinstance(rules, Mapping)
        ):
            raise ValueError
        parsed_rules = {
            language: (
                _exact_time_from_json(rule.get("minimum_duration")),
                _exact_time_from_json(rule.get("maximum_duration")),
            )
            for language, rule in rules.items()
            if isinstance(language, str) and language and isinstance(rule, Mapping)
        }
        if len(parsed_rules) != len(rules):
            raise ValueError
        return float(confidence), parsed_rules
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise AudioAnalysisError(
            "alignment_calibration_required", "Alignment calibration thresholds are invalid."
        ) from error


def _projected_alignment_proposals(
    candidate: Mapping[str, object],
    selections: tuple[AnalysisAudioStreamSelection, ...],
    project_root: Path,
) -> dict[str, ProjectedAlignmentPart]:
    adapter = candidate.get("adapter")
    if not isinstance(adapter, Mapping) or not isinstance(adapter.get("projection"), Mapping):
        raise AudioAnalysisError(
            "model_output_invalid", "Alignment projection evidence is missing."
        )
    projection_path = _retained_evidence_path(adapter["projection"], project_root)
    try:
        projection = json.loads(projection_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AudioAnalysisError(
            "model_output_invalid", "Alignment projection cannot be read."
        ) from error
    result = projection.get("result") if isinstance(projection, Mapping) else None
    parts = result.get("parts") if isinstance(result, Mapping) else None
    if not isinstance(parts, list):
        raise AudioAnalysisError("model_output_invalid", "Alignment projection must provide Parts.")
    selections_by_key = {
        (selection.source_id, selection.stream_index): selection for selection in selections
    }
    projected: dict[str, ProjectedAlignmentPart] = {}
    for part in parts:
        if not isinstance(part, Mapping):
            raise AudioAnalysisError(
                "model_output_invalid", "Alignment projection Part is invalid."
            )
        source_id = part.get("source_id")
        stream_index = part.get("stream_index")
        language = part.get("language")
        cue_values = part.get("cues")
        if (
            not isinstance(source_id, str)
            or not isinstance(stream_index, int)
            or isinstance(stream_index, bool)
            or not isinstance(language, str)
            or not language
            or not isinstance(cue_values, list)
            or (source_id, stream_index) not in selections_by_key
            or source_id in projected
        ):
            raise AudioAnalysisError(
                "model_output_invalid", "Alignment projection Part identity is invalid."
            )
        _validate_vad_time_mapping(
            part.get("source_time_mapping"),
            selections_by_key[(source_id, stream_index)],
            project_root,
        )
        projected[source_id] = ProjectedAlignmentPart(
            language, tuple(_alignment_proposal(value) for value in cue_values)
        )
    return projected


def _alignment_proposal(value: object) -> AlignmentProposal:
    if not isinstance(value, Mapping):
        raise AudioAnalysisError(
            "model_output_invalid", "Alignment cue proposal must be an object."
        )
    source_ordinal = value.get("source_ordinal")
    text = value.get("text")
    confidence = value.get("confidence")
    if (
        not isinstance(source_ordinal, int)
        or isinstance(source_ordinal, bool)
        or source_ordinal < 0
        or not isinstance(text, str)
        or not isinstance(confidence, float | int)
        or isinstance(confidence, bool)
    ):
        raise AudioAnalysisError(
            "model_output_invalid", "Alignment cue proposal identity is invalid."
        )
    try:
        return AlignmentProposal(
            source_ordinal,
            text,
            HalfOpenInterval(
                _exact_time_from_json(value.get("start")), _exact_time_from_json(value.get("end"))
            ),
            float(confidence),
        )
    except (TypeError, ValueError) as error:
        raise AudioAnalysisError(
            "model_output_invalid", "Alignment cue proposal timing is invalid."
        ) from error


def _vad_intervals_by_source(
    evidence: Mapping[str, object],
) -> dict[str, tuple[VoiceActivityInterval, ...]]:
    parts = evidence.get("parts")
    if not isinstance(parts, list):
        raise AudioAnalysisError(
            "alignment_vad_required", "Alignment requires retained VAD evidence."
        )
    result: dict[str, tuple[VoiceActivityInterval, ...]] = {}
    for part in parts:
        if not isinstance(part, Mapping) or not isinstance(part.get("source_id"), str):
            raise AudioAnalysisError(
                "alignment_vad_required", "Retained VAD Part evidence is invalid."
            )
        source_id = part["source_id"]
        intervals = part.get("voice_activity_intervals")
        if source_id in result or not isinstance(intervals, list):
            raise AudioAnalysisError(
                "alignment_vad_required", "Retained VAD intervals are invalid."
            )
        try:
            result[source_id] = tuple(
                VoiceActivityInterval(
                    HalfOpenInterval(
                        _exact_time_from_json(value.get("interval", {}).get("start")),
                        _exact_time_from_json(value.get("interval", {}).get("end")),
                    ),
                    VoiceActivityState(str(value.get("state"))),
                )
                for value in intervals
                if isinstance(value, Mapping) and isinstance(value.get("interval"), Mapping)
            )
        except (TypeError, ValueError) as error:
            raise AudioAnalysisError(
                "alignment_vad_required", "Retained VAD intervals are invalid."
            ) from error
        if len(result[source_id]) != len(intervals):
            raise AudioAnalysisError(
                "alignment_vad_required", "Retained VAD intervals are invalid."
            )
    return result


def _primary_alignment_cues(
    subtitle_report: SubtitleCandidateReport, source_id: str, project_root: Path
) -> tuple[tuple[AlignmentCue, ...], InputEvidence]:
    primary = _primary_subtitle_candidate(subtitle_report, source_id)
    if (
        primary is None
        or primary.source_candidate_path is None
        or primary.source_candidate_sha256 is None
    ):
        raise AudioAnalysisError(
            "alignment_primary_subtitle_unavailable",
            "Alignment requires retained Primary subtitle source-candidate evidence.",
        )
    path = Path(primary.source_candidate_path).resolve()
    if not path.is_relative_to(project_root.resolve()) or not path.is_file():
        raise AudioAnalysisError(
            "alignment_primary_subtitle_unavailable",
            "Primary subtitle source evidence is unavailable.",
        )
    evidence = _input_evidence(path)
    if evidence.sha256 != primary.source_candidate_sha256:
        raise AudioAnalysisError(
            "alignment_primary_subtitle_changed", "Primary subtitle source evidence changed."
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        cues = document.get("cues") if isinstance(document, Mapping) else None
        if document.get("schema_version") != 1 or not isinstance(cues, list):
            raise ValueError
        parsed = tuple(
            AlignmentCue(
                value["source_ordinal"],
                value["text"],
                HalfOpenInterval(
                    _exact_time_from_json(value.get("raw_pts_interval", {}).get("start")),
                    _exact_time_from_json(value.get("raw_pts_interval", {}).get("end")),
                ),
            )
            for value in cues
            if isinstance(value, Mapping)
            and isinstance(value.get("source_ordinal"), int)
            and not isinstance(value.get("source_ordinal"), bool)
            and isinstance(value.get("text"), str)
            and isinstance(value.get("raw_pts_interval"), Mapping)
        )
    except (OSError, TypeError, ValueError, KeyError) as error:
        raise AudioAnalysisError(
            "alignment_primary_subtitle_unavailable", "Primary subtitle source evidence is invalid."
        ) from error
    if not parsed or len(parsed) != len(cues):
        raise AudioAnalysisError(
            "alignment_primary_subtitle_unavailable", "Primary subtitle source evidence is invalid."
        )
    return parsed, evidence


def _primary_alignment_source_ids(
    subtitle_report: SubtitleCandidateReport, selections: tuple[AnalysisAudioStreamSelection, ...]
) -> set[str]:
    return {
        selection.source_id
        for selection in selections
        if _primary_subtitle_candidate(subtitle_report, selection.source_id) is not None
    }


def _primary_subtitle_candidate(
    subtitle_report: SubtitleCandidateReport, source_id: str
) -> SubtitleCandidate | None:
    valid = [
        candidate
        for candidate in subtitle_report.candidates
        if candidate.source_id == source_id and candidate.state is CandidateState.VALID
    ]
    selections = {
        selection.stream_index
        for selection in subtitle_report.selections
        if selection.source_id == source_id
    }
    if len(valid) == 1:
        return valid[0]
    return next((candidate for candidate in valid if candidate.stream_index in selections), None)


def _alignment_view_as_json(
    view: AdoptedAlignmentTimingView, source_evidence: InputEvidence
) -> dict[str, object]:
    candidates_by_ordinal = {candidate.source_ordinal: candidate for candidate in view.candidates}
    return {
        "schema_version": 1,
        "state": view.state,
        "source_id": view.source_id,
        "language": view.language,
        "source_candidate": source_evidence.as_json(),
        "diagnostic": view.diagnostic,
        "cues": [
            {
                "source_ordinal": cue.source_ordinal,
                "text": cue.text,
                "original_raw_pts_interval": _interval_as_json(
                    candidates_by_ordinal[cue.source_ordinal].original_interval
                ),
                "proposed_raw_pts_interval": _interval_as_json(
                    candidates_by_ordinal[cue.source_ordinal].proposed_interval
                ),
                "adopted_raw_pts_interval": _interval_as_json(cue.interval),
                "adopted": candidates_by_ordinal[cue.source_ordinal].adopted,
                "reason": candidates_by_ordinal[cue.source_ordinal].reason,
                "global_reason": candidates_by_ordinal[cue.source_ordinal].global_reason,
                "vad_indeterminate_risk": candidates_by_ordinal[
                    cue.source_ordinal
                ].vad_indeterminate_risk,
            }
            for cue in view.cues
        ],
    }


def _alignment_failure_fingerprint(
    plan: RunPlan,
    source_id: str,
    source_evidence: InputEvidence,
    candidate_id: str,
    calibration: Mapping[str, object],
    view: AdoptedAlignmentTimingView,
) -> str:
    return _sha256_json(
        {
            "recurrence_key": _alignment_recurrence_key(
                plan, source_id, source_evidence, candidate_id, calibration
            ),
            "failed_gate": view.diagnostic,
        }
    )


def _alignment_recurrence_key(
    plan: RunPlan,
    source_id: str,
    source_evidence: InputEvidence,
    candidate_id: str,
    calibration: Mapping[str, object],
) -> str:
    artifact = next((item for item in plan.source_artifacts if item.source_id == source_id), None)
    profile = calibration.get("profile")
    if artifact is None or not isinstance(profile, Mapping):
        raise AudioAnalysisError("alignment_input_invalid", "Alignment provenance is incomplete.")
    return _sha256_json(
        {
            "source_artifact_sha256": artifact.sha256,
            "source_candidate_sha256": source_evidence.sha256,
            "candidate_id": candidate_id,
            "calibration_profile_sha256": profile.get("sha256"),
        }
    )


def _matching_alignment_failure_reports(project_root: Path, fingerprint: str) -> tuple[Path, ...]:
    reports_root = project_root / "work" / "audio-analysis-reports"
    if not reports_root.is_dir():
        return ()
    matching: list[Path] = []
    for report_path in reports_root.glob("*/audio-analysis-report.json"):
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            formal_evidence = report.get("formal_evidence") if isinstance(report, Mapping) else None
            if not isinstance(formal_evidence, list):
                continue
            for evidence in formal_evidence:
                if (
                    not isinstance(evidence, Mapping)
                    or evidence.get("capability") != "forced_alignment"
                ):
                    continue
                parts = evidence.get("parts")
                if not isinstance(parts, list):
                    continue
                for part in parts:
                    timing_view = part.get("timing_view") if isinstance(part, Mapping) else None
                    if not isinstance(timing_view, Mapping):
                        continue
                    view_path = _retained_evidence_path(timing_view, project_root)
                    view = json.loads(view_path.read_text(encoding="utf-8"))
                    if isinstance(view, Mapping) and view.get("failure_fingerprint") == fingerprint:
                        matching.append(report_path)
                        break
        except (AudioAnalysisError, OSError, ValueError, json.JSONDecodeError):
            continue
    return tuple(matching)


def _revalidate_analysis_inputs(
    plan: RunPlan, subtitle_report: SubtitleCandidateReport, project_root: Path
) -> None:
    for artifact in plan.source_artifacts:
        digest, byte_count = sha256_file(artifact.media_path)
        if digest != artifact.sha256 or byte_count != artifact.byte_count:
            raise AudioAnalysisError(
                "source_artifact_changed", "A source artifact changed after RunPlan confirmation."
            )
    if subtitle_report.subtitle_rules_fingerprint != subtitle_rules_fingerprint(project_root):
        raise AudioAnalysisError(
            "subtitle_rules_changed", "Subtitle rules changed after subtitle processing."
        )


def _vad_thresholds(candidate: Mapping[str, object], project_root: Path) -> dict[str, ExactTime]:
    calibration = candidate.get("calibration")
    if not isinstance(calibration, Mapping):
        raise AudioAnalysisError("vad_threshold_invalid", "VAD calibration evidence is missing.")
    record = calibration.get("record")
    if not isinstance(record, Mapping):
        raise AudioAnalysisError("vad_threshold_invalid", "VAD calibration record is missing.")
    record_path = _retained_evidence_path(record, project_root)
    try:
        decoded = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AudioAnalysisError(
            "vad_threshold_invalid", "VAD calibration record cannot be read."
        ) from error
    thresholds = decoded.get("thresholds") if isinstance(decoded, Mapping) else None
    if not isinstance(thresholds, Mapping):
        raise AudioAnalysisError("vad_threshold_invalid", "VAD calibration thresholds are missing.")
    try:
        return {
            "uncovered_speech_duration": _exact_time_from_json(
                thresholds.get("uncovered_speech_duration")
            ),
            "long_silence_duration": _exact_time_from_json(thresholds.get("long_silence_duration")),
        }
    except (TypeError, ValueError) as error:
        raise AudioAnalysisError(
            "vad_threshold_invalid", "VAD calibration duration thresholds are invalid."
        ) from error


def _projected_vad_segments(
    candidate: Mapping[str, object],
    selections: tuple[AnalysisAudioStreamSelection, ...],
    project_root: Path,
) -> dict[tuple[str, int], tuple[VoiceActivityCandidateSegment, ...]]:
    adapter = candidate.get("adapter")
    if not isinstance(adapter, Mapping):
        raise AudioAnalysisError("model_output_invalid", "VAD adapter evidence is missing.")
    projection = adapter.get("projection")
    if not isinstance(projection, Mapping):
        raise AudioAnalysisError("model_output_invalid", "VAD projection evidence is missing.")
    projection_path = _retained_evidence_path(projection, project_root)
    try:
        decoded = json.loads(projection_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AudioAnalysisError(
            "model_output_invalid", "VAD projection cannot be read."
        ) from error
    result = decoded.get("result") if isinstance(decoded, Mapping) else None
    parts = result.get("parts") if isinstance(result, Mapping) else None
    if not isinstance(parts, list):
        raise AudioAnalysisError(
            "model_output_invalid", "VAD projection must provide every selected Part."
        )
    selections_by_key = {
        (selection.source_id, selection.stream_index): selection for selection in selections
    }
    expected = set(selections_by_key)
    projected: dict[tuple[str, int], tuple[VoiceActivityCandidateSegment, ...]] = {}
    for part in parts:
        if not isinstance(part, Mapping):
            raise AudioAnalysisError(
                "model_output_invalid", "VAD projected Part must be an object."
            )
        source_id = part.get("source_id")
        stream_index = part.get("stream_index")
        segments = part.get("segments")
        if (
            not isinstance(source_id, str)
            or not isinstance(stream_index, int)
            or isinstance(stream_index, bool)
            or not isinstance(segments, list)
        ):
            raise AudioAnalysisError(
                "model_output_invalid", "VAD projection Part identity is invalid."
            )
        key = (source_id, stream_index)
        if key not in expected or key in projected:
            raise AudioAnalysisError(
                "model_output_invalid", "VAD projection Part identity is invalid."
            )
        _validate_vad_time_mapping(
            part.get("source_time_mapping"), selections_by_key[key], project_root
        )
        projected[key] = tuple(_vad_candidate_segment(segment) for segment in segments)
    if set(projected) != expected:
        raise AudioAnalysisError("model_output_invalid", "VAD projection omitted a selected Part.")
    return projected


def _validate_vad_time_mapping(
    value: object, selection: AnalysisAudioStreamSelection, project_root: Path
) -> None:
    if not isinstance(value, Mapping):
        raise AudioAnalysisError("model_output_invalid", "VAD source-time mapping is missing.")
    derivative = value.get("derivative_evidence")
    if (
        value.get("schema_version") != 1
        or value.get("coordinate") != "raw_pts_identity"
        or value.get("structural_evidence_sha256") != selection.structural_evidence_sha256
        or value.get("coverage_evidence_sha256") != selection.coverage_evidence_sha256
        or not isinstance(derivative, Mapping)
    ):
        raise AudioAnalysisError("model_output_invalid", "VAD source-time mapping is invalid.")
    _retained_evidence_path(derivative, project_root)


def _vad_candidate_segment(value: object) -> VoiceActivityCandidateSegment:
    if not isinstance(value, Mapping):
        raise AudioAnalysisError("model_output_invalid", "VAD segment must be an object.")
    try:
        return VoiceActivityCandidateSegment(
            HalfOpenInterval(
                _exact_time_from_json(value.get("start")),
                _exact_time_from_json(value.get("end")),
            ),
            VoiceActivityState(str(value.get("state"))),
        )
    except (TypeError, ValueError) as error:
        raise AudioAnalysisError("model_output_invalid", "VAD segment is invalid.") from error


def _primary_caption_intervals(
    subtitle_report: SubtitleCandidateReport, source_id: str
) -> tuple[HalfOpenInterval, ...]:
    valid = [
        candidate
        for candidate in subtitle_report.candidates
        if candidate.source_id == source_id and candidate.state is CandidateState.VALID
    ]
    selected_indexes = {
        selection.stream_index
        for selection in subtitle_report.selections
        if selection.source_id == source_id
    }
    if len(valid) > 1 and len(selected_indexes) != 1:
        raise AudioAnalysisError(
            "subtitle_selection_required",
            "Multiple valid subtitle tracks require an explicit retained selection.",
        )
    if subtitle_report.state is CandidateReportState.AWAITING_SUBTITLE_SELECTION:
        raise AudioAnalysisError(
            "subtitle_selection_required", "Subtitle track selection remains unresolved."
        )
    if len(valid) == 1:
        return valid[0].raw_pts_cue_intervals
    if len(selected_indexes) == 1:
        selected = next(
            (candidate for candidate in valid if candidate.stream_index in selected_indexes), None
        )
        if selected is not None:
            return selected.raw_pts_cue_intervals
    return ()


def _retained_evidence_path(evidence: Mapping[str, object], project_root: Path) -> Path:
    value = evidence.get("path")
    if not isinstance(value, str):
        raise AudioAnalysisError("model_output_invalid", "Retained evidence path is invalid.")
    path = Path(value).resolve()
    if not path.is_relative_to(project_root.resolve()) or not path.is_file():
        raise AudioAnalysisError("model_output_invalid", "Retained evidence is unavailable.")
    expected_sha256 = evidence.get("sha256")
    expected_bytes = evidence.get("byte_count")
    actual_sha256, actual_bytes = sha256_file(path)
    if actual_sha256 != expected_sha256 or actual_bytes != expected_bytes:
        raise AudioAnalysisError("model_output_invalid", "Retained evidence changed.")
    return path


def _exact_time_from_json(value: object) -> ExactTime:
    if not isinstance(value, Mapping):
        raise ValueError("Exact time must be an object.")
    numerator = value.get("numerator")
    denominator = value.get("denominator")
    if (
        not isinstance(numerator, int)
        or isinstance(numerator, bool)
        or not isinstance(denominator, int)
        or isinstance(denominator, bool)
    ):
        raise ValueError("Exact time needs integer numerator and denominator.")
    return ExactTime(numerator, denominator)


def _vad_part_evidence_as_json(evidence: VadPartEvidence) -> dict[str, object]:
    return {
        "source_id": evidence.source_id,
        "stream_index": evidence.stream_index,
        "voice_activity_intervals": [
            {"interval": _interval_as_json(item.interval), "state": item.state.value}
            for item in evidence.voice_activity_intervals
        ],
        "uncovered_speech_risks": [
            {
                "interval": _interval_as_json(item.interval),
                "elevated": item.elevated,
                "asr_planning_recommendation": "required" if item.elevated else "not_required",
            }
            for item in evidence.uncovered_speech_risks
        ],
        "audio_state_indeterminate": [
            {"interval": _interval_as_json(item.interval), "elevated": item.elevated}
            for item in evidence.audio_state_indeterminate
        ],
        "long_silences": [
            {"interval": _interval_as_json(item.interval)} for item in evidence.long_silences
        ],
    }


def _interval_as_json(interval: HalfOpenInterval) -> dict[str, object]:
    return {
        "start": {"numerator": interval.start.numerator, "denominator": interval.start.denominator},
        "end": {"numerator": interval.end.numerator, "denominator": interval.end.denominator},
    }


def _stream_coverage_as_json(coverage: StreamCoverage) -> dict[str, object]:
    return {
        "coverage": _interval_as_json(coverage.coverage) if coverage.coverage is not None else None,
        "gaps": [_interval_as_json(gap) for gap in coverage.gaps],
        "diagnostics": [
            {"reason": item.reason, "path": item.path, "message": item.message}
            for item in coverage.diagnostics
        ],
    }
