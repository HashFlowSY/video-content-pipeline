"""Immutable Phase 3 reports, estimates, revalidation, and RunPlans."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from pathlib import Path

from video_content_pipeline.external_tools import (
    PinnedExternalTool,
    revalidate_external_tool,
    run_tool,
)
from video_content_pipeline.inspection import PlanInspectionEvidence
from video_content_pipeline.source import (
    DiskHeadroom,
    SourceArtifact,
    calculate_disk_headroom,
    sha256_file,
)


class PlanningError(ValueError):
    """A planning failure with a machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class PlanState(StrEnum):
    """The only legal persistent outcomes of one planning attempt."""

    BLOCKED = "blocked"
    AWAITING_DECODE_CONFIRMATION = "awaiting_decode_confirmation"
    READY_FOR_CONFIRMATION = "ready_for_confirmation"


@dataclass(frozen=True)
class PlanningDiagnostic:
    """One structured reason for a blocked or stale planning state."""

    reason: str
    message: str

    def as_json(self) -> dict[str, str]:
        return {"reason": self.reason, "message": self.message}


@dataclass(frozen=True)
class DecodeThroughputProfile:
    """A versioned, low-confidence source of first-run decode estimates."""

    version: str
    optimistic_realtime_factor: Fraction
    likely_realtime_factor: Fraction
    conservative_realtime_factor: Fraction


@dataclass(frozen=True)
class ThreePointEstimate:
    """A non-authoritative three-point estimate expressed in whole seconds."""

    optimistic_seconds: int
    likely_seconds: int
    conservative_seconds: int
    confidence: str
    basis: str

    def as_json(self) -> dict[str, object]:
        return {
            "optimistic_seconds": self.optimistic_seconds,
            "likely_seconds": self.likely_seconds,
            "conservative_seconds": self.conservative_seconds,
            "confidence": self.confidence,
            "basis": self.basis,
        }


@dataclass(frozen=True)
class PlanReport:
    """An immutable audit record for successful, blocked, or pending planning."""

    report_id: str
    state: PlanState
    source_artifacts: tuple[SourceArtifact, ...]
    tools: tuple[PinnedExternalTool, ...]
    disk_headroom: DiskHeadroom
    configuration_fingerprint: str
    decode_estimate: ThreePointEstimate | None
    diagnostics: tuple[PlanningDiagnostic, ...]
    inspection_evidence: tuple[PlanInspectionEvidence, ...] = ()
    parent_report_id: str | None = None

    def as_json(self) -> dict[str, object]:
        return {
            "report_id": self.report_id,
            "parent_report_id": self.parent_report_id,
            "state": self.state.value,
            "source_artifacts": [artifact.as_json() for artifact in self.source_artifacts],
            "tools": [tool.as_json() for tool in self.tools],
            "disk_headroom": {
                "increment_bytes": self.disk_headroom.increment_bytes,
                "reserve_bytes": self.disk_headroom.reserve_bytes,
                "required_bytes": self.disk_headroom.required_bytes,
            },
            "configuration_fingerprint": self.configuration_fingerprint,
            "decode_estimate": self.decode_estimate.as_json() if self.decode_estimate else None,
            "diagnostics": [diagnostic.as_json() for diagnostic in self.diagnostics],
            "inspection_evidence": [evidence.as_json() for evidence in self.inspection_evidence],
            "future_stages": {"status": "unavailable/not_estimated"},
        }


@dataclass(frozen=True)
class RunPlan:
    """A revalidated immutable plan that references already snapshotted sources."""

    plan_id: str
    report_id: str
    source_artifacts: tuple[SourceArtifact, ...]
    configuration_fingerprint: str

    def as_json(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "report_id": self.report_id,
            "source_artifacts": [artifact.as_json() for artifact in self.source_artifacts],
            "configuration_fingerprint": self.configuration_fingerprint,
        }


def estimate_full_decode(
    duration_seconds: Fraction, profile: DecodeThroughputProfile
) -> ThreePointEstimate:
    """Estimate linear decode without reading source media for calibration."""

    if duration_seconds <= 0:
        raise PlanningError("duration_invalid", "Decode duration must be positive.")
    return ThreePointEstimate(
        optimistic_seconds=_ceil_fraction(duration_seconds / profile.optimistic_realtime_factor),
        likely_seconds=_ceil_fraction(duration_seconds / profile.likely_realtime_factor),
        conservative_seconds=_ceil_fraction(
            duration_seconds / profile.conservative_realtime_factor
        ),
        confidence="low",
        basis=f"decode-throughput-profile:{profile.version}",
    )


