"""Phase 5's no-model audio-analysis CLI contract."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

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
    SubtitleCandidateReport,
    SubtitleReportError,
)


class AudioAnalysisReportState(StrEnum):
    """The current minimum Phase 5 report can only be blocked by missing models."""

    BLOCKED = "blocked"


class AudioAnalysisError(ValueError):
    """A rejected Phase 5 input with a machine-readable diagnostic reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


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

    def as_json(self) -> dict[str, object]:
        return {
            "capability": self.capability,
            "state": self.state,
            "model": None,
            "diagnostic": {
                "reason": self.state,
                "message": _capability_message(self.capability, self.state),
            },
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
    capabilities: tuple[CapabilityAvailability, ...]
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
            },
            "capabilities": [capability.as_json() for capability in self.capabilities],
            "diagnostics": [diagnostic.as_json() for diagnostic in self.diagnostics],
            "processing_authorization": {
                "state": "not_started",
                "reason": "model_availability_only",
            },
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


def analyze_audio(plan_id: str, subtitle_report_id: str, project_root: Path) -> dict[str, object]:
    """Retain the Phase 5 no-model result without media processing or a model runtime."""

    report_id = uuid.uuid4().hex
    workspace_path = project_root / "work" / "audio-analysis-reports" / report_id
    report_path = workspace_path / "audio-analysis-report.json"
    run_plan_evidence: InputEvidence | None = None
    subtitle_report_evidence: InputEvidence | None = None
    diagnostics: tuple[PlanningDiagnostic, ...] = ()
    capabilities: tuple[CapabilityAvailability, ...] = ()
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
        capabilities = _capabilities_from_registry(project_root)
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
        state=AudioAnalysisReportState.BLOCKED,
        workspace_path=workspace_path,
        report_path=report_path,
        run_plan_evidence=run_plan_evidence,
        subtitle_report_evidence=subtitle_report_evidence,
        capabilities=capabilities,
        diagnostics=diagnostics,
    )
    _write_json_once(report_path, report.as_json())
    return {"status": report.state.value, "report": report.as_json()}


def _matches_confirmed_plan(confirmed_report: PlanReport, plan: RunPlan) -> bool:
    return (
        confirmed_report.state is PlanState.READY_FOR_CONFIRMATION
        and confirmed_report.source_artifacts == plan.source_artifacts
        and confirmed_report.tools == plan.tools
        and confirmed_report.disk_headroom == plan.disk_headroom
        and confirmed_report.configuration_fingerprint == plan.configuration_fingerprint
        and confirmed_report.url_authorizations == plan.url_authorizations
    )


def _capabilities_from_registry(project_root: Path) -> tuple[CapabilityAvailability, ...]:
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
    if not isinstance(decoded, Mapping) or decoded.get("schema_version") != 1:
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


def _write_json_once(path: Path, payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != encoded:
            raise AudioAnalysisError(
                "audio_analysis_report_conflict", f"Immutable record differs: {path}"
            )
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded, encoding="utf-8")
