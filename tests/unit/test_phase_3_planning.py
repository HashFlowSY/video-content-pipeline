from __future__ import annotations

import json
from collections import namedtuple
from fractions import Fraction
from pathlib import Path

import pytest

import video_content_pipeline.planning as planning
from video_content_pipeline.coverage import StreamCoverage
from video_content_pipeline.external_tools import PinnedExternalTool
from video_content_pipeline.inspection import PlanInspectionEvidence, SubtitleTrackCandidate
from video_content_pipeline.planning import (
    PLAN_MODEL_CAPABILITIES,
    CapabilityPeakMemory,
    DecodeThroughputProfile,
    PlanningError,
    PlanState,
    ThreePointEstimate,
    build_full_decode_command,
    confirm_run_plan,
    confirmed_plan_matches,
    create_plan_report,
    estimate_full_decode,
    estimate_plan_peak_memory,
    load_decode_measurements,
    load_decode_throughput_profile,
    load_plan_peak_memory_estimate,
    load_plan_report,
    load_run_plan,
    perform_full_decode_validation,
    persist_plan_report,
    plan_model_statuses,
    planning_configuration_fingerprint,
    record_decode_measurement,
    revalidate_report,
)
from video_content_pipeline.probe import ProbeDocument
from video_content_pipeline.prototype import DeviceBaseline
from video_content_pipeline.run_choices import (
    COLLECTION_SCOPE,
    KEY_ASR_MODE,
    KEY_ENHANCEMENT_CUE,
    KEY_VISUAL_TEXT_ENABLED,
    STAGE_ENHANCEMENT,
    STAGE_RUN,
    AsrMode,
    ChoiceProvenance,
    RunChoice,
    RunPlanChoices,
    missing_required_choices,
)
from video_content_pipeline.source import SourceArtifact, sha256_file
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval


def _artifact(tmp_path: Path) -> SourceArtifact:
    media = tmp_path / "input" / "hash" / "media"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"media")
    digest, byte_count = sha256_file(media)
    return SourceArtifact(digest, digest, byte_count, media)


def _inspection(artifact: SourceArtifact) -> PlanInspectionEvidence:
    return PlanInspectionEvidence(
        source_id=artifact.source_id,
        structural_document=ProbeDocument('{"streams": []}'),
        coverage_document=ProbeDocument('{"packets": []}'),
        coverage_by_stream=(),
        subtitle_tracks=(),
    )


def _write_planning_configuration(project_root: Path) -> str:
    config_root = project_root / "config"
    config_root.mkdir()
    (config_root / "decode-throughput-profiles.json").write_text(
        '{"profiles": []}\n', encoding="utf-8"
    )
    (config_root / "tools.json").write_text('{"tools": []}\n', encoding="utf-8")
    return planning_configuration_fingerprint(project_root)


def test_low_confidence_profile_estimate_has_ordered_three_points() -> None:
    estimate = estimate_full_decode(
        Fraction(120, 1),
        DecodeThroughputProfile("v1", Fraction(8), Fraction(3), Fraction(1)),
    )

    assert estimate.optimistic_seconds == 15
    assert estimate.likely_seconds == 40
    assert estimate.conservative_seconds == 120
    assert estimate.confidence == "low"


def test_matching_observed_decode_history_replaces_the_low_confidence_profile(
    tmp_path: Path,
) -> None:
    history_path = tmp_path / "plans" / "decode-throughput-history.json"
    record_decode_measurement(history_path, "source-id", 17)
    measurement = load_decode_measurements(history_path)[0]

    estimate = estimate_full_decode(
        Fraction(120, 1),
        DecodeThroughputProfile("v1", Fraction(8), Fraction(3), Fraction(1)),
        matching_measurement=measurement,
    )

    assert estimate == ThreePointEstimate(
        optimistic_seconds=17,
        likely_seconds=17,
        conservative_seconds=17,
        confidence="observed",
        basis="decode-history:source-id",
    )