def load_decode_throughput_profile(
    path: Path, profile_id: str = "phase-03-default-v1"
) -> DecodeThroughputProfile:
    """Load one versioned planning profile without any network or model access."""

    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PlanningError(
            "decode_profile_invalid", f"Cannot read decode profile: {path}"
        ) from error
    profiles = decoded.get("profiles") if isinstance(decoded, dict) else None
    if not isinstance(profiles, list):
        raise PlanningError(
            "decode_profile_invalid", "Decode profile document needs a profiles array."
        )
    for profile in profiles:
        if not isinstance(profile, dict) or profile.get("id") != profile_id:
            continue
        try:
            return DecodeThroughputProfile(
                version=profile_id,
                optimistic_realtime_factor=Fraction(str(profile["optimistic_realtime_factor"])),
                likely_realtime_factor=Fraction(str(profile["likely_realtime_factor"])),
                conservative_realtime_factor=Fraction(str(profile["conservative_realtime_factor"])),
            )
        except (KeyError, ValueError, ZeroDivisionError) as error:
            raise PlanningError(
                "decode_profile_invalid", "Decode throughput values must be positive ratios."
            ) from error
    raise PlanningError("decode_profile_missing", f"Decode profile is absent: {profile_id}")


def create_plan_report(
    *,
    state: PlanState,
    source_artifacts: tuple[SourceArtifact, ...],
    tools: tuple[PinnedExternalTool, ...],
    planned_increment_bytes: int,
    configuration_fingerprint: str,
    decode_estimate: ThreePointEstimate | None = None,
    diagnostics: tuple[PlanningDiagnostic, ...] = (),
    inspection_evidence: tuple[PlanInspectionEvidence, ...] = (),
    parent_report_id: str | None = None,
) -> PlanReport:
    """Build one new immutable report without embedding raw URL inputs."""

    _validate_inspection_evidence(source_artifacts, inspection_evidence)
    return PlanReport(
        report_id=uuid.uuid4().hex,
        state=state,
        source_artifacts=source_artifacts,
        tools=tools,
        disk_headroom=calculate_disk_headroom(planned_increment_bytes),
        configuration_fingerprint=configuration_fingerprint,
        decode_estimate=decode_estimate,
        diagnostics=diagnostics,
        inspection_evidence=inspection_evidence,
        parent_report_id=parent_report_id,
    )


def persist_plan_report(report: PlanReport, plans_root: Path) -> Path:
    """Write a report once beneath its stable report-ID directory."""

    report_path = plans_root / "reports" / report.report_id / "plan-report.json"
    _write_json_once(report_path, report.as_json())
    return report_path


