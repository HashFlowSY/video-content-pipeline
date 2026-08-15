"""Offline proof for Phase 6 ticket 08: the complete ``analyze-text`` CLI contract.

Ticket 08 proves the whole Phase 6 public contract end to end through the CLI,
driving the Controlled offline text adapter across the approved offline fixture
matrix. The adapter is not a model asset: each scenario binds a hash-pinned
synthetic output fixture to the exact revalidated input-cue manifest, so
generation is deterministic and offline. These tests assert deterministic contract
properties — statuses, evidence-bound citations, exactly-once cue ownership, Part
boundaries, unsupported-item pruning, immutability, deterministic Markdown, and no
external side effects — never prose quality.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from video_content_pipeline import cli, text_generation
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

_OUTPUT_SCHEMA_VERSION = "phase-06-output-schema-fixture"
_ADAPTER_IDENTITY = "phase-06-controlled-text-adapter-fixture"
_FIXTURE_RELATIVE = "config/text-analysis/fixtures/controlled-output.json"


# --------------------------------------------------------------------------- #
# Fixture construction
# --------------------------------------------------------------------------- #


def _write_text_analysis_contracts(project_root: Path) -> None:
    config = project_root / "config"
    contract_dir = config / "text-analysis"
    contract_dir.mkdir(parents=True, exist_ok=True)
    (config / "text-analysis-rules.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "phase-06-fixture-rules",
                "cue_rules_version": "phase-06-cue-rules-fixture",
                "prompt_template_version": "phase-06-prompt-fixture",
                "output_schema_version": _OUTPUT_SCHEMA_VERSION,
                "evidence_rules_version": "phase-06-evidence-rules-fixture",
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
            "version": "phase-06-prompt-fixture",
            "sections": [{"id": "task", "role": "system", "text": "Segment cues."}],
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
            "version": "phase-06-evidence-rules-fixture",
        },
        "controlled-adapter.json": {
            "schema_version": 1,
            "version": _ADAPTER_IDENTITY,
            "implementation_version": "phase-06-controlled-text-adapter-impl-fixture",
            "prompt_template_version": "phase-06-prompt-fixture",
            "output_schema_version": _OUTPUT_SCHEMA_VERSION,
            "evidence_rules_version": "phase-06-evidence-rules-fixture",
            "sampling_configuration": {"mode": "deterministic", "temperature": 0, "seed": 0},
        },
    }
    for name, payload in artifacts.items():
        (contract_dir / name).write_text(
            json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8"
        )


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
        plan_id="confirmed-phase-6-generation-plan",
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


def _source_candidate_payload(texts: list[str]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "cues": [
            {
                "source_ordinal": ordinal,
                "text": text,
                "raw_pts_interval": {
                    "start": {"numerator": ordinal, "denominator": 1},
                    "end": {"numerator": ordinal + 1, "denominator": 1},
                },
            }
            for ordinal, text in enumerate(texts)
        ],
    }


def _retained_subtitle_report(
    project_root: Path,
    plan: RunPlan,
    part_cues: dict[str, list[str] | None],
) -> tuple[SubtitleCandidateReport, list[tuple[str, int, str]]]:
    """Retain a subtitle report; ``None`` cues declare a subtitle-unavailable Part.

    Returns the report and the ``(source_id, stream_index, source_candidate_sha256)``
    tuples for every available Part, which the caller uses to bind the controlled
    generation fixture to the exact input-cue manifest identity.
    """

    _write_text_analysis_contracts(project_root)
    rules_path = project_root / "config" / "subtitle-rules.json"
    rules_path.write_text(
        '{"schema_version": 1, "id": "phase-04-fixture-rules"}\n', encoding="utf-8"
    )
    report_id = "1" * 32
    multi = len(plan.source_artifacts) > 1
    if multi:
        report_path = (
            project_root / "work" / "subtitle-reports" / report_id / "report.json"
        )
    else:
        report_path = (
            project_root
            / "work"
            / plan.source_artifacts[0].source_id
            / report_id
            / "candidate-report.json"
        )
    report_path.parent.mkdir(parents=True, exist_ok=True)

    candidates: list[SubtitleCandidate] = []
    tracks: list[tuple[str, int, str]] = []
    for index, artifact in enumerate(plan.source_artifacts):
        texts = part_cues.get(artifact.source_id)
        stream_index = 1
        if texts is None:
            candidates.append(
                SubtitleCandidate(
                    source_id=artifact.source_id,
                    stream_index=stream_index,
                    state=CandidateState.INVALID,
                    diagnostic=None,
                    raw_pts_cue_intervals=(),
                )
            )
            continue
        candidate_path = report_path.parent / f"source-candidate-{index}.json"
        candidate_path.write_text(
            json.dumps(_source_candidate_payload(texts), sort_keys=True) + "\n",
            encoding="utf-8",
        )
        candidate_sha = sha256(candidate_path.read_bytes()).hexdigest()
        candidates.append(
            SubtitleCandidate(
                source_id=artifact.source_id,
                stream_index=stream_index,
                state=CandidateState.VALID,
                source_candidate_path=candidate_path.as_posix(),
                source_candidate_sha256=candidate_sha,
                source_vtt_path=(report_path.parent / f"source-{index}.vtt").as_posix(),
                readable_vtt_path=(report_path.parent / f"readable-{index}.vtt").as_posix(),
                raw_pts_cue_intervals=(),
            )
        )
        tracks.append((artifact.source_id, stream_index, candidate_sha))

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
    return report, tracks


def _bind_generation(
    project_root: Path, tracks: list[tuple[str, int, str]], result: dict[str, object]
) -> None:
    """Write the hash-pinned output fixture and bind it to the input-cue manifest."""

    manifest = text_generation.input_cue_manifest_document(tracks)
    manifest_sha = text_generation.input_cue_manifest_sha256(manifest)
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


def _cue(source_id: str, ordinal: int, stream_index: int = 1) -> str:
    return text_generation.cue_id(source_id, f"stream-{stream_index}", ordinal)


def _run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    project_root: Path,
    plan: RunPlan,
    subtitle_report: SubtitleCandidateReport,
    audio_report_id: str | None = None,
) -> dict[str, object]:
    monkeypatch.setattr(cli, "assert_runtime_policy", lambda: None)
    monkeypatch.setattr(cli, "assert_project_venv", lambda: object())
    monkeypatch.setattr(cli, "_project_root", lambda: project_root)
    argv = ["analyze-text", plan.plan_id, subtitle_report.report_id]
    if audio_report_id is not None:
        argv += ["--audio-report", audio_report_id]
    argv.append("--json")
    code = cli.main(argv)
    assert code == 0
    return json.loads(capsys.readouterr().out)


# --------------------------------------------------------------------------- #
# The fixture matrix
# --------------------------------------------------------------------------- #


def test_complete_report_has_verified_segments_chapters_and_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path, [b"part-a"])
    source_id = plan.source_artifacts[0].source_id
    report, tracks = _retained_subtitle_report(
        tmp_path, plan, {source_id: ["你好", "world", "结束"]}
    )
    result = {
        "parts": [
            {
                "part_id": source_id,
                "segments": [
                    {
                        "boundary": {
                            "start_cue_id": _cue(source_id, 0),
                            "end_cue_id": _cue(source_id, 1),
                        },
                        "content": {
                            "title": {"text": "开场白", "cue_ids": [_cue(source_id, 0)]},
                            "detailed_content": [
                                {"text": "问候", "cue_ids": [_cue(source_id, 1)]}
                            ],
                        },
                        "source_languages": ["en", "zh"],
                    },
                    {
                        "boundary": {
                            "start_cue_id": _cue(source_id, 2),
                            "end_cue_id": _cue(source_id, 2),
                        },
                        "content": {
                            "title": {"text": "收尾", "cue_ids": [_cue(source_id, 2)]}
                        },
                        "source_languages": ["zh"],
                    },
                ],
                "chapters": [{"start_ordinal": 0, "end_ordinal": 1, "title": "全片"}],
            }
        ],
        "collection_summary": {
            "entries": [
                {"segment_refs": [{"part_id": source_id, "ordinal": 0}], "text": "总述"}
            ]
        },
    }
    _bind_generation(tmp_path, tracks, result)

    response = _run(monkeypatch, capsys, tmp_path, plan, report)
    document = response["report"]

    assert response["status"] == "complete"
    assert document["status"] == "complete"
    assert [segment["ordinal"] for segment in document["segments"]] == [0, 1]
    assert document["segments"][0]["origin"] == "adjudicated"
    assert document["segments"][0]["title"]["text"] == "开场白"
    assert document["segments"][0]["source_languages"] == ["en", "zh"]
    # Every cue is owned exactly once across the Part's segments.
    owned = [cue for segment in document["segments"] for cue in segment["cue_ids"]]
    assert owned == [_cue(source_id, 0), _cue(source_id, 1), _cue(source_id, 2)]
    assert len(owned) == len(set(owned))
    assert len(document["chapters"]) == 1
    assert document["chapters"][0]["segment_ordinals"] == [0, 1]
    assert len(document["collection_summary"]["entries"]) == 1
    assert document["unsupported_item_count"] == 0
    assert document["audio_completeness"] == "not_verified"
    assert document["controlled_text_adapter"]["state"] == "controlled_generation_complete"
    assert document["attempt_provenance"]["projection"]["state"] == "projected"
    assert document["attempt_provenance"]["raw_output"]["state"] == "generated"

    # JSON authority: the retained JSON report matches exactly, Markdown is derived.
    report_path = Path(document["report_path"])
    assert json.loads(report_path.read_text(encoding="utf-8")) == document
    markdown = Path(document["rendered_report"]["path"]).read_text(encoding="utf-8")
    assert document["rendered_report"]["sha256"] == sha256(markdown.encode("utf-8")).hexdigest()
    assert "verified segments: 2" in markdown
    assert "not_verified" in markdown
    # No mutation of retained inputs, no outputs publication.
    assert not (tmp_path / "outputs").exists()


def test_out_of_segment_citation_is_pruned_but_report_stays_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path, [b"prune-case"])
    source_id = plan.source_artifacts[0].source_id
    report, tracks = _retained_subtitle_report(tmp_path, plan, {source_id: ["甲", "乙"]})
    result = {
        "parts": [
            {
                "part_id": source_id,
                "segments": [
                    {
                        "boundary": {
                            "start_cue_id": _cue(source_id, 0),
                            "end_cue_id": _cue(source_id, 1),
                        },
                        "content": {
                            "title": {"text": "标题", "cue_ids": [_cue(source_id, 0)]},
                            "numeric_values": [
                                {"text": "42", "cue_ids": [_cue(source_id, 9)]}
                            ],
                        },
                    }
                ],
                "chapters": [],
            }
        ],
        "collection_summary": None,
    }
    _bind_generation(tmp_path, tracks, result)

    document = _run(monkeypatch, capsys, tmp_path, plan, report)["report"]

    assert document["status"] == "complete"
    assert document["unsupported_item_count"] == 1
    assert document["segments"][0]["details"] == []
    assert any(
        item["reason"] == "unsupported_generated_claim"
        for item in document["segments"][0]["content_diagnostics"]
    )


def test_verified_structured_content_kinds_are_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path, [b"structured"])
    source_id = plan.source_artifacts[0].source_id
    report, tracks = _retained_subtitle_report(
        tmp_path, plan, {source_id: ["问", "答", "矛盾甲", "矛盾乙"]}
    )
    result = {
        "parts": [
            {
                "part_id": source_id,
                "segments": [
                    {
                        "boundary": {
                            "start_cue_id": _cue(source_id, 0),
                            "end_cue_id": _cue(source_id, 3),
                        },
                        "content": {
                            "title": {"text": "综合", "cue_ids": [_cue(source_id, 0)]},
                            "questions_and_answers": [
                                {
                                    "question": {
                                        "text": "为什么?",
                                        "cue_ids": [_cue(source_id, 0)],
                                    },
                                    "answer": {"text": "因为", "cue_ids": [_cue(source_id, 1)]},
                                }
                            ],
                            "people": [
                                {
                                    "reference": "讲者甲",
                                    "role": "主持",
                                    "identity_source": "named_in_subtitle",
                                    "cue_ids": [_cue(source_id, 0)],
                                }
                            ],
                            "numeric_values": [
                                {"text": "3 个要点", "cue_ids": [_cue(source_id, 1)]}
                            ],
                            "contradictions": [
                                {
                                    "sides": [
                                        {"text": "是A", "cue_ids": [_cue(source_id, 2)]},
                                        {"text": "是B", "cue_ids": [_cue(source_id, 3)]},
                                    ]
                                }
                            ],
                            "unresolved_questions": [
                                {"text": "何时?", "cue_ids": [_cue(source_id, 2)]}
                            ],
                        },
                    }
                ],
                "chapters": [],
            }
        ],
        "collection_summary": None,
    }
    _bind_generation(tmp_path, tracks, result)

    segment = _run(monkeypatch, capsys, tmp_path, plan, report)["report"]["segments"][0]

    assert segment["questions_and_answers"][0]["question"]["text"] == "为什么?"
    assert segment["people"][0]["reference"] == "讲者甲"
    assert [detail["kind"] for detail in segment["details"]] == ["numeric_value"]
    assert len(segment["contradictions"][0]["sides"]) == 2
    assert segment["unresolved_questions"][0]["text"] == "何时?"


def test_anonymous_speaker_label_never_establishes_a_person(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path, [b"anon"])
    source_id = plan.source_artifacts[0].source_id
    report, tracks = _retained_subtitle_report(tmp_path, plan, {source_id: ["独白"]})
    result = {
        "parts": [
            {
                "part_id": source_id,
                "segments": [
                    {
                        "boundary": {
                            "start_cue_id": _cue(source_id, 0),
                            "end_cue_id": _cue(source_id, 0),
                        },
                        "content": {
                            "title": {"text": "唯一", "cue_ids": [_cue(source_id, 0)]},
                            "people": [
                                {
                                    "reference": "Speaker 1",
                                    "identity_source": "speaker_label",
                                    "cue_ids": [_cue(source_id, 0)],
                                }
                            ],
                        },
                    }
                ],
                "chapters": [],
            }
        ],
        "collection_summary": None,
    }
    _bind_generation(tmp_path, tracks, result)

    document = _run(monkeypatch, capsys, tmp_path, plan, report)["report"]

    assert document["segments"][0]["people"] == []
    assert document["unsupported_item_count"] == 1


def test_coverage_breaking_boundaries_fall_back_and_report_is_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path, [b"fallback"])
    source_id = plan.source_artifacts[0].source_id
    report, tracks = _retained_subtitle_report(tmp_path, plan, {source_id: ["一", "二", "三"]})
    result = {
        "parts": [
            {
                "part_id": source_id,
                "segments": [
                    {
                        # Leaves cue 2 uncovered -> no exact tiling -> conservative fallback.
                        "boundary": {
                            "start_cue_id": _cue(source_id, 0),
                            "end_cue_id": _cue(source_id, 1),
                        },
                        "content": {
                            "title": {"text": "回退", "cue_ids": [_cue(source_id, 2)]}
                        },
                    }
                ],
                "chapters": [],
            }
        ],
        "collection_summary": {"entries": []},
    }
    _bind_generation(tmp_path, tracks, result)

    document = _run(monkeypatch, capsys, tmp_path, plan, report)["report"]

    assert document["status"] == "partial"
    assert len(document["segments"]) == 1
    assert document["segments"][0]["origin"] == "conservative_fallback"
    assert document["segments"][0]["cue_ids"] == [
        _cue(source_id, 0),
        _cue(source_id, 1),
        _cue(source_id, 2),
    ]
    reasons = {item["reason"] for item in document["diagnostics"]}
    assert "conservative_single_segment_fallback" in reasons


def test_technical_block_crossing_duplicates_are_deduplicated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path, [b"overlap"])
    source_id = plan.source_artifacts[0].source_id
    report, tracks = _retained_subtitle_report(tmp_path, plan, {source_id: ["a", "b"]})
    result = {
        "parts": [
            {
                "part_id": source_id,
                "segments": [
                    {
                        "boundary": {
                            "start_cue_id": _cue(source_id, 0),
                            "end_cue_id": _cue(source_id, 1),
                            "technical_block_id": "block-1",
                        },
                        "content": {"title": {"text": "单段", "cue_ids": [_cue(source_id, 0)]}},
                    },
                    {
                        # An overlapping technical block proposes the identical span.
                        "boundary": {
                            "start_cue_id": _cue(source_id, 0),
                            "end_cue_id": _cue(source_id, 1),
                            "technical_block_id": "block-2",
                        },
                        "content": {},
                    },
                ],
                "chapters": [],
            }
        ],
        "collection_summary": None,
    }
    _bind_generation(tmp_path, tracks, result)

    document = _run(monkeypatch, capsys, tmp_path, plan, report)["report"]

    assert document["status"] == "complete"
    assert len(document["segments"]) == 1
    assert any(item["reason"] == "boundary_duplicate" for item in document["diagnostics"])


def test_multi_part_collection_declares_an_unavailable_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path, [b"part-one", b"part-two"])
    part_a = plan.source_artifacts[0].source_id
    part_b = plan.source_artifacts[1].source_id
    report, tracks = _retained_subtitle_report(
        tmp_path, plan, {part_a: ["前", "后"], part_b: None}
    )
    result = {
        "parts": [
            {
                "part_id": part_a,
                "segments": [
                    {
                        "boundary": {
                            "start_cue_id": _cue(part_a, 0),
                            "end_cue_id": _cue(part_a, 1),
                        },
                        "content": {"title": {"text": "甲卷", "cue_ids": [_cue(part_a, 0)]}},
                    }
                ],
                "chapters": [],
            }
        ],
        "collection_summary": {
            "entries": [{"segment_refs": [{"part_id": part_a, "ordinal": 0}], "text": "跨卷"}]
        },
    }
    _bind_generation(tmp_path, tracks, result)

    document = _run(monkeypatch, capsys, tmp_path, plan, report)["report"]

    assert document["status"] == "partial"
    summary = document["collection_summary"]
    assert [item["part_id"] for item in summary["omitted_parts"]] == [part_b]
    assert summary["part_ids"] == [part_a, part_b]
    assert summary["partial"] is True
    # A Part boundary is never crossed: the only verified segment belongs to Part A.
    assert {segment["part_id"] for segment in document["segments"]} == {part_a}


def test_invalid_whole_projection_fails_and_retains_restricted_raw_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path, [b"bad-projection"])
    source_id = plan.source_artifacts[0].source_id
    report, tracks = _retained_subtitle_report(tmp_path, plan, {source_id: ["x"]})
    # ``result`` omits the required ``parts`` list -> whole projection is invalid.
    _bind_generation(tmp_path, tracks, {"collection_summary": None})

    document = _run(monkeypatch, capsys, tmp_path, plan, report)["report"]

    assert document["status"] == "failed"
    assert document["segments"] == []
    assert document["diagnostics"][0]["reason"] == "model_output_invalid"
    assert document["attempt_provenance"]["projection"]["state"] == "model_output_invalid"
    # The raw output is retained as restricted local audit evidence.
    assert document["restricted_raw_output"][0]["restriction"] == "local_audit_only"
    assert document["attempt_provenance"]["raw_output"]["state"] == "generated"


def test_input_fixture_mismatch_blocks_the_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path, [b"mismatch"])
    source_id = plan.source_artifacts[0].source_id
    report, tracks = _retained_subtitle_report(tmp_path, plan, {source_id: ["y"]})
    _bind_generation(
        tmp_path,
        tracks,
        {
            "parts": [
                {
                    "part_id": source_id,
                    "segments": [
                        {
                            "boundary": {
                                "start_cue_id": _cue(source_id, 0),
                                "end_cue_id": _cue(source_id, 0),
                            },
                            "content": {},
                        }
                    ],
                    "chapters": [],
                }
            ],
            "collection_summary": None,
        },
    )
    # Rebind the adapter to a different input identity than the retained cues.
    adapter_path = tmp_path / "config" / "text-analysis" / "controlled-adapter.json"
    adapter = json.loads(adapter_path.read_text(encoding="utf-8"))
    adapter["generation"]["input_fixture_sha256"] = "0" * 64
    adapter_path.write_text(json.dumps(adapter, sort_keys=True) + "\n", encoding="utf-8")

    document = _run(monkeypatch, capsys, tmp_path, plan, report)["report"]

    assert document["status"] == "failed"
    assert document["diagnostics"][0]["reason"] == "controlled_generation_input_mismatch"


def test_generation_is_write_once_immutable_and_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path, [b"immutable"])
    source_id = plan.source_artifacts[0].source_id
    report, tracks = _retained_subtitle_report(tmp_path, plan, {source_id: ["p", "q"]})
    result = {
        "parts": [
            {
                "part_id": source_id,
                "segments": [
                    {
                        "boundary": {
                            "start_cue_id": _cue(source_id, 0),
                            "end_cue_id": _cue(source_id, 1),
                        },
                        "content": {"title": {"text": "稳定", "cue_ids": [_cue(source_id, 0)]}},
                    }
                ],
                "chapters": [],
            }
        ],
        "collection_summary": None,
    }
    _bind_generation(tmp_path, tracks, result)
    plan_path = tmp_path / "plans" / plan.plan_id / "run-plan.json"
    plan_before = plan_path.read_bytes()
    subtitles_before = report.report_path.read_bytes()

    first = _run(monkeypatch, capsys, tmp_path, plan, report)["report"]
    second = _run(monkeypatch, capsys, tmp_path, plan, report)["report"]

    # Two attempts own separate immutable workspaces but render identically.
    assert first["report_id"] != second["report_id"]
    assert first["rendered_report"]["sha256"] == second["rendered_report"]["sha256"]
    first_segments = [segment["cue_ids"] for segment in first["segments"]]
    second_segments = [segment["cue_ids"] for segment in second["segments"]]
    assert first_segments == second_segments
    # Retained inputs are never mutated.
    assert plan_path.read_bytes() == plan_before
    assert report.report_path.read_bytes() == subtitles_before
    assert not (tmp_path / "outputs").exists()
