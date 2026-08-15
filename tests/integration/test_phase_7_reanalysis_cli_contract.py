"""Offline CLI contract for Phase 7 ticket 09 (Affected-Part re-analysis, ADR 0046).

After ``vcp enhance`` changes a cue basis, ``vcp reanalyze-text`` starts a new
immutable text-analysis attempt: it regenerates only the Parts whose cue
identities changed against the changed basis (through the Controlled offline text
adapter), carries unaffected Parts forward with an explicit provenance link to the
retained prior report, and recomputes chapters and the collection summary over the
combined set. These tests drive the command with a retained prior report and a
retained enhancement report plus a hash-pinned synthetic text fixture, asserting
deterministic contract properties -- affected-Part selection, regeneration,
carry-forward provenance, recomputed aggregation, immutability, and the
no-side-effect guarantees -- never prose quality.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from video_content_pipeline import cli
from video_content_pipeline import text_reanalysis as reanalysis
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
from video_content_pipeline.text_contracts import render_text_analysis_markdown

_OUTPUT_SCHEMA_VERSION = "phase-07-reanalysis-output-schema-fixture"
_ADAPTER_IDENTITY = "phase-07-reanalysis-text-adapter-fixture"
_FIXTURE_RELATIVE = "config/text-analysis/fixtures/reanalysis-output.json"
_GUARANTEES_SUBSET = {
    "model_acquisition": "not_attempted",
    "model_execution": "not_attempted",
    "network_access": "not_attempted",
    "outputs_publication": "not_attempted",
}


# --------------------------------------------------------------------------- #
# Plan, subtitle, and contract fixtures
# --------------------------------------------------------------------------- #


def _confirmed_plan(project_root: Path, media_variants: list[bytes]) -> RunPlan:
    artifacts: list[SourceArtifact] = []
    evidence: list[PlanInspectionEvidence] = []
    for index, media in enumerate(media_variants):
        media_path = project_root / "input" / "source" / f"synthetic-media-{index}"
        media_path.parent.mkdir(parents=True, exist_ok=True)
        media_path.write_bytes(media)
        digest, byte_count = sha256_file(media_path)
        artifact = SourceArtifact(
            digest, digest, byte_count, media_path, origin_kind="synthetic_fixture"
        )
        artifacts.append(artifact)
        evidence.append(
            PlanInspectionEvidence(
                source_id=artifact.source_id,
                structural_document=ProbeDocument(
                    json.dumps({"streams": [{"index": 1, "codec_type": "subtitle"}]})
                ),
                coverage_document=ProbeDocument('{"packets": []}'),
                coverage_by_stream=(),
                subtitle_tracks=(),
            )
        )
    plan_report = create_plan_report(
        state=PlanState.READY_FOR_CONFIRMATION,
        source_artifacts=tuple(artifacts),
        tools=(),
        planned_increment_bytes=0,
        configuration_fingerprint="phase-03-fixture",
        inspection_evidence=tuple(evidence),
    )
    persist_plan_report(plan_report, project_root / "plans")
    plan = RunPlan(
        plan_id="confirmed-phase-7-reanalysis-plan",
        report_id=plan_report.report_id,
        source_artifacts=tuple(artifacts),
        tools=(),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint=plan_report.configuration_fingerprint,
        inspection_evidence_fingerprints=inspection_evidence_fingerprints(tuple(evidence)),
    )
    plan_path = project_root / "plans" / plan.plan_id / "run-plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(
        json.dumps(plan.as_json(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return plan


def _retained_subtitle_report(project_root: Path, plan: RunPlan) -> SubtitleCandidateReport:
    """Retain a minimal completed subtitle report for two available Parts."""

    rules_path = project_root / "config" / "subtitle-rules.json"
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text(
        '{"schema_version": 1, "id": "phase-04-fixture-rules"}\n', encoding="utf-8"
    )
    report_id = "1" * 32
    report_path = project_root / "work" / "subtitle-reports" / report_id / "report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    candidates = tuple(
        SubtitleCandidate(
            source_id=artifact.source_id,
            stream_index=1,
            state=CandidateState.VALID,
            source_candidate_path=(report_path.parent / f"candidate-{index}.json").as_posix(),
            source_candidate_sha256="0" * 64,
            raw_pts_cue_intervals=(),
        )
        for index, artifact in enumerate(plan.source_artifacts)
    )
    report = SubtitleCandidateReport(
        report_id=report_id,
        plan_id=plan.plan_id,
        state=CandidateReportState.COMPLETED,
        subtitle_rules_fingerprint=subtitle_rules_fingerprint(project_root),
        candidates=candidates,
        diagnostics=(),
        report_path=report_path,
    )
    report_path.write_text(
        json.dumps(report.as_json(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return report


def _write_text_analysis_contracts(project_root: Path) -> None:
    config = project_root / "config"
    contract_dir = config / "text-analysis"
    contract_dir.mkdir(parents=True, exist_ok=True)
    (config / "text-analysis-rules.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "phase-07-reanalysis-fixture-rules",
                "cue_rules_version": "phase-07-cue-rules-fixture",
                "prompt_template_version": "phase-07-prompt-fixture",
                "output_schema_version": _OUTPUT_SCHEMA_VERSION,
                "evidence_rules_version": "phase-07-evidence-rules-fixture",
                "controlled_adapter_identity": _ADAPTER_IDENTITY,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    artifacts = {
        "prompt-template.json": {
            "schema_version": 1,
            "version": "phase-07-prompt-fixture",
            "sections": [{"id": "task", "role": "system", "text": "Re-segment cues."}],
        },
        "output-schema.json": {
            "schema_version": 1,
            "version": _OUTPUT_SCHEMA_VERSION,
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
            "version": "phase-07-evidence-rules-fixture",
        },
        "controlled-adapter.json": {
            "schema_version": 1,
            "version": _ADAPTER_IDENTITY,
            "implementation_version": "phase-07-reanalysis-text-adapter-impl-fixture",
            "prompt_template_version": "phase-07-prompt-fixture",
            "output_schema_version": _OUTPUT_SCHEMA_VERSION,
            "evidence_rules_version": "phase-07-evidence-rules-fixture",
            "sampling_configuration": {"mode": "deterministic", "temperature": 0, "seed": 0},
        },
    }
    for name, payload in artifacts.items():
        (contract_dir / name).write_text(
            json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8"
        )


def _bind_regeneration(
    project_root: Path,
    affected_bases: dict[str, tuple[str, ...]],
    *,
    prior_id: str,
    enh_id: str,
    result: dict[str, object],
) -> None:
    """Write the text fixture and bind it to the re-analysis input-cue manifest."""

    manifest = reanalysis.reanalysis_input_cue_manifest_document(
        affected_bases, prior_report_id=prior_id, enhancement_report_id=enh_id
    )
    manifest_sha = reanalysis.reanalysis_input_cue_manifest_sha256(manifest)
    output = {
        "schema_version": 1,
        "output_schema_version": _OUTPUT_SCHEMA_VERSION,
        "adapter_identity": _ADAPTER_IDENTITY,
        "result": result,
    }
    fixture_path = project_root / _FIXTURE_RELATIVE
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(output, sort_keys=True).encode("utf-8")
    fixture_path.write_bytes(raw)
    adapter_path = project_root / "config" / "text-analysis" / "controlled-adapter.json"
    adapter = json.loads(adapter_path.read_text(encoding="utf-8"))
    adapter["generation"] = {
        "output_fixture_path": _FIXTURE_RELATIVE,
        "output_fixture_sha256": sha256(raw).hexdigest(),
        "input_fixture_sha256": manifest_sha,
    }
    adapter_path.write_text(json.dumps(adapter, sort_keys=True) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# Retained prior and enhancement reports
# --------------------------------------------------------------------------- #


def _segment(part_id: str, ordinal: int, cue_ids: tuple[str, ...]) -> dict[str, object]:
    return {
        "part_id": part_id,
        "ordinal": ordinal,
        "origin": "adjudicated",
        "cue_ids": list(cue_ids),
        "source_languages": ["zh"],
        "title": None,
        "details": [],
        "questions_and_answers": [],
        "people": [],
        "contradictions": [],
        "unresolved_questions": [],
        "content_diagnostics": [],
    }


def _cue(part_id: str, ordinal: int) -> str:
    return f"{part_id}:stream-1:{ordinal}"


def _write_prior_report(
    project_root: Path,
    plan: RunPlan,
    subtitle_report_id: str,
    part_a: str,
    part_b: str,
    *,
    report_id: str = "a" * 32,
) -> str:
    """Retain a valid prior text-analysis report over two available Parts."""

    document: dict[str, object] = {
        "report_id": report_id,
        "plan_id": plan.plan_id,
        "subtitle_report_id": subtitle_report_id,
        "status": "complete",
        "audio_completeness": "not_verified",
        "segments": [
            _segment(part_a, 0, (_cue(part_a, 0), _cue(part_a, 1))),
            _segment(part_a, 1, (_cue(part_a, 2),)),
            _segment(part_b, 0, (_cue(part_b, 0),)),
        ],
        "chapters": [
            {
                "part_id": part_b,
                "ordinal": 0,
                "title": "旧章",
                "segment_ordinals": [0],
                "source_languages": ["zh"],
            }
        ],
        "collection_summary": {
            "part_ids": [part_a, part_b],
            "partial": False,
            "entries": [
                {"text": "旧合集", "segment_refs": [{"part_id": part_b, "ordinal": 0}]},
            ],
            "omitted_parts": [],
            "limitations": [],
            "rejected": [],
        },
        "unsupported_item_count": 0,
        "diagnostics": [],
        "required_decision": None,
        "rendered_report": None,
    }
    document["rendered_report"] = render_text_analysis_markdown(document).as_json()
    path = project_root / "work" / "text-analysis-reports" / report_id / "text-analysis-report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return report_id


def _write_enhancement_report(
    project_root: Path,
    plan: RunPlan,
    subtitle_report_id: str,
    *,
    part_id: str,
    cue_refs: list[str],
    status: str = "complete",
) -> str:
    """Retain a minimal enhancement report whose enhanced Part carries ``cue_refs``."""

    report_id = "b" * 32
    document = {
        "report_id": report_id,
        "plan_id": plan.plan_id,
        "subtitle_report_id": subtitle_report_id,
        "status": status,
        "audio_completeness": "not_verified",
        "verbatim_completeness_claimed": False,
        "enhanced_parts": [
            {
                "part_id": part_id,
                "cues": [
                    {
                        "provenance": "asr" if ":asr:" in cue_ref else "subtitle_track",
                        "interval": {
                            "start": {"numerator": index, "denominator": 1},
                            "end": {"numerator": index + 1, "denominator": 1},
                        },
                        "text": f"line-{index}",
                        "cue_ref": cue_ref,
                    }
                    for index, cue_ref in enumerate(cue_refs)
                ],
                "corrections": [],
            }
        ],
    }
    path = (
        project_root / "work" / "enhancement-reports" / report_id / "enhancement-report.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return report_id


def _run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    project_root: Path,
    argv: list[str],
) -> tuple[int, dict[str, object]]:
    monkeypatch.setattr(cli, "assert_runtime_policy", lambda: None)
    monkeypatch.setattr(cli, "assert_project_venv", lambda: object())
    monkeypatch.setattr(cli, "_project_root", lambda: project_root)
    code = cli.main(argv)
    return code, json.loads(capsys.readouterr().out)


def _prepare(project_root: Path) -> tuple[RunPlan, str, str, str]:
    plan = _confirmed_plan(project_root, [b"reanalysis-part-a", b"reanalysis-part-b"])
    _write_text_analysis_contracts(project_root)
    subtitle_report = _retained_subtitle_report(project_root, plan)
    part_a = plan.source_artifacts[0].source_id
    part_b = plan.source_artifacts[1].source_id
    prior_id = _write_prior_report(project_root, plan, subtitle_report.report_id, part_a, part_b)
    return plan, subtitle_report.report_id, prior_id, part_a


# --------------------------------------------------------------------------- #
# The re-analysis contract
# --------------------------------------------------------------------------- #


def test_reanalyze_regenerates_affected_and_carries_the_rest_forward(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan, subtitle_id, prior_id, part_a = _prepare(tmp_path)
    part_b = plan.source_artifacts[1].source_id
    # Enhancement replaced part-a's whole cue basis with two ASR cues; part-b is
    # untouched and never appears in the enhancement report.
    new_a = (f"{part_a}:asr:0", f"{part_a}:asr:1")
    enh_id = _write_enhancement_report(
        tmp_path, plan, subtitle_id, part_id=part_a, cue_refs=list(new_a)
    )
    result = {
        "parts": [
            {
                "part_id": part_a,
                "segments": [
                    {
                        "boundary": {"start_cue_id": new_a[0], "end_cue_id": new_a[0]},
                        "content": {"title": {"text": "甲", "cue_ids": [new_a[0]]}},
                    },
                    {
                        "boundary": {"start_cue_id": new_a[1], "end_cue_id": new_a[1]},
                        "content": {"title": {"text": "乙", "cue_ids": [new_a[1]]}},
                    },
                ],
                "chapters": [{"start_ordinal": 0, "end_ordinal": 1, "title": "新章"}],
            }
        ],
        "collection_summary": {
            "entries": [
                {
                    "segment_refs": [
                        {"part_id": part_a, "ordinal": 0},
                        {"part_id": part_b, "ordinal": 0},
                    ],
                    "text": "新合集摘要",
                }
            ]
        },
    }
    _bind_regeneration(tmp_path, {part_a: new_a}, prior_id=prior_id, enh_id=enh_id, result=result)
    prior_path = (
        tmp_path / "work" / "text-analysis-reports" / prior_id / "text-analysis-report.json"
    )
    prior_bytes = prior_path.read_bytes()

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        [
            "reanalyze-text",
            plan.plan_id,
            subtitle_id,
            "--prior-report",
            prior_id,
            "--enhancement-report",
            enh_id,
            "--json",
        ],
    )

    assert code == 0
    assert response["status"] == "complete"
    report = response["report"]
    assert report["attempt_kind"] == "affected_part_reanalysis"
    assert report["audio_completeness"] == "not_verified"
    for guarantee, value in _GUARANTEES_SUBSET.items():
        assert report["guarantees"][guarantee] == value

    # Affected part-a regenerated; part-b carried forward with a link to its source.
    assert report["reanalysis"]["regenerated_parts"] == [part_a]
    carried = report["reanalysis"]["carried_forward_parts"]
    assert [item["part_id"] for item in carried] == [part_b]
    assert carried[0]["source_report_id"] == prior_id
    assert carried[0]["source_report_sha256"] == sha256(prior_bytes).hexdigest()

    provenances = [(seg["part_id"], seg["provenance"]) for seg in report["segments"]]
    assert provenances == [
        (part_a, "regenerated"),
        (part_a, "regenerated"),
        (part_b, "carried_forward"),
    ]
    # The carried-forward segment links to its source and copies no prose.
    carried_segment = report["segments"][-1]
    assert carried_segment["source_report_id"] == prior_id
    assert "title" not in carried_segment

    # Chapters and the collection are recomputed over the combined set.
    assert any(chapter["provenance"] == "regenerated" for chapter in report["chapters"])
    assert any(chapter["provenance"] == "carried_forward" for chapter in report["chapters"])
    assert len(report["collection_summary"]["entries"]) == 1

    # The new attempt is immutable and never overwrote the prior report.
    report_path = Path(report["report_path"])
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    assert prior_path.read_bytes() == prior_bytes
    assert not (tmp_path / "outputs").exists()


def test_reanalyze_carries_everything_forward_when_nothing_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan, subtitle_id, prior_id, part_a = _prepare(tmp_path)
    part_b = plan.source_artifacts[1].source_id
    # The enhancement kept part-a's original cues unchanged (gate failure), so no
    # Part is affected and the whole collection is carried forward -- no regeneration.
    unchanged = [_cue(part_a, 0), _cue(part_a, 1), _cue(part_a, 2)]
    enh_id = _write_enhancement_report(
        tmp_path, plan, subtitle_id, part_id=part_a, cue_refs=unchanged
    )

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        [
            "reanalyze-text",
            plan.plan_id,
            subtitle_id,
            "--prior-report",
            prior_id,
            "--enhancement-report",
            enh_id,
            "--json",
        ],
    )

    assert code == 0
    report = response["report"]
    assert report["status"] == "complete"
    assert report["reanalysis"]["regenerated_parts"] == []
    assert {item["part_id"] for item in report["reanalysis"]["carried_forward_parts"]} == {
        part_a,
        part_b,
    }
    assert all(seg["provenance"] == "carried_forward" for seg in report["segments"])
    # The prior collection entry is re-validated and preserved.
    assert len(report["collection_summary"]["entries"]) == 1


def test_reanalyze_fails_when_the_prior_report_belongs_to_another_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan, subtitle_id, _prior_id, part_a = _prepare(tmp_path)
    part_b = plan.source_artifacts[1].source_id
    # A prior report that names a different subtitle report must not be reused for
    # this run, even though the RunPlan and subtitle report themselves revalidate.
    drifted_prior = _write_prior_report(
        tmp_path, plan, "9" * 32, part_a, part_b, report_id="c" * 32
    )
    enh_id = _write_enhancement_report(
        tmp_path, plan, subtitle_id, part_id=part_a, cue_refs=[f"{part_a}:asr:0"]
    )

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        [
            "reanalyze-text",
            plan.plan_id,
            subtitle_id,
            "--prior-report",
            drifted_prior,
            "--enhancement-report",
            enh_id,
            "--json",
        ],
    )

    assert code == 0
    assert response["status"] == "failed"
    assert response["report"]["diagnostics"][0]["reason"] == "reanalysis_report_mismatch"


def test_reanalyze_rejects_a_non_loadable_enhancement_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan, subtitle_id, prior_id, part_a = _prepare(tmp_path)
    # A failed enhancement produced no changed cue basis to re-analyze.
    enh_id = _write_enhancement_report(
        tmp_path, plan, subtitle_id, part_id=part_a, cue_refs=[f"{part_a}:asr:0"], status="failed"
    )

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        [
            "reanalyze-text",
            plan.plan_id,
            subtitle_id,
            "--prior-report",
            prior_id,
            "--enhancement-report",
            enh_id,
            "--json",
        ],
    )

    assert code == 0
    assert response["status"] == "failed"
    assert response["report"]["diagnostics"][0]["reason"] == "enhancement_report_not_loadable"


def test_reanalyze_fails_when_no_regeneration_fixture_is_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan, subtitle_id, prior_id, part_a = _prepare(tmp_path)
    # part-a is affected but no controlled text fixture is bound to the re-analysis
    # manifest, so the attempt cannot regenerate and fails before composing evidence.
    enh_id = _write_enhancement_report(
        tmp_path, plan, subtitle_id, part_id=part_a, cue_refs=[f"{part_a}:asr:0"]
    )

    code, response = _run(
        monkeypatch,
        capsys,
        tmp_path,
        [
            "reanalyze-text",
            plan.plan_id,
            subtitle_id,
            "--prior-report",
            prior_id,
            "--enhancement-report",
            enh_id,
            "--json",
        ],
    )

    assert code == 0
    assert response["status"] == "failed"
    assert response["report"]["diagnostics"][0]["reason"] == "reanalysis_regeneration_unavailable"
