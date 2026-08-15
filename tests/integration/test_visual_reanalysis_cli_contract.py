"""Offline CLI contract for Phase 8 ticket 07 (visual Affected-Part re-analysis).

After ``vcp visual-text`` retains classified on-screen evidence,
``vcp reanalyze-text-visual`` starts a new immutable text-analysis attempt: it
regenerates only the Parts carrying new visual evidence (through the Controlled
offline text adapter, with their Visual page changes as candidate boundary
evidence and their admitted page-text facts owned exactly once by the resulting
segments), carries the unaffected Parts forward with a provenance link to the
retained prior report, and recomputes chapters and the collection summary over
the combined set. These tests drive the command with a retained prior report, a
retained visual-text report, real retained subtitle cue timing, and a hash-pinned
synthetic text fixture, asserting deterministic contract properties -- never prose
quality.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from video_content_pipeline import cli
from video_content_pipeline import visual_reanalysis as vr
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

_OUTPUT_SCHEMA_VERSION = "phase-08-visual-reanalysis-output-schema-fixture"
_ADAPTER_IDENTITY = "phase-08-visual-reanalysis-text-adapter-fixture"
_FIXTURE_RELATIVE = "config/text-analysis/fixtures/visual-reanalysis-output.json"
_GUARANTEES_SUBSET = {
    "frame_extraction": "not_attempted",
    "model_acquisition": "not_attempted",
    "model_execution": "not_attempted",
    "network_access": "not_attempted",
    "outputs_publication": "not_attempted",
}


# --------------------------------------------------------------------------- #
# Plan, subtitle cue evidence, and contract fixtures
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
        configuration_fingerprint="phase-08-fixture",
        inspection_evidence=tuple(evidence),
    )
    persist_plan_report(plan_report, project_root / "plans")
    plan = RunPlan(
        plan_id="confirmed-phase-8-visual-reanalysis-plan",
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


def _write_source_candidate(path: Path, cue_starts: list[int]) -> str:
    """Write a retained ``source-candidate.json`` with one cue per start second."""

    payload = {
        "schema_version": 1,
        "cues": [
            {
                "source_ordinal": ordinal,
                "text": f"字幕-{ordinal}",
                "raw_pts_interval": {
                    "start": {"numerator": start, "denominator": 1},
                    "end": {"numerator": start + 2, "denominator": 1},
                },
            }
            for ordinal, start in enumerate(cue_starts)
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    digest, _ = sha256_file(path)
    return digest


def _retained_subtitle_report(
    project_root: Path, plan: RunPlan, *, affected_cue_starts: dict[str, list[int]]
) -> SubtitleCandidateReport:
    """Retain a completed subtitle report; affected Parts get real cue evidence."""

    rules_path = project_root / "config" / "subtitle-rules.json"
    rules_path.parent.mkdir(parents=True, exist_ok=True)
    rules_path.write_text(
        '{"schema_version": 1, "id": "phase-04-fixture-rules"}\n', encoding="utf-8"
    )
    report_id = "1" * 32
    report_path = project_root / "work" / "subtitle-reports" / report_id / "report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    candidates: list[SubtitleCandidate] = []
    for index, artifact in enumerate(plan.source_artifacts):
        part_id = artifact.source_id
        candidate_path = report_path.parent / f"candidate-{index}.json"
        if part_id in affected_cue_starts:
            digest = _write_source_candidate(candidate_path, affected_cue_starts[part_id])
        else:
            digest = _write_source_candidate(candidate_path, [0])
        candidates.append(
            SubtitleCandidate(
                source_id=part_id,
                stream_index=1,
                state=CandidateState.VALID,
                source_candidate_path=candidate_path.as_posix(),
                source_candidate_sha256=digest,
                raw_pts_cue_intervals=(),
            )
        )
    report = SubtitleCandidateReport(
        report_id=report_id,
        plan_id=plan.plan_id,
        state=CandidateReportState.COMPLETED,
        subtitle_rules_fingerprint=subtitle_rules_fingerprint(project_root),
        candidates=tuple(candidates),
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
                "id": "phase-08-visual-reanalysis-fixture-rules",
                "cue_rules_version": "phase-08-cue-rules-fixture",
                "prompt_template_version": "phase-08-prompt-fixture",
                "output_schema_version": _OUTPUT_SCHEMA_VERSION,
                "evidence_rules_version": "phase-08-evidence-rules-fixture",
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
            "version": "phase-08-prompt-fixture",
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
            "version": "phase-08-evidence-rules-fixture",
        },
        "controlled-adapter.json": {
            "schema_version": 1,
            "version": _ADAPTER_IDENTITY,
            "implementation_version": "phase-08-visual-reanalysis-text-adapter-impl-fixture",
            "prompt_template_version": "phase-08-prompt-fixture",
            "output_schema_version": _OUTPUT_SCHEMA_VERSION,
            "evidence_rules_version": "phase-08-evidence-rules-fixture",
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
    visual_id: str,
    result: dict[str, object],
) -> None:
    """Write the text fixture and bind it to the visual re-analysis manifest."""

    manifest = vr.visual_reanalysis_manifest_document(
        affected_bases, prior_report_id=prior_id, visual_report_id=visual_id
    )
    manifest_sha = vr.visual_reanalysis_manifest_sha256(manifest)
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
# Retained prior and visual-text reports
# --------------------------------------------------------------------------- #


def _cue(part_id: str, ordinal: int) -> str:
    return f"{part_id}:stream-1:{ordinal}"


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


def _write_prior_report(
    project_root: Path, plan: RunPlan, subtitle_report_id: str, part_a: str, part_b: str
) -> str:
    """Retain a valid prior text-analysis report: part-a one segment, part-b one."""

    report_id = "a" * 32
    document: dict[str, object] = {
        "report_id": report_id,
        "plan_id": plan.plan_id,
        "subtitle_report_id": subtitle_report_id,
        "status": "complete",
        "audio_completeness": "not_verified",
        "segments": [
            _segment(part_a, 0, (_cue(part_a, 0), _cue(part_a, 1), _cue(part_a, 2))),
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


def _time(seconds: int) -> dict[str, int]:
    return {"numerator": seconds, "denominator": 1}


def _classified(
    part_id: str, page_id: str, pts: int, *, category: str, text: str
) -> dict[str, object]:
    return {
        "part_id": part_id,
        "visual_page_id": page_id,
        "pts": _time(pts),
        "text": text,
        "confidence": 0.95,
        "language_spans": [],
        "category": category,
    }


def _write_visual_report(
    project_root: Path,
    plan: RunPlan,
    part_id: str,
    *,
    status: str = "complete",
    pages: list[tuple[str, list[tuple[int, int]]]],
    classified: list[dict[str, object]],
    report_id: str = "c" * 32,
) -> str:
    """Retain a visual-text report with a page index and classified OCR items."""

    document = {
        "report_id": report_id,
        "plan_id": plan.plan_id,
        "status": status,
        "page_index": {
            "parts": [
                {
                    "part_id": part_id,
                    "detection_version": "d1",
                    "sampling_version": "s1",
                    "pages": [
                        {
                            "visual_page_id": page_id,
                            "content_fingerprint": page_id,
                            "appearances": [
                                {"start": _time(start), "end": _time(end), "frame_count": 1}
                                for start, end in appearances
                            ],
                            "selected_frame_pts": _time(appearances[0][0]),
                        }
                        for page_id, appearances in pages
                    ],
                    "retained_frames": [],
                }
            ]
        },
        "classification": {
            "version": "phase-08-ocr-item-classification-v1",
            "calibration_required": True,
            "parts": [
                {
                    "part_id": part_id,
                    "rules_version": "phase-08-ocr-item-classification-v1",
                    "calibration_required": True,
                    "classified": classified,
                    "excluded": [],
                }
            ],
        },
        "ocr_evidence": {"state": "projected"},
    }
    path = project_root / "work" / "visual-text-reports" / report_id / "visual-report.json"
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


def _argv(plan: RunPlan, subtitle_id: str, prior_id: str, visual_id: str) -> list[str]:
    return [
        "reanalyze-text-visual",
        plan.plan_id,
        subtitle_id,
        "--prior-report",
        prior_id,
        "--visual-text-report",
        visual_id,
        "--json",
    ]


# --------------------------------------------------------------------------- #
# The visual re-analysis contract
# --------------------------------------------------------------------------- #


def _happy(tmp_path: Path) -> tuple[RunPlan, str, str, str, str, tuple[str, ...]]:
    plan = _confirmed_plan(tmp_path, [b"visual-part-a", b"visual-part-b"])
    _write_text_analysis_contracts(tmp_path)
    part_a = plan.source_artifacts[0].source_id
    part_b = plan.source_artifacts[1].source_id
    subtitle = _retained_subtitle_report(tmp_path, plan, affected_cue_starts={part_a: [0, 2, 4]})
    prior_id = _write_prior_report(tmp_path, plan, subtitle.report_id, part_a, part_b)
    cue_ids = (_cue(part_a, 0), _cue(part_a, 1), _cue(part_a, 2))
    visual_id = _write_visual_report(
        tmp_path,
        plan,
        part_a,
        pages=[("page-01", [(0, 3)]), ("page-02", [(4, 5)])],
        classified=[
            _classified(part_a, "page-01", 1, category="page_text", text="标题甲"),
            _classified(part_a, "page-02", 5, category="page_text", text="标题乙"),
        ],
    )
    # The text model proposes no boundaries for part-a; the Visual page change is
    # the only candidate boundary evidence, so it alone must tile the Part.
    result = {
        "parts": [{"part_id": part_a, "segments": [], "chapters": []}],
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
    _bind_regeneration(
        tmp_path, {part_a: cue_ids}, prior_id=prior_id, visual_id=visual_id, result=result
    )
    return plan, subtitle.report_id, prior_id, visual_id, part_b, cue_ids


def test_visual_reanalysis_resegments_and_owns_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan, subtitle_id, prior_id, visual_id, part_b, _ = _happy(tmp_path)
    part_a = plan.source_artifacts[0].source_id
    prior_path = (
        tmp_path / "work" / "text-analysis-reports" / prior_id / "text-analysis-report.json"
    )
    prior_bytes = prior_path.read_bytes()
    visual_path = tmp_path / "work" / "visual-text-reports" / visual_id / "visual-report.json"
    visual_bytes = visual_path.read_bytes()

    code, response = _run(
        monkeypatch, capsys, tmp_path, _argv(plan, subtitle_id, prior_id, visual_id)
    )

    assert code == 0
    assert response["status"] == "complete"
    report = response["report"]
    assert report["attempt_kind"] == "visual_affected_part_reanalysis"
    for guarantee, value in _GUARANTEES_SUBSET.items():
        assert report["guarantees"][guarantee] == value

    # part-a is affected (carries visual evidence) and regenerated; part-b has no
    # visual evidence and is carried forward with a provenance link to its source.
    assert report["reanalysis"]["affected_parts"] == [part_a]
    assert report["reanalysis"]["regenerated_parts"] == [part_a]
    carried = report["reanalysis"]["carried_forward_parts"]
    assert [item["part_id"] for item in carried] == [part_b]
    assert carried[0]["source_report_id"] == prior_id
    assert carried[0]["source_report_sha256"] == sha256(prior_bytes).hexdigest()

    # AC2: the page change alone tiled part-a into two adjudicated segments (not a
    # conservative fallback) -- page changes participated as candidate boundaries.
    part_a_segments = [seg for seg in report["segments"] if seg["part_id"] == part_a]
    assert len(part_a_segments) == 2
    assert all(seg["origin"] == "adjudicated" for seg in part_a_segments)
    assert all(seg["provenance"] == "regenerated" for seg in part_a_segments)

    # AC3/AC4: each admitted page-text fact is owned by exactly one segment, and a
    # cited page fact appears only where classified page-text evidence exists.
    facts_by_ordinal = {
        seg["ordinal"]: [fact["text"] for fact in seg["visual_page_facts"]]
        for seg in part_a_segments
    }
    assert facts_by_ordinal == {0: ["标题甲"], 1: ["标题乙"]}
    assert report["reanalysis"]["visual_fact_count"] == 2

    # The carried-forward part-b segment owns no visual facts and copies no prose.
    carried_segment = next(seg for seg in report["segments"] if seg["part_id"] == part_b)
    assert carried_segment["provenance"] == "carried_forward"
    assert "visual_page_facts" not in carried_segment
    assert "title" not in carried_segment

    # AC4: chapters and the collection are recomputed over the combined set.
    assert len(report["collection_summary"]["entries"]) == 1

    # AC5: the attempt is immutable and never overwrote the prior or visual reports.
    report_path = Path(report["report_path"])
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    assert prior_path.read_bytes() == prior_bytes
    assert visual_path.read_bytes() == visual_bytes
    assert not (tmp_path / "outputs").exists()


def test_absence_of_visual_evidence_carries_everything_forward(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path, [b"visual-part-a", b"visual-part-b"])
    _write_text_analysis_contracts(tmp_path)
    part_a = plan.source_artifacts[0].source_id
    part_b = plan.source_artifacts[1].source_id
    subtitle = _retained_subtitle_report(tmp_path, plan, affected_cue_starts={})
    prior_id = _write_prior_report(tmp_path, plan, subtitle.report_id, part_a, part_b)
    # A single page with no page-text items carries no visual evidence at all.
    visual_id = _write_visual_report(
        tmp_path,
        plan,
        part_a,
        status="partial",
        pages=[("page-01", [(0, 9)])],
        classified=[_classified(part_a, "page-01", 1, category="background_ui", text="下一页")],
    )

    code, response = _run(
        monkeypatch, capsys, tmp_path, _argv(plan, subtitle.report_id, prior_id, visual_id)
    )

    assert code == 0
    report = response["report"]
    # No Part is affected; the whole prior analysis is carried forward untouched and
    # the subtitle-derived claims are never blocked by the absence of visual facts.
    assert report["reanalysis"]["affected_parts"] == []
    assert report["reanalysis"]["regenerated_parts"] == []
    assert {item["part_id"] for item in report["reanalysis"]["carried_forward_parts"]} == {
        part_a,
        part_b,
    }
    assert report["reanalysis"]["visual_fact_count"] == 0
    assert all("visual_page_facts" not in seg for seg in report["segments"])
    assert response["status"] in {"complete", "partial"}


def test_disagreeing_boundaries_defer_to_a_conservative_segment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path, [b"visual-part-a", b"visual-part-b"])
    _write_text_analysis_contracts(tmp_path)
    part_a = plan.source_artifacts[0].source_id
    part_b = plan.source_artifacts[1].source_id
    subtitle = _retained_subtitle_report(tmp_path, plan, affected_cue_starts={part_a: [0, 2, 4]})
    prior_id = _write_prior_report(tmp_path, plan, subtitle.report_id, part_a, part_b)
    cue_ids = (_cue(part_a, 0), _cue(part_a, 1), _cue(part_a, 2))
    # The Visual page change at t=4 splits before cue-2; the text model proposes a
    # *different* split (before cue-1). The two candidate sets cannot jointly tile,
    # so adjudication defers to one conservative segment -- the Phase 6 contract.
    visual_id = _write_visual_report(
        tmp_path,
        plan,
        part_a,
        pages=[("page-01", [(0, 3)]), ("page-02", [(4, 5)])],
        classified=[
            _classified(part_a, "page-01", 1, category="page_text", text="标题甲"),
            _classified(part_a, "page-02", 5, category="page_text", text="标题乙"),
        ],
    )
    result = {
        "parts": [
            {
                "part_id": part_a,
                "segments": [
                    {"boundary": {"start_cue_id": cue_ids[0], "end_cue_id": cue_ids[0]}},
                    {"boundary": {"start_cue_id": cue_ids[1], "end_cue_id": cue_ids[2]}},
                ],
                "chapters": [],
            }
        ],
        "collection_summary": {"entries": []},
    }
    _bind_regeneration(
        tmp_path, {part_a: cue_ids}, prior_id=prior_id, visual_id=visual_id, result=result
    )

    code, response = _run(
        monkeypatch, capsys, tmp_path, _argv(plan, subtitle.report_id, prior_id, visual_id)
    )

    assert code == 0
    report = response["report"]
    part_a_segments = [seg for seg in report["segments"] if seg["part_id"] == part_a]
    # Disagreement collapses to one conservative segment; the whole attempt is partial.
    assert len(part_a_segments) == 1
    assert part_a_segments[0]["origin"] == "conservative_fallback"
    assert response["status"] == "partial"
    # Both admitted page facts are still owned exactly once -- by the sole segment.
    owned = [fact["text"] for fact in part_a_segments[0]["visual_page_facts"]]
    assert owned == ["标题甲", "标题乙"]
    assert report["reanalysis"]["visual_fact_count"] == 2


def test_visual_report_from_a_different_plan_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan, subtitle_id, prior_id, visual_id, _, _ = _happy(tmp_path)
    # Corrupt the retained visual report's plan binding after it was authored.
    visual_path = tmp_path / "work" / "visual-text-reports" / visual_id / "visual-report.json"
    document = json.loads(visual_path.read_text(encoding="utf-8"))
    document["plan_id"] = "some-other-plan"
    visual_path.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    code, response = _run(
        monkeypatch, capsys, tmp_path, _argv(plan, subtitle_id, prior_id, visual_id)
    )

    assert code == 0  # the command always writes an immutable report
    assert response["status"] == "failed"
    reasons = {diag["reason"] for diag in response["report"]["diagnostics"]}
    assert "visual_reanalysis_report_mismatch" in reasons
