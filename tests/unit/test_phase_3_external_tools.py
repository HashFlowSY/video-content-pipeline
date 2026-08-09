from __future__ import annotations

import subprocess
from pathlib import Path

import video_content_pipeline.external_tools as external_tools


def test_ffprobe_uses_its_documented_dash_version_argument(tmp_path: Path, monkeypatch) -> None:
    binary = tmp_path / "ffprobe"
    binary.write_bytes(b"not-executed")
    observed: list[list[str]] = []

    def fake_run(arguments: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        observed.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, stdout="ffprobe version test\n", stderr="")

    monkeypatch.setattr(external_tools.subprocess, "run", fake_run)

    tool = external_tools.identify_external_tool("ffprobe", binary)

    assert observed == [[str(binary), "-version"]]
    assert tool.version == "ffprobe version test"


def test_yt_dlp_uses_double_dash_version_argument(tmp_path: Path, monkeypatch) -> None:
    binary = tmp_path / "yt-dlp"
    binary.write_bytes(b"not-executed")
    observed: list[list[str]] = []

    def fake_run(arguments: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        observed.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, stdout="2026.07.04\n", stderr="")

    monkeypatch.setattr(external_tools.subprocess, "run", fake_run)

    external_tools.identify_external_tool("yt-dlp", binary)

    assert observed == [[str(binary), "--version"]]
