"""Offline CLI contract for the first Phase 5 audio-analysis slice."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from video_content_pipeline import audio_analysis, cli
from video_content_pipeline.coverage import StreamCoverage
from video_content_pipeline.inspection import PlanInspectionEvidence
from video_content_pipeline.planning import (
    PlanState,
    RunPlan,
    create_plan_report,
    persist_plan_report,
)
from video_content_pipeline.probe import ProbeDocument
from video_content_pipeline.source import (
    SourceArtifact,
    calculate_disk_headroom,
    sha256_file,
)
from video_content_pipeline.subtitle_pipeline import (
    CandidateReportState,
    CandidateState,
    SubtitleCandidate,
    SubtitleCandidateReport,
    subtitle_rules_fingerprint,
)
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval


def _confirmed_plan(
    project_root: Path,
    audio_coverage: StreamCoverage | None = None,
    *,
    audio_coverages: tuple[tuple[int, StreamCoverage], ...] = (),
) -> RunPlan:
    if audio_coverage is not None and audio_coverages:
        raise ValueError("Specify one audio coverage form.")
    coverage_by_stream = (
        audio_coverages if audio_coverages else ((2, audio_coverage),) if audio_coverage else ()
    )
    media_path = project_root / "input" / "source" / "synthetic-media"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"phase-5-cli-contract-fixture")
    digest, byte_count = sha256_file(media_path)
    artifact = SourceArtifact(
        digest, digest, byte_count, media_path, origin_kind="synthetic_fixture"
    )
    evidence = PlanInspectionEvidence(
        source_id=artifact.source_id,
        structural_document=ProbeDocument(
            json.dumps(
                {
                    "streams": [
                        {"index": stream_index, "codec_type": "audio", "time_base": "1/1"}
                        for stream_index, _coverage in coverage_by_stream
                    ]
                }
            )
        ),
        coverage_document=ProbeDocument('{"packets": []}'),
        coverage_by_stream=coverage_by_stream,
        subtitle_tracks=(),
    )
    plan_report = create_plan_report(
        state=PlanState.READY_FOR_CONFIRMATION,
        source_artifacts=(artifact,),
        tools=(),
        planned_increment_bytes=0,
        configuration_fingerprint="phase-03-fixture",
        inspection_evidence=(evidence,),
    )
    persist_plan_report(plan_report, project_root / "plans")
    plan = RunPlan(
        plan_id="confirmed-phase-5-fixture-plan",
        report_id=plan_report.report_id,
        source_artifacts=(artifact,),
        tools=(),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint=plan_report.configuration_fingerprint,
    )
    plan_path = project_root / "plans" / plan.plan_id / "run-plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(
        json.dumps(plan.as_json(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return plan


def _retained_subtitle_report(
    project_root: Path,
    plan: RunPlan,
    raw_pts_cue_intervals: tuple[HalfOpenInterval, ...] = (),
) -> SubtitleCandidateReport:
    report_id = "1" * 32
    report_path = project_root / "work" / plan.source_artifacts[0].source_id / report_id
    report_path = report_path / "candidate-report.json"
    rules_path = project_root / "config" / "subtitle-rules.json"
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text(
        '{"schema_version": 1, "id": "phase-04-fixture-rules"}\n', encoding="utf-8"
    )
    source_artifact_path = report_path.parent / "source.vtt"
    readable_artifact_path = report_path.parent / "readable.vtt"
    source_candidate_path = report_path.parent / "source-candidate.json"
    source_artifact_path.parent.mkdir(parents=True)
    source_artifact_path.write_text("WEBVTT\n\n", encoding="utf-8")
    readable_artifact_path.write_text("WEBVTT\n\n", encoding="utf-8")
    source_candidate_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cues": [
                    {
                        "source_ordinal": ordinal,
                        "text": f"Cue {ordinal}",
                        "raw_pts_interval": {
                            "start": {
                                "numerator": interval.start.numerator,
                                "denominator": interval.start.denominator,
                            },
                            "end": {
                                "numerator": interval.end.numerator,
                                "denominator": interval.end.denominator,
                            },
                        },
                    }
                    for ordinal, interval in enumerate(raw_pts_cue_intervals)
                ],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    report = SubtitleCandidateReport(
        report_id=report_id,
        plan_id=plan.plan_id,
        state=CandidateReportState.COMPLETED,
        subtitle_rules_fingerprint=subtitle_rules_fingerprint(project_root),
        candidates=(
            SubtitleCandidate(
                source_id=plan.source_artifacts[0].source_id,
                stream_index=1,
                state=CandidateState.VALID,
                source_candidate_path=source_candidate_path.as_posix(),
                source_candidate_sha256=sha256(source_candidate_path.read_bytes()).hexdigest(),
                source_vtt_path=source_artifact_path.as_posix(),
                readable_vtt_path=readable_artifact_path.as_posix(),
                raw_pts_cue_intervals=raw_pts_cue_intervals,
            ),
        ),
        diagnostics=(),
        report_path=report_path,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report.as_json(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return report


def _configure_cli(project_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "assert_runtime_policy", lambda: None)
    monkeypatch.setattr(cli, "assert_project_venv", lambda: object())
    monkeypatch.setattr(cli, "_project_root", lambda: project_root)


def test_analyze_audio_retains_a_model_acquisition_required_report_without_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    _configure_cli(tmp_path, monkeypatch)
    plan_path = tmp_path / "plans" / plan.plan_id / "run-plan.json"
    plan_before = plan_path.read_bytes()
    subtitles_before = subtitle_report.report_path.read_bytes()
    phase_4_artifacts_before = {
        path: path.read_bytes()
        for path in (
            Path(subtitle_report.candidates[0].source_vtt_path or ""),
            Path(subtitle_report.candidates[0].readable_vtt_path or ""),
        )
    }

    assert cli.main(["analyze-audio", plan.plan_id, subtitle_report.report_id, "--json"]) == 0
    response = json.loads(capsys.readouterr().out)

    assert response["status"] == "blocked"
    report = response["report"]
    assert report["state"] == "blocked"
    assert report["processing_authorization"]["state"] == "not_started"
    assert report["plan_id"] == plan.plan_id
    assert report["subtitle_report_id"] == subtitle_report.report_id
    assert [capability["state"] for capability in report["capabilities"]] == [
        "model_acquisition_required",
        "model_acquisition_required",
        "model_acquisition_required",
    ]
    assert [capability["capability"] for capability in report["capabilities"]] == [
        "vad",
        "forced_alignment",
        "diarization",
    ]
    assert report["guarantees"] == {
        "asr": "not_attempted",
        "model_acquisition": "not_attempted",
        "model_execution": "not_attempted",
        "network_access": "not_attempted",
        "outputs_publication": "not_attempted",
        "phase_4_artifact_mutation": "not_attempted",
        "run_plan_mutation": "not_attempted",
    }
    report_path = Path(report["report_path"])
    assert report_path.parent == Path(report["workspace_path"])
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    assert plan_path.read_bytes() == plan_before
    assert subtitle_report.report_path.read_bytes() == subtitles_before
    assert {
        path: path.read_bytes() for path in phase_4_artifacts_before
    } == phase_4_artifacts_before
    assert not (tmp_path / "outputs").exists()


def test_analyze_audio_auto_selects_a_unique_usable_audio_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(
        tmp_path,
        audio_coverage=StreamCoverage(
            coverage=HalfOpenInterval(ExactTime(0), ExactTime(1)), gaps=(), diagnostics=()
        ),
    )
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    _configure_cli(tmp_path, monkeypatch)

    assert cli.main(["analyze-audio", plan.plan_id, subtitle_report.report_id, "--json"]) == 0
    report = json.loads(capsys.readouterr().out)["report"]

    assert report["state"] == "blocked"
    assert report["analysis_audio_streams"][0]["stream_index"] == 2
    assert report["diagnostics"] == []
    assert [item["state"] for item in report["capabilities"]] == [
        "model_acquisition_required",
        "model_acquisition_required",
        "model_acquisition_required",
    ]


def test_analyze_audio_rejects_a_retained_subtitle_report_for_another_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    invalid_report = subtitle_report.as_json()
    invalid_report["plan_id"] = "another-confirmed-plan"
    subtitle_report.report_path.write_text(
        json.dumps(invalid_report, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    _configure_cli(tmp_path, monkeypatch)

    assert cli.main(["analyze-audio", plan.plan_id, subtitle_report.report_id, "--json"]) == 0
    response = json.loads(capsys.readouterr().out)

    assert response["status"] == "blocked"
    assert response["report"]["capabilities"] == []
    assert response["report"]["diagnostics"] == [
        {
            "reason": "subtitle_report_mismatch",
            "message": "Subtitle candidate report does not belong to this RunPlan.",
        }
    ]


def test_analyze_audio_does_not_read_source_artifact_before_model_availability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    original_sha256_file = audio_analysis.sha256_file
    hashed_paths: list[Path] = []

    def record_hash(path: Path) -> tuple[str, int]:
        hashed_paths.append(path)
        return original_sha256_file(path)

    monkeypatch.setattr(audio_analysis, "sha256_file", record_hash)
    _configure_cli(tmp_path, monkeypatch)

    assert cli.main(["analyze-audio", plan.plan_id, subtitle_report.report_id, "--json"]) == 0
    response = json.loads(capsys.readouterr().out)

    assert response["report"]["processing_authorization"]["state"] == "not_started"
    assert plan.source_artifacts[0].media_path not in hashed_paths


def test_analyze_audio_rejects_a_run_plan_with_changed_confirmation_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    plan_path = tmp_path / "plans" / plan.plan_id / "run-plan.json"
    invalid_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    invalid_plan["configuration_fingerprint"] = "changed-planning-configuration"
    plan_path.write_text(json.dumps(invalid_plan), encoding="utf-8")
    _configure_cli(tmp_path, monkeypatch)

    assert cli.main(["analyze-audio", plan.plan_id, subtitle_report.report_id, "--json"]) == 0
    response = json.loads(capsys.readouterr().out)

    assert response["report"]["capabilities"] == []
    assert response["report"]["diagnostics"][0]["reason"] == "run_plan_not_confirmed"


def test_analyze_audio_reports_non_acquiring_registered_capability_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    registry_path = tmp_path / "models" / "registry.json"
    registry_path.parent.mkdir()
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "models": [
                    {"capability": "vad", "status": "model_credential_gated"},
                    {"capability": "forced_alignment", "status": "model_unavailable"},
                    {"capability": "diarization", "status": "model_ineligible"},
                ],
            }
        ),
        encoding="utf-8",
    )
    _configure_cli(tmp_path, monkeypatch)

    assert cli.main(["analyze-audio", plan.plan_id, subtitle_report.report_id, "--json"]) == 0
    response = json.loads(capsys.readouterr().out)

    assert [capability["state"] for capability in response["report"]["capabilities"]] == [
        "model_credential_gated",
        "model_unavailable",
        "model_ineligible",
    ]
    assert response["report"]["guarantees"]["model_acquisition"] == "not_attempted"


def test_analyze_audio_retains_controlled_adapter_and_calibration_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(
        tmp_path,
        audio_coverage=StreamCoverage(
            coverage=HalfOpenInterval(ExactTime(0), ExactTime(1)), gaps=(), diagnostics=()
        ),
    )
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    fixture_path = tmp_path / "tests" / "fixtures" / "calibration" / "vad.json"
    fixture_path.parent.mkdir(parents=True)
    projection = {
        "schema_version": 1,
        "capability": "vad",
        "model_identity": {
            "asset_sha256": "a" * 64,
            "backend": "controlled-offline-adapter",
            "backend_version": "1.0.0",
            "precision": "fixture",
            "device_class": "fixture-cpu",
            "rules_fingerprint": "vad-rules-v1",
        },
        "result": {"segments": []},
    }
    fixture_path.write_text(
        json.dumps({"expected_projection": projection, "thresholds": {"speech_score": "0.5"}}),
        encoding="utf-8",
    )
    dependency_plan = tmp_path / "models" / "plans" / "controlled-vad.md"
    dependency_plan.parent.mkdir(parents=True)
    dependency_plan.write_text("# Controlled VAD dependency plan\n", encoding="utf-8")
    registry_path = tmp_path / "models" / "registry.json"
    registry_path.parent.mkdir(exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "candidates": [
                    {
                        "candidate_id": "controlled-vad",
                        "capability": "vad",
                        "official_source": {
                            "url": "https://example.invalid/controlled-vad",
                            "approved": True,
                        },
                        "license_approved": True,
                        "revision": "fixture-r1",
                        "asset_sha256": "a" * 64,
                        "offline_runtime": True,
                        "credential_required": False,
                        "telemetry": False,
                        "dependency_plan": "models/plans/controlled-vad.md",
                        "resource_estimate": {"high_bytes": 1024},
                        "execution_controls": {
                            "resource_measurement": {"peak_bytes": 512},
                            "unload_evidence": {"state": "released", "resident_bytes": 0},
                        },
                        "controlled_adapter": {
                            "adapter_version": "fixture-adapter-v1",
                            "raw_output": {"native_segments": []},
                            "projection": projection,
                        },
                        "calibration_evaluation": {
                            "schema_version": 1,
                            "reference_fixture": {
                                "path": "tests/fixtures/calibration/vad.json",
                                "sha256": sha256(fixture_path.read_bytes()).hexdigest(),
                            },
                            "evaluator_version": "fixture-evaluator-v1",
                        },
                    },
                    {
                        "candidate_id": "credential-alignment",
                        "capability": "forced_alignment",
                        "credential_required": True,
                    },
                    {
                        "candidate_id": "incomplete-diarization",
                        "capability": "diarization",
                    },
                    {
                        "candidate_id": "drifted-vad",
                        "capability": "vad",
                        "official_source": {
                            "url": "https://example.invalid/controlled-vad",
                            "approved": True,
                        },
                        "license_approved": True,
                        "revision": "fixture-r1",
                        "asset_sha256": "a" * 64,
                        "offline_runtime": True,
                        "credential_required": False,
                        "telemetry": False,
                        "dependency_plan": "models/plans/controlled-vad.md",
                        "resource_estimate": {"high_bytes": 1024},
                        "execution_controls": {
                            "resource_measurement": {"peak_bytes": 512},
                            "unload_evidence": {"state": "released", "resident_bytes": 0},
                        },
                        "controlled_adapter": {
                            "adapter_version": "fixture-adapter-v1",
                            "raw_output": {"native_segments": []},
                            "projection": {
                                **projection,
                                "model_identity": {
                                    **projection["model_identity"],
                                    "backend_version": "2.0.0",
                                },
                            },
                        },
                        "calibration_evaluation": {
                            "schema_version": 1,
                            "reference_fixture": {
                                "path": "tests/fixtures/calibration/vad.json",
                                "sha256": sha256(fixture_path.read_bytes()).hexdigest(),
                            },
                            "evaluator_version": "fixture-evaluator-v1",
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    _configure_cli(tmp_path, monkeypatch)

    assert cli.main(["analyze-audio", plan.plan_id, subtitle_report.report_id, "--json"]) == 0
    report = json.loads(capsys.readouterr().out)["report"]
    candidates = {
        candidate["candidate_id"]: candidate
        for capability in report["capabilities"]
        for candidate in capability["candidates"]
    }

    assert candidates["controlled-vad"]["state"] == "eligible"
    assert candidates["controlled-vad"]["adapter"]["state"] == "projected"
    assert candidates["controlled-vad"]["calibration"]["state"] == "qualified"
    assert (
        report["input_evidence"]["model_registry"]["sha256"]
        == sha256(registry_path.read_bytes()).hexdigest()
    )
    assert Path(candidates["controlled-vad"]["adapter"]["raw_output"]["path"]).exists()
    assert Path(candidates["controlled-vad"]["adapter"]["projection"]["path"]).exists()
    assert Path(candidates["controlled-vad"]["calibration"]["record"]["path"]).exists()
    assert Path(candidates["controlled-vad"]["adapter"]["adapter_version"]["path"]).exists()
    profile = json.loads(
        Path(candidates["controlled-vad"]["calibration"]["profile"]["path"]).read_text(
            encoding="utf-8"
        )
    )
    assert profile["model_identity"] == projection["model_identity"]
    assert candidates["credential-alignment"]["state"] == "blocked"
    assert candidates["incomplete-diarization"]["state"] == "unsupported"
    assert candidates["drifted-vad"]["calibration"]["state"] == "calibration_failed"
    assert candidates["drifted-vad"]["calibration"]["profile"] is None
    assert report["formal_evidence"] == []

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    controlled_vad = next(
        candidate
        for candidate in registry["candidates"]
        if candidate["candidate_id"] == "controlled-vad"
    )
    controlled_vad["resource_estimate"] = {
        "high_bytes": audio_analysis._MAX_MODEL_RESOURCE_BYTES + 1
    }
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    assert cli.main(["analyze-audio", plan.plan_id, subtitle_report.report_id, "--json"]) == 0
    resource_paused = json.loads(capsys.readouterr().out)["report"]

    assert resource_paused["state"] == "blocked"
    assert resource_paused["diagnostics"][0]["reason"] == "resource_envelope_exceeded"
    assert resource_paused["partial_analysis"] == {
        "missing_stage": "vad",
        "required_decision": {"reason": "resource_envelope_exceeded"},
    }


def test_analyze_audio_rejects_an_incomplete_controlled_adapter_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    dependency_plan = tmp_path / "models" / "plans" / "controlled-vad.md"
    dependency_plan.parent.mkdir(parents=True)
    dependency_plan.write_text("# Controlled VAD dependency plan\n", encoding="utf-8")
    registry_path = tmp_path / "models" / "registry.json"
    registry_path.parent.mkdir(exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "candidates": [
                    {
                        "candidate_id": "invalid-vad-output",
                        "capability": "vad",
                        "official_source": {
                            "url": "https://example.invalid/controlled-vad",
                            "approved": True,
                        },
                        "license_approved": True,
                        "revision": "fixture-r1",
                        "asset_sha256": "a" * 64,
                        "offline_runtime": True,
                        "credential_required": False,
                        "telemetry": False,
                        "dependency_plan": "models/plans/controlled-vad.md",
                        "resource_estimate": {"high_bytes": 1024},
                        "execution_controls": {
                            "resource_measurement": {"peak_bytes": 512},
                            "unload_evidence": {"state": "released", "resident_bytes": 0},
                        },
                        "controlled_adapter": {
                            "adapter_version": "fixture-adapter-v1",
                            "raw_output": {"native_segments": []},
                            "projection": {"schema_version": 1, "capability": "vad"},
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _configure_cli(tmp_path, monkeypatch)

    assert cli.main(["analyze-audio", plan.plan_id, subtitle_report.report_id, "--json"]) == 0
    report = json.loads(capsys.readouterr().out)["report"]
    candidate = report["capabilities"][0]["candidates"][0]

    assert candidate["state"] == "eligible"
    assert candidate["adapter"]["state"] == "model_output_invalid"
    assert Path(candidate["adapter"]["raw_output"]["path"]).exists()
    assert candidate["adapter"]["projection"] is None
    assert candidate["calibration"]["state"] == "not_evaluated"
    assert report["formal_evidence"] == []


def test_analyze_audio_publishes_calibrated_vad_and_anonymous_speaker_turn_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def interval(start: int, end: int) -> HalfOpenInterval:
        return HalfOpenInterval(ExactTime(start), ExactTime(end))

    audio_coverage = StreamCoverage(
        coverage=interval(0, 10), gaps=(interval(4, 5),), diagnostics=()
    )
    plan = _confirmed_plan(
        tmp_path,
        audio_coverages=((2, audio_coverage), (3, audio_coverage)),
    )
    subtitle_report = _retained_subtitle_report(tmp_path, plan, (interval(0, 1),))
    source_candidate_path = Path(subtitle_report.candidates[0].source_candidate_path or "")
    source_candidate_before = source_candidate_path.read_bytes()
    derivative_path = tmp_path / "work" / "controlled-vad.derivative"
    derivative_path.parent.mkdir(parents=True, exist_ok=True)
    derivative_path.write_bytes(b"controlled-vad-analysis-audio")
    derivative_evidence = {
        "path": derivative_path.as_posix(),
        "sha256": sha256(derivative_path.read_bytes()).hexdigest(),
        "byte_count": derivative_path.stat().st_size,
    }
    coverage_evidence_sha256 = audio_analysis._sha256_json(
        audio_analysis._stream_coverage_as_json(audio_coverage)
    )
    projection = {
        "schema_version": 1,
        "capability": "vad",
        "model_identity": {
            "asset_sha256": "b" * 64,
            "backend": "controlled-offline-adapter",
            "backend_version": "1.0.0",
            "precision": "fixture",
            "device_class": "fixture-cpu",
            "rules_fingerprint": "vad-rules-v1",
        },
        "result": {
            "parts": [
                {
                    "source_id": plan.source_artifacts[0].source_id,
                    "stream_index": 2,
                    "source_time_mapping": {
                        "schema_version": 1,
                        "coordinate": "raw_pts_identity",
                        "structural_evidence_sha256": audio_analysis._sha256_json(
                            json.dumps(
                                {
                                    "streams": [
                                        {"index": 2, "codec_type": "audio", "time_base": "1/1"},
                                        {"index": 3, "codec_type": "audio", "time_base": "1/1"},
                                    ]
                                }
                            )
                        ),
                        "coverage_evidence_sha256": coverage_evidence_sha256,
                        "derivative_evidence": derivative_evidence,
                    },
                    "segments": [
                        {
                            "start": {"numerator": 0, "denominator": 1},
                            "end": {"numerator": 3, "denominator": 1},
                            "state": "speech_likely",
                        },
                        {
                            "start": {"numerator": 5, "denominator": 1},
                            "end": {"numerator": 8, "denominator": 1},
                            "state": "non_speech",
                        },
                    ],
                }
            ]
        },
    }
    fixture_path = tmp_path / "tests" / "fixtures" / "calibration" / "vad.json"
    fixture_path.parent.mkdir(parents=True)
    fixture_path.write_text(
        json.dumps(
            {
                "expected_projection": projection,
                "thresholds": {
                    "uncovered_speech_duration": {"numerator": 1, "denominator": 1},
                    "long_silence_duration": {"numerator": 2, "denominator": 1},
                },
            }
        ),
        encoding="utf-8",
    )
    alignment_projection = {
        "schema_version": 1,
        "capability": "forced_alignment",
        "model_identity": {
            "asset_sha256": "c" * 64,
            "backend": "controlled-offline-adapter",
            "backend_version": "1.0.0",
            "precision": "fixture",
            "device_class": "fixture-cpu",
            "rules_fingerprint": "alignment-rules-v1",
        },
        "result": {
            "parts": [
                {
                    "source_id": plan.source_artifacts[0].source_id,
                    "stream_index": 2,
                    "language": "en",
                    "source_time_mapping": projection["result"]["parts"][0]["source_time_mapping"],
                    "cues": [
                        {
                            "source_ordinal": 0,
                            "text": "Cue 0",
                            "start": {"numerator": 0, "denominator": 1},
                            "end": {"numerator": 1, "denominator": 1},
                            "confidence": 0.9,
                        }
                    ],
                }
            ]
        },
    }
    alignment_fixture_path = tmp_path / "tests" / "fixtures" / "calibration" / "alignment.json"
    alignment_fixture_path.write_text(
        json.dumps(
            {
                "expected_projection": alignment_projection,
                "thresholds": {
                    "minimum_confidence": 0.8,
                    "duration_rules": {
                        "en": {
                            "minimum_duration": {"numerator": 1, "denominator": 2},
                            "maximum_duration": {"numerator": 3, "denominator": 1},
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    diarization_projection = {
        "schema_version": 1,
        "capability": "diarization",
        "model_identity": {
            "asset_sha256": "d" * 64,
            "backend": "controlled-offline-adapter",
            "backend_version": "1.0.0",
            "precision": "fixture",
            "device_class": "fixture-cpu",
            "rules_fingerprint": "diarization-rules-v1",
        },
        "result": {
            "parts": [
                {
                    "source_id": plan.source_artifacts[0].source_id,
                    "stream_index": 2,
                    "source_time_mapping": projection["result"]["parts"][0]["source_time_mapping"],
                    "turns": [
                        {
                            "cluster_id": "alpha",
                            "start": {"numerator": 0, "denominator": 1},
                            "end": {"numerator": 2, "denominator": 1},
                            "confidence": 0.9,
                        },
                        {
                            "cluster_id": "bravo",
                            "start": {"numerator": 1, "denominator": 1},
                            "end": {"numerator": 3, "denominator": 1},
                            "confidence": 0.8,
                        },
                        {
                            "cluster_id": "charlie",
                            "start": {"numerator": 3, "denominator": 1},
                            "end": {"numerator": 4, "denominator": 1},
                            "confidence": 0.9,
                        },
                        {
                            "cluster_id": "delta",
                            "start": {"numerator": 5, "denominator": 1},
                            "end": {"numerator": 6, "denominator": 1},
                            "confidence": 0.9,
                        },
                    ],
                    "role_candidates": [
                        {
                            "cluster_id": "alpha",
                            "role": "host",
                            "subtitle_text": {"source_ordinal": 0, "text": "Cue 0"},
                        },
                    ],
                }
            ]
        },
    }
    diarization_fixture_path = tmp_path / "tests" / "fixtures" / "calibration" / "diarization.json"
    diarization_fixture_path.write_text(
        json.dumps(
            {
                "expected_projection": diarization_projection,
                "thresholds": {"minimum_confidence": 0.8},
            }
        ),
        encoding="utf-8",
    )
    dependency_plan = tmp_path / "models" / "plans" / "controlled-vad.md"
    dependency_plan.parent.mkdir(parents=True)
    dependency_plan.write_text("# Controlled VAD dependency plan\n", encoding="utf-8")
    registry_path = tmp_path / "models" / "registry.json"
    registry_path.parent.mkdir(exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "candidates": [
                    {
                        "candidate_id": "controlled-vad",
                        "capability": "vad",
                        "official_source": {"url": "https://example.invalid/vad", "approved": True},
                        "license_approved": True,
                        "revision": "fixture-r1",
                        "asset_sha256": "b" * 64,
                        "offline_runtime": True,
                        "credential_required": False,
                        "telemetry": False,
                        "dependency_plan": "models/plans/controlled-vad.md",
                        "resource_estimate": {"high_bytes": 1024},
                        "execution_controls": {
                            "resource_measurement": {"peak_bytes": 512},
                            "unload_evidence": {"state": "released", "resident_bytes": 0},
                        },
                        "controlled_adapter": {
                            "adapter_version": "fixture-adapter-v1",
                            "raw_output": {"native_segments": []},
                            "projection": projection,
                        },
                        "calibration_evaluation": {
                            "schema_version": 1,
                            "reference_fixture": {
                                "path": "tests/fixtures/calibration/vad.json",
                                "sha256": sha256(fixture_path.read_bytes()).hexdigest(),
                            },
                            "evaluator_version": "fixture-evaluator-v1",
                        },
                    },
                    {
                        "candidate_id": "controlled-alignment",
                        "capability": "forced_alignment",
                        "official_source": {
                            "url": "https://example.invalid/alignment",
                            "approved": True,
                        },
                        "license_approved": True,
                        "revision": "fixture-r1",
                        "asset_sha256": "c" * 64,
                        "offline_runtime": True,
                        "credential_required": False,
                        "telemetry": False,
                        "dependency_plan": "models/plans/controlled-vad.md",
                        "resource_estimate": {"high_bytes": 1024},
                        "execution_controls": {
                            "resource_measurement": {"peak_bytes": 512},
                            "unload_evidence": {"state": "released", "resident_bytes": 0},
                        },
                        "controlled_adapter": {
                            "adapter_version": "fixture-adapter-v1",
                            "raw_output": {"native_cues": []},
                            "projection": alignment_projection,
                        },
                        "calibration_evaluation": {
                            "schema_version": 1,
                            "reference_fixture": {
                                "path": "tests/fixtures/calibration/alignment.json",
                                "sha256": sha256(alignment_fixture_path.read_bytes()).hexdigest(),
                            },
                            "evaluator_version": "fixture-evaluator-v1",
                        },
                    },
                    {
                        "candidate_id": "controlled-diarization",
                        "capability": "diarization",
                        "official_source": {
                            "url": "https://example.invalid/diarization",
                            "approved": True,
                        },
                        "license_approved": True,
                        "revision": "fixture-r1",
                        "asset_sha256": "d" * 64,
                        "offline_runtime": True,
                        "credential_required": False,
                        "telemetry": False,
                        "dependency_plan": "models/plans/controlled-vad.md",
                        "resource_estimate": {"high_bytes": 1024},
                        "execution_controls": {
                            "resource_measurement": {"peak_bytes": 512},
                            "unload_evidence": {"state": "released", "resident_bytes": 0},
                        },
                        "controlled_adapter": {
                            "adapter_version": "fixture-adapter-v1",
                            "raw_output": {"native_turns": []},
                            "projection": diarization_projection,
                        },
                        "calibration_evaluation": {
                            "schema_version": 1,
                            "reference_fixture": {
                                "path": "tests/fixtures/calibration/diarization.json",
                                "sha256": sha256(diarization_fixture_path.read_bytes()).hexdigest(),
                            },
                            "evaluator_version": "fixture-evaluator-v1",
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    _configure_cli(tmp_path, monkeypatch)

    assert cli.main(["analyze-audio", plan.plan_id, subtitle_report.report_id, "--json"]) == 0
    stream_selection_paused = json.loads(capsys.readouterr().out)["report"]

    assert stream_selection_paused["state"] == "partial"
    assert stream_selection_paused["partial_analysis"] == {
        "missing_stage": "vad",
        "required_decision": {"reason": "awaiting_audio_stream_selection"},
    }
    assert stream_selection_paused["analysis_audio_streams"] == []

    assert (
        cli.main(
            [
                "resume-audio-analysis",
                stream_selection_paused["report_id"],
                "--audio-stream",
                f"{plan.source_artifacts[0].source_id}=2",
                "--diarization-candidate",
                "controlled-diarization",
                "--json",
            ]
        )
        == 0
    )
    stream_selection_resumed = json.loads(capsys.readouterr().out)["report"]

    assert stream_selection_resumed["state"] == "complete"
    assert stream_selection_resumed["analysis_audio_streams"][0]["stream_index"] == 2

    assert (
        cli.main(
            [
                "analyze-audio",
                plan.plan_id,
                subtitle_report.report_id,
                "--audio-stream",
                f"{plan.source_artifacts[0].source_id}=2",
                "--json",
            ]
        )
        == 0
    )
    unselected_report = json.loads(capsys.readouterr().out)["report"]
    assert [evidence["capability"] for evidence in unselected_report["formal_evidence"]] == [
        "vad",
        "forced_alignment",
    ]
    assert unselected_report["diagnostics"] == [
        {
            "reason": "diarization_model_selection_required",
            "message": "A calibrated diarization candidate requires explicit user selection.",
        }
    ]
    assert unselected_report["partial_analysis"] == {
        "missing_stage": "diarization",
        "required_decision": {"reason": "diarization_model_selection_required"},
    }

    assert (
        cli.main(
            [
                "resume-audio-analysis",
                unselected_report["report_id"],
                "--diarization-candidate",
                "controlled-diarization",
                "--role-metadata",
                f"{plan.source_artifacts[0].source_id}=bravo=guest",
                "--json",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)["report"]

    assert report["diagnostics"] == []
    assert report["state"] == "complete"
    assert (
        report["input_evidence"]["resumed_from_report"]["path"] == unselected_report["report_path"]
    )
    assert report["processing_authorization"]["state"] == "approved"
    assert report["analysis_audio_streams"][0]["source_id"] == plan.source_artifacts[0].source_id
    assert report["analysis_audio_streams"][0]["stream_index"] == 2
    assert (
        report["analysis_audio_streams"][0]["coverage_evidence_sha256"] == coverage_evidence_sha256
    )
    vad = report["formal_evidence"][0]
    assert vad["capability"] == "vad"
    assert [item["state"] for item in vad["parts"][0]["voice_activity_intervals"]] == [
        "speech_likely",
        "indeterminate",
        "non_speech",
        "indeterminate",
    ]
    assert vad["parts"][0]["uncovered_speech_risks"] == [
        {
            "interval": {
                "start": {"numerator": 1, "denominator": 1},
                "end": {"numerator": 3, "denominator": 1},
            },
            "elevated": True,
            "asr_planning_recommendation": "required",
        }
    ]
    assert [item["interval"] for item in vad["parts"][0]["audio_state_indeterminate"]] == [
        {
            "start": {"numerator": 3, "denominator": 1},
            "end": {"numerator": 4, "denominator": 1},
        },
        {
            "start": {"numerator": 4, "denominator": 1},
            "end": {"numerator": 5, "denominator": 1},
        },
        {
            "start": {"numerator": 8, "denominator": 1},
            "end": {"numerator": 10, "denominator": 1},
        },
    ]
    assert vad["parts"][0]["long_silences"] == [
        {
            "interval": {
                "start": {"numerator": 5, "denominator": 1},
                "end": {"numerator": 8, "denominator": 1},
            }
        }
    ]
    alignment = report["formal_evidence"][1]
    assert alignment["capability"] == "forced_alignment"
    timing_view_path = Path(alignment["parts"][0]["timing_view"]["path"])
    timing_view = json.loads(timing_view_path.read_text(encoding="utf-8"))
    assert timing_view["state"] == "adopted"
    assert timing_view["cues"] == [
        {
            "source_ordinal": 0,
            "text": "Cue 0",
            "original_raw_pts_interval": {
                "start": {"numerator": 0, "denominator": 1},
                "end": {"numerator": 1, "denominator": 1},
            },
            "proposed_raw_pts_interval": {
                "start": {"numerator": 0, "denominator": 1},
                "end": {"numerator": 1, "denominator": 1},
            },
            "adopted_raw_pts_interval": {
                "start": {"numerator": 0, "denominator": 1},
                "end": {"numerator": 1, "denominator": 1},
            },
            "adopted": True,
            "reason": "adopted",
            "global_reason": None,
            "vad_indeterminate_risk": False,
        }
    ]
    diarization = report["formal_evidence"][2]
    assert diarization["capability"] == "diarization"
    speaker_part = diarization["parts"][0]
    assert speaker_part["speaker_turns"] == [
        {
            "speaker_label": "part-01:speaker-01",
            "raw_pts_interval": {
                "start": {"numerator": 0, "denominator": 1},
                "end": {"numerator": 2, "denominator": 1},
            },
            "confidence": 0.9,
        },
        {
            "speaker_label": "part-01:speaker-02",
            "raw_pts_interval": {
                "start": {"numerator": 1, "denominator": 1},
                "end": {"numerator": 3, "denominator": 1},
            },
            "confidence": 0.8,
        },
    ]
    assert speaker_part["diarization_vad_conflicts"] == [
        {
            "candidate_speaker_label": "part-01:speaker-03",
            "raw_pts_interval": {
                "start": {"numerator": 3, "denominator": 1},
                "end": {"numerator": 4, "denominator": 1},
            },
            "confidence": 0.9,
            "reason": "diarization_vad_conflict",
            "vad_states": ["indeterminate"],
        },
        {
            "candidate_speaker_label": "part-01:speaker-04",
            "raw_pts_interval": {
                "start": {"numerator": 5, "denominator": 1},
                "end": {"numerator": 6, "denominator": 1},
            },
            "confidence": 0.9,
            "reason": "diarization_vad_conflict",
            "vad_states": ["non_speech"],
        },
    ]
    assert speaker_part["role_candidates"][0] == {
        "speaker_label": "part-01:speaker-01",
        "role": "host",
        "evidence": {"kind": "subtitle_text", "source_ordinal": 0, "text": "Cue 0"},
    }
    metadata_role = speaker_part["role_candidates"][1]
    assert metadata_role["speaker_label"] == "part-01:speaker-02"
    assert metadata_role["role"] == "guest"
    assert metadata_role["evidence"]["kind"] == "user_metadata"
    assert metadata_role["evidence"]["entry_id"] == audio_analysis._sha256_json(
        {
            "source_id": plan.source_artifacts[0].source_id,
            "cluster_id": "bravo",
            "role": "guest",
        }
    )
    metadata_record = metadata_role["evidence"]["record"]
    assert metadata_record["path"] == (
        report["workspace_path"] + "/diarization/controlled-diarization/user-role-metadata.json"
    )
    assert Path(metadata_record["path"]).is_file()
    assert json.loads(Path(metadata_record["path"]).read_text(encoding="utf-8"))["records"] == [
        {
            "entry_id": metadata_role["evidence"]["entry_id"],
            "source_id": plan.source_artifacts[0].source_id,
            "cluster_id": "bravo",
            "role": "guest",
        }
    ]
    assert source_candidate_path.read_bytes() == source_candidate_before
    assert not (tmp_path / "outputs").exists()

    assert [stage["capability"] for stage in report["stage_execution"]] == [
        "vad",
        "forced_alignment",
        "diarization",
    ]
    assert all(stage["state"] == "completed" for stage in report["stage_execution"])
    assert all(Path(stage["output"]["path"]).is_file() for stage in report["stage_execution"])
    assert all(
        Path(stage["resource_measurement"]["path"]).is_file() for stage in report["stage_execution"]
    )
    assert all(
        Path(stage["unload_evidence"]["path"]).is_file() for stage in report["stage_execution"]
    )
    assert report["partial_analysis"] is None

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    alignment_candidate = next(
        candidate
        for candidate in registry["candidates"]
        if candidate["capability"] == "forced_alignment"
    )
    alignment_candidate["resource_estimate"] = {
        "high_bytes": audio_analysis._MAX_MODEL_RESOURCE_BYTES + 1
    }
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    assert (
        cli.main(
            [
                "analyze-audio",
                plan.plan_id,
                subtitle_report.report_id,
                "--audio-stream",
                f"{plan.source_artifacts[0].source_id}=2",
                "--json",
            ]
        )
        == 0
    )
    resource_paused = json.loads(capsys.readouterr().out)["report"]
    assert resource_paused["state"] == "partial"
    assert [entry["capability"] for entry in resource_paused["formal_evidence"]] == ["vad"]
    assert resource_paused["partial_analysis"] == {
        "missing_stage": "forced_alignment",
        "required_decision": {"reason": "resource_envelope_exceeded"},
    }

    alignment_candidate["resource_estimate"] = {"high_bytes": 1024}
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    assert (
        cli.main(
            [
                "resume-audio-analysis",
                resource_paused["report_id"],
                "--decision",
                "resource_configuration_changed",
                "--json",
            ]
        )
        == 0
    )
    resource_resumed = json.loads(capsys.readouterr().out)["report"]
    assert (
        resource_resumed["input_evidence"]["resumed_from_report"]["path"]
        == resource_paused["report_path"]
    )
    assert resource_resumed["input_evidence"]["resumption_decision"] == (
        "resource_configuration_changed"
    )
    assert [entry["capability"] for entry in resource_resumed["formal_evidence"]] == [
        "vad",
        "forced_alignment",
    ]
    assert resource_resumed["formal_evidence"][0] == resource_paused["formal_evidence"][0]
    assert resource_resumed["stage_execution"][0] == resource_paused["stage_execution"][0]

    vad_candidate = next(
        candidate for candidate in registry["candidates"] if candidate["capability"] == "vad"
    )
    vad_candidate["execution_controls"].pop("unload_evidence")
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    assert (
        cli.main(
            [
                "analyze-audio",
                plan.plan_id,
                subtitle_report.report_id,
                "--audio-stream",
                f"{plan.source_artifacts[0].source_id}=2",
                "--json",
            ]
        )
        == 0
    )
    release_paused = json.loads(capsys.readouterr().out)["report"]
    assert release_paused["state"] == "partial"
    assert [entry["capability"] for entry in release_paused["formal_evidence"]] == ["vad"]
    assert release_paused["partial_analysis"] == {
        "missing_stage": "forced_alignment",
        "required_decision": {"reason": "model_release_unverified"},
    }

    plan_report_path = tmp_path / "plans" / "reports" / plan.report_id / "plan-report.json"
    original_plan_report = plan_report_path.read_bytes()
    drifted_plan_report = json.loads(plan_report_path.read_text(encoding="utf-8"))
    drifted_plan_report["inspection_evidence"][0]["stream_coverage"][0]["gaps"] = []
    plan_report_path.write_text(
        json.dumps(drifted_plan_report, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )

    assert (
        cli.main(
            [
                "resume-audio-analysis",
                resource_paused["report_id"],
                "--decision",
                "resource_configuration_changed",
                "--json",
            ]
        )
        == 0
    )
    selection_drift = json.loads(capsys.readouterr().out)["report"]

    assert selection_drift["state"] == "blocked"
    assert selection_drift["formal_evidence"] == []
    assert selection_drift["diagnostics"] == [
        {
            "reason": "analysis_audio_stream_selection_changed",
            "message": "Analysis audio stream evidence changed after the prior report.",
        }
    ]

    plan_report_path.write_bytes(original_plan_report)
    assert (
        cli.main(
            [
                "resume-audio-analysis",
                release_paused["report_id"],
                "--decision",
                "model_release_verified",
                "--json",
            ]
        )
        == 0
    )
    release_resumed = json.loads(capsys.readouterr().out)["report"]

    assert release_resumed["state"] == "partial"
    assert [entry["capability"] for entry in release_resumed["formal_evidence"]] == [
        "vad",
        "forced_alignment",
    ]
    assert release_resumed["stage_execution"][0]["state"] == "release_unverified"
    assert release_resumed["input_evidence"]["resumption_decision"] == "model_release_verified"

    assert (
        cli.main(
            [
                "resume-audio-analysis",
                release_resumed["report_id"],
                "--diarization-candidate",
                "controlled-diarization",
                "--json",
            ]
        )
        == 0
    )
    completed_after_release = json.loads(capsys.readouterr().out)["report"]

    assert completed_after_release["state"] == "complete"
    assert [entry["capability"] for entry in completed_after_release["formal_evidence"]] == [
        "vad",
        "forced_alignment",
        "diarization",
    ]
