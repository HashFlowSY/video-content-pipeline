"""Visual-Text Context: the provider-neutral OCR capability contract (Phase 8).

Ticket 01 registers the Visual-text capability contract -- provider-neutral and
carrying exactly one model capability, ``ocr_primary`` -- and evaluates its
candidates from ``models/registry.json`` using the shared Phase 5 eligibility
gate. Detection, sampling, and classification are deterministic and are never
model capabilities (ADR 0047), so they hold no slot here; general vision models
are entirely outside the contract -- not a required dependency and not an
optional slot -- so a foreign ``vlm_*`` candidate in the registry is simply an
unread capability, never a visual-text one.

With no eligible, acquired OCR model available the result is always the
Model-acquisition-required visual-text result: an immutable outcome carrying no
OCR evidence, so acquisition stays a separately authorized decision. The whole
evaluation is offline -- no model is downloaded or executed, no frame of user
media is extracted, and no network is accessed. An eligible candidate is one
that *could* be acquired; the result stays ``model_acquisition_required`` until
a separately authorized acquisition step exists (see
docs/PHASE_08_SPECIFICATION.md and ADR 0036/0047).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from video_content_pipeline.capabilities import (
    CandidateAssessment,
    assess_candidate,
    capability_state_from_grades,
    load_candidate_matrix,
)
from video_content_pipeline.evidence import InputEvidence, input_evidence

# The provider-neutral visual-text capability contract holds exactly one model
# capability. Detection, sampling, and classification are deterministic (ADR
# 0047) and never appear here, and no general-vision slot exists.
OCR_PRIMARY_CAPABILITY = "ocr_primary"
VISUAL_TEXT_CAPABILITIES: tuple[str, ...] = (OCR_PRIMARY_CAPABILITY,)

# Every offline capability evaluation asserts these guarantees. Frame extraction
# joins the Phase 5/7 set because a visual-text attempt is the only place frames
# would ever be read, and none is here.
_OFFLINE_GUARANTEES: dict[str, str] = {
    "frame_extraction": "not_attempted",
    "model_acquisition": "not_attempted",
    "model_execution": "not_attempted",
    "network_access": "not_attempted",
    "outputs_publication": "not_attempted",
}


def offline_guarantees() -> dict[str, str]:
    """Return a fresh copy of the offline guarantees every visual-text record asserts.

    Both the capability evaluation and the command boundary assert the same block,
    so it lives here once and callers copy it into their own report JSON.
    """

    return dict(_OFFLINE_GUARANTEES)


class VisualTextError(ValueError):
    """A visual-text evaluation failure that names a stable diagnostic reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class OcrCapabilityAvailability:
    """The explicit availability state for the ``ocr_primary`` capability."""

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
class VisualTextCapabilityReport:
    """Immutable OCR-capability evaluation with no OCR evidence."""

    result: str
    capabilities: tuple[OcrCapabilityAvailability, ...]
    model_registry_evidence: InputEvidence | None

    def as_json(self) -> dict[str, object]:
        return {
            "result": self.result,
            "capabilities": [capability.as_json() for capability in self.capabilities],
            "ocr_evidence": None,
            "model_registry": (
                self.model_registry_evidence.as_json()
                if self.model_registry_evidence is not None
                else None
            ),
            "guarantees": offline_guarantees(),
        }


def evaluate_ocr_capabilities(project_root: Path) -> VisualTextCapabilityReport:
    """Evaluate ``ocr_primary`` from the model registry, offline.

    With no eligible, acquired model available the result is always
    ``model_acquisition_required`` and no OCR evidence is produced; the
    per-capability state and candidate grades carry the detail a later,
    separately authorized acquisition step will consume.
    """

    registry_path = project_root / "models" / "registry.json"
    if not registry_path.exists():
        return _report(
            tuple(
                OcrCapabilityAvailability(capability, "model_acquisition_required", ())
                for capability in VISUAL_TEXT_CAPABILITIES
            ),
            registry_evidence=None,
        )

    grouped = load_candidate_matrix(
        registry_path,
        VISUAL_TEXT_CAPABILITIES,
        invalid_error=lambda message: VisualTextError("model_registry_invalid", message),
    )
    capabilities = tuple(
        _availability(capability, grouped[capability], project_root)
        for capability in VISUAL_TEXT_CAPABILITIES
    )
    return _report(capabilities, registry_evidence=input_evidence(registry_path))


def _availability(
    capability: str, candidates: list[Mapping[str, object]], project_root: Path
) -> OcrCapabilityAvailability:
    # The asset-level model identity for an eligible OCR candidate binds later at
    # the OCR output projection (ADR 0036, ticket 05); assess_candidate exposes
    # it only when eligible.
    assessments = tuple(
        assess_candidate(candidate, capability, project_root) for candidate in candidates
    )
    state = capability_state_from_grades(
        (assessment.state, assessment.reason) for assessment in assessments
    )
    return OcrCapabilityAvailability(capability, state, assessments)


def _report(
    capabilities: tuple[OcrCapabilityAvailability, ...],
    *,
    registry_evidence: InputEvidence | None,
) -> VisualTextCapabilityReport:
    return VisualTextCapabilityReport(
        result="model_acquisition_required",
        capabilities=capabilities,
        model_registry_evidence=registry_evidence,
    )


def _capability_message(capability: str, state: str) -> str:
    messages = {
        "model_acquisition_required": (
            f"No acquired offline model is available for {capability}; acquisition is required "
            "before OCR evidence can be produced."
        ),
        "model_credential_gated": (
            f"An {capability} candidate requires credentials and is blocked."
        ),
        "model_ineligible": (
            f"No registered {capability} candidate satisfies the eligibility gates."
        ),
    }
    return messages[state]
