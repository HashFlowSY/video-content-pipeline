"""Phase 12 ticket 06: the real-engine adapter seam the stage functions reach.

When run composition selects a real capability for an orchestrated run, the stage
function delegates to :func:`dispatch_real_stage`. This is the reachable real
branch's *entry*: it verifies each selected capability's pinned model asset from
disk through that capability's own engine loader — a typed failure when the asset
is missing or drifted, and never a download — then fails closed with a typed
``real_engine_execution_deferred`` marker, because the heavy inference body and
its report/stage_execution bridge land against run #1 (ticket 08), never silently
falling back to the controlled offline adapter. The automated suite never reaches
this seam (no capability is ever selected real in CI), so no real model is loaded.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from video_content_pipeline import real_engine_adapter
from video_content_pipeline.real_engine_adapter import (
    DEFERRED_REASON,
    RealEngineSelection,
    dispatch_real_stage,
)


def _split(result: dict[str, object]) -> tuple[str, dict]:
    report = result["report"]
    assert isinstance(report, dict)
    return str(result["status"]), report


def test_missing_asset_is_a_typed_failure_never_a_download(tmp_path: Path) -> None:
    # A capability selected real but whose pinned asset tree is absent: the
    # engine loader raises its typed ``*_asset_unavailable`` and dispatch surfaces
    # it verbatim. Nothing is fetched — the loader only reads local files.
    selection = RealEngineSelection(project_root=tmp_path, capabilities=frozenset({"vad"}))
    status, report = _split(dispatch_real_stage(selection, stage="audio_analysis"))
    assert status == "failed"
    diagnostics = report["diagnostics"]
    assert isinstance(diagnostics, list) and diagnostics
    assert diagnostics[0]["capability"] == "vad"
    assert diagnostics[0]["reason"] == "vad_asset_unavailable"


def test_verified_assets_defer_execution_to_run_1(tmp_path: Path, monkeypatch) -> None:
    # With the pinned asset verified from disk, the body is deferred: dispatch
    # fails closed with the typed deferral, listing the verified capability so a
    # real run leaves an auditable trail rather than a silent offline fallback.
    monkeypatch.setitem(
        real_engine_adapter._VERIFIERS,
        "vad",
        lambda project_root: (project_root / "asset", "d" * 64),
    )
    selection = RealEngineSelection(project_root=tmp_path, capabilities=frozenset({"vad"}))
    status, report = _split(dispatch_real_stage(selection, stage="audio_analysis"))
    assert status == "failed"
    assert report["stage"] == "audio_analysis"
    diagnostics = report["diagnostics"]
    assert diagnostics[0]["reason"] == DEFERRED_REASON
    verified = report["verified"]
    assert verified == [{"capability": "vad", "asset_sha256": "d" * 64}]


def test_first_missing_asset_short_circuits_before_deferral(tmp_path: Path, monkeypatch) -> None:
    # One verified capability and one missing: the missing asset's typed failure
    # wins over the deferral, and capabilities are checked in sorted order.
    monkeypatch.setitem(
        real_engine_adapter._VERIFIERS,
        "asr_primary",
        lambda project_root: (project_root / "asset", "a" * 64),
    )
    selection = RealEngineSelection(
        project_root=tmp_path, capabilities=frozenset({"asr_primary", "asr_review"})
    )
    status, report = _split(dispatch_real_stage(selection, stage="transcription"))
    assert status == "failed"
    # asr_review sorts after asr_primary; its absent asset is the surfaced failure.
    # Both ASR capabilities share the engine's own ``asr_asset_unavailable`` reason.
    assert report["diagnostics"][0]["capability"] == "asr_review"
    assert report["diagnostics"][0]["reason"] == "asr_asset_unavailable"


def test_report_carries_a_report_id(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(
        real_engine_adapter._VERIFIERS,
        "text_semantics",
        lambda project_root: (project_root / "asset", "c" * 64),
    )
    selection = RealEngineSelection(
        project_root=tmp_path, capabilities=frozenset({"text_semantics"})
    )
    _status, report = _split(dispatch_real_stage(selection, stage="text_analysis"))
    assert isinstance(report["report_id"], str) and report["report_id"]


def test_every_ticket_capability_has_a_verifier() -> None:
    # Each of the seven real capabilities the ticket names maps to an engine
    # loader, so a real selection can always be verified rather than crashing.
    for capability in (
        "vad",
        "forced_alignment",
        "diarization",
        "asr_primary",
        "asr_review",
        "ocr_primary",
        "text_semantics",
    ):
        assert capability in real_engine_adapter._VERIFIERS


@pytest.mark.parametrize(
    "capability",
    ["vad", "forced_alignment", "diarization", "asr_primary", "asr_review", "text_semantics"],
)
def test_verifiers_raise_typed_failures_for_absent_assets(tmp_path: Path, capability: str) -> None:
    # Against an empty project root every disk-backed verifier fails typed rather
    # than fetching anything. (ocr verifies wheel-bundled files, exercised apart.)
    selection = RealEngineSelection(project_root=tmp_path, capabilities=frozenset({capability}))
    status, report = _split(dispatch_real_stage(selection, stage="s"))
    assert status == "failed"
    assert report["diagnostics"][0]["reason"].endswith("_asset_unavailable")
