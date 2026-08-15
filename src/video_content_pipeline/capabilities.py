"""Shared, context-neutral model-capability eligibility helpers.

More than one Context evaluates model candidates from ``models/registry.json``:
audio-analysis owns ``vad``/``forced_alignment``/``diarization`` and
transcription owns ``asr_primary``/``asr_review``. The eligibility gate is a
single, security-sensitive policy (approved HTTPS source, approved license,
pinned revision and asset hash, offline runtime, no credentials, no telemetry,
a project-local dependency plan, and a resource envelope within the 24 GiB
ceiling), so it lives in exactly one place and every Context reads it the same
way.

Like :mod:`video_content_pipeline.evidence`, these helpers raise no
phase-specific error: callers inject an ``invalid_error`` factory so each
Context keeps its own diagnostic identity while parsing and gate logic stay
shared. The candidate matrix is validated as a whole -- shape and global
identity uniqueness -- but only the requested capabilities are returned, so one
Context's candidates never invalidate another Context's read of the same file.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from video_content_pipeline.evidence import input_evidence

CANDIDATE_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
MAX_MODEL_RESOURCE_BYTES = 24 * 1024**3


def load_registry_document(
    registry_path: Path, *, invalid_error: Callable[[str], Exception]
) -> Mapping[str, object]:
    """Read and JSON-decode a model registry into a mapping, or raise the caller's error."""

    try:
        decoded = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise invalid_error("Model registry cannot be read.") from error
    if not isinstance(decoded, Mapping):
        raise invalid_error("Model registry has an invalid schema.")
    return decoded


def capability_state_from_grades(grades: Iterable[tuple[object, object]]) -> str:
    """Aggregate candidate ``(state, reason)`` grades into one capability state.

    An eligible candidate means acquisition is the remaining step; otherwise a
    credential-gated candidate keeps the capability blocked, any other candidate
    is ineligible, and no candidate at all still requires acquisition.
    """

    graded = list(grades)
    if any(state == "eligible" for state, _reason in graded):
        return "model_acquisition_required"
    if any(reason == "model_credential_gated" for _state, reason in graded):
        return "model_credential_gated"
    if graded:
        return "model_ineligible"
    return "model_acquisition_required"


def project_local_file(project_root: Path, value: object) -> Path | None:
    """Resolve ``value`` to an existing file inside ``project_root`` or return None."""

    if not isinstance(value, str) or not value:
        return None
    path = (project_root / value).resolve()
    if not path.is_relative_to(project_root.resolve()) or not path.is_file():
        return None
    return path


def candidate_eligibility(
    candidate: Mapping[str, object], project_root: Path
) -> tuple[str, str | None]:
    """Grade one registry candidate against the shared eligibility gates.

    Returns ``("eligible", None)`` when every gate passes, ``("blocked", reason)``
    for a credential requirement or an over-envelope resource estimate, and
    ``("unsupported", "model_candidate_evidence_incomplete")`` when any required
    evidence field is missing or malformed.
    """

    if candidate.get("credential_required") is True:
        return "blocked", "model_credential_gated"
    source = candidate.get("official_source")
    asset_sha256 = candidate.get("asset_sha256")
    resource_estimate = candidate.get("resource_estimate")
    if (
        isinstance(resource_estimate, Mapping)
        and isinstance(resource_estimate.get("high_bytes"), int)
        and not isinstance(resource_estimate.get("high_bytes"), bool)
        and resource_estimate["high_bytes"] > MAX_MODEL_RESOURCE_BYTES
    ):
        return "blocked", "resource_envelope_exceeded"
    required = (
        isinstance(source, Mapping)
        and isinstance(source.get("url"), str)
        and source["url"].startswith("https://")
        and source.get("approved") is True
        and candidate.get("license_approved") is True
        and isinstance(candidate.get("revision"), str)
        and bool(candidate.get("revision"))
        and isinstance(asset_sha256, str)
        and SHA256_PATTERN.fullmatch(asset_sha256) is not None
        and candidate.get("offline_runtime") is True
        and candidate.get("credential_required") is False
        and candidate.get("telemetry") is False
        and project_local_file(project_root, candidate.get("dependency_plan")) is not None
        and isinstance(resource_estimate, Mapping)
        and isinstance(resource_estimate.get("high_bytes"), int)
        and not isinstance(resource_estimate.get("high_bytes"), bool)
        and 0 <= resource_estimate["high_bytes"] <= MAX_MODEL_RESOURCE_BYTES
    )
    if required:
        return "eligible", None
    return "unsupported", "model_candidate_evidence_incomplete"


