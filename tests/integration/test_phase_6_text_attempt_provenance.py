"""Offline contract for Phase 6 ticket 07.

Ticket 07 makes text-generation attempts, pauses, resources, diagnostics, and
synthetic human-review records immutable and auditable. Every attempt records
its bound generation identities (prompt and rendered prompt, input-cue manifest,
adapter identity, sampling, output-schema and evidence-rule hashes, raw-output
and projection state, and an execution-resource measurement). The only resumable
decision pause is the future-real-model 24 GiB resource envelope, and no attempt
ever retries automatically: a resume is an explicit, fresh, non-overwriting
attempt. These tests drive ``analyze_text``/``resume_text_analysis`` and assert
deterministic contract properties and no-side-effect guarantees.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from video_content_pipeline import text_analysis
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

_ENVELOPE = 24 * 1024**3


def _write_text_analysis_contracts(project_root: Path) -> None:
    config = project_root / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "text-analysis-rules.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "phase-06-fixture-rules",
                "cue_rules_version": "phase-06-cue-rules-fixture",
                "prompt_template_version": "phase-06-prompt-fixture",
                "output_schema_version": "phase-06-output-schema-fixture",
                "evidence_rules_version": "phase-06-evidence-rules-fixture",
                "controlled_adapter_identity": "phase-06-controlled-text-adapter-fixture",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    contract_dir = config / "text-analysis"
    contract_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "prompt-template.json": {
            "schema_version": 1,
            "version": "phase-06-prompt-fixture",
            "sections": [{"id": "task", "role": "system", "text": "Segment cues."}],
        },
        "output-schema.json": {
            "schema_version": 1,
            "version": "phase-06-output-schema-fixture",
            "envelope": {
                "expected_schema_version": 1,
                "required_fields": [
                    "schema_version",
                    "output_schema_version",
                    "adapter_identity",
                    "result",
                ],
                "result": {
                    "required_fields": ["parts"],
                    "list_fields": ["parts"],
                    "optional_object_or_null_fields": ["collection_summary"],
                },
            },
        },
        "evidence-rules.json": {
            "schema_version": 1,
            "version": "phase-06-evidence-rules-fixture",
        },
        "controlled-adapter.json": {
            "schema_version": 1,
            "version": "phase-06-controlled-text-adapter-fixture",
            "implementation_version": "phase-06-controlled-text-adapter-impl-fixture",
            "prompt_template_version": "phase-06-prompt-fixture",
            "output_schema_version": "phase-06-output-schema-fixture",
            "evidence_rules_version": "phase-06-evidence-rules-fixture",
            "sampling_configuration": {"mode": "deterministic", "temperature": 0, "seed": 0},
        },
    }
    for name, payload in artifacts.items():
        (contract_dir / name).write_text(
            json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8"
        )


def _set_resource_plan(project_root: Path, conservative_high_bytes: int | None) -> None:
    rules_path = project_root / "config" / "text-analysis-rules.json"
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    if conservative_high_bytes is None:
        rules.pop("resource_plan", None)
    else:
        rules["resource_plan"] = {"conservative_high_bytes": conservative_high_bytes}
    rules_path.write_text(json.dumps(rules, sort_keys=True) + "\n", encoding="utf-8")


def _retained_plan(project_root: Path) -> RunPlan:
    media_path = project_root / "input" / "source" / "synthetic-media"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"phase-6-text-analysis-fixture")
    digest, byte_count = sha256_file(media_path)
    artifact = SourceArtifact(
        digest, digest, byte_count, media_path, origin_kind="synthetic_fixture"
    )
    evidence = PlanInspectionEvidence(
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
        inspection_evidence=(evidence,),
    )
    persist_plan_report(plan_report, project_root / "plans")
    plan = RunPlan(
        plan_id="confirmed-phase-6-fixture-plan",
        report_id=plan_report.report_id,
        source_artifacts=(artifact,),
        tools=(),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint=plan_report.configuration_fingerprint,
        inspection_evidence_fingerprints=inspection_evidence_fingerprints((evidence,)),
    )
    plan_path = project_root / "plans" / plan.plan_id / "run-plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(
        json.dumps(plan.as_json(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return plan


def _retained_subtitle_report(project_root: Path, plan: RunPlan) -> SubtitleCandidateReport:
    report_id = "1" * 32
    report_path = (
        project_root
        / "work"
        / plan.source_artifacts[0].source_id
        / report_id
        / "candidate-report.json"
    )
    rules_path = project_root / "config" / "subtitle-rules.json"
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text(
        '{"schema_version": 1, "id": "phase-04-fixture-rules"}\n', encoding="utf-8"
    )
    _write_text_analysis_contracts(project_root)
    source_artifact_path = report_path.parent / "source.vtt"
    readable_artifact_path = report_path.parent / "readable.vtt"
    source_candidate_path = report_path.parent / "source-candidate.json"
    source_artifact_path.parent.mkdir(parents=True)
    source_artifact_path.write_text("WEBVTT\n\n", encoding="utf-8")
    readable_artifact_path.write_text("WEBVTT\n\n", encoding="utf-8")
    source_candidate_path.write_text(
        json.dumps({"schema_version": 1, "cues": []}, sort_keys=True) + "\n", encoding="utf-8"
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
                raw_pts_cue_intervals=(),
            ),
        ),
        diagnostics=(),
        report_path=report_path,
    )
    report_path.write_text(
        json.dumps(report.as_json(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return report


def _analyze(project_root: Path, plan: RunPlan, subtitle_report: SubtitleCandidateReport) -> dict:
    return text_analysis.analyze_text(plan.plan_id, subtitle_report.report_id, project_root)


def test_attempt_provenance_records_bound_generation_identities(tmp_path: Path) -> None:
    plan = _retained_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)

    report = _analyze(tmp_path, plan, subtitle_report)["report"]

    assert report["status"] == "controlled_adapter_unavailable"
    provenance = report["attempt_provenance"]
    assert provenance["attempt_id"] == report["report_id"]
    assert provenance["resumed_from_report_id"] is None
    assert provenance["resumption_decision"] is None
    assert provenance["adapter_identity"]["version"] == "phase-06-controlled-text-adapter-fixture"
    assert provenance["adapter_identity"]["implementation_version"] == (
        "phase-06-controlled-text-adapter-impl-fixture"
    )
    assert provenance["output_schema_identity"]["version"] == "phase-06-output-schema-fixture"
    assert provenance["evidence_rules_identity"]["version"] == "phase-06-evidence-rules-fixture"
    assert provenance["prompt"]["version"] == "phase-06-prompt-fixture"
    assert provenance["raw_output"] == {
        "state": "not_generated",
        "restriction": "local_audit_only",
        "artifacts": [],
    }
    assert provenance["projection"] == {"state": "not_projected"}

    sampling = provenance["sampling"]
    expected_sampling = json.dumps(
        {"mode": "deterministic", "temperature": 0, "seed": 0}, sort_keys=True
    ).encode("utf-8")
    assert sampling["sha256"] == sha256(expected_sampling).hexdigest()

    workspace = Path(report["workspace_path"])
    rendered_prompt_path = workspace / "provenance" / "rendered-prompt.txt"
    manifest_path = workspace / "provenance" / "input-cue-manifest.json"
    assert rendered_prompt_path.exists()
    assert manifest_path.exists()
    assert provenance["prompt"]["rendered_prompt"]["sha256"] == (
        sha256(rendered_prompt_path.read_bytes()).hexdigest()
    )
    assert provenance["input_cue_manifest"]["sha256"] == (
        sha256(manifest_path.read_bytes()).hexdigest()
    )
    assert provenance["input_cue_manifest"]["track_count"] == 1

    resource = provenance["resource_measurement"]
    assert resource["state"] == "not_applicable"
    assert resource["envelope_limit_bytes"] == _ENVELOPE
    assert resource["reason"] == "controlled_offline_adapter"
    assert report["required_decision"] is None


def test_attempt_provenance_on_a_failed_attempt_is_minimal(tmp_path: Path) -> None:
    (tmp_path / "plans").mkdir()

    report = text_analysis.analyze_text("no-such-plan", "1" * 32, tmp_path)["report"]

    assert report["status"] == "failed"
    provenance = report["attempt_provenance"]
    assert provenance["attempt_id"] == report["report_id"]
    assert provenance["adapter_identity"] is None
    assert provenance["prompt"] is None
    assert provenance["input_cue_manifest"] is None
    assert provenance["resource_measurement"]["state"] == "not_applicable"
    assert report["required_decision"] is None


def test_resource_plan_over_envelope_pauses_for_a_decision(tmp_path: Path) -> None:
    plan = _retained_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    _set_resource_plan(tmp_path, _ENVELOPE + 1)
    subtitles_before = subtitle_report.report_path.read_bytes()

    result = _analyze(tmp_path, plan, subtitle_report)
    report = result["report"]

    assert result["status"] == "resource_envelope_exceeded"
    assert report["status"] == "resource_envelope_exceeded"
    assert report["required_decision"] == {
        "reason": "resource_envelope_exceeded",
        "decision": "resource_configuration_changed",
    }
    assert report["diagnostics"] == [
        {
            "reason": "resource_envelope_exceeded",
            "message": (
                "A conservative text-model resource estimate exceeds the 24 GiB envelope."
            ),
        }
    ]
    resource = report["attempt_provenance"]["resource_measurement"]
    assert resource["state"] == "resource_envelope_exceeded"
    assert resource["conservative_high_bytes"] == _ENVELOPE + 1
    assert resource["envelope_limit_bytes"] == _ENVELOPE
    assert report["segments"] == []
    assert subtitle_report.report_path.read_bytes() == subtitles_before
    assert not (tmp_path / "outputs").exists()


def test_resource_plan_within_envelope_does_not_pause(tmp_path: Path) -> None:
    plan = _retained_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    _set_resource_plan(tmp_path, _ENVELOPE)

    report = _analyze(tmp_path, plan, subtitle_report)["report"]

    assert report["status"] == "controlled_adapter_unavailable"
    assert report["required_decision"] is None


def test_resume_resource_pause_with_a_configuration_change_is_a_fresh_attempt(
    tmp_path: Path,
) -> None:
    plan = _retained_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    _set_resource_plan(tmp_path, _ENVELOPE + 1)
    paused = _analyze(tmp_path, plan, subtitle_report)["report"]
    paused_path = Path(paused["report_path"])
    paused_bytes = paused_path.read_bytes()

    # The user reconfigures the future-real-model resource plan below the envelope.
    _set_resource_plan(tmp_path, None)
    resumed = text_analysis.resume_text_analysis(
        paused["report_id"], "resource_configuration_changed", tmp_path
    )["report"]

    assert resumed["report_id"] != paused["report_id"]
    assert resumed["status"] == "controlled_adapter_unavailable"
    assert resumed["plan_id"] == plan.plan_id
    assert resumed["input_evidence"]["resumption_decision"] == "resource_configuration_changed"
    assert resumed["input_evidence"]["resumed_from_report"]["sha256"] == (
        sha256(paused_bytes).hexdigest()
    )
    assert resumed["attempt_provenance"]["resumed_from_report_id"] == paused["report_id"]
    # No automatic retry: the paused report is never overwritten.
    assert paused_path.read_bytes() == paused_bytes


def test_resume_resource_pause_rejects_the_wrong_decision(tmp_path: Path) -> None:
    plan = _retained_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    _set_resource_plan(tmp_path, _ENVELOPE + 1)
    paused = _analyze(tmp_path, plan, subtitle_report)["report"]

    try:
        text_analysis.resume_text_analysis(paused["report_id"], "model_release_verified", tmp_path)
    except text_analysis.TextAnalysisError as error:
        assert error.reason == "text_analysis_resume_invalid"
    else:  # pragma: no cover - the resume must reject the wrong decision
        raise AssertionError("resume accepted the wrong decision")


def test_resume_rejects_a_terminal_report(tmp_path: Path) -> None:
    plan = _retained_plan(tmp_path)
    subtitle_report = _retained_subtitle_report(tmp_path, plan)
    terminal = _analyze(tmp_path, plan, subtitle_report)["report"]

    try:
        text_analysis.resume_text_analysis(
            terminal["report_id"], "resource_configuration_changed", tmp_path
        )
    except text_analysis.TextAnalysisError as error:
        assert error.reason == "text_analysis_resume_invalid"
    else:  # pragma: no cover
        raise AssertionError("resume accepted a terminal report")


def test_restricted_raw_output_pointer_excludes_content(tmp_path: Path) -> None:
    workspace = tmp_path / "work" / "text-analysis-reports" / ("2" * 32)
    workspace.mkdir(parents=True)
    raw = b'{"secret": "restricted raw generation"}'

    pointer = text_analysis.record_restricted_raw_output(workspace, "attempt", raw)

    as_json = pointer.as_json()
    assert set(as_json) == {"path", "sha256", "byte_count", "restriction"}
    assert as_json["restriction"] == "local_audit_only"
    assert as_json["sha256"] == sha256(raw).hexdigest()
    assert as_json["byte_count"] == len(raw)
    assert "secret" not in json.dumps(as_json)
    assert Path(as_json["path"]).read_bytes() == raw
