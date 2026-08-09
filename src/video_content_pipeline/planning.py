"""Immutable Phase 3 reports, estimates, revalidation, and RunPlans."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import stat
import time
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
from video_content_pipeline.url_policy import URLAuthorizationEvidence


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


_REVALIDATION_DIAGNOSTIC_REASONS = frozenset(
    {
        "planning_configuration_changed",
        "source_artifact_missing",
        "source_artifact_changed",
        "source_artifact_unavailable",
        "tool_identity_changed",
        "disk_headroom_insufficient",
    }
)


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
class DecodeMeasurement:
    """One completed full-decode observation for an exact SourceArtifact."""

    source_id: str
    elapsed_seconds: int


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
    url_authorizations: tuple[URLAuthorizationEvidence, ...] = ()
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
            "url_authorizations": [
                authorization.as_json() for authorization in self.url_authorizations
            ],
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
    tools: tuple[PinnedExternalTool, ...]
    disk_headroom: DiskHeadroom
    configuration_fingerprint: str
    url_authorizations: tuple[URLAuthorizationEvidence, ...] = ()

    def as_json(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "report_id": self.report_id,
            "source_artifacts": [artifact.as_json() for artifact in self.source_artifacts],
            "tools": [tool.as_json() for tool in self.tools],
            "disk_headroom": {
                "increment_bytes": self.disk_headroom.increment_bytes,
                "reserve_bytes": self.disk_headroom.reserve_bytes,
                "required_bytes": self.disk_headroom.required_bytes,
            },
            "configuration_fingerprint": self.configuration_fingerprint,
            "url_authorizations": [
                authorization.as_json() for authorization in self.url_authorizations
            ],
        }


def estimate_full_decode(
    duration_seconds: Fraction,
    profile: DecodeThroughputProfile,
    *,
    matching_measurement: DecodeMeasurement | None = None,
) -> ThreePointEstimate:
    """Estimate linear decode without reading source media for calibration."""

    if duration_seconds <= 0:
        raise PlanningError("duration_invalid", "Decode duration must be positive.")
    if matching_measurement is not None:
        return ThreePointEstimate(
            optimistic_seconds=matching_measurement.elapsed_seconds,
            likely_seconds=matching_measurement.elapsed_seconds,
            conservative_seconds=matching_measurement.elapsed_seconds,
            confidence="observed",
            basis=f"decode-history:{matching_measurement.source_id}",
        )
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
            throughput_profile = DecodeThroughputProfile(
                version=profile_id,
                optimistic_realtime_factor=Fraction(str(profile["optimistic_realtime_factor"])),
                likely_realtime_factor=Fraction(str(profile["likely_realtime_factor"])),
                conservative_realtime_factor=Fraction(str(profile["conservative_realtime_factor"])),
            )
            if any(
                factor <= 0
                for factor in (
                    throughput_profile.optimistic_realtime_factor,
                    throughput_profile.likely_realtime_factor,
                    throughput_profile.conservative_realtime_factor,
                )
            ):
                raise ValueError
            return throughput_profile
        except (KeyError, ValueError, ZeroDivisionError) as error:
            raise PlanningError(
                "decode_profile_invalid", "Decode throughput values must be positive ratios."
            ) from error
    raise PlanningError("decode_profile_missing", f"Decode profile is absent: {profile_id}")


def load_decode_measurements(path: Path) -> tuple[DecodeMeasurement, ...]:
    """Load retained full-decode observations without treating absence as an error."""

    if not path.exists():
        return ()
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(decoded, dict):
            raise TypeError
        measurements = decoded.get("measurements")
        if decoded.get("schema_version") != 1 or not isinstance(measurements, list):
            raise TypeError
        parsed = tuple(
            DecodeMeasurement(
                source_id=_required_string(value, "source_id"),
                elapsed_seconds=_required_integer(value, "elapsed_seconds"),
            )
            for value in measurements
        )
        if any(measurement.elapsed_seconds <= 0 for measurement in parsed):
            raise ValueError
        return parsed
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise PlanningError(
            "decode_history_invalid", "Decode history has an invalid schema."
        ) from error


def find_matching_decode_measurement(
    measurements: tuple[DecodeMeasurement, ...], source_id: str
) -> DecodeMeasurement | None:
    """Select the newest observation only when the source identity matches exactly."""

    return next(
        (
            measurement
            for measurement in reversed(measurements)
            if measurement.source_id == source_id
        ),
        None,
    )


def record_decode_measurement(path: Path, source_id: str, elapsed_seconds: int) -> None:
    """Append completed validation timing while retaining all prior observations."""

    if elapsed_seconds <= 0:
        raise PlanningError("decode_history_invalid", "Decode measurement must be positive.")
    measurements = (*load_decode_measurements(path), DecodeMeasurement(source_id, elapsed_seconds))
    payload = {
        "schema_version": 1,
        "measurements": [
            {"source_id": measurement.source_id, "elapsed_seconds": measurement.elapsed_seconds}
            for measurement in measurements
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def create_plan_report(
    *,
    state: PlanState,
    source_artifacts: tuple[SourceArtifact, ...],
    tools: tuple[PinnedExternalTool, ...],
    planned_increment_bytes: int,
    configuration_fingerprint: str,
    decode_estimate: ThreePointEstimate | None = None,
    diagnostics: tuple[PlanningDiagnostic, ...] = (),
    url_authorizations: tuple[URLAuthorizationEvidence, ...] = (),
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
        url_authorizations=url_authorizations,
        inspection_evidence=inspection_evidence,
        parent_report_id=parent_report_id,
    )


def planning_configuration_fingerprint(project_root: Path) -> str:
    """Hash every project-owned configuration input that affects Phase 3 plans."""

    evidence: list[dict[str, object]] = []
    for relative_path in (
        Path("config/decode-throughput-profiles.json"),
        Path("config/tools.json"),
    ):
        path = project_root / relative_path
        item: dict[str, object] = {"path": relative_path.as_posix()}
        try:
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode):
                item["state"] = "not_regular_file"
            else:
                digest, byte_count = sha256_file(path)
                item["sha256"] = digest
                item["byte_count"] = byte_count
        except FileNotFoundError:
            item["state"] = "missing"
        except OSError:
            item["state"] = "unreadable"
        evidence.append(item)
    payload = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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
        authorization_values = decoded.get("url_authorizations", [])
        inspection_values = decoded.get("inspection_evidence", [])
        if not isinstance(diagnostic_values, list) or not isinstance(authorization_values, list):
            raise TypeError
        if not isinstance(inspection_values, list):
            raise TypeError
        diagnostics = tuple(
            PlanningDiagnostic(
                reason=_required_string(value, "reason"), message=_required_string(value, "message")
            )
            for value in diagnostic_values
        )
        url_authorizations = tuple(
            URLAuthorizationEvidence.from_json(authorization)
            for authorization in authorization_values
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
            url_authorizations=url_authorizations,
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


def perform_full_decode_validation(ffmpeg: PinnedExternalTool, artifact: SourceArtifact) -> int:
    """Run an explicitly requested null-output full decode and fail closed on error."""

    try:
        started_at = time.monotonic()
        result = run_tool(build_full_decode_command(ffmpeg, artifact))
    except OSError as error:
        raise PlanningError(
            "full_decode_failed", f"FFmpeg decode validation could not start: {error}"
        ) from error
    if result.returncode != 0:
        raise PlanningError(
            "full_decode_failed", f"FFmpeg decode validation failed: {result.stderr.strip()}"
        )
    return max(1, math.ceil(time.monotonic() - started_at))


def revalidate_report(report: PlanReport, project_root: Path) -> tuple[PlanningDiagnostic, ...]:
    """Compare report evidence with current artifact, tool, and disk state."""

    diagnostics: list[PlanningDiagnostic] = []
    if planning_configuration_fingerprint(project_root) != report.configuration_fingerprint:
        diagnostics.append(
            PlanningDiagnostic(
                "planning_configuration_changed",
                "Planning configuration no longer matches the report.",
            )
        )
    for artifact in report.source_artifacts:
        try:
            actual_hash, actual_size = sha256_file(artifact.media_path)
        except FileNotFoundError:
            diagnostics.append(
                PlanningDiagnostic("source_artifact_missing", str(artifact.media_path))
            )
            continue
        except OSError:
            diagnostics.append(
                PlanningDiagnostic(
                    "source_artifact_unavailable",
                    "A SourceArtifact can no longer be read for hash revalidation.",
                )
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
    if _has_stale_confirmation_child(report.report_id, plans_root):
        raise PlanningError(
            "report_superseded", "A stale confirmation already requires a new planning attempt."
        )
    diagnostics = revalidate_report(report, project_root)
    if diagnostics:
        stale = create_plan_report(
            state=PlanState.BLOCKED,
            source_artifacts=report.source_artifacts,
            tools=report.tools,
            planned_increment_bytes=report.disk_headroom.increment_bytes,
            configuration_fingerprint=report.configuration_fingerprint,
            decode_estimate=report.decode_estimate,
            diagnostics=diagnostics,
            url_authorizations=report.url_authorizations,
            inspection_evidence=report.inspection_evidence,
            parent_report_id=report.report_id,
        )
        persist_plan_report(stale, plans_root)
        raise PlanningError(
            "report_stale",
            f"{'; '.join(diagnostic.reason for diagnostic in diagnostics)}; "
            f"stale report: {stale.report_id}",
        )
    payload = json.dumps(report.as_json(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    plan_id = hashlib.sha256(payload).hexdigest()[:24]
    plan = RunPlan(
        plan_id=plan_id,
        report_id=report.report_id,
        source_artifacts=report.source_artifacts,
        tools=report.tools,
        disk_headroom=report.disk_headroom,
        configuration_fingerprint=report.configuration_fingerprint,
        url_authorizations=report.url_authorizations,
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


def _has_stale_confirmation_child(report_id: str, plans_root: Path) -> bool:
    reports_root = plans_root / "reports"
    if not reports_root.is_dir():
        return False
    for path in reports_root.glob("*/plan-report.json"):
        try:
            child = load_plan_report(path)
        except PlanningError:
            continue
        if (
            child.parent_report_id == report_id
            and child.state == PlanState.BLOCKED
            and any(
                diagnostic.reason in _REVALIDATION_DIAGNOSTIC_REASONS
                for diagnostic in child.diagnostics
            )
        ):
            return True
    return False


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