def candidate_eligibility_evidence(
    candidate: Mapping[str, object], project_root: Path
) -> dict[str, object]:
    """Record the fields the eligibility gate read, for an auditable trail."""

    resource_estimate = candidate.get("resource_estimate")
    dependency_plan = project_local_file(project_root, candidate.get("dependency_plan"))
    return {
        "official_source": candidate.get("official_source"),
        "license_approved": candidate.get("license_approved"),
        "revision": candidate.get("revision"),
        "asset_sha256": candidate.get("asset_sha256"),
        "offline_runtime": candidate.get("offline_runtime"),
        "credential_required": candidate.get("credential_required"),
        "telemetry": candidate.get("telemetry"),
        "dependency_plan": (
            input_evidence(dependency_plan).as_json() if dependency_plan is not None else None
        ),
        "resource_high_bytes": (
            resource_estimate.get("high_bytes") if isinstance(resource_estimate, Mapping) else None
        ),
    }


def parse_candidate_matrix(
    registry: Mapping[str, object],
    capabilities: Collection[str],
    *,
    invalid_error: Callable[[str], Exception],
) -> dict[str, list[Mapping[str, object]]]:
    """Validate the schema-2 candidate matrix and group the requested capabilities.

    Every candidate is validated for shape and for global ``candidate_id``
    uniqueness across all capabilities. Candidates whose capability is not in
    ``capabilities`` are validated but not returned, so a Context reading its own
    capabilities never rejects a registry that also carries another Context's
    candidates.
    """

    candidates = registry.get("candidates")
    if not isinstance(candidates, list):
        raise invalid_error("Model registry needs a candidates list.")
    grouped: dict[str, list[Mapping[str, object]]] = {capability: [] for capability in capabilities}
    seen_ids: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise invalid_error("Model candidate must be an object.")
        candidate_id = candidate.get("candidate_id")
        capability = candidate.get("capability")
        if (
            not isinstance(candidate_id, str)
            or CANDIDATE_ID_PATTERN.fullmatch(candidate_id) is None
            or not isinstance(capability, str)
            or not capability
        ):
            raise invalid_error("Model candidate identity is invalid.")
        if candidate_id in seen_ids:
            raise invalid_error("Model candidate IDs must be unique.")
        seen_ids.add(candidate_id)
        if capability in grouped:
            grouped[capability].append(candidate)
    return grouped


@dataclass(frozen=True)
class CandidateAssessment:
    """The eligibility grade of one registered model candidate.

    Shared across Contexts that evaluate a provider-neutral capability from the
    registry (transcription's ``asr_*``, visual-text's ``ocr_primary``): the
    fields and their JSON shape are identical, so they live here rather than
    being re-declared per Context. The report a Context wraps around these
    assessments -- its statuses, diagnostics, and guarantees -- stays
    Context-local.
    """

    candidate_id: str
    capability: str
    state: str
    reason: str | None
    model_identity: str | None
    eligibility_evidence: dict[str, object]

    def as_json(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "capability": self.capability,
            "state": self.state,
            "reason": self.reason,
            "model_identity": self.model_identity,
            "eligibility_evidence": self.eligibility_evidence,
        }


def assess_candidate(
    candidate: Mapping[str, object], capability: str, project_root: Path
) -> CandidateAssessment:
    """Grade one registry candidate and bind its asset-level model identity.

    At the registry layer a model is identified by its pinned asset hash; the
    finer identity a Context may later name (backend, quantization, decoding,
    projection schema, rule versions) binds at the model-output projection
    (ADR 0036), not here. Independence at this layer is asset-level, so a
    ``model_identity`` is exposed only for an eligible candidate.
    """

    state, reason = candidate_eligibility(candidate, project_root)
    asset_sha256 = candidate.get("asset_sha256")
    model_identity = asset_sha256 if state == "eligible" and isinstance(asset_sha256, str) else None
    return CandidateAssessment(
        candidate_id=str(candidate["candidate_id"]),
        capability=capability,
        state=state,
        reason=reason,
        model_identity=model_identity,
        eligibility_evidence=candidate_eligibility_evidence(candidate, project_root),
    )


def load_candidate_matrix(
    registry_path: Path,
    capabilities: Collection[str],
    *,
    invalid_error: Callable[[str], Exception],
) -> dict[str, list[Mapping[str, object]]]:
    """Load and group the requested capabilities, tolerating the legacy schema.

    A schema-2 registry is validated and grouped (:func:`parse_candidate_matrix`).
    A legacy schema-1 registry (a ``models`` list, no candidate matrix) is a
    valid registry that simply carries no candidates for these capabilities, so
    it yields empty groups rather than an error. Any other schema is rejected
    through the caller's ``invalid_error`` factory.
    """

    decoded = load_registry_document(registry_path, invalid_error=invalid_error)
    schema_version = decoded.get("schema_version")
    if schema_version == 2:
        return parse_candidate_matrix(decoded, capabilities, invalid_error=invalid_error)
    if schema_version == 1:
        return {capability: [] for capability in capabilities}
    raise invalid_error("Model registry has an invalid schema.")
