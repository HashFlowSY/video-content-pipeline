from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_content_pipeline import cli
from video_content_pipeline.cli import _parser
from video_content_pipeline.external_tools import PinnedExternalTool
from video_content_pipeline.inspection import PlanInspectionEvidence
from video_content_pipeline.planning import (
    PlanningError,
    PlanState,
    ThreePointEstimate,
    create_plan_report,
    load_plan_report,
    persist_plan_report,
)
from video_content_pipeline.probe import ProbeDocument
from video_content_pipeline.source import SourceArtifact, SourceIntakeError, sha256_file


def test_phase_3_cli_exposes_local_url_collection_decode_and_confirmation_forms() -> None:
    parser = _parser()

    local = parser.parse_args(["plan", "/tmp/source.mp4"])
    url = parser.parse_args(["plan", "https://example.test/video", "--url-mode", "filtered"])
    collection = parser.parse_args(["plan", "--collect", "--url-mode", "direct"])
    decode = parser.parse_args(["plan", "decode", "report-id"])
    confirm = parser.parse_args(["plan", "confirm", "report-id"])

    assert local.target == "/tmp/source.mp4"
    assert url.url_mode == "filtered"
    assert collection.collect is True
    assert decode.target == "decode" and decode.report_id == "report-id"
    assert confirm.target == "confirm" and confirm.report_id == "report-id"


def test_public_url_cli_persists_only_redacted_authorization_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _prepare_phase_3_cli(monkeypatch, tmp_path)

    exit_code = cli.main(
        [
            "plan",
            "https://example.test/watch/1?token=secret#fragment",
            "--url-mode",
            "filtered",
            "--json",
        ]
    )

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "blocked"
    assert result["report"]["url_authorizations"] == [
        {
            "mode": "filtered",
            "provenance": {
                "scheme": "https",
                "host": "example.test",
                "path": "/watch/1",
                "transport_integrity_verified": True,
            },
        }
    ]
    report_path = _report_path(tmp_path, result["report"])
    assert "secret" not in report_path.read_text(encoding="utf-8")
    assert (
        load_plan_report(report_path).as_json()["url_authorizations"]
        == result["report"]["url_authorizations"]
    )


def test_manual_collection_cli_preserves_input_order_and_closes_on_endsignal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _prepare_phase_3_cli(monkeypatch, tmp_path)
    submitted = iter(
        [
            "https://example.test/part-two?signature=secret",
            "https://example.test/part-one#fragment",
            "结束",
        ]
    )

    def read_line(_prompt: str) -> str:
        return next(submitted)

    monkeypatch.setattr(cli, "_read_collection_line", read_line)

    exit_code = cli.main(["plan", "--collect", "--url-mode", "direct", "--json"])

    assert exit_code == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["status"] == "blocked"
    assert [entry["provenance"]["path"] for entry in result["report"]["url_authorizations"]] == [
        "/part-two",
        "/part-one",
    ]
    assert "presentation order" in captured.err
    report_path = _report_path(tmp_path, result["report"])
    assert "secret" not in report_path.read_text(encoding="utf-8")


