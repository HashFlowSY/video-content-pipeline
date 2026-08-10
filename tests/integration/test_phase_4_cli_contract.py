"""Offline CLI contract for the first Phase 4 subtitle-processing slice."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from video_content_pipeline import cli, subtitle_pipeline
from video_content_pipeline.coverage import StreamCoverage
from video_content_pipeline.external_tools import PinnedExternalTool
from video_content_pipeline.inspection import PlanInspectionEvidence, SubtitleTrackCandidate
from video_content_pipeline.planning import (
    PlanState,
    RunPlan,
    create_plan_report,
    persist_plan_report,
)
from video_content_pipeline.probe import ProbeDocument
from video_content_pipeline.source import SourceArtifact, calculate_disk_headroom, sha256_file
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval


def _confirmed_plan(
    project_root: Path,
    *,
    subtitle_codecs: tuple[str, ...] = ("subrip",),
    subtitle_stream_indexes: tuple[int, ...] | None = None,
) -> RunPlan:
    media_path = project_root / "input" / "source" / "media"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"synthetic-embedded-subtitle-source")
    digest, byte_count = sha256_file(media_path)
    artifact = SourceArtifact(digest, digest, byte_count, media_path)
    ffmpeg = PinnedExternalTool("ffmpeg", project_root / "controlled-ffmpeg", "fixture", "f" * 64)
    stream_indexes = subtitle_stream_indexes or tuple(range(1, len(subtitle_codecs) + 1))
    assert len(stream_indexes) == len(subtitle_codecs)
    subtitle_streams = [
        {"index": stream_index, "codec_type": "subtitle", "codec_name": codec}
        for stream_index, codec in zip(stream_indexes, subtitle_codecs, strict=True)
    ]
    inspection = PlanInspectionEvidence(
        source_id=artifact.source_id,
        structural_document=ProbeDocument(json.dumps({"streams": subtitle_streams})),
        coverage_document=ProbeDocument('{"packets": []}'),
        coverage_by_stream=(
            (
                0,
                StreamCoverage(
                    coverage=HalfOpenInterval(ExactTime(-1), ExactTime(2)),
                    gaps=(),
                    diagnostics=(),
                ),
            ),
        ),
        subtitle_tracks=tuple(
            SubtitleTrackCandidate(index, "zh", "matroska", "embedded", True)
            for index in stream_indexes
        ),
    )
    report = create_plan_report(
        state=PlanState.READY_FOR_CONFIRMATION,
        source_artifacts=(artifact,),
        tools=(ffmpeg,),
        planned_increment_bytes=0,
        configuration_fingerprint="phase-03-fixture",
        inspection_evidence=(inspection,),
    )
    persist_plan_report(report, project_root / "plans")
    plan = RunPlan(
        plan_id="confirmed-fixture-plan",
        report_id=report.report_id,
        source_artifacts=(artifact,),
        tools=(ffmpeg,),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint=report.configuration_fingerprint,
    )
    plan_path = project_root / "plans" / plan.plan_id / "run-plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(
        json.dumps(plan.as_json(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return plan


def _confirmed_collection_plan(project_root: Path) -> RunPlan:
    coverage_by_part = (
        HalfOpenInterval(ExactTime(-1), ExactTime(2)),
        HalfOpenInterval(ExactTime(10), ExactTime(14)),
    )
    artifacts: list[SourceArtifact] = []
    inspection_evidence: list[PlanInspectionEvidence] = []
    for ordinal, coverage in enumerate(coverage_by_part, start=1):
        media_path = project_root / "input" / "source" / f"media-{ordinal}"
        media_path.parent.mkdir(parents=True, exist_ok=True)
        media_path.write_bytes(f"synthetic-part-{ordinal}".encode("ascii"))
        digest, byte_count = sha256_file(media_path)
        artifact = SourceArtifact(digest, digest, byte_count, media_path)
        codec = "subrip" if ordinal == 1 else "hdmv_pgs_subtitle"
        artifacts.append(artifact)
        inspection_evidence.append(
            PlanInspectionEvidence(
                source_id=artifact.source_id,
                structural_document=ProbeDocument(
                    json.dumps(
                        {
                            "streams": [
                                {
                                    "index": ordinal,
                                    "codec_type": "subtitle",
                                    "codec_name": codec,
                                }
                            ]
                        }
                    )
                ),
                coverage_document=ProbeDocument('{"packets": []}'),
                coverage_by_stream=(
                    (0, StreamCoverage(coverage=coverage, gaps=(), diagnostics=())),
                ),
                subtitle_tracks=(
                    SubtitleTrackCandidate(ordinal, "zh", "matroska", "embedded", True),
                ),
            )
        )
    ffmpeg = PinnedExternalTool("ffmpeg", project_root / "controlled-ffmpeg", "fixture", "f" * 64)
    report = create_plan_report(
        state=PlanState.READY_FOR_CONFIRMATION,
        source_artifacts=tuple(artifacts),
        tools=(ffmpeg,),
        planned_increment_bytes=0,
        configuration_fingerprint="phase-03-fixture",
        inspection_evidence=tuple(inspection_evidence),
    )
    persist_plan_report(report, project_root / "plans")
    plan = RunPlan(
        plan_id="confirmed-collection-fixture-plan",
        report_id=report.report_id,
        source_artifacts=tuple(artifacts),
        tools=(ffmpeg,),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint=report.configuration_fingerprint,
    )
    plan_path = project_root / "plans" / plan.plan_id / "run-plan.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(
        json.dumps(plan.as_json(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return plan


def _configure_cli(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    payload: bytes = b"1\n00:00:00,000 --> 00:00:02,000\n\xe4\xbd\xa0\xe5\xa5\xbd, subtitle.\n",
) -> list[tuple[str, ...]]:
    (project_root / "config").mkdir()
    (project_root / "config" / "subtitle-rules.json").write_text(
        '{"schema_version": 1, "id": "phase-04-subtitle-rules-v1"}\n', encoding="utf-8"
    )
    extraction_calls: list[tuple[str, ...]] = []

    def controlled_extraction(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        extraction_calls.append(arguments)
        destination = Path(arguments[-1])
        destination.write_bytes(payload)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(cli, "assert_runtime_policy", lambda: None)
    monkeypatch.setattr(cli, "assert_project_venv", lambda: object())
    monkeypatch.setattr(cli, "_project_root", lambda: project_root)
    monkeypatch.setattr(subtitle_pipeline, "run_tool", controlled_extraction)
    monkeypatch.setattr(subtitle_pipeline, "revalidate_external_tool", lambda _tool: None)
    return extraction_calls


def test_subtitles_reports_a_partial_collection_and_asr_planning_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_collection_plan(tmp_path)
    extraction_calls = _configure_cli(
        tmp_path,
        monkeypatch,
        payload=(
            b"1\n00:00:00,000 --> 00:00:02,000\nfirst\n\n2\n00:00:01,000 --> 00:00:03,000\nsecond\n"
        ),
    )

    assert cli.main(["subtitles", plan.plan_id, "--json"]) == 0
    response = json.loads(capsys.readouterr().out)

    assert response["status"] == "partial"
    report = response["report"]
    assert report["state"] == "partial"
    assert report["caption_time_coverage"] == {
        "covered_duration": {"numerator": 3, "denominator": 1},
        "playback_duration": {"numerator": 7, "denominator": 1},
        "ratio": {"numerator": 3, "denominator": 7},
    }
    assert report["audio_completeness"] == "not_verified"
    assert report["risks"] == [
        {
            "reason": "partial_subtitle_collection",
            "message": "One or more Parts require ASR planning.",
        }
    ]
    completed, unavailable = report["part_reports"]
    assert completed["state"] == "completed"
    assert completed["selected_stream_index"] == 1
    assert completed["collection_virtual_time"] == {
        "start": {"numerator": 0, "denominator": 1},
        "end": {"numerator": 3, "denominator": 1},
    }
    assert completed["caption_time_coverage"]["ratio"] == {"numerator": 1, "denominator": 1}
    assert unavailable["source_id"] == plan.source_artifacts[1].source_id
    assert unavailable["state"] == "subtitle_unavailable_requires_asr_plan"
    assert unavailable["selected_stream_index"] is None
    assert unavailable["collection_virtual_time"] == {
        "start": {"numerator": 3, "denominator": 1},
        "end": {"numerator": 7, "denominator": 1},
    }
    assert unavailable["caption_time_coverage"] == {
        "covered_duration": {"numerator": 0, "denominator": 1},
        "playback_duration": {"numerator": 4, "denominator": 1},
        "ratio": {"numerator": 0, "denominator": 1},
    }
    assert unavailable["audio_completeness"] == "not_verified"
    assert unavailable["risks"][0]["reason"] == "subtitle_format_unsupported"
    assert unavailable["asr_planning_handoff"] == {
        "reason": "subtitle_unavailable_requires_asr_plan",
        "message": "No valid embedded subtitle track remains for this Part.",
    }
    assert extraction_calls[0][extraction_calls[0].index("-map") + 1] == "0:1"
    assert len(extraction_calls) == 1
    assert not (tmp_path / "outputs").exists()


def test_subtitles_rejects_an_unconfirmed_plan_without_reading_subtitle_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    extraction_calls = _configure_cli(tmp_path, monkeypatch)

    assert cli.main(["subtitles", "not-confirmed", "--json"]) == 0
    response = json.loads(capsys.readouterr().out)

    assert response["status"] == "blocked"
    assert response["report"]["diagnostics"][0]["reason"] == "run_plan_not_confirmed"
    assert extraction_calls == []
    report_path = (
        tmp_path / "work" / "subtitle-reports" / response["report"]["report_id"] / "report.json"
    )
    assert json.loads(report_path.read_text(encoding="utf-8")) == response["report"]


def test_subtitles_extracts_and_validates_one_utf8_srt_track_into_a_retained_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    extraction_calls = _configure_cli(tmp_path, monkeypatch)

    assert cli.main(["subtitles", plan.plan_id, "--json"]) == 0
    response = json.loads(capsys.readouterr().out)

    assert response["status"] == "completed"
    report = response["report"]
    assert report["plan_id"] == plan.plan_id
    assert report["state"] == "completed"
    assert report["subtitle_rules_fingerprint"]
    assert len(report["candidates"]) == 1
    candidate = report["candidates"][0]
    assert candidate["state"] == "valid"
    assert candidate["source_format"] == "srt"
    raw_payload = Path(candidate["raw_payload_path"])
    assert raw_payload.read_text(encoding="utf-8") == (
        "1\n00:00:00,000 --> 00:00:02,000\n你好, subtitle.\n"
    )
    assert sha256_file(raw_payload) == (
        candidate["raw_payload_sha256"],
        candidate["raw_payload_bytes"],
    )
    source_candidate = Path(candidate["source_candidate_path"])
    source_candidate_evidence = json.loads(source_candidate.read_text(encoding="utf-8"))
    assert source_candidate_evidence["cues"] == [
        {
            "raw_pts_interval": {
                "end": {"denominator": 1, "numerator": 1},
                "start": {"denominator": 1, "numerator": -1},
            },
            "source_ordinal": 0,
            "text": "你好, subtitle.",
        }
    ]
    assert extraction_calls == [
        (
            str(plan.tools[0].path),
            "-v",
            "error",
            "-nostdin",
            "-n",
            "-i",
            str(plan.source_artifacts[0].media_path),
            "-map",
            "0:1",
            "-c:s",
            "copy",
            "-f",
            "srt",
            "-fs",
            str(subtitle_pipeline.SUBTITLE_MAX_PAYLOAD_BYTES),
            str(raw_payload),
        )
    ]
    report_path = Path(report["report_path"])
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    assert not (tmp_path / "outputs").exists()


def test_subtitles_writes_lossless_source_exports_and_traceable_readable_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path, subtitle_codecs=("webvtt",))
    _configure_cli(
        tmp_path,
        monkeypatch,
        payload=(
            b"WEBVTT - source label\n\n"
            b"STYLE\n"
            b"::cue { color: lime; }\n\n"
            b"REGION\n"
            b"id:top\n\n"
            b"cue-a\n"
            b"00:00:00.000 --> 00:00:01.000 line:80%\n"
            b"<b>Hello</b> need\n\n"
            b"cue-b\n"
            b"00:00:00.500 --> 00:00:02.000\n"
            b"need <i>to act</i>\n"
        ),
    )

    assert cli.main(["subtitles", plan.plan_id, "--json"]) == 0
    candidate = json.loads(capsys.readouterr().out)["report"]["candidates"][0]

    assert Path(candidate["source_vtt_path"]).read_text(encoding="utf-8") == (
        "WEBVTT - source label\n\n"
        "STYLE\n"
        "::cue { color: lime; }\n\n"
        "REGION\n"
        "id:top\n\n"
        "cue-a\n"
        "00:00:00.000 --> 00:00:01.000 line:80%\n"
        "<b>Hello</b> need\n\n"
        "cue-b\n"
        "00:00:00.500 --> 00:00:02.000\n"
        "need <i>to act</i>\n"
    )
    assert Path(candidate["source_srt_path"]).read_text(encoding="utf-8") == (
        "cue-a\n"
        "00:00:00,000 --> 00:00:01,000\n"
        "<b>Hello</b> need\n\n"
        "cue-b\n"
        "00:00:00,500 --> 00:00:02,000\n"
        "need <i>to act</i>\n"
    )
    assert candidate["format_projection_losses"] == [
        {
            "reason": "format_projection_loss",
            "source_ordinal": None,
            "setting": "WEBVTT - source label",
        },
        {
            "reason": "format_projection_loss",
            "source_ordinal": None,
            "setting": "STYLE\n::cue { color: lime; }",
        },
        {
            "reason": "format_projection_loss",
            "source_ordinal": None,
            "setting": "REGION\nid:top",
        },
        {
            "reason": "format_projection_loss",
            "source_ordinal": 0,
            "setting": "line:80%",
        },
    ]
    assert Path(candidate["readable_vtt_path"]).read_text(encoding="utf-8") == (
        "WEBVTT\n\n"
        "cue-a\n"
        "00:00:00.000 --> 00:00:01.000 line:80%\n"
        "Hello need\n\n"
        "cue-b\n"
        "00:00:00.500 --> 00:00:02.000\n"
        " to act\n"
    )
    correction_path = Path(candidate["readable_corrections_path"])
    corrections = json.loads(correction_path.read_text(encoding="utf-8"))
    assert corrections["corrections"] == [
        {
            "compared_to_source_ordinal": 0,
            "reason": "proven_rolling_overlap",
            "source_character_range": None,
            "source_ordinal": 1,
            "source_token_range": [0, 1],
        },
        {
            "compared_to_source_ordinal": None,
            "reason": "approved_markup_removed",
            "source_character_range": [0, 3],
            "source_ordinal": 0,
            "source_token_range": [0, 1],
        },
        {
            "compared_to_source_ordinal": None,
            "reason": "approved_markup_removed",
            "source_character_range": [8, 12],
            "source_ordinal": 0,
            "source_token_range": [0, 1],
        },
        {
            "compared_to_source_ordinal": None,
            "reason": "approved_markup_removed",
            "source_character_range": [5, 8],
            "source_ordinal": 1,
            "source_token_range": [2, 3],
        },
        {
            "compared_to_source_ordinal": None,
            "reason": "approved_markup_removed",
            "source_character_range": [14, 18],
            "source_ordinal": 1,
            "source_token_range": [4, 5],
        },
    ]


def test_subtitles_converts_mov_text_to_a_retained_srt_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path, subtitle_codecs=("mov_text",))
    extraction_calls = _configure_cli(tmp_path, monkeypatch)

    assert cli.main(["subtitles", plan.plan_id, "--json"]) == 0
    candidate = json.loads(capsys.readouterr().out)["report"]["candidates"][0]

    assert candidate["state"] == "valid"
    assert candidate["source_format"] == "srt"
    assert extraction_calls[0][-7:] == (
        "-c:s",
        "srt",
        "-f",
        "srt",
        "-fs",
        str(subtitle_pipeline.SUBTITLE_MAX_PAYLOAD_BYTES),
        candidate["raw_payload_path"],
    )


def test_subtitles_preserves_original_cue_order_in_source_exports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _configure_cli(
        tmp_path,
        monkeypatch,
        payload=(
            b"1\n00:00:02,000 --> 00:00:03,000\nsecond in source\n\n"
            b"2\n00:00:00,000 --> 00:00:01,000\nfirst in source\n"
        ),
    )

    assert cli.main(["subtitles", plan.plan_id, "--json"]) == 0
    candidate = json.loads(capsys.readouterr().out)["report"]["candidates"][0]

    for export_path in (candidate["source_vtt_path"], candidate["source_srt_path"]):
        export = Path(export_path).read_text(encoding="utf-8")
        assert export.index("second in source") < export.index("first in source")


def test_subtitles_rejects_a_drifted_plan_without_reading_subtitle_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    extraction_calls = _configure_cli(tmp_path, monkeypatch)
    plan.source_artifacts[0].media_path.write_bytes(b"changed-after-confirmation")

    assert cli.main(["subtitles", plan.plan_id, "--json"]) == 0
    response = json.loads(capsys.readouterr().out)

    assert response["status"] == "blocked"
    assert response["report"]["diagnostics"] == [
        {"reason": "source_artifact_changed", "message": "A SourceArtifact hash no longer matches."}
    ]
    assert extraction_calls == []


def test_subtitles_retains_a_partial_failed_extraction_as_an_incomplete_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _configure_cli(tmp_path, monkeypatch)

    def failed_extraction(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        Path(arguments[-1]).write_bytes(b"partial subtitle bytes")
        return subprocess.CompletedProcess(arguments, 1, "", "controlled extraction failure")

    monkeypatch.setattr(subtitle_pipeline, "run_tool", failed_extraction)

    assert cli.main(["subtitles", plan.plan_id, "--json"]) == 0
    response = json.loads(capsys.readouterr().out)

    assert response["status"] == "blocked"
    candidate = response["report"]["candidates"][0]
    assert candidate["state"] == "incomplete"
    part_report = response["report"]["part_reports"][0]
    assert part_report["state"] == "subtitle_unavailable_requires_asr_plan"
    assert part_report["asr_planning_handoff"] == {
        "reason": "subtitle_unavailable_requires_asr_plan",
        "message": "No valid embedded subtitle track remains for this Part.",
    }
    raw_payload = Path(candidate["raw_payload_path"])
    assert raw_payload.read_bytes() == b"partial subtitle bytes"
    assert sha256_file(raw_payload) == (
        candidate["raw_payload_sha256"],
        candidate["raw_payload_bytes"],
    )


def test_subtitles_keeps_an_unsupported_second_track_unavailable_without_extracting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path, subtitle_codecs=("subrip", "hdmv_pgs_subtitle"))
    extraction_calls = _configure_cli(tmp_path, monkeypatch)

    assert cli.main(["subtitles", plan.plan_id, "--json"]) == 0
    response = json.loads(capsys.readouterr().out)

    assert response["status"] == "completed"
    assert [candidate["state"] for candidate in response["report"]["candidates"]] == [
        "valid",
        "unavailable",
    ]
    assert len(extraction_calls) == 1


def test_subtitles_requires_an_explicit_selection_for_ambiguous_valid_tracks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path, subtitle_codecs=("subrip", "subrip"))
    extraction_calls = _configure_cli(tmp_path, monkeypatch)

    assert cli.main(["subtitles", plan.plan_id, "--json"]) == 0
    initial = json.loads(capsys.readouterr().out)

    assert initial["status"] == "awaiting_subtitle_selection"
    report = initial["report"]
    assert report["state"] == "awaiting_subtitle_selection"
    assert [candidate["stream_index"] for candidate in report["candidates"]] == [1, 2]
    assert all(candidate["state"] == "valid" for candidate in report["candidates"])
    original_report = Path(report["report_path"]).read_text(encoding="utf-8")

    selection = f"{plan.source_artifacts[0].source_id}=2"
    assert (
        cli.main(
            [
                "subtitles",
                plan.plan_id,
                "--resume",
                report["report_id"],
                "--select",
                selection,
                "--json",
            ]
        )
        == 0
    )
    resumed = json.loads(capsys.readouterr().out)

    assert resumed["status"] == "completed"
    assert resumed["report"]["parent_report_id"] == report["report_id"]
    assert resumed["report"]["selections"] == [
        {"source_id": plan.source_artifacts[0].source_id, "stream_index": 2}
    ]
    assert len(extraction_calls) == 2
    assert Path(report["report_path"]).read_text(encoding="utf-8") == original_report


def test_subtitle_selection_can_resume_a_zero_index_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(
        tmp_path,
        subtitle_codecs=("subrip", "subrip"),
        subtitle_stream_indexes=(0, 1),
    )
    _configure_cli(tmp_path, monkeypatch)

    assert cli.main(["subtitles", plan.plan_id, "--json"]) == 0
    report = json.loads(capsys.readouterr().out)["report"]

    selection = f"{plan.source_artifacts[0].source_id}=0"
    assert (
        cli.main(
            [
                "subtitles",
                plan.plan_id,
                "--resume",
                report["report_id"],
                "--select",
                selection,
                "--json",
            ]
        )
        == 0
    )
    resumed = json.loads(capsys.readouterr().out)

    assert resumed["status"] == "completed"
    assert resumed["report"]["selections"] == [
        {"source_id": plan.source_artifacts[0].source_id, "stream_index": 0}
    ]


def test_subtitle_selection_resume_revalidates_before_reusing_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path, subtitle_codecs=("subrip", "subrip"))
    extraction_calls = _configure_cli(tmp_path, monkeypatch)

    assert cli.main(["subtitles", plan.plan_id, "--json"]) == 0
    report = json.loads(capsys.readouterr().out)["report"]
    original_report = Path(report["report_path"]).read_text(encoding="utf-8")
    plan.source_artifacts[0].media_path.write_bytes(b"changed-after-ambiguous-report")

    selection = f"{plan.source_artifacts[0].source_id}=1"
    assert (
        cli.main(
            [
                "subtitles",
                plan.plan_id,
                "--resume",
                report["report_id"],
                "--select",
                selection,
                "--json",
            ]
        )
        == 0
    )
    response = json.loads(capsys.readouterr().out)

    assert response["status"] == "blocked"
    assert response["report"]["diagnostics"] == [
        {"reason": "source_artifact_changed", "message": "A SourceArtifact hash no longer matches."}
    ]
    assert len(extraction_calls) == 2
    assert Path(report["report_path"]).read_text(encoding="utf-8") == original_report


def test_subtitle_selection_cannot_promote_an_invalid_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path, subtitle_codecs=("subrip", "subrip", "subrip"))
    _configure_cli(tmp_path, monkeypatch)

    def controlled_extraction(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        destination = Path(arguments[-1])
        if arguments[arguments.index("-map") + 1] == "0:3":
            destination.write_bytes(b"not a subtitle payload")
        else:
            destination.write_bytes(b"1\n00:00:00,000 --> 00:00:02,000\nvalid track\n")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(subtitle_pipeline, "run_tool", controlled_extraction)
    assert cli.main(["subtitles", plan.plan_id, "--json"]) == 0
    report = json.loads(capsys.readouterr().out)["report"]

    selection = f"{plan.source_artifacts[0].source_id}=3"
    assert (
        cli.main(
            [
                "subtitles",
                plan.plan_id,
                "--resume",
                report["report_id"],
                "--select",
                selection,
                "--json",
            ]
        )
        == 0
    )
    response = json.loads(capsys.readouterr().out)
    source_id = plan.source_artifacts[0].source_id

    assert [candidate["state"] for candidate in report["candidates"]] == [
        "valid",
        "valid",
        "invalid",
    ]
    assert response["status"] == "blocked"
    assert response["report"]["diagnostics"] == [
        {
            "reason": "subtitle_selection_invalid",
            "message": (f"Part {source_id} does not have a valid selected subtitle stream."),
        }
    ]


def test_subtitles_requires_and_records_an_explicit_decoder_for_ambiguous_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _configure_cli(tmp_path, monkeypatch, payload=b"1\n00:00:00,000 --> 00:00:01,000\ncaf\xe9\n")

    assert cli.main(["subtitles", plan.plan_id, "--json"]) == 0
    initial = json.loads(capsys.readouterr().out)
    candidate = initial["report"]["candidates"][0]
    assert initial["status"] == "blocked"
    assert candidate["state"] == "encoding_ambiguous"
    assert candidate["decoder"] is None

    decoder = f"{plan.source_artifacts[0].source_id}=1=cp1252"
    assert (
        cli.main(
            [
                "subtitles",
                plan.plan_id,
                "--resume",
                initial["report"]["report_id"],
                "--decoder",
                decoder,
                "--json",
            ]
        )
        == 0
    )
    resumed = json.loads(capsys.readouterr().out)
    resolved = resumed["report"]["candidates"][0]
    assert resumed["status"] == "completed"
    assert resolved["state"] == "valid"
    assert resolved["decoder"] == "cp1252"
    assert "café" in Path(resolved["source_vtt_path"]).read_text(encoding="utf-8")


def test_subtitles_decodes_an_ambiguous_sibling_then_requires_track_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path, subtitle_codecs=("subrip", "subrip"))
    _configure_cli(tmp_path, monkeypatch)

    def mixed_extraction(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        destination = Path(arguments[-1])
        if arguments[arguments.index("-map") + 1] == "0:2":
            destination.write_bytes(b"1\n00:00:00,000 --> 00:00:01,000\ncaf\xe9\n")
        else:
            destination.write_bytes(b"1\n00:00:00,000 --> 00:00:01,000\nutf8\n")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(subtitle_pipeline, "run_tool", mixed_extraction)
    assert cli.main(["subtitles", plan.plan_id, "--json"]) == 0
    initial = json.loads(capsys.readouterr().out)
    assert initial["status"] == "completed"
    assert [candidate["state"] for candidate in initial["report"]["candidates"]] == [
        "valid",
        "encoding_ambiguous",
    ]

    source_id = plan.source_artifacts[0].source_id
    decoder = f"{source_id}=2=cp1252"
    assert (
        cli.main(
            [
                "subtitles",
                plan.plan_id,
                "--resume",
                initial["report"]["report_id"],
                "--decoder",
                decoder,
                "--json",
            ]
        )
        == 0
    )
    decoded = json.loads(capsys.readouterr().out)
    assert decoded["status"] == "awaiting_subtitle_selection"
    assert decoded["report"]["candidates"][1]["decoder"] == "cp1252"

    assert (
        cli.main(
            [
                "subtitles",
                plan.plan_id,
                "--resume",
                decoded["report"]["report_id"],
                "--select",
                f"{source_id}=2",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "completed"


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        ("timeout", "subtitle_extraction_timeout"),
        ("interrupt", "subtitle_extraction_interrupted"),
        ("size", "extraction_size_limit"),
    ],
)
def test_subtitles_retains_bounded_extraction_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
    reason: str,
) -> None:
    plan = _confirmed_plan(tmp_path)
    _configure_cli(tmp_path, monkeypatch)

    def failed_extraction(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        destination = Path(arguments[-1])
        if failure == "timeout":
            destination.write_bytes(b"partial")
            raise subprocess.TimeoutExpired(arguments, 1)
        if failure == "interrupt":
            destination.write_bytes(b"partial")
            raise KeyboardInterrupt
        with destination.open("wb") as payload:
            payload.seek(subtitle_pipeline.SUBTITLE_MAX_PAYLOAD_BYTES - 1)
            payload.write(b"x")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(subtitle_pipeline, "run_tool", failed_extraction)

    assert cli.main(["subtitles", plan.plan_id, "--json"]) == 0
    response = json.loads(capsys.readouterr().out)
    candidate = response["report"]["candidates"][0]
    assert response["status"] == "blocked"
    assert candidate["state"] == "incomplete"
    assert candidate["diagnostic"]["reason"] == reason
    assert candidate["attempt_id"] == response["report"]["report_id"]


def test_subtitles_blocks_before_extraction_when_disk_preflight_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    extraction_calls = _configure_cli(tmp_path, monkeypatch)

    def no_headroom(_root: Path, _requirement: object) -> None:
        raise subtitle_pipeline.SourceIntakeError(
            "disk_headroom_insufficient", "controlled disk preflight failure"
        )

    monkeypatch.setattr(subtitle_pipeline, "ensure_disk_headroom", no_headroom)

    assert cli.main(["subtitles", plan.plan_id, "--json"]) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "blocked"
    assert response["report"]["diagnostics"] == [
        {
            "reason": "disk_headroom_insufficient",
            "message": "controlled disk preflight failure",
        }
    ]
    assert extraction_calls == []


def test_subtitles_rejects_decoder_choices_for_nonambiguous_or_unknown_tracks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    extraction_calls = _configure_cli(tmp_path, monkeypatch)
    source_id = plan.source_artifacts[0].source_id

    assert (
        cli.main(
            [
                "subtitles",
                plan.plan_id,
                "--decoder",
                f"{source_id}=1=cp1252",
                "--json",
            ]
        )
        == 0
    )
    nonambiguous = json.loads(capsys.readouterr().out)
    assert nonambiguous["status"] == "blocked"
    assert nonambiguous["report"]["diagnostics"][0]["reason"] == "subtitle_decoder_not_required"

    assert (
        cli.main(
            [
                "subtitles",
                plan.plan_id,
                "--decoder",
                f"{source_id}=99=cp1252",
                "--json",
            ]
        )
        == 0
    )
    unknown = json.loads(capsys.readouterr().out)
    assert unknown["status"] == "blocked"
    assert unknown["report"]["diagnostics"][0]["reason"] == "subtitle_decoder_invalid"
    assert len(extraction_calls) == 2


def test_subtitles_retains_a_filesystem_resource_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _configure_cli(tmp_path, monkeypatch)

    def unavailable_destination(_arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        raise OSError("controlled workspace failure")

    monkeypatch.setattr(subtitle_pipeline, "run_tool", unavailable_destination)

    assert cli.main(["subtitles", plan.plan_id, "--json"]) == 0
    response = json.loads(capsys.readouterr().out)
    candidate = response["report"]["candidates"][0]
    assert response["status"] == "blocked"
    assert candidate["state"] == "incomplete"
    assert candidate["diagnostic"]["reason"] == "subtitle_extraction_resource_failure"


def test_subtitles_retries_to_a_new_attempt_without_reusing_incomplete_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _confirmed_plan(tmp_path)
    _configure_cli(tmp_path, monkeypatch)
    attempts = 0

    def retrying_extraction(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        nonlocal attempts
        attempts += 1
        destination = Path(arguments[-1])
        if attempts == 1:
            destination.write_bytes(b"partial")
            return subprocess.CompletedProcess(arguments, 1, "", "interrupted fixture")
        destination.write_bytes(b"1\n00:00:00,000 --> 00:00:01,000\nretry\n")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(subtitle_pipeline, "run_tool", retrying_extraction)
    assert cli.main(["subtitles", plan.plan_id, "--json"]) == 0
    first = json.loads(capsys.readouterr().out)
    first_candidate = first["report"]["candidates"][0]
    assert first_candidate["state"] == "incomplete"

    assert cli.main(["subtitles", plan.plan_id, "--json"]) == 0
    second = json.loads(capsys.readouterr().out)
    second_candidate = second["report"]["candidates"][0]
    assert second_candidate["state"] == "valid"
    assert first["report"]["report_id"] != second["report"]["report_id"]
    assert first_candidate["raw_payload_path"] != second_candidate["raw_payload_path"]
    assert Path(first_candidate["raw_payload_path"]).read_bytes() == b"partial"
    assert attempts == 2
