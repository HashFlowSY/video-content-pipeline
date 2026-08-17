"""The real-engine adapter seam the model-bearing stages reach when selected.

Phase 12 ticket 06 wires the orchestrated run to the real engines. Run
composition selects, per capability, whether a stage runs its acquired real
engine or the controlled offline adapter that ADR 0037 keeps as the
automated-test path (:func:`~video_content_pipeline.run_composition.select_adapter_profile`).
When a capability is selected real, its stage function delegates here.

This module is the reachable real-branch **entry**, not the inference body. It
verifies each selected capability's pinned model asset from disk through that
capability's own engine loader — which re-hashes the vendored tree against the
registry manifest and raises a typed ``*_asset_unavailable`` / ``*_asset_mismatch``
when the asset is missing or drifted, touching only local files so nothing is
ever fetched (ADR 0055's hub-offline boundary; the eventual model load runs
through the Model runtime subprocess, which forces the hub-offline guards). With
the assets verified it fails closed with a typed ``real_engine_execution_deferred``
marker: the heavy inference and its per-stage report / ``stage_execution`` bridge
land against run #1's real evidence (ticket 08), and until then a real run must
fail rather than silently fall back to the offline adapter.

The verifier map is the single seam; each entry lazy-imports its engine module so
the automated suite — which never selects a capability real (no acquired,
promoted candidate exists) — never imports a heavy inference dependency.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

#: The typed reason a real selection fails with once its assets verify: the
#: inference body is completed against run #1 (ticket 08), so a real run fails
#: closed here rather than using the controlled offline adapter.
DEFERRED_REASON = "real_engine_execution_deferred"


@dataclass(frozen=True)
class RealEngineSelection:
    """The capabilities of one stage that run composition selected real.

    ``project_root`` locates the registry and the pinned assets; ``capabilities``
    is the subset of that stage's capabilities the adapter profile graded real.
    An empty selection is never constructed — run composition passes ``None`` for
    an all-offline stage — so a selection here always names at least one real
    capability.
    """

    project_root: Path
    capabilities: frozenset[str]


def _verify_vad(project_root: Path) -> tuple[Path, str]:
    from video_content_pipeline.vad_engine import load_silero_asset

    return load_silero_asset(project_root)


def _verify_forced_alignment(project_root: Path) -> tuple[Path, str]:
    from video_content_pipeline.alignment_engine import load_aligner_asset

    return load_aligner_asset(project_root)


def _verify_diarization(project_root: Path) -> tuple[Path, str]:
    from video_content_pipeline.diarization_engine import load_segmentation_asset

    return load_segmentation_asset(project_root)


def _verify_asr_primary(project_root: Path) -> tuple[Path, str]:
    from video_content_pipeline.asr_engine import load_primary_asset

    return load_primary_asset(project_root)


def _verify_asr_review(project_root: Path) -> tuple[Path, str]:
    from video_content_pipeline.asr_engine import load_review_asset

    return load_review_asset(project_root)


def _verify_ocr_primary(project_root: Path) -> tuple[Path, str]:
    from video_content_pipeline.ocr_engine import verify_bundled_models

    models_dir, asset_sha256, _roles = verify_bundled_models(project_root)
    return models_dir, asset_sha256


def _verify_text_semantics(project_root: Path) -> tuple[Path, str]:
    from video_content_pipeline.text_semantics_engine import load_text_semantics_asset

    return load_text_semantics_asset(project_root)


#: capability -> a verifier that returns ``(asset_path, asset_sha256)`` or raises
#: the engine's typed ``*EngineError`` (a ``ValueError`` carrying ``reason``) when
#: the pinned asset is absent or drifted. Every real capability the ticket names
#: appears here; the map is monkeypatched in tests to exercise the verified path.
_VERIFIERS: dict[str, Callable[[Path], tuple[Path, str]]] = {
    "vad": _verify_vad,
    "forced_alignment": _verify_forced_alignment,
    "diarization": _verify_diarization,
    "asr_primary": _verify_asr_primary,
    "asr_review": _verify_asr_review,
    "ocr_primary": _verify_ocr_primary,
    "text_semantics": _verify_text_semantics,
}


def _failed(report_id: str, stage: str, diagnostics: list[dict[str, object]]) -> dict[str, object]:
    return {
        "status": "failed",
        "report": {
            "report_id": report_id,
            "stage": stage,
            "diagnostics": diagnostics,
        },
    }


def dispatch_real_stage(selection: RealEngineSelection, *, stage: str) -> dict[str, object]:
    """Verify a real selection's pinned assets, then defer the inference to run #1.

    Returns a ``{"status", "report"}`` pair in the per-phase functions' own shape
    so run composition maps it exactly as any stage return. Verifies each selected
    capability in a stable order; the first missing or drifted asset short-circuits
    to that engine's typed failure (never a download). If every asset verifies, the
    stage fails closed with :data:`DEFERRED_REASON` and the verified identities, so
    a real run leaves an auditable trail and never falls back to the offline
    adapter. The heavy inference body lands against run #1 (ticket 08).
    """

    report_id = uuid.uuid4().hex
    verified: list[dict[str, object]] = []
    for capability in sorted(selection.capabilities):
        verifier = _VERIFIERS.get(capability)
        if verifier is None:  # pragma: no cover - defensive; the profile only names known ones
            return _failed(
                report_id,
                stage,
                [{"capability": capability, "reason": "real_engine_unknown_capability"}],
            )
        try:
            _asset_path, asset_sha256 = verifier(selection.project_root)
        except ValueError as error:
            # The engine loaders raise their own typed ``*EngineError`` (a
            # ``ValueError`` carrying ``reason``) for a missing or drifted asset;
            # an unexpected ``ValueError`` with no reason is reported honestly as a
            # generic verification failure rather than relabelled asset-unavailable.
            reason = getattr(error, "reason", "real_engine_verification_failed")
            return _failed(
                report_id,
                stage,
                [{"capability": capability, "reason": reason, "message": str(error)}],
            )
        verified.append({"capability": capability, "asset_sha256": asset_sha256})
    deferred: dict[str, object] = {
        "reason": DEFERRED_REASON,
        "message": (
            "Real-engine inference lands against run #1 (Phase 12 ticket 08); "
            "the run fails closed rather than using the offline adapter."
        ),
    }
    result = _failed(report_id, stage, [deferred])
    report = result["report"]
    assert isinstance(report, dict)
    report["verified"] = verified
    return result
