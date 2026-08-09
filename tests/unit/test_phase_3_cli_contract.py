from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_content_pipeline import cli
from video_content_pipeline.cli import _parser
from video_content_pipeline.source import SourceIntakeError


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