def test_manual_collection_cli_persists_a_blocked_report_for_duplicate_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _prepare_phase_3_cli(monkeypatch, tmp_path)
    submitted = iter(
        ["https://example.test/part?token=secret", "https://example.test/part?token=secret"]
    )
    monkeypatch.setattr(cli, "_read_collection_line", lambda _prompt: next(submitted))

    exit_code = cli.main(["plan", "--collect", "--url-mode", "direct", "--json"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "blocked"
    assert result["report"]["diagnostics"][0]["reason"] == "duplicate_url"
    assert len(result["report"]["url_authorizations"]) == 1
    assert "secret" not in _report_path(tmp_path, result["report"]).read_text(encoding="utf-8")


def _prepare_phase_3_cli(monkeypatch: pytest.MonkeyPatch, project_root: Path) -> None:
    monkeypatch.setattr(cli, "assert_runtime_policy", lambda: None)
    monkeypatch.setattr(cli, "assert_project_venv", lambda: object())
    monkeypatch.setattr(cli, "_project_root", lambda: project_root)


def _report_path(project_root: Path, report: dict[str, object]) -> Path:
    report_id = report["report_id"]
    assert isinstance(report_id, str)
    return project_root / "plans" / "reports" / report_id / "plan-report.json"


def test_local_non_regular_input_returns_a_retained_blocked_report(tmp_path: Path) -> None:
    result = cli._plan_local_file(tmp_path, tmp_path, tmp_path / "plans")

    assert result["status"] == "blocked"
    report = result["report"]
    assert report["diagnostics"] == [
        {
            "reason": "source_not_regular_file",
            "message": "A local source must be one regular file, not a directory or stream.",
        }
    ]
    report_path = tmp_path / "plans" / "reports" / report["report_id"] / "plan-report.json"
    assert json.loads(report_path.read_text(encoding="utf-8")) == report


def test_changed_local_input_returns_a_retained_blocked_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source")

    def changed_source(*_args: object, before_copy: object | None = None) -> None:
        assert before_copy is not None
        before_copy(6)  # type: ignore[operator]
        raise SourceIntakeError("source_changed_during_snapshot", "Source changed during copy.")

    monkeypatch.setattr(cli, "snapshot_local_source", changed_source)

    result = cli._plan_local_file(source_path, tmp_path, tmp_path / "plans")

    assert result["status"] == "blocked"
    assert result["report"]["diagnostics"] == [
        {"reason": "source_changed_during_snapshot", "message": "Source changed during copy."}
    ]


def test_insufficient_disk_headroom_returns_a_retained_blocked_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source")

    def insufficient_headroom(*_args: object) -> None:
        raise SourceIntakeError("disk_headroom_insufficient", "Not enough space.")

    monkeypatch.setattr(cli, "ensure_disk_headroom", insufficient_headroom)

    result = cli._plan_local_file(source_path, tmp_path, tmp_path / "plans")

    assert result["status"] == "blocked"
    assert result["report"]["diagnostics"] == [
        {"reason": "disk_headroom_insufficient", "message": "Not enough space."}
    ]


def test_disk_headroom_is_checked_from_the_precopy_snapshot_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source")
    requirements: list[int] = []

    def record_headroom(_root: Path, requirement: object) -> None:
        requirements.append(requirement.increment_bytes)  # type: ignore[attr-defined]

    def changed_source(
        _source_path: Path, _input_root: Path, *, before_copy: object | None = None
    ) -> None:
        assert before_copy is not None
        before_copy(4096)  # type: ignore[operator]
        raise SourceIntakeError("source_changed_during_snapshot", "Source changed during copy.")

    monkeypatch.setattr(cli, "ensure_disk_headroom", record_headroom)
    monkeypatch.setattr(cli, "snapshot_local_source", changed_source)

    result = cli._plan_local_file(source_path, tmp_path, tmp_path / "plans")

    assert result["status"] == "blocked"
    assert requirements == [4096 * 2 + 64 * 1024**2]


def test_invalid_inspection_evidence_returns_a_retained_blocked_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source")
    ffprobe = PinnedExternalTool("ffprobe", tmp_path / "ffprobe", "test", "a" * 64)
    ffmpeg = PinnedExternalTool("ffmpeg", tmp_path / "ffmpeg", "test", "b" * 64)

    def configured_tool(_root: Path, tool_id: str) -> PinnedExternalTool:
        return {"ffprobe": ffprobe, "ffmpeg": ffmpeg}[tool_id]

    def invalid_probe_documents(*_args: object) -> tuple[ProbeDocument, ProbeDocument]:
        return ProbeDocument('{"streams": []}'), ProbeDocument('{"packets": []}')

    monkeypatch.setattr(cli, "_configured_tool", configured_tool)
    monkeypatch.setattr(cli, "capture_probe_documents", invalid_probe_documents)

    result = cli._plan_local_file(source_path, tmp_path, tmp_path / "plans")

    assert result["status"] == "blocked"
    report = result["report"]
    assert report["source_artifacts"]
    assert report["tools"] == [ffprobe.as_json(), ffmpeg.as_json()]
    assert report["diagnostics"] == [
        {
            "reason": "probe_invalid",
            "message": "Structural ProbeDocument has no valid typed projection.",
        }
    ]
    assert report["inspection_evidence"] == [
        {
            "source_id": report["source_artifacts"][0]["source_id"],
            "structural_probe_document": {"raw_json": '{"streams": []}'},
            "coverage_probe_document": {"raw_json": '{"packets": []}'},
            "stream_coverage": [],
            "subtitle_track_candidates": [],
        }
    ]
    report_path = tmp_path / "plans" / "reports" / report["report_id"] / "plan-report.json"
    assert json.loads(report_path.read_text(encoding="utf-8")) == report


def test_decode_confirmation_only_advances_to_final_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _awaiting_decode_report(tmp_path)
    persist_plan_report(report, tmp_path / "plans")
    validated_source_ids: list[str] = []

    def no_stale_evidence(*_args: object) -> tuple[object, ...]:
        return ()

    def record_validation(_tool: PinnedExternalTool, artifact: SourceArtifact) -> int:
        validated_source_ids.append(artifact.source_id)
        return 1

    monkeypatch.setattr(cli, "revalidate_report", no_stale_evidence)
    monkeypatch.setattr(cli, "perform_full_decode_validation", record_validation)

    result = cli._decode_report(report.report_id, tmp_path, tmp_path / "plans")

    assert result["status"] == "ready_for_confirmation"
    assert result["report"]["state"] == "ready_for_confirmation"
    assert result["report"]["parent_report_id"] == report.report_id
    assert result["report"]["decode_estimate"] == report.decode_estimate.as_json()
    assert validated_source_ids == [report.source_artifacts[0].source_id]
    assert json.loads(
        (tmp_path / "plans" / "decode-throughput-history.json").read_text(encoding="utf-8")
    ) == {
        "schema_version": 1,
        "measurements": [{"source_id": report.source_artifacts[0].source_id, "elapsed_seconds": 1}],
    }


def test_decode_failure_writes_a_blocked_child_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _awaiting_decode_report(tmp_path)
    persist_plan_report(report, tmp_path / "plans")

    def no_stale_evidence(*_args: object) -> tuple[object, ...]:
        return ()

    def failed_validation(*_args: object) -> None:
        raise PlanningError("full_decode_failed", "FFmpeg rejected a stream.")

    monkeypatch.setattr(cli, "revalidate_report", no_stale_evidence)
    monkeypatch.setattr(cli, "perform_full_decode_validation", failed_validation)

    result = cli._decode_report(report.report_id, tmp_path, tmp_path / "plans")

    assert result["status"] == "blocked"
    assert result["report"]["state"] == "blocked"
    assert result["report"]["parent_report_id"] == report.report_id
    assert result["report"]["diagnostics"] == [
        {"reason": "full_decode_failed", "message": "FFmpeg rejected a stream."}
    ]
    blocked_path = (
        tmp_path / "plans" / "reports" / result["report"]["report_id"] / "plan-report.json"
    )
    assert json.loads(blocked_path.read_text(encoding="utf-8")) == result["report"]


def _awaiting_decode_report(tmp_path: Path):
    media_path = tmp_path / "input" / "source" / "media"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"source")
    digest, byte_count = sha256_file(media_path)
    artifact = SourceArtifact(digest, digest, byte_count, media_path)
    inspection = PlanInspectionEvidence(
        source_id=artifact.source_id,
        structural_document=ProbeDocument('{"streams": []}'),
        coverage_document=ProbeDocument('{"packets": []}'),
        coverage_by_stream=(),
        subtitle_tracks=(),
    )
    return create_plan_report(
        state=PlanState.AWAITING_DECODE_CONFIRMATION,
        source_artifacts=(artifact,),
        tools=(PinnedExternalTool("ffmpeg", tmp_path / "ffmpeg", "test", "f" * 64),),
        planned_increment_bytes=byte_count,
        configuration_fingerprint="config-v1",
        decode_estimate=ThreePointEstimate(1, 2, 3, "low", "decode-throughput-profile:v1"),
        inspection_evidence=(inspection,),
    )
