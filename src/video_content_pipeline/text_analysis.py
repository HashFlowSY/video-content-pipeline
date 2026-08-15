"""Phase 6's immutable text-analysis workspace and report identity.

This first Phase 6 slice establishes the domain records, the immutable
workspace, the report identity, and the ``controlled_adapter_unavailable``
result. It binds the explicitly named retained RunPlan and Subtitle candidate
report and records their read-only evidence without modifying them, the Phase 5
reports, or ``outputs/``. Full input revalidation, the public CLI, and the
Controlled offline text adapter's generation contract belong to later Phase 6
tickets. See ``docs/PHASE_06_SPECIFICATION.md`` and the Text Analysis Context.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from video_content_pipeline.evidence import (
    InputEvidence,
    validated_report_id,
    write_json_once,
)
from video_content_pipeline.planning import (
    PlanningDiagnostic,
    PlanningError,
    load_run_plan,
)
from video_content_pipeline.source import SourceArtifact, sha256_file
from video_content_pipeline.subtitle_pipeline import (
    SubtitleCandidateReport,
    SubtitleReportError,
)


class TextAnalysisReportStatus(StrEnum):
    """The recorded outcome of one text-analysis attempt.

    ``complete``/``partial``/``failed`` are the formal Text analysis report
    statuses. ``controlled_adapter_unavailable`` is the availability outcome
    recorded when no eligible offline text adapter exists; it retains no
    SemanticSegments. (A future real-model path would add its own
    ``model_acquisition_required`` outcome when that capability is built.)
    """

    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    CONTROLLED_ADAPTER_UNAVAILABLE = "controlled_adapter_unavailable"


class TextAnalysisError(ValueError):
    """A rejected Phase 6 input with a machine-readable diagnostic reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


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
            "restriction": "local_audit_only",
        }


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
    controlled_text_adapter: ControlledTextAdapterState
    restricted_raw_output: tuple[RestrictedRawOutput, ...]
    diagnostics: tuple[PlanningDiagnostic, ...]

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
            },
            "controlled_text_adapter": self.controlled_text_adapter.as_json(),
            "segments": [],
            "chapters": [],
            "collection_summary": None,
            "restricted_raw_output": [output.as_json() for output in self.restricted_raw_output],
            "diagnostics": [diagnostic.as_json() for diagnostic in self.diagnostics],
            "guarantees": {
                "asr_or_ocr": "not_attempted",
                "external_knowledge": "not_used",
                "model_acquisition": "not_attempted",
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


def analyze_text(
    plan_id: str,
    subtitle_report_id: str,
    project_root: Path,
) -> dict[str, object]:
    """Create one immutable text-analysis report from retained planning inputs.

    Ticket 01 binds the named RunPlan and Subtitle candidate report, records
    their read-only evidence, and — because no Controlled offline text adapter
    capability exists yet — retains a ``controlled_adapter_unavailable`` report
    with no semantic content. Any binding failure retains a ``failed`` report.
    """

    report_id = uuid.uuid4().hex
    workspace_path = project_root / "work" / "text-analysis-reports" / report_id
    report_path = workspace_path / "text-analysis-report.json"
    run_plan_evidence: InputEvidence | None = None
    subtitle_report_evidence: InputEvidence | None = None
    diagnostics: tuple[PlanningDiagnostic, ...] = ()
    status = TextAnalysisReportStatus.FAILED
    report_plan_id = plan_id
    report_subtitle_id = subtitle_report_id

    try:
        plan_path = project_root / "plans" / plan_id / "run-plan.json"
        plan = load_run_plan(plan_path)
        if plan.plan_id != plan_id:
            raise TextAnalysisError(
                "run_plan_not_confirmed", "RunPlan identity does not match the requested plan ID."
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
            raise TextAnalysisError(
                "subtitle_report_mismatch",
                "Subtitle candidate report does not belong to this RunPlan.",
            )
        run_plan_evidence = _input_evidence(plan_path)
        subtitle_report_evidence = _input_evidence(subtitle_path)
        report_plan_id = plan.plan_id
        report_subtitle_id = subtitle_report.report_id
        status = TextAnalysisReportStatus.CONTROLLED_ADAPTER_UNAVAILABLE
        diagnostics = (_adapter_unavailable_diagnostic(),)
    except (TextAnalysisError, PlanningError, SubtitleReportError, OSError, ValueError) as error:
        status = TextAnalysisReportStatus.FAILED
        diagnostics = (
            PlanningDiagnostic(
                getattr(error, "reason", "text_analysis_input_invalid"),
                str(error),
            ),
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
        controlled_text_adapter=ControlledTextAdapterState(
            state=TextAnalysisReportStatus.CONTROLLED_ADAPTER_UNAVAILABLE.value,
            diagnostic=_adapter_unavailable_diagnostic(),
        ),
        restricted_raw_output=(),
        diagnostics=diagnostics,
    )
    _write_json_once(report_path, report.as_json())
    return {"status": report.status.value, "report": report.as_json()}


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
