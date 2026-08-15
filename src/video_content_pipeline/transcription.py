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

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from video_content_pipeline.capabilities import (
    candidate_eligibility,
    candidate_eligibility_evidence,
    capability_state_from_grades,
    load_registry_document,
    parse_candidate_matrix,
)
from video_content_pipeline.evidence import InputEvidence, input_evidence

ASR_CAPABILITIES = ("asr_primary", "asr_review")


class TranscriptionError(ValueError):
    """A transcription evaluation failure that names a stable diagnostic reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class AsrCandidateAssessment:
    """The eligibility grade of one registered ASR candidate."""

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


@dataclass(frozen=True)
class AsrCapabilityAvailability:
    """The explicit availability state for one provider-neutral ASR capability."""

    capability: str
    state: str
    candidates: tuple[AsrCandidateAssessment, ...]

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

    grouped = _load_asr_candidate_matrix(registry_path)
    capabilities = tuple(
        _availability(capability, grouped[capability], project_root)
        for capability in ASR_CAPABILITIES
    )
    return _report(capabilities, registry_evidence=input_evidence(registry_path))


def _load_asr_candidate_matrix(
    registry_path: Path,
) -> dict[str, list[Mapping[str, object]]]:
    decoded = load_registry_document(
        registry_path,
        invalid_error=lambda message: TranscriptionError("model_registry_invalid", message),
    )
    schema_version = decoded.get("schema_version")
    if schema_version == 2:
        return parse_candidate_matrix(
            decoded,
            ASR_CAPABILITIES,
            invalid_error=lambda message: TranscriptionError("model_registry_invalid", message),
        )
    if schema_version == 1:
        # The legacy models list holds no ASR entries; treat it as no candidates.
        return {capability: [] for capability in ASR_CAPABILITIES}
    raise TranscriptionError("model_registry_invalid", "Model registry has an invalid schema.")


def _availability(
    capability: str, candidates: list[Mapping[str, object]], project_root: Path
) -> AsrCapabilityAvailability:
    assessments = tuple(
        _assess_candidate(candidate, capability, project_root) for candidate in candidates
    )
    state = capability_state_from_grades(
        (assessment.state, assessment.reason) for assessment in assessments
    )
    return AsrCapabilityAvailability(capability, state, assessments)


def _assess_candidate(
    candidate: Mapping[str, object], capability: str, project_root: Path
) -> AsrCandidateAssessment:
    state, reason = candidate_eligibility(candidate, project_root)
    # At the registry layer a model is identified by its pinned asset hash; the
    # finer identity the spec names (backend, quantization, decoding, projection
    # schema, rule versions) binds later, at the model-output projection (ADR
    # 0036), which enters in tickets 03+. Independence here is asset-level.
    asset_sha256 = candidate.get("asset_sha256")
    model_identity = (
        asset_sha256 if state == "eligible" and isinstance(asset_sha256, str) else None
    )
    return AsrCandidateAssessment(
        candidate_id=str(candidate["candidate_id"]),
        capability=capability,
        state=state,
        reason=reason,
        model_identity=model_identity,
        eligibility_evidence=candidate_eligibility_evidence(candidate, project_root),
    )


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
