"""Phase 5's no-model audio-analysis CLI contract."""

from __future__ import annotations

import json
import re
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
                "model_registry": (
                    self.model_registry_evidence.as_json()
                    if self.model_registry_evidence is not None
                    else None
                ),
            },
            "capabilities": [capability.as_json() for capability in self.capabilities],
            "formal_evidence": [],
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
    model_registry_evidence: InputEvidence | None = None
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
        registry_path = project_root / "models" / "registry.json"
        if registry_path.exists():
            model_registry_evidence = _input_evidence(registry_path)
        capabilities = _capabilities_from_registry(project_root, workspace_path)
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
        model_registry_evidence=model_registry_evidence,
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


def _capability_state_from_candidates(
    capability: str, candidates: list[dict[str, object]]
) -> str:
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
            resource_estimate.get("high_bytes")
            if isinstance(resource_estimate, Mapping)
            else None
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
        workspace_path
        / "capabilities"
        / capability
        / candidate_id
        / "calibration-evaluation.json"
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
