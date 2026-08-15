"""Offline unit contract for Phase 7 ticket 02 deterministic core.

The transcription workspace flow is proven end-to-end at the CLI seam
(``tests/integration/test_phase_7_transcription_cli_contract.py``). These unit
tests pin the two pure detectors that decide the start precondition and the
Transcription resource-envelope pause, following the strict-TDD rule that the
deterministic core is tested directly.
"""

from __future__ import annotations

import json
from pathlib import Path

from video_content_pipeline.planning import PlanningDiagnostic
from video_content_pipeline.subtitle_pipeline import (
    CandidateReportState,
    SubtitleCandidateReport,
    SubtitlePartReport,
    SubtitlePartState,
)
from video_content_pipeline.transcription import (
    evaluate_asr_capabilities,
    subtitle_unavailable_parts,
    transcription_resource_envelope_pause,
)


def _report(part: SubtitlePartReport) -> SubtitleCandidateReport:
    return SubtitleCandidateReport(
        report_id="1" * 32,
        plan_id="plan-x",
        state=CandidateReportState.COMPLETED,
        subtitle_rules_fingerprint="fingerprint",
        candidates=(),
        diagnostics=(),
        report_path=Path("/tmp/does-not-matter/candidate-report.json"),
        part_reports=(part,),
    )


def test_subtitle_unavailable_parts_detects_retained_handoff() -> None:
    part = SubtitlePartReport(
        "src-a",
        SubtitlePartState.SUBTITLE_UNAVAILABLE_REQUIRES_ASR_PLAN,
        None,
        None,
        None,
        (),
        PlanningDiagnostic("subtitle_unavailable_requires_asr_plan", "No valid track."),
    )

    assert subtitle_unavailable_parts(_report(part)) == ("src-a",)


def test_subtitle_unavailable_parts_ignores_resolved_parts() -> None:
    part = SubtitlePartReport("src-a", SubtitlePartState.COMPLETED, 1, None, None, (), None)

    assert subtitle_unavailable_parts(_report(part)) == ()


def test_subtitle_unavailable_parts_requires_the_handoff_diagnostic() -> None:
    # The unavailable state without a retained handoff diagnostic is not a valid
    # ASR-plan authorization, so it is not treated as a precondition.
    part = SubtitlePartReport(
        "src-a",
        SubtitlePartState.SUBTITLE_UNAVAILABLE_REQUIRES_ASR_PLAN,
        None,
        None,
        None,
        (),
        None,
    )

    assert subtitle_unavailable_parts(_report(part)) == ()


def test_resource_envelope_pause_detects_over_envelope_candidate(tmp_path: Path) -> None:
    registry_path = tmp_path / "models" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    dependency_plan = tmp_path / "models" / "plans" / "qwen3-asr-1-7b.md"
    dependency_plan.parent.mkdir(parents=True)
    dependency_plan.write_text("# plan\n", encoding="utf-8")
    candidate = {
        "candidate_id": "qwen3-asr-1-7b",
        "capability": "asr_primary",
        "official_source": {"url": "https://example.invalid/qwen3-asr", "approved": True},
        "license_approved": True,
        "revision": "fixture-r1",
        "asset_sha256": "a" * 64,
        "offline_runtime": True,
        "credential_required": False,
        "telemetry": False,
        "dependency_plan": "models/plans/qwen3-asr-1-7b.md",
        "resource_estimate": {"high_bytes": 24 * 1024**3 + 1},
    }
    registry_path.write_text(
        json.dumps({"schema_version": 2, "candidates": [candidate]}, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    pause = transcription_resource_envelope_pause(evaluate_asr_capabilities(tmp_path))

    assert pause is not None
    assert pause["capability"] == "asr_primary"
    assert pause["candidate_id"] == "qwen3-asr-1-7b"
    assert pause["resource_high_bytes"] == 24 * 1024**3 + 1


def test_resource_envelope_pause_absent_within_envelope(tmp_path: Path) -> None:
    # No registry means no candidate exceeds the envelope; the pause is absent.
    pause = transcription_resource_envelope_pause(evaluate_asr_capabilities(tmp_path))

    assert pause is None
