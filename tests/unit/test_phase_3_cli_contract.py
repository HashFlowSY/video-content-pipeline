from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_content_pipeline import cli
from video_content_pipeline.acquisition import MediaDownloadPlan, URLAcquisitionError
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


@pytest.mark.parametrize(
    "target",
    [
        "HTTPS://example.test/watch?token=secret",
        "ftp://example.test/watch?token=secret",
    ],
)
def test_url_shaped_input_never_persists_its_raw_locator(
    target: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _prepare_phase_3_cli(monkeypatch, tmp_path)

    exit_code = cli.main(["plan", target, "--url-mode", "direct", "--json"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "blocked"
    assert "secret" not in _report_path(tmp_path, result["report"]).read_text(encoding="utf-8")


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


def test_public_url_acquisition_enters_the_local_probe_and_decode_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _prepare_phase_3_cli(monkeypatch, tmp_path)
    _write_url_planning_configuration(tmp_path)
    artifact = _public_artifact(tmp_path)
    yt_dlp = PinnedExternalTool("yt-dlp", tmp_path / "yt-dlp", "test", "a" * 64)
    ffprobe = PinnedExternalTool("ffprobe", tmp_path / "ffprobe", "test", "b" * 64)
    ffmpeg = PinnedExternalTool("ffmpeg", tmp_path / "ffmpeg", "test", "c" * 64)

    monkeypatch.setattr(
        cli,
        "_configured_tool",
        lambda _root, tool_id: {"yt-dlp": yt_dlp, "ffprobe": ffprobe, "ffmpeg": ffmpeg}[tool_id],
    )
    monkeypatch.setattr(cli, "acquire_public_source", lambda *_args: artifact)
    monkeypatch.setattr(cli, "capture_probe_documents", lambda *_args: _valid_probe_documents())

    exit_code = cli.main(
        ["plan", "https://example.test/watch?token=secret", "--url-mode", "direct", "--json"]
    )

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "awaiting_decode_confirmation"
    assert result["report"]["source_artifacts"] == [artifact.as_json()]
    assert result["report"]["tools"] == [yt_dlp.as_json(), ffprobe.as_json(), ffmpeg.as_json()]
    assert result["report"]["url_authorizations"][0]["provenance"]["path"] == "/watch"
    assert "secret" not in _report_path(tmp_path, result["report"]).read_text(encoding="utf-8")


def _acquire_after_confirmation(artifact: SourceArtifact) -> object:
    """A stand-in acquisition that mirrors the real plan-then-confirm contract."""

    def fake_acquire(
        authorization: object, _downloader: object, _project_root: object, confirm: object
    ) -> SourceArtifact:
        plan = MediaDownloadPlan(
            provenance=authorization.provenance,  # type: ignore[attr-defined]
            media_hosts=("cdn-a.fake.test", "example.test"),
            byte_count=12,
            duration_seconds=61.5,
            planned_increment_bytes=12 * 2 + 64 * 1024**2,
            required_free_bytes=12 * 2 + 64 * 1024**2 + 1024**3,
        )
        if not confirm(plan):  # type: ignore[operator]
            raise URLAcquisitionError("download_plan_unconfirmed", "declined in test")
        return artifact

    return fake_acquire


def test_public_url_download_plan_is_disclosed_and_confirmed_before_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _prepare_phase_3_cli(monkeypatch, tmp_path)
    _write_url_planning_configuration(tmp_path)
    artifact = _public_artifact(tmp_path)
    yt_dlp = PinnedExternalTool("yt-dlp", tmp_path / "yt-dlp", "test", "a" * 64)
    ffprobe = PinnedExternalTool("ffprobe", tmp_path / "ffprobe", "test", "b" * 64)
    ffmpeg = PinnedExternalTool("ffmpeg", tmp_path / "ffmpeg", "test", "c" * 64)

    monkeypatch.setattr(
        cli,
        "_configured_tool",
        lambda _root, tool_id: {"yt-dlp": yt_dlp, "ffprobe": ffprobe, "ffmpeg": ffmpeg}[tool_id],
    )
    monkeypatch.setattr(cli, "acquire_public_source", _acquire_after_confirmation(artifact))
    monkeypatch.setattr(cli, "capture_probe_documents", lambda *_args: _valid_probe_documents())
    monkeypatch.setattr(cli, "_read_download_confirmation_line", lambda _prompt: "确认")

    exit_code = cli.main(["plan", "https://example.test/watch", "--url-mode", "direct", "--json"])

    assert exit_code == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["status"] == "awaiting_decode_confirmation"
    assert "cdn-a.fake.test" in captured.err
    assert "确认" in captured.err


def test_declined_download_plan_blocks_the_public_url_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _prepare_phase_3_cli(monkeypatch, tmp_path)
    artifact = _public_artifact(tmp_path)
    yt_dlp = PinnedExternalTool("yt-dlp", tmp_path / "yt-dlp", "test", "a" * 64)

    monkeypatch.setattr(cli, "_configured_tool", lambda _root, _tool_id: yt_dlp)
    monkeypatch.setattr(cli, "acquire_public_source", _acquire_after_confirmation(artifact))
    monkeypatch.setattr(cli, "_read_download_confirmation_line", lambda _prompt: "yes")

    exit_code = cli.main(["plan", "https://example.test/watch", "--url-mode", "direct", "--json"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "blocked"
    assert result["report"]["diagnostics"][0]["reason"] == "download_plan_unconfirmed"
    assert result["report"]["source_artifacts"] == []


def test_duplicate_collection_content_is_retained_once_and_blocks_the_timeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _prepare_phase_3_cli(monkeypatch, tmp_path)
    artifact = _public_artifact(tmp_path)
    yt_dlp = PinnedExternalTool("yt-dlp", tmp_path / "yt-dlp", "test", "a" * 64)
    submitted = iter(["https://example.test/part-one", "https://example.test/part-two", "结束"])

    monkeypatch.setattr(cli, "_configured_tool", lambda _root, _tool_id: yt_dlp)
    monkeypatch.setattr(cli, "acquire_public_source", lambda *_args: artifact)
    monkeypatch.setattr(cli, "_read_collection_line", lambda _prompt: next(submitted))

    exit_code = cli.main(["plan", "--collect", "--url-mode", "direct", "--json"])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "blocked"
    assert result["report"]["diagnostics"][0]["reason"] == "duplicate_part"
    assert result["report"]["source_artifacts"] == [artifact.as_json()]
    assert len(result["report"]["url_authorizations"]) == 2


def _prepare_phase_3_cli(monkeypatch: pytest.MonkeyPatch, project_root: Path) -> None:
    monkeypatch.setattr(cli, "assert_runtime_policy", lambda: None)
    monkeypatch.setattr(cli, "assert_project_venv", lambda: object())
    monkeypatch.setattr(cli, "_project_root", lambda: project_root)


def _report_path(project_root: Path, report: dict[str, object]) -> Path:
    report_id = report["report_id"]
    assert isinstance(report_id, str)
    return project_root / "plans" / "reports" / report_id / "plan-report.json"


def _public_artifact(tmp_path: Path) -> SourceArtifact:
    media_path = tmp_path / "input" / "public" / "media"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"public-source")
    digest, byte_count = sha256_file(media_path)
    return SourceArtifact(digest, digest, byte_count, media_path, origin_kind="public_url")


def _valid_probe_documents() -> tuple[ProbeDocument, ProbeDocument]:
    return (
        ProbeDocument('{"streams": [{"index": 0, "codec_type": "video", "time_base": "1/1000"}]}'),
        ProbeDocument('{"packets": [{"stream_index": 0, "pts": 0, "duration": 1000}]}'),
    )


def _write_url_planning_configuration(project_root: Path) -> None:
    config = project_root / "config"
    config.mkdir()
    (config / "decode-throughput-profiles.json").write_text(
        """{
  "profiles": [{
    "id": "phase-03-default-v1",
    "optimistic_realtime_factor": "8",
    "likely_realtime_factor": "3",
    "conservative_realtime_factor": "1"
  }]
}
""",
        encoding="utf-8",
    )
    (config / "tools.json").write_text('{"tools": []}\n', encoding="utf-8")


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


def _write_model_registry(project_root: Path) -> None:
    """A schema-2 registry with one ineligible candidate; every other capability empty."""

    registry_path = project_root / "models" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "candidates": [{"candidate_id": "rapidocr", "capability": "ocr_primary"}],
            }
        ),
        encoding="utf-8",
    )


def _write_device_baselines(project_root: Path) -> None:
    baselines_path = project_root / "docs" / "phase-11-prototypes" / "device-baselines.json"
    baselines_path.parent.mkdir(parents=True)
    baselines_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "device_class": "apple-m1",
                "baselines": [
                    {
                        "capability": "asr_primary",
                        "candidate_id": "qwen3-asr-1-7b",
                        "device_class": "apple-m1",
                        "real_time_factor": {"numerator": 5, "denominator": 1},
                        "real_time_factor_approx": 5.0,
                        "peak_memory_bytes": 5_462_840_040,
                        "basis": "prototype:104eeec2:en",
                        "confidence": "measured",
                    },
                    {
                        "capability": "vad",
                        "candidate_id": "silero-vad",
                        "device_class": "apple-m1",
                        "real_time_factor": {"numerator": 285, "denominator": 1},
                        "real_time_factor_approx": 285.0,
                        "peak_memory_bytes": 124_551_168,
                        "basis": "prototype:104eeec2:en",
                        "confidence": "measured",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def test_plan_report_carries_peak_memory_and_model_status_legal_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Phase 12 ticket 04: the plan the maintainer confirms shows all four legal
    # fields -- estimated time and disk (already present) plus a device-baseline
    # peak-memory estimate and per-capability model status from the registry.
    _prepare_phase_3_cli(monkeypatch, tmp_path)
    _write_url_planning_configuration(tmp_path)
    _write_model_registry(tmp_path)
    _write_device_baselines(tmp_path)
    artifact = _public_artifact(tmp_path)
    yt_dlp = PinnedExternalTool("yt-dlp", tmp_path / "yt-dlp", "test", "a" * 64)
    ffprobe = PinnedExternalTool("ffprobe", tmp_path / "ffprobe", "test", "b" * 64)
    ffmpeg = PinnedExternalTool("ffmpeg", tmp_path / "ffmpeg", "test", "c" * 64)
    monkeypatch.setattr(
        cli,
        "_configured_tool",
        lambda _root, tool_id: {"yt-dlp": yt_dlp, "ffprobe": ffprobe, "ffmpeg": ffmpeg}[tool_id],
    )
    monkeypatch.setattr(cli, "acquire_public_source", lambda *_args: artifact)
    monkeypatch.setattr(cli, "capture_probe_documents", lambda *_args: _valid_probe_documents())

    exit_code = cli.main(
        ["plan", "https://example.test/watch", "--url-mode", "direct", "--json"]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)["report"]
    # Estimated time and disk headroom (the two pre-existing legal fields).
    assert report["decode_estimate"] is not None
    assert report["disk_headroom"]["required_bytes"] > 0
    # Peak-memory estimate, backed by the retained device-baseline measurements.
    assert report["peak_memory_estimate"]["peak_memory_bytes"] == 5_462_840_040
    assert report["peak_memory_estimate"]["basis"] == "device-baselines:apple-m1"
    assert report["peak_memory_estimate"]["confidence"] == "measured"
    # Per-capability model status: ocr_primary is ineligible, others still need
    # acquisition -- both surfaced at plan time rather than mid-run.
    statuses = {status["capability"]: status["state"] for status in report["model_statuses"]}
    assert statuses["ocr_primary"] == "model_ineligible"
    assert statuses["asr_primary"] == "model_acquisition_required"
    assert statuses["text_semantics"] == "model_acquisition_required"
    # The legal fields survive a persistence round-trip.
    assert load_plan_report(_report_path(tmp_path, report)).as_json()["model_statuses"] == (
        report["model_statuses"]
    )