def load_plan_report(path: Path) -> PlanReport:
    """Load a persisted report whose serialized form contains no raw URL."""

    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PlanningError("plan_report_invalid", f"Cannot read PlanReport: {path}") from error
    if not isinstance(decoded, dict):
        raise PlanningError("plan_report_invalid", "PlanReport must be a JSON object.")
    try:
        source_values = decoded["source_artifacts"]
        tool_values = decoded["tools"]
        disk_value = decoded["disk_headroom"]
        if not isinstance(source_values, list) or not isinstance(tool_values, list):
            raise TypeError
        if not isinstance(disk_value, dict):
            raise TypeError
        sources = tuple(
            SourceArtifact(
                source_id=_required_string(value, "source_id"),
                sha256=_required_string(value, "sha256"),
                byte_count=_required_integer(value, "byte_count"),
                media_path=Path(_required_string(value, "media_path")),
                origin_kind=_required_string(value, "origin_kind"),
            )
            for value in source_values
        )
        tools = tuple(
            PinnedExternalTool(
                tool_id=_required_string(value, "tool_id"),
                path=Path(_required_string(value, "path")),
                version=_required_string(value, "version"),
                sha256=_required_string(value, "sha256"),
            )
            for value in tool_values
        )
        estimate_value = decoded.get("decode_estimate")
        estimate = (
            None
            if estimate_value is None
            else ThreePointEstimate(
                optimistic_seconds=_required_integer(estimate_value, "optimistic_seconds"),
                likely_seconds=_required_integer(estimate_value, "likely_seconds"),
                conservative_seconds=_required_integer(estimate_value, "conservative_seconds"),
                confidence=_required_string(estimate_value, "confidence"),
                basis=_required_string(estimate_value, "basis"),
            )
        )
        diagnostic_values = decoded.get("diagnostics", [])
        inspection_values = decoded.get("inspection_evidence", [])
        if not isinstance(diagnostic_values, list):
            raise TypeError
        if not isinstance(inspection_values, list):
            raise TypeError
        diagnostics = tuple(
            PlanningDiagnostic(
                reason=_required_string(value, "reason"), message=_required_string(value, "message")
            )
            for value in diagnostic_values
        )
        inspection_evidence = tuple(
            PlanInspectionEvidence.from_json(evidence) for evidence in inspection_values
        )
        _validate_inspection_evidence(sources, inspection_evidence)
        return PlanReport(
            report_id=_required_string(decoded, "report_id"),
            state=PlanState(_required_string(decoded, "state")),
            source_artifacts=sources,
            tools=tools,
            disk_headroom=DiskHeadroom(
                increment_bytes=_required_integer(disk_value, "increment_bytes"),
                reserve_bytes=_required_integer(disk_value, "reserve_bytes"),
                required_bytes=_required_integer(disk_value, "required_bytes"),
            ),
            configuration_fingerprint=_required_string(decoded, "configuration_fingerprint"),
            decode_estimate=estimate,
            diagnostics=diagnostics,
            inspection_evidence=inspection_evidence,
            parent_report_id=decoded.get("parent_report_id")
            if isinstance(decoded.get("parent_report_id"), str)
            else None,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PlanningError("plan_report_invalid", "PlanReport has an invalid schema.") from error


def build_full_decode_command(
    ffmpeg: PinnedExternalTool, artifact: SourceArtifact
) -> tuple[str, ...]:
    """Construct the only allowed Phase 3 FFmpeg operation: null-output decode."""

    return (
        str(ffmpeg.path),
        "-v",
        "error",
        "-xerror",
        "-i",
        str(artifact.media_path),
        "-map",
        "0:v?",
        "-map",
        "0:a?",
        "-f",
        "null",
        "-",
    )


def perform_full_decode_validation(ffmpeg: PinnedExternalTool, artifact: SourceArtifact) -> None:
    """Run an explicitly requested null-output full decode and fail closed on error."""

    result = run_tool(build_full_decode_command(ffmpeg, artifact))
    if result.returncode != 0:
        raise PlanningError(
            "full_decode_failed", f"FFmpeg decode validation failed: {result.stderr.strip()}"
        )


def revalidate_report(report: PlanReport, project_root: Path) -> tuple[PlanningDiagnostic, ...]:
    """Compare report evidence with current artifact, tool, and disk state."""

    diagnostics: list[PlanningDiagnostic] = []
    for artifact in report.source_artifacts:
        try:
            actual_hash, actual_size = sha256_file(artifact.media_path)
        except FileNotFoundError:
            diagnostics.append(
                PlanningDiagnostic("source_artifact_missing", str(artifact.media_path))
            )
            continue
        if actual_hash != artifact.sha256 or actual_size != artifact.byte_count:
            diagnostics.append(
                PlanningDiagnostic(
                    "source_artifact_changed", "A SourceArtifact hash no longer matches."
                )
            )
    for tool in report.tools:
        try:
            revalidate_external_tool(tool)
        except (FileNotFoundError, ValueError) as error:
            diagnostics.append(PlanningDiagnostic("tool_identity_changed", str(error)))
    if shutil.disk_usage(project_root).free < report.disk_headroom.required_bytes:
        diagnostics.append(
            PlanningDiagnostic(
                "disk_headroom_insufficient", "Current free space no longer meets the report."
            )
        )
    return tuple(diagnostics)


def confirm_run_plan(report: PlanReport, project_root: Path, plans_root: Path) -> RunPlan:
    """Create a RunPlan only from a ready report whose evidence still matches."""

    if report.state != PlanState.READY_FOR_CONFIRMATION:
        raise PlanningError(
            "report_not_ready", "Only a decode-validated report can create a RunPlan."
        )
    diagnostics = revalidate_report(report, project_root)
    if diagnostics:
        raise PlanningError(
            "report_stale", "; ".join(diagnostic.reason for diagnostic in diagnostics)
        )
    payload = json.dumps(report.as_json(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    plan_id = hashlib.sha256(payload).hexdigest()[:24]
    plan = RunPlan(
        plan_id=plan_id,
        report_id=report.report_id,
        source_artifacts=report.source_artifacts,
        configuration_fingerprint=report.configuration_fingerprint,
    )
    _write_json_once(plans_root / plan.plan_id / "run-plan.json", plan.as_json())
    return plan


def _ceil_fraction(value: Fraction) -> int:
    return (value.numerator + value.denominator - 1) // value.denominator


def _validate_inspection_evidence(
    source_artifacts: tuple[SourceArtifact, ...],
    inspection_evidence: tuple[PlanInspectionEvidence, ...],
) -> None:
    source_ids = tuple(artifact.source_id for artifact in source_artifacts)
    evidence_source_ids = tuple(evidence.source_id for evidence in inspection_evidence)
    if source_ids != evidence_source_ids:
        raise PlanningError(
            "inspection_evidence_invalid",
            "Each SourceArtifact needs one matching retained inspection evidence record.",
        )


def _write_json_once(path: Path, payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != encoded:
            raise PlanningError(
                "immutable_record_conflict", f"Immutable record already differs: {path}"
            )
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded, encoding="utf-8")


def _required_string(value: object, key: str) -> str:
    if not isinstance(value, dict):
        raise TypeError
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise TypeError
    return result


def _required_integer(value: object, key: str) -> int:
    if not isinstance(value, dict):
        raise TypeError
    result = value.get(key)
    if not isinstance(result, int) or isinstance(result, bool):
        raise TypeError
    return result
