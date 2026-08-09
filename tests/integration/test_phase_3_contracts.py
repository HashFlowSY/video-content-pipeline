"""End-to-end offline proof for the Phase 3 planning contract."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from video_content_pipeline import cli, inspection, planning
from video_content_pipeline.external_tools import ExternalToolError, PinnedExternalTool
from video_content_pipeline.source import sha256_file

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures"
FIXTURE_MEDIA = FIXTURE_ROOT / "media" / "phase-02-offset-av-aac.mkv"
STRUCTURAL_EVIDENCE = FIXTURE_ROOT / "evidence" / "phase-02-offset-av-aac.ffprobe.json"


@dataclass
class ControlledWorkflow:
    """Test-only controlled substitutes and their observable evidence."""

    tools: dict[str, PinnedExternalTool]
    structural_json: str
    coverage_json: str
    probe_calls: list[tuple[str, ...]]
    decode_calls: list[tuple[str, ...]]
    headroom_checks: list[int]


def _configure_controlled_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> ControlledWorkflow:
    config_root = tmp_path / "config"
    config_root.mkdir()
    (config_root / "decode-throughput-profiles.json").write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "id": "phase-03-default-v1",
                        "optimistic_realtime_factor": "8",
                        "likely_realtime_factor": "3",
                        "conservative_realtime_factor": "1",
                    }
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (config_root / "tools.json").write_text('{"tools": []}\n', encoding="utf-8")

    tools = {
        "ffprobe": PinnedExternalTool(
            "ffprobe", tmp_path / "controlled-ffprobe", "fixture-ffprobe", "a" * 64
        ),
        "ffmpeg": PinnedExternalTool(
            "ffmpeg", tmp_path / "controlled-ffmpeg", "fixture-ffmpeg", "b" * 64
        ),
    }
    monkeypatch.setattr(cli, "assert_runtime_policy", lambda: None)
    monkeypatch.setattr(cli, "assert_project_venv", lambda: object())
    monkeypatch.setattr(cli, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(cli, "_configured_tool", lambda _root, tool_id: tools[tool_id])

    structural_json = STRUCTURAL_EVIDENCE.read_text(encoding="utf-8")
    fixture_document = json.loads(structural_json)
    assert isinstance(fixture_document, dict)
    packet_values = fixture_document.get("packets_and_frames")
    assert isinstance(packet_values, list)
    coverage_json = json.dumps(
        {
            "streams": fixture_document["streams"],
            "packets": [
                packet
                for packet in packet_values
                if isinstance(packet, dict) and packet.get("type") == "packet"
            ],
        }
    )
    probe_calls: list[tuple[str, ...]] = []

    def controlled_probe(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        probe_calls.append(arguments)
        output = coverage_json if "-show_packets" in arguments else structural_json
        return subprocess.CompletedProcess(arguments, 0, output, "")

    monkeypatch.setattr(inspection, "run_tool", controlled_probe)
    decode_calls: list[tuple[str, ...]] = []

    def controlled_decode(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        decode_calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(planning, "run_tool", controlled_decode)
    monkeypatch.setattr(planning, "revalidate_external_tool", lambda _tool: None)
    monkeypatch.setattr(planning.shutil, "disk_usage", lambda _path: SimpleNamespace(free=2**40))
    headroom_checks: list[int] = []
    monkeypatch.setattr(
        cli,
        "ensure_disk_headroom",
        lambda _root, requirement: headroom_checks.append(requirement.increment_bytes),
    )
    return ControlledWorkflow(
        tools,
        structural_json,
        coverage_json,
        probe_calls,
        decode_calls,
        headroom_checks,
    )


def test_cli_plan_decode_confirm_retains_fixture_evidence_and_run_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The complete planning seam remains offline, explicit, and auditable."""

    workflow = _configure_controlled_cli(tmp_path, monkeypatch)

    plan_result = cli.main(["plan", str(FIXTURE_MEDIA), "--json"])
    assert plan_result == 0
    preflight_response = json.loads(capsys.readouterr().out)
    assert preflight_response["status"] == "awaiting_decode_confirmation"
    report = preflight_response["report"]
    report_id = report["report_id"]
    source = report["source_artifacts"][0]
    source_id = source["source_id"]
    snapshot = tmp_path / "input" / source_id / "media"
    assert snapshot.is_file()
    assert sha256_file(snapshot) == (source["sha256"], source["byte_count"])
    assert workflow.headroom_checks == [FIXTURE_MEDIA.stat().st_size * 2 + 64 * 1024**2]
    assert len(workflow.probe_calls) == 2

    evidence_root = snapshot.parent / "evidence"
    assert (evidence_root / "structural.ffprobe.json").read_text(
        encoding="utf-8"
    ) == workflow.structural_json
    assert (evidence_root / "coverage.ffprobe.json").read_text(
        encoding="utf-8"
    ) == workflow.coverage_json
    assert (
        report["inspection_evidence"][0]["structural_probe_document"]["raw_json"]
        == workflow.structural_json
    )
    assert (
        report["inspection_evidence"][0]["coverage_probe_document"]["raw_json"]
        == workflow.coverage_json
    )

    decode_result = cli.main(["plan", "decode", report_id, "--json"])
    assert decode_result == 0
    decode_response = json.loads(capsys.readouterr().out)
    assert decode_response["status"] == "ready_for_confirmation"
    ready_report = decode_response["report"]
    assert ready_report["parent_report_id"] == report_id
    assert workflow.decode_calls == [
        (
            str(workflow.tools["ffmpeg"].path),
            "-v",
            "error",
            "-xerror",
            "-i",
            str(snapshot),
            "-map",
            "0:v?",
            "-map",
            "0:a?",
            "-f",
            "null",
            "-",
        )
    ]

    confirm_result = cli.main(["plan", "confirm", ready_report["report_id"], "--json"])
    assert confirm_result == 0
    confirmation_response = json.loads(capsys.readouterr().out)
    assert confirmation_response["status"] == "confirmed"
    run_plan = confirmation_response["plan"]
    assert run_plan["report_id"] == ready_report["report_id"]
    assert run_plan["plan_id"] != ready_report["report_id"]
    run_plan_path = tmp_path / "plans" / run_plan["plan_id"] / "run-plan.json"
    assert json.loads(run_plan_path.read_text(encoding="utf-8")) == run_plan
    assert (tmp_path / "plans" / "decode-throughput-history.json").is_file()
    assert not (tmp_path / "outputs").exists()