def test_report_and_plan_are_persisted_under_separate_ids(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    configuration_fingerprint = _write_planning_configuration(tmp_path)
    report = create_plan_report(
        state=PlanState.READY_FOR_CONFIRMATION,
        source_artifacts=(artifact,),
        tools=(),
        planned_increment_bytes=artifact.byte_count,
        configuration_fingerprint=configuration_fingerprint,
        inspection_evidence=(_inspection(artifact),),
    )

    report_path = persist_plan_report(report, tmp_path / "plans")
    loaded = load_plan_report(report_path)
    plan = confirm_run_plan(report, tmp_path, tmp_path / "plans")

    assert report_path == tmp_path / "plans" / "reports" / report.report_id / "plan-report.json"
    assert loaded == report
    assert (tmp_path / "plans" / plan.plan_id / "run-plan.json").is_file()
    assert plan.report_id == report.report_id
    assert plan.tools == report.tools
    assert plan.disk_headroom == report.disk_headroom
    assert plan.inspection_evidence_fingerprints[0][0] == artifact.source_id
    assert len(plan.inspection_evidence_fingerprints[0][1]) == 64


def test_confirmation_fixes_front_loaded_run_choices(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    configuration_fingerprint = _write_planning_configuration(tmp_path)
    report = create_plan_report(
        state=PlanState.READY_FOR_CONFIRMATION,
        source_artifacts=(artifact,),
        tools=(),
        planned_increment_bytes=artifact.byte_count,
        configuration_fingerprint=configuration_fingerprint,
        inspection_evidence=(_inspection(artifact),),
    )
    choices = RunPlanChoices.build(
        (
            RunChoice(
                STAGE_RUN, KEY_ASR_MODE, COLLECTION_SCOPE, "full_asr", ChoiceProvenance.USER_CHOSEN
            ),
            RunChoice(
                STAGE_RUN,
                KEY_VISUAL_TEXT_ENABLED,
                COLLECTION_SCOPE,
                "false",
                ChoiceProvenance.USER_CHOSEN,
            ),
        )
    )

    plan = confirm_run_plan(report, tmp_path, tmp_path / "plans", run_choices=choices)

    assert plan.run_choices.asr_mode() is AsrMode.FULL_ASR
    assert plan.run_choices.visual_text_enabled() is False
    # The plan and the report it revalidates against must agree on run_choices, or
    # source revalidation fails the run with plan_confirmation_drift. Re-issuing the
    # report with the choices is what keeps confirmed_plan_matches true.
    reissued = load_plan_report(
        tmp_path / "plans" / "reports" / plan.report_id / "plan-report.json"
    )
    assert reissued.run_choices == plan.run_choices
    assert confirmed_plan_matches(reissued, plan)


def test_confirmation_rejects_incomplete_run_choices(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    configuration_fingerprint = _write_planning_configuration(tmp_path)
    report = create_plan_report(
        state=PlanState.READY_FOR_CONFIRMATION,
        source_artifacts=(artifact,),
        tools=(),
        planned_increment_bytes=artifact.byte_count,
        configuration_fingerprint=configuration_fingerprint,
        inspection_evidence=(_inspection(artifact),),
    )
    # asr_mode without the required visual-text toggle is an incomplete selection.
    incomplete = RunPlanChoices.build(
        (
            RunChoice(
                STAGE_RUN, KEY_ASR_MODE, COLLECTION_SCOPE, "full_asr", ChoiceProvenance.USER_CHOSEN
            ),
        )
    )

    with pytest.raises(PlanningError) as error:
        confirm_run_plan(report, tmp_path, tmp_path / "plans", run_choices=incomplete)
    assert error.value.reason == "run_choices_incomplete"


def test_confirmation_rejects_a_changed_planning_configuration(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    configuration_fingerprint = _write_planning_configuration(tmp_path)
    report = create_plan_report(
        state=PlanState.READY_FOR_CONFIRMATION,
        source_artifacts=(artifact,),
        tools=(),
        planned_increment_bytes=artifact.byte_count,
        configuration_fingerprint=configuration_fingerprint,
        inspection_evidence=(_inspection(artifact),),
    )
    (tmp_path / "config" / "tools.json").write_text('{"tools": ["changed"]}\n', encoding="utf-8")

    diagnostics = revalidate_report(report, tmp_path)

    assert [diagnostic.reason for diagnostic in diagnostics] == ["planning_configuration_changed"]
    with pytest.raises(PlanningError, match="planning_configuration_changed") as error:
        confirm_run_plan(report, tmp_path, tmp_path / "plans")
    assert error.value.reason == "report_stale"


def test_confirmation_rejects_a_changed_source_artifact(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    report = create_plan_report(
        state=PlanState.READY_FOR_CONFIRMATION,
        source_artifacts=(artifact,),
        tools=(),
        planned_increment_bytes=artifact.byte_count,
        configuration_fingerprint=_write_planning_configuration(tmp_path),
        inspection_evidence=(_inspection(artifact),),
    )
    artifact.media_path.write_bytes(b"changed media")

    diagnostics = revalidate_report(report, tmp_path)

    assert [diagnostic.reason for diagnostic in diagnostics] == ["source_artifact_changed"]
    with pytest.raises(PlanningError, match="source_artifact_changed"):
        confirm_run_plan(report, tmp_path, tmp_path / "plans")


def test_stale_confirmation_retains_a_child_report_and_expires_its_parent(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    report = create_plan_report(
        state=PlanState.READY_FOR_CONFIRMATION,
        source_artifacts=(artifact,),
        tools=(),
        planned_increment_bytes=artifact.byte_count,
        configuration_fingerprint=_write_planning_configuration(tmp_path),
        inspection_evidence=(_inspection(artifact),),
    )
    plans_root = tmp_path / "plans"
    report_path = persist_plan_report(report, plans_root)
    persisted_report = report_path.read_text(encoding="utf-8")
    artifact.media_path.write_bytes(b"changed media")

    with pytest.raises(PlanningError, match="source_artifact_changed"):
        confirm_run_plan(report, tmp_path, plans_root)

    stale_paths = list((plans_root / "reports").glob("*/plan-report.json"))
    stale_paths.remove(report_path)
    assert len(stale_paths) == 1
    stale = load_plan_report(stale_paths[0])
    assert stale.state == PlanState.BLOCKED
    assert stale.parent_report_id == report.report_id
    assert [diagnostic.reason for diagnostic in stale.diagnostics] == ["source_artifact_changed"]
    assert report_path.read_text(encoding="utf-8") == persisted_report

    artifact.media_path.write_bytes(b"media")
    with pytest.raises(PlanningError) as error:
        confirm_run_plan(report, tmp_path, plans_root)
    assert error.value.reason == "report_superseded"


def test_confirmation_treats_an_unreadable_source_artifact_as_stale(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    directory_artifact = SourceArtifact(
        artifact.source_id,
        artifact.sha256,
        artifact.byte_count,
        tmp_path / "input",
    )
    report = create_plan_report(
        state=PlanState.READY_FOR_CONFIRMATION,
        source_artifacts=(directory_artifact,),
        tools=(),
        planned_increment_bytes=artifact.byte_count,
        configuration_fingerprint=_write_planning_configuration(tmp_path),
        inspection_evidence=(_inspection(directory_artifact),),
    )

    diagnostics = revalidate_report(report, tmp_path)

    assert [diagnostic.reason for diagnostic in diagnostics] == ["source_artifact_unavailable"]


def test_confirmation_rejects_changed_pinned_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _artifact(tmp_path)
    report = create_plan_report(
        state=PlanState.READY_FOR_CONFIRMATION,
        source_artifacts=(artifact,),
        tools=(PinnedExternalTool("ffmpeg", tmp_path / "ffmpeg", "test", "f" * 64),),
        planned_increment_bytes=artifact.byte_count,
        configuration_fingerprint=_write_planning_configuration(tmp_path),
        inspection_evidence=(_inspection(artifact),),
    )

    def changed_tool(*_args: object) -> None:
        raise ValueError("Pinned tool identity changed.")

    monkeypatch.setattr(planning, "revalidate_external_tool", changed_tool)

    diagnostics = revalidate_report(report, tmp_path)

    assert [diagnostic.reason for diagnostic in diagnostics] == ["tool_identity_changed"]
    with pytest.raises(PlanningError, match="tool_identity_changed"):
        confirm_run_plan(report, tmp_path, tmp_path / "plans")


def test_confirmation_rejects_insufficient_current_disk_headroom(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _artifact(tmp_path)
    report = create_plan_report(
        state=PlanState.READY_FOR_CONFIRMATION,
        source_artifacts=(artifact,),
        tools=(),
        planned_increment_bytes=artifact.byte_count,
        configuration_fingerprint=_write_planning_configuration(tmp_path),
        inspection_evidence=(_inspection(artifact),),
    )
    disk_usage = namedtuple("DiskUsage", "total used free")
    monkeypatch.setattr(planning.shutil, "disk_usage", lambda _path: disk_usage(10, 9, 1))

    diagnostics = revalidate_report(report, tmp_path)

    assert [diagnostic.reason for diagnostic in diagnostics] == ["disk_headroom_insufficient"]
    with pytest.raises(PlanningError, match="disk_headroom_insufficient"):
        confirm_run_plan(report, tmp_path, tmp_path / "plans")


def test_report_retains_probe_documents_coverage_and_subtitle_metadata(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    inspection_evidence = PlanInspectionEvidence(
        source_id=artifact.source_id,
        structural_document=ProbeDocument('{"streams": [{"index": 0}]}'),
        coverage_document=ProbeDocument('{"packets": [{"stream_index": 0}]}'),
        coverage_by_stream=(
            (
                0,
                StreamCoverage(
                    coverage=HalfOpenInterval(ExactTime(1, 2), ExactTime(3, 2)),
                    gaps=(),
                    diagnostics=(),
                ),
            ),
        ),
        subtitle_tracks=(SubtitleTrackCandidate(1, "en", "webvtt", "embedded", True),),
    )
    report = create_plan_report(
        state=PlanState.AWAITING_DECODE_CONFIRMATION,
        source_artifacts=(artifact,),
        tools=(),
        planned_increment_bytes=artifact.byte_count,
        configuration_fingerprint="config-v1",
        inspection_evidence=(inspection_evidence,),
    )

    report_path = persist_plan_report(report, tmp_path / "plans")
    payload = report.as_json()["inspection_evidence"]

    assert load_plan_report(report_path) == report
    assert payload == [
        {
            "source_id": artifact.source_id,
            "structural_probe_document": {"raw_json": '{"streams": [{"index": 0}]}'},
            "coverage_probe_document": {"raw_json": '{"packets": [{"stream_index": 0}]}'},
            "stream_coverage": [
                {
                    "stream_index": 0,
                    "coverage": {
                        "start": {"numerator": 1, "denominator": 2},
                        "end": {"numerator": 3, "denominator": 2},
                    },
                    "gaps": [],
                    "diagnostics": [],
                }
            ],
            "subtitle_track_candidates": [
                {
                    "stream_index": 1,
                    "language": "en",
                    "container_format": "webvtt",
                    "origin": "embedded",
                    "available": True,
                }
            ],
        }
    ]


def test_non_ready_report_cannot_create_run_plan(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    report = create_plan_report(
        state=PlanState.AWAITING_DECODE_CONFIRMATION,
        source_artifacts=(artifact,),
        tools=(),
        planned_increment_bytes=artifact.byte_count,
        configuration_fingerprint="config-v1",
        inspection_evidence=(_inspection(artifact),),
    )

    with pytest.raises(PlanningError) as error:
        confirm_run_plan(report, tmp_path, tmp_path / "plans")

    assert error.value.reason == "report_not_ready"


def test_report_rejects_source_artifacts_without_one_matching_inspection(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)

    with pytest.raises(PlanningError) as error:
        create_plan_report(
            state=PlanState.BLOCKED,
            source_artifacts=(artifact,),
            tools=(),
            planned_increment_bytes=artifact.byte_count,
            configuration_fingerprint="config-v1",
        )

    assert error.value.reason == "inspection_evidence_invalid"


def test_full_decode_command_has_null_output_only(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    ffmpeg = PinnedExternalTool("ffmpeg", Path("/tool/ffmpeg"), "test", "a" * 64)

    assert build_full_decode_command(ffmpeg, artifact) == (
        "/tool/ffmpeg",
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


def test_decode_profile_rejects_non_positive_throughput(tmp_path: Path) -> None:
    profile_path = tmp_path / "decode-throughput-profiles.json"
    profile_path.write_text(
        """{
  \"profiles\": [{
    \"id\": \"phase-03-default-v1\",
    \"optimistic_realtime_factor\": \"8\",
    \"likely_realtime_factor\": \"0\",
    \"conservative_realtime_factor\": \"1\"
  }]
}
""",
        encoding="utf-8",
    )

    with pytest.raises(PlanningError) as error:
        load_decode_throughput_profile(profile_path)

    assert error.value.reason == "decode_profile_invalid"


def test_full_decode_start_failure_is_reported_as_a_planning_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _artifact(tmp_path)
    ffmpeg = PinnedExternalTool("ffmpeg", Path("/tool/ffmpeg"), "test", "a" * 64)

    def unavailable_tool(*_args: object) -> object:
        raise OSError("FFmpeg is unavailable")

    monkeypatch.setattr("video_content_pipeline.planning.run_tool", unavailable_tool)

    with pytest.raises(PlanningError) as error:
        perform_full_decode_validation(ffmpeg, artifact)

    assert error.value.reason == "full_decode_failed"


def _ready_report_with_choices(
    tmp_path: Path, choices: RunPlanChoices
) -> tuple[SourceArtifact, planning.PlanReport]:
    artifact = _artifact(tmp_path)
    report = create_plan_report(
        state=PlanState.READY_FOR_CONFIRMATION,
        source_artifacts=(artifact,),
        tools=(),
        planned_increment_bytes=artifact.byte_count,
        configuration_fingerprint=_write_planning_configuration(tmp_path),
        inspection_evidence=(_inspection(artifact),),
        run_choices=choices,
    )
    return artifact, report


def _mode_choices(mode: AsrMode, *, visual: bool = False) -> tuple[RunChoice, ...]:
    return (
        RunChoice(
            STAGE_RUN, KEY_ASR_MODE, COLLECTION_SCOPE, mode.value, ChoiceProvenance.USER_CHOSEN
        ),
        RunChoice(
            STAGE_RUN,
            KEY_VISUAL_TEXT_ENABLED,
            COLLECTION_SCOPE,
            "true" if visual else "false",
            ChoiceProvenance.RECOMMENDED_AND_CONFIRMED,
        ),
    )


def test_confirmed_plan_carries_and_persists_front_loaded_choices(tmp_path: Path) -> None:
    choices = RunPlanChoices.build(_mode_choices(AsrMode.FULL_ASR))
    _, report = _ready_report_with_choices(tmp_path, choices)

    plan = confirm_run_plan(report, tmp_path, tmp_path / "plans")
    reloaded = load_run_plan(tmp_path / "plans" / plan.plan_id / "run-plan.json")

    assert plan.run_choices == choices
    assert reloaded.run_choices == choices
    assert confirmed_plan_matches(report, plan)


def test_changing_a_choice_requires_a_new_plan(tmp_path: Path) -> None:
    first_report = create_plan_report(
        state=PlanState.READY_FOR_CONFIRMATION,
        source_artifacts=(_artifact(tmp_path),),
        tools=(),
        planned_increment_bytes=5,
        configuration_fingerprint=_write_planning_configuration(tmp_path),
        inspection_evidence=(_inspection(_artifact_reuse(tmp_path)),),
        run_choices=RunPlanChoices.build(_mode_choices(AsrMode.FULL_ASR)),
    )
    second_report = create_plan_report(
        state=PlanState.READY_FOR_CONFIRMATION,
        source_artifacts=(_artifact_reuse(tmp_path),),
        tools=(),
        planned_increment_bytes=5,
        configuration_fingerprint=planning_configuration_fingerprint(tmp_path),
        inspection_evidence=(_inspection(_artifact_reuse(tmp_path)),),
        run_choices=RunPlanChoices.build(_mode_choices(AsrMode.SUBTITLE_FIRST)),
    )

    first_plan = confirm_run_plan(first_report, tmp_path, tmp_path / "plans")
    second_plan = confirm_run_plan(second_report, tmp_path, tmp_path / "plans")

    assert first_plan.plan_id != second_plan.plan_id
    assert not confirmed_plan_matches(first_report, second_plan)


def test_plan_missing_a_required_choice_is_still_confirmable(tmp_path: Path) -> None:
    # ASR mode is enhancement but no enhancement scope is front-loaded: still
    # confirmable, and the gap is machine-detectable for a Run decision pause.
    choices = RunPlanChoices.build(_mode_choices(AsrMode.ENHANCEMENT))
    _, report = _ready_report_with_choices(tmp_path, choices)

    plan = confirm_run_plan(report, tmp_path, tmp_path / "plans")

    gaps = missing_required_choices(plan.run_choices)
    assert (tmp_path / "plans" / plan.plan_id / "run-plan.json").is_file()
    assert any(gap.stage == STAGE_ENHANCEMENT for gap in gaps)


def test_front_loaded_enhancement_scope_closes_the_gap(tmp_path: Path) -> None:
    choices = RunPlanChoices.build(
        (
            *_mode_choices(AsrMode.ENHANCEMENT),
            RunChoice(
                STAGE_ENHANCEMENT, KEY_ENHANCEMENT_CUE, "part-a", "3", ChoiceProvenance.USER_CHOSEN
            ),
        )
    )
    _, report = _ready_report_with_choices(tmp_path, choices)

    plan = confirm_run_plan(report, tmp_path, tmp_path / "plans")

    assert missing_required_choices(plan.run_choices) == ()


def _artifact_reuse(tmp_path: Path) -> SourceArtifact:
    media = tmp_path / "input" / "hash" / "media"
    digest, byte_count = sha256_file(media)
    return SourceArtifact(digest, digest, byte_count, media)


def _baseline(capability: str, candidate_id: str, peak_memory_bytes: int) -> DeviceBaseline:
    return DeviceBaseline(
        capability=capability,
        candidate_id=candidate_id,
        device_class="apple-m1",
        real_time_factor=Fraction(5, 1),
        peak_memory_bytes=peak_memory_bytes,
        basis=f"prototype:{candidate_id}",
    )


def test_peak_memory_estimate_is_the_largest_measured_capability_peak() -> None:
    # Stages load one model at a time, so the plan peak is the largest single
    # capability peak, and each capability keeps its own largest measurement.
    baselines = (
        _baseline("asr_primary", "qwen3-asr", 5_000),
        _baseline("asr_primary", "qwen3-asr", 5_400),
        _baseline("vad", "silero-vad", 120),
    )

    estimate = estimate_plan_peak_memory(baselines)

    assert estimate is not None
    assert estimate.peak_memory_bytes == 5_400
    assert estimate.confidence == "measured"
    assert estimate.basis == "device-baselines:apple-m1"
    assert estimate.per_capability == (
        CapabilityPeakMemory("asr_primary", 5_400),
        CapabilityPeakMemory("vad", 120),
    )


def test_peak_memory_estimate_is_absent_without_measurements() -> None:
    assert estimate_plan_peak_memory(()) is None


def test_peak_memory_confidence_is_derived_from_the_baselines_not_asserted() -> None:
    # An estimated baseline mixed with measured ones must never present itself as
    # "measured": the estimate reports the weaker, evidence-honest label.
    estimated = DeviceBaseline(
        capability="text_semantics",
        candidate_id="qwen3-4b",
        device_class="apple-m1",
        real_time_factor=Fraction(3, 1),
        peak_memory_bytes=4_000,
        basis="projection",
        confidence="estimated",
    )

    estimate = estimate_plan_peak_memory((_baseline("vad", "silero-vad", 120), estimated))

    assert estimate is not None
    assert estimate.confidence == "mixed"


def test_load_peak_memory_estimate_reads_the_retained_device_baselines(tmp_path: Path) -> None:
    baselines_path = tmp_path / "docs" / "phase-11-prototypes" / "device-baselines.json"
    baselines_path.parent.mkdir(parents=True)
    baselines_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "device_class": "apple-m1",
                "baselines": [_baseline("vad", "silero-vad", 200).as_json()],
            }
        ),
        encoding="utf-8",
    )

    estimate = load_plan_peak_memory_estimate(tmp_path)

    assert estimate is not None
    assert estimate.peak_memory_bytes == 200


def test_load_peak_memory_estimate_is_none_when_the_baseline_file_is_absent(tmp_path: Path) -> None:
    assert load_plan_peak_memory_estimate(tmp_path) is None


def _write_registry(tmp_path: Path, candidates: list[dict[str, object]]) -> None:
    registry_path = tmp_path / "models" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps({"schema_version": 2, "candidates": candidates}), encoding="utf-8"
    )


def test_plan_model_statuses_flag_missing_and_ineligible_capabilities(tmp_path: Path) -> None:
    # One capability has a candidate with incomplete evidence (ineligible); every
    # other capability has no candidate at all (acquisition still required).
    _write_registry(tmp_path, [{"candidate_id": "rapidocr", "capability": "ocr_primary"}])

    statuses = plan_model_statuses(tmp_path)

    by_capability = {status.capability: status.state for status in statuses}
    assert tuple(status.capability for status in statuses) == PLAN_MODEL_CAPABILITIES
    assert by_capability["ocr_primary"] == "model_ineligible"
    assert by_capability["vad"] == "model_acquisition_required"
    assert by_capability["asr_primary"] == "model_acquisition_required"


def test_plan_model_statuses_treat_a_missing_registry_as_acquisition_required(
    tmp_path: Path,
) -> None:
    statuses = plan_model_statuses(tmp_path)

    assert tuple(status.capability for status in statuses) == PLAN_MODEL_CAPABILITIES
    assert all(status.state == "model_acquisition_required" for status in statuses)


def test_plan_model_statuses_surface_a_credential_gated_capability(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        [{"candidate_id": "gated-asr", "capability": "asr_primary", "credential_required": True}],
    )

    statuses = {status.capability: status.state for status in plan_model_statuses(tmp_path)}

    assert statuses["asr_primary"] == "model_credential_gated"


def test_plan_model_capabilities_cover_every_stage_capability() -> None:
    from video_content_pipeline.audio_analysis import _CAPABILITIES as AUDIO_CAPABILITIES
    from video_content_pipeline.text_analysis import TEXT_SEMANTICS_CAPABILITIES
    from video_content_pipeline.transcription_contracts import ASR_CAPABILITIES
    from video_content_pipeline.visual_text import VISUAL_TEXT_CAPABILITIES

    assert set(PLAN_MODEL_CAPABILITIES) == (
        set(AUDIO_CAPABILITIES)
        | set(ASR_CAPABILITIES)
        | set(VISUAL_TEXT_CAPABILITIES)
        | set(TEXT_SEMANTICS_CAPABILITIES)
    )
