"""Offline CLI contract for Phase 7 ticket 07 (local enhancement).

Ticket 07 adds ``vcp enhance`` and ``vcp resume-enhancement``. Enhancement scope
comes only from user-named Parts, ranges, or cues, each revalidated against
retained cue identities and stream coverage. Inside a user interval, ASR cues from
the Controlled offline ASR adapter replace the subtitle display layer only after
passing the timing gates; on failure the original cues stay with a recorded
reason (ADR 0045). Every enhanced cue carries ``subtitle_track`` or ``asr``
provenance, enhanced artifacts never claim verbatim completeness and never change
``audio_completeness=not_verified``, and every replacement and rejection is written
to the correction log and its readable rendering. No model is downloaded or
executed; the ASR text comes from a hash-pinned synthetic fixture.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from video_content_pipeline import cli, evidence
from video_content_pipeline.enhancement import (
    PartEnhancementScope,
    SelectedEnhancementTrack,
    enhancement_input_manifest_document,
    enhancement_input_manifest_sha256,
)
from video_content_pipeline.inspection import PlanInspectionEvidence
from video_content_pipeline.planning import (
    PlanState,
    RunPlan,
    create_plan_report,
    inspection_evidence_fingerprints,
    persist_plan_report,
)
from video_content_pipeline.probe import ProbeDocument
from video_content_pipeline.source import (
    SourceArtifact,
    sha256_file,
)
from video_content_pipeline.subtitle_pipeline import (
    CandidateReportState,
    CandidateState,
    SubtitleCandidate,
    SubtitleCandidateReport,
    SubtitlePartReport,
    SubtitlePartState,
    subtitle_rules_fingerprint,
)
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval

_REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "transcription"
_GUARANTEES = {
    "model_acquisition": "not_attempted",
    "model_execution": "not_attempted",
    "network_access": "not_attempted",
    "outputs_publication": "not_attempted",
}


def _confirmed_plan(project_root: Path) -> RunPlan:
    media_path = project_root / "input" / "source" / "synthetic-media"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"phase-7-enhance-cli-fixture")
    digest, byte_count = sha256_file(media_path)
    artifact = SourceArtifact(
        digest, digest, byte_count, media_path, origin_kind="synthetic_fixture"
    )
    evidence_record = PlanInspectionEvidence(
        source_id=artifact.source_id,
        structural_document=ProbeDocument(
            json.dumps({"streams": [{"index": 1, "codec_type": "subtitle"}]})
        ),
        coverage_document=ProbeDocument('{"packets": []}'),
        coverage_by_stream=(),
        subtitle_tracks=(),
    )
    plan_report = create_plan_report(
        state=PlanState.READY_FOR_CONFIRMATION,
        source_artifacts=(artifact,),
        tools=(),
        planned_increment_bytes=0,
        configuration_fingerprint="phase-03-fixture",
        inspection_evidence=(evidence_record,),
    )
    persist_plan_report(plan_report, project_root / "plans")
    plan = RunPlan(
        plan_id="confirmed-phase-7-enhance-plan",
        report_id=plan_report.report_id,
        source_artifacts=(artifact,),
        tools=(),
        disk_headroom=plan_report.disk_headroom,
        configuration_fingerprint=plan_report.configuration_fingerprint,
        inspection_evidence_fingerprints=inspection_evidence_fingerprints((evidence_record,)),
    )
    plan_path = project_root / "plans" / plan.plan_id / "run-plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(
        json.dumps(plan.as_json(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return plan


def _write_subtitle_rules(project_root: Path) -> None:
    rules_path = project_root / "config" / "subtitle-rules.json"
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text(
        '{"schema_version": 1, "id": "phase-04-fixture-rules"}\n', encoding="utf-8"
    )


def _source_candidate(project_root: Path, source_id: str) -> tuple[Path, str]:
    """Write a retained subtitle cue basis: three cues spanning [0, 15) raw PTS."""

    path = project_root / "work" / source_id / "subtitle-evidence" / "source-candidate.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schema_version": 1,
        "cues": [
            _cue(0, 0, 5, "原句甲"),
            _cue(1, 5, 10, "second line"),
            _cue(2, 10, 15, "结尾原句"),
        ],
    }
    path.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    digest, _ = sha256_file(path)
    return path, digest


def _cue(ordinal: int, start: int, end: int, text: str) -> dict[str, object]:
    return {
        "source_ordinal": ordinal,
        "text": text,
        "raw_pts_interval": {
            "start": {"numerator": start, "denominator": 1},
            "end": {"numerator": end, "denominator": 1},
        },
    }


def _subtitle_report(
    project_root: Path, plan: RunPlan, *, report_id: str = "1" * 32
) -> SubtitleCandidateReport:
    _write_subtitle_rules(project_root)
    source_id = plan.source_artifacts[0].source_id
    candidate_path, candidate_sha = _source_candidate(project_root, source_id)
    candidate = SubtitleCandidate(
        source_id=source_id,
        stream_index=1,
        state=CandidateState.VALID,
        source_format="srt",
        source_candidate_path=str(candidate_path),
        source_candidate_sha256=candidate_sha,
        cue_count=3,
    )
    part = SubtitlePartReport(
        source_id, SubtitlePartState.COMPLETED, 1, None, None, (), None
    )
    report_path = project_root / "work" / source_id / report_id / "candidate-report.json"
    report = SubtitleCandidateReport(
        report_id=report_id,
        plan_id=plan.plan_id,
        state=CandidateReportState.COMPLETED,
        subtitle_rules_fingerprint=subtitle_rules_fingerprint(project_root),
        candidates=(candidate,),
        diagnostics=(),
        report_path=report_path,
        part_reports=(part,),
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report.as_json(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return report


def _install_config(project_root: Path, *, with_fixture: bool) -> None:
    """Copy the shipped transcription contracts, optionally binding an ASR fixture."""

    destination = project_root / "config" / "transcription"
    destination.mkdir(parents=True, exist_ok=True)
    for source in _REPO_CONFIG.glob("*.json"):
        shutil.copy(source, destination / source.name)
    if not with_fixture:
        return


def _bind_fixture(
    project_root: Path,
    subtitle_report: SubtitleCandidateReport,
    *,
    part_id: str,
    scope_interval: HalfOpenInterval,
    asr_cues: list[dict[str, object]],
) -> None:
    """Write the ASR output fixture and bind it into the controlled adapter identity."""

    candidate = subtitle_report.candidates[0]
    assert candidate.source_candidate_sha256 is not None
    track = SelectedEnhancementTrack(
        part_id=part_id,
        stream_index=candidate.stream_index,
        source_candidate_sha256=candidate.source_candidate_sha256,
    )
    scope = PartEnhancementScope(part_id, (scope_interval,))
    manifest = enhancement_input_manifest_document(
        subtitle_report.report_id, (track,), (scope,)
    )
    manifest_sha = enhancement_input_manifest_sha256(manifest)

    raw_output = {
        "schema_version": 1,
        "projection_schema_version": "phase-07-asr-projection-schema-v1",
        "adapter_identity": "phase-07-controlled-asr-adapter-v1",
        "capability": "asr_primary",
        "result": {"cues": asr_cues},
    }
    fixture_bytes = (json.dumps(raw_output, sort_keys=True, indent=2) + "\n").encode("utf-8")
    fixture_path = project_root / "fixtures" / "asr-enhance-output.json"
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_bytes(fixture_bytes)
    from hashlib import sha256

    adapter_path = project_root / "config" / "transcription" / "controlled-adapter.json"
    adapter = json.loads(adapter_path.read_text(encoding="utf-8"))
    adapter["fixture"] = {
        "capability": "asr_primary",
        "output_fixture_path": "fixtures/asr-enhance-output.json",
        "output_fixture_sha256": sha256(fixture_bytes).hexdigest(),
        "input_fixture_sha256": manifest_sha,
    }
    adapter_path.write_text(json.dumps(adapter, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _over_envelope_registry(project_root: Path) -> None:
    """Register an ASR candidate whose conservative estimate exceeds the 24 GiB envelope."""

    dependency_plan = "models/plans/qwen3-asr-1-7b.md"
    plan_path = project_root / dependency_plan
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text("# qwen3-asr-1-7b dependency plan\n", encoding="utf-8")
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
        "dependency_plan": dependency_plan,
        "resource_estimate": {"high_bytes": 24 * 1024**3 + 1},
    }
    registry_path = project_root / "models" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps({"schema_version": 2, "candidates": [candidate]}, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _asr_cue(ordinal: int, start: int, end: int, text: str) -> dict[str, object]:
    return {
        "ordinal": ordinal,
        "start": {"numerator": start, "denominator": 1},
        "end": {"numerator": end, "denominator": 1},
        "text": text,
    }


def _configure_cli(project_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "assert_runtime_policy", lambda: None)
    monkeypatch.setattr(cli, "assert_project_venv", lambda: object())
    monkeypatch.setattr(cli, "_project_root", lambda: project_root)


def _run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    project_root: Path,
    argv: list[str],
) -> tuple[int, dict[str, object]]:
    _configure_cli(project_root, monkeypatch)
    code = cli.main(argv)
    return code, json.loads(capsys.readouterr().out)


# --- Happy path: gate-checked interval replacement -------------------------


def test_enhance_replaces_the_display_layer_over_a_named_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_config(tmp_path, with_fixture=True)
    subtitle_report = _subtitle_report(tmp_path, plan)
    part_id = plan.source_artifacts[0].source_id
    _bind_fixture(
        tmp_path,
        subtitle_report,
        part_id=part_id,
        scope_interval=HalfOpenInterval(ExactTime(0), ExactTime(10)),
        asr_cues=[_asr_cue(0, 0, 5, "ASR 甲"), _asr_cue(1, 5, 10, "ASR 乙")],
    )

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        [
            "enhance",
            plan.plan_id,
            subtitle_report.report_id,
            "--range",
            f"{part_id}:0-10",
            "--json",
        ],
    )

    assert code == 0
    assert response["status"] == "complete"
    report = response["report"]
    assert report["audio_completeness"] == "not_verified"
    assert report["verbatim_completeness_claimed"] is False
    assert report["guarantees"] == _GUARANTEES
    (enhanced_part,) = report["enhanced_parts"]
    provenances = [(cue["provenance"], cue["text"]) for cue in enhanced_part["cues"]]
    assert provenances == [
        ("asr", "ASR 甲"),
        ("asr", "ASR 乙"),
        ("subtitle_track", "结尾原句"),
    ]
    (correction,) = enhanced_part["corrections"]
    assert correction["kind"] == "replacement"
    assert correction["replaced_cue_ids"] == [
        f"{part_id}:stream-1:0",
        f"{part_id}:stream-1:1",
    ]
    # Enhanced, correction-log, and readable correction artifacts are retained.
    assert set(report["artifacts"]) >= {
        "subtitles_enhanced",
        "transcript_enhanced",
        "correction_log",
        "correction_report",
    }
    report_path = Path(report["report_path"])
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    assert not (tmp_path / "outputs").exists()


def test_enhance_keeps_originals_when_asr_fails_the_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_config(tmp_path, with_fixture=True)
    subtitle_report = _subtitle_report(tmp_path, plan)
    part_id = plan.source_artifacts[0].source_id
    # An ASR cue with hundreds of characters crammed into one second is implausibly
    # short for its text, so it fails the duration-to-text gate and the interval
    # must keep its original subtitle cues.
    _bind_fixture(
        tmp_path,
        subtitle_report,
        part_id=part_id,
        scope_interval=HalfOpenInterval(ExactTime(0), ExactTime(10)),
        asr_cues=[_asr_cue(0, 0, 1, "字" * 400)],
    )

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        [
            "enhance",
            plan.plan_id,
            subtitle_report.report_id,
            "--range",
            f"{part_id}:0-10",
            "--json",
        ],
    )

    assert code == 0
    assert response["status"] == "partial"
    (enhanced_part,) = response["report"]["enhanced_parts"]
    assert all(cue["provenance"] == "subtitle_track" for cue in enhanced_part["cues"])
    (correction,) = enhanced_part["corrections"]
    assert correction["kind"] == "rejection"
    assert correction["reason"] == "asr_cues_failed_gates"
    assert correction["gate_reasons"] == ["cue_duration_implausible"]


# --- Scope revalidation ----------------------------------------------------


def test_enhance_rejects_a_range_outside_retained_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_config(tmp_path, with_fixture=False)
    subtitle_report = _subtitle_report(tmp_path, plan)
    part_id = plan.source_artifacts[0].source_id

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        [
            "enhance",
            plan.plan_id,
            subtitle_report.report_id,
            "--range",
            f"{part_id}:0-99",
            "--json",
        ],
    )

    assert code == 0
    assert response["status"] == "failed"
    assert response["report"]["diagnostics"][0]["reason"] == "enhancement_range_out_of_coverage"
    assert response["report"]["enhanced_parts"] == []


def test_enhance_rejects_an_unknown_cue_selector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_config(tmp_path, with_fixture=False)
    subtitle_report = _subtitle_report(tmp_path, plan)
    part_id = plan.source_artifacts[0].source_id

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["enhance", plan.plan_id, subtitle_report.report_id, "--cue", f"{part_id}:9", "--json"],
    )

    assert code == 0
    assert response["status"] == "failed"
    assert response["report"]["diagnostics"][0]["reason"] == "enhancement_cue_unknown"


def test_enhance_blocks_on_subtitle_rules_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_config(tmp_path, with_fixture=False)
    subtitle_report = _subtitle_report(tmp_path, plan)
    part_id = plan.source_artifacts[0].source_id
    (tmp_path / "config" / "subtitle-rules.json").write_text(
        '{"schema_version": 1, "id": "DRIFTED"}\n', encoding="utf-8"
    )

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["enhance", plan.plan_id, subtitle_report.report_id, "--part", part_id, "--json"],
    )

    assert code == 0
    assert response["status"] == "failed"
    assert response["report"]["diagnostics"][0]["reason"] == "subtitle_rules_changed"


# --- No controlled fixture: acquisition required ----------------------------


def test_enhance_without_a_fixture_requires_model_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_config(tmp_path, with_fixture=False)
    subtitle_report = _subtitle_report(tmp_path, plan)
    part_id = plan.source_artifacts[0].source_id

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["enhance", plan.plan_id, subtitle_report.report_id, "--part", part_id, "--json"],
    )

    assert code == 0
    assert response["status"] == "model_acquisition_required"
    assert response["report"]["audio_completeness"] == "not_verified"
    assert response["report"]["enhanced_parts"] == []


# --- Resource-envelope pause + resume --------------------------------------


def test_enhance_pauses_when_conservative_estimate_exceeds_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_config(tmp_path, with_fixture=False)
    subtitle_report = _subtitle_report(tmp_path, plan)
    _over_envelope_registry(tmp_path)
    part_id = plan.source_artifacts[0].source_id

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["enhance", plan.plan_id, subtitle_report.report_id, "--part", part_id, "--json"],
    )

    assert code == 0
    assert response["status"] == "resource_envelope_exceeded"
    report = response["report"]
    assert report["required_decision"] == {
        "reason": "resource_envelope_exceeded",
        "decision": "resource_configuration_changed",
    }
    # A wrong resume decision is rejected; the recorded scope is preserved for a
    # correctly-decided resume to reconstruct.
    assert report["scope"][0]["part_id"] == part_id

    resume_code, resume_response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        [
            "resume-enhancement",
            report["report_id"],
            "--decision",
            "resource_configuration_changed",
            "--json",
        ],
    )

    assert resume_code == 0
    # A fresh attempt is minted from the retained identities; it is never the paused report.
    assert resume_response["report"]["report_id"] != report["report_id"]
    assert resume_response["report"]["input_evidence"]["resumed_from_report_id"] == (
        report["report_id"]
    )


def test_enhance_reads_no_source_media(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_config(tmp_path, with_fixture=True)
    subtitle_report = _subtitle_report(tmp_path, plan)
    part_id = plan.source_artifacts[0].source_id
    _bind_fixture(
        tmp_path,
        subtitle_report,
        part_id=part_id,
        scope_interval=HalfOpenInterval(ExactTime(0), ExactTime(10)),
        asr_cues=[_asr_cue(0, 0, 5, "ASR 甲"), _asr_cue(1, 5, 10, "ASR 乙")],
    )
    hashed: list[Path] = []
    real_sha256_file = evidence.sha256_file

    def _spy(path: Path) -> tuple[str, int]:
        hashed.append(Path(path))
        return real_sha256_file(path)

    monkeypatch.setattr(evidence, "sha256_file", _spy)

    code, _response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        [
            "enhance",
            plan.plan_id,
            subtitle_report.report_id,
            "--range",
            f"{part_id}:0-10",
            "--json",
        ],
    )

    assert code == 0
    assert plan.source_artifacts[0].media_path not in hashed


# --- resume-enhancement guards ---------------------------------------------


def test_resume_enhancement_rejects_a_non_paused_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_config(tmp_path, with_fixture=False)
    subtitle_report = _subtitle_report(tmp_path, plan)
    part_id = plan.source_artifacts[0].source_id
    _, terminal = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["enhance", plan.plan_id, subtitle_report.report_id, "--part", part_id, "--json"],
    )
    terminal_report_id = terminal["report"]["report_id"]

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        [
            "resume-enhancement",
            terminal_report_id,
            "--decision",
            "resource_configuration_changed",
            "--json",
        ],
    )

    assert code == 2
    assert response["status"] == "error"
    assert response["reason"] == "enhancement_resume_invalid"


def test_resume_enhancement_requires_an_explicit_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _install_config(tmp_path, with_fixture=False)
    subtitle_report = _subtitle_report(tmp_path, plan)
    part_id = plan.source_artifacts[0].source_id
    _, terminal = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["enhance", plan.plan_id, subtitle_report.report_id, "--part", part_id, "--json"],
    )

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        ["resume-enhancement", terminal["report"]["report_id"], "--json"],
    )

    assert code == 2
    assert response["reason"] == "enhancement_resume_invalid"