def test_cli_decode_revalidation_failure_retains_auditable_tool_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A stale pinned tool blocks decode and leaves a complete child report."""

    workflow = _configure_controlled_cli(tmp_path, monkeypatch)
    assert cli.main(["plan", str(FIXTURE_MEDIA), "--json"]) == 0
    preflight_response = json.loads(capsys.readouterr().out)
    report = preflight_response["report"]

    def changed_tool(_tool: PinnedExternalTool) -> None:
        raise ExternalToolError("tool_identity_changed", "Controlled tool identity changed.")

    monkeypatch.setattr(planning, "revalidate_external_tool", changed_tool)
    assert cli.main(["plan", "decode", report["report_id"], "--json"]) == 0
    decode_response = json.loads(capsys.readouterr().out)

    assert decode_response["status"] == "blocked"
    blocked_report = decode_response["report"]
    assert blocked_report["parent_report_id"] == report["report_id"]
    assert blocked_report["diagnostics"] == [
        {
            "reason": "tool_identity_changed",
            "message": "Controlled tool identity changed.",
        },
        {
            "reason": "tool_identity_changed",
            "message": "Controlled tool identity changed.",
        },
    ]
    assert blocked_report["tools"] == [
        workflow.tools["ffprobe"].as_json(),
        workflow.tools["ffmpeg"].as_json(),
    ]
    blocked_path = tmp_path / "plans" / "reports" / blocked_report["report_id"] / "plan-report.json"
    assert json.loads(blocked_path.read_text(encoding="utf-8")) == blocked_report
    assert workflow.decode_calls == []
