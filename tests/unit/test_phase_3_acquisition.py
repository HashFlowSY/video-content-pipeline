from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import video_content_pipeline.acquisition as acquisition
from video_content_pipeline.external_tools import PinnedExternalTool
from video_content_pipeline.url_policy import URLAccessMode, URLPolicyError, authorize_public_url


def _downloader(tmp_path: Path) -> PinnedExternalTool:
    return PinnedExternalTool("yt-dlp", tmp_path / "yt-dlp", "test", "a" * 64)


def test_acquisition_uses_pinned_downloader_project_paths_and_creates_a_public_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorization = authorize_public_url(
        "https://example.test/watch/1?token=secret", URLAccessMode.DIRECT
    )
    downloader = _downloader(tmp_path)
    commands: list[tuple[str, ...]] = []

    def valid_tool(expected: PinnedExternalTool) -> None:
        assert expected == downloader

    def controlled_downloader(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        commands.append(arguments)
        if "--dump-single-json" in arguments:
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout='{"url": "https://example.test/media.mp4", "filesize": 12}\n',
                stderr="",
            )
        staged_media = _staging_root(arguments) / "media.mp4"
        staged_media.parent.mkdir(parents=True, exist_ok=True)
        staged_media.write_bytes(b"public-media")
        return subprocess.CompletedProcess(arguments, 0, stdout=f"{staged_media}\n", stderr="")

    monkeypatch.setattr(acquisition, "revalidate_external_tool", valid_tool)
    monkeypatch.setattr(acquisition, "run_tool", controlled_downloader)

    artifact = acquisition.acquire_public_source(authorization, downloader, tmp_path)

    assert artifact.origin_kind == "public_url"
    assert artifact.media_path.read_bytes() == b"public-media"
    assert len(commands) == 2
    assert "--no-config" in commands[0]
    assert "--no-plugin-dirs" in commands[0]
    assert "--no-cookies" in commands[0]
    assert "--no-cookies-from-browser" in commands[0]
    assert "--no-playlist" in commands[0]
    assert "--proxy" in commands[0]
    assert "--cache-dir" in commands[0]
    assert str(tmp_path / "cache" / "yt-dlp") in commands[0]
    assert "--paths" in commands[1]
    assert str(tmp_path / "tmp" / "url-acquisition") in " ".join(commands[1])
    assert commands[1][commands[1].index("--max-filesize") + 1] == "12"
    assert "secret" not in str(artifact.as_json())


def test_unapproved_media_host_stops_before_download_and_does_not_leak_raw_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorization = authorize_public_url(
        "https://example.test/watch/1?token=secret", URLAccessMode.DIRECT
    )
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(acquisition, "revalidate_external_tool", lambda _tool: None)

    def host_escalation(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return subprocess.CompletedProcess(
            arguments,
            0,
            stdout='{"url": "https://cdn.example.test/media.mp4", "filesize": 12}\n',
            stderr="",
        )

    monkeypatch.setattr(acquisition, "run_tool", host_escalation)

    with pytest.raises(URLPolicyError) as error:
        acquisition.acquire_public_source(authorization, _downloader(tmp_path), tmp_path)

    assert error.value.reason == "host_escalation"
    assert len(calls) == 1
    assert "secret" not in str(error.value)


def test_https_downgrade_stops_before_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorization = authorize_public_url("https://example.test/watch/1", URLAccessMode.DIRECT)
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(acquisition, "revalidate_external_tool", lambda _tool: None)

    def downgraded_media(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return subprocess.CompletedProcess(
            arguments,
            0,
            stdout='{"url": "http://example.test/media.mp4", "filesize": 12}\n',
            stderr="",
        )

    monkeypatch.setattr(acquisition, "run_tool", downgraded_media)

    with pytest.raises(URLPolicyError) as error:
        acquisition.acquire_public_source(authorization, _downloader(tmp_path), tmp_path)

    assert error.value.reason == "https_downgrade"
    assert len(calls) == 1


def test_download_larger_than_its_authorized_metadata_size_is_not_snapshotted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorization = authorize_public_url("https://example.test/watch/1", URLAccessMode.DIRECT)
    downloader = _downloader(tmp_path)

    monkeypatch.setattr(acquisition, "revalidate_external_tool", lambda _tool: None)

    def oversized_media(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        if "--dump-single-json" in arguments:
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout='{"url": "https://example.test/media.mp4", "filesize": 3}\n',
                stderr="",
            )
        staged_media = _staging_root(arguments) / "media.mp4"
        staged_media.parent.mkdir(parents=True, exist_ok=True)
        staged_media.write_bytes(b"oversized")
        return subprocess.CompletedProcess(arguments, 0, stdout=f"{staged_media}\n", stderr="")

    monkeypatch.setattr(acquisition, "run_tool", oversized_media)

    with pytest.raises(acquisition.URLAcquisitionError) as error:
        acquisition.acquire_public_source(authorization, downloader, tmp_path)

    assert error.value.reason == "url_download_size_mismatch"
    assert not list((tmp_path / "input").glob("*/media"))


def test_filtered_mode_never_falls_back_to_direct_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorization = authorize_public_url("https://example.test/watch/1", URLAccessMode.FILTERED)
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(acquisition, "run_tool", lambda arguments: calls.append(arguments))

    with pytest.raises(acquisition.URLAcquisitionError) as error:
        acquisition.acquire_public_source(authorization, _downloader(tmp_path), tmp_path)

    assert error.value.reason == "filtered_mode_unavailable"
    assert calls == []


def test_duplicate_acquired_bytes_reuse_one_source_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = authorize_public_url("https://example.test/part-one", URLAccessMode.DIRECT)
    second = authorize_public_url("https://example.test/part-two", URLAccessMode.DIRECT)
    downloader = _downloader(tmp_path)

    monkeypatch.setattr(acquisition, "revalidate_external_tool", lambda _tool: None)

    def same_media(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        if "--dump-single-json" in arguments:
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout='{"url": "https://example.test/media.mp4", "filesize": 11}\n',
                stderr="",
            )
        path = _staging_root(arguments) / "media.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"same-public")
        return subprocess.CompletedProcess(arguments, 0, stdout=f"{path}\n", stderr="")

    monkeypatch.setattr(acquisition, "run_tool", same_media)

    first_artifact = acquisition.acquire_public_source(first, downloader, tmp_path)
    second_artifact = acquisition.acquire_public_source(second, downloader, tmp_path)

    assert first_artifact == second_artifact
    assert first_artifact.origin_kind == "public_url"


def test_multicomponent_source_sums_component_sizes_and_unblocks_the_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorization = authorize_public_url("https://example.test/watch/1", URLAccessMode.DIRECT)
    downloader = _downloader(tmp_path)
    commands: list[tuple[str, ...]] = []

    monkeypatch.setattr(acquisition, "revalidate_external_tool", lambda _tool: None)

    def dash_media(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        commands.append(arguments)
        if "--dump-single-json" in arguments:
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout=(
                    '{"url": "https://example.test/media", "requested_formats": ['
                    '{"url": "https://example.test/video", "filesize": 10}, '
                    '{"url": "https://example.test/audio", "filesize": 5}]}\n'
                ),
                stderr="",
            )
        staged_media = _staging_root(arguments) / "media.mp4"
        staged_media.parent.mkdir(parents=True, exist_ok=True)
        staged_media.write_bytes(b"public-dash-vid")  # exactly 15 bytes = 10 + 5
        return subprocess.CompletedProcess(arguments, 0, stdout=f"{staged_media}\n", stderr="")

    monkeypatch.setattr(acquisition, "run_tool", dash_media)

    artifact = acquisition.acquire_public_source(authorization, downloader, tmp_path)

    assert artifact.origin_kind == "public_url"
    assert commands[1][commands[1].index("--max-filesize") + 1] == "15"


def test_multicomponent_source_with_an_indeterminable_component_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorization = authorize_public_url("https://example.test/watch/1", URLAccessMode.DIRECT)
    downloader = _downloader(tmp_path)
    commands: list[tuple[str, ...]] = []

    monkeypatch.setattr(acquisition, "revalidate_external_tool", lambda _tool: None)

    def partial_dash_media(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        commands.append(arguments)
        if "--dump-single-json" in arguments:
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout=(
                    '{"url": "https://example.test/media", "requested_formats": ['
                    '{"url": "https://example.test/video", "filesize": 10}, '
                    '{"url": "https://example.test/audio", "filesize_approx": 5}]}\n'
                ),
                stderr="",
            )
        raise AssertionError("download must not start when a component size is indeterminable")

    monkeypatch.setattr(acquisition, "run_tool", partial_dash_media)

    with pytest.raises(acquisition.URLAcquisitionError) as error:
        acquisition.acquire_public_source(authorization, downloader, tmp_path)

    assert error.value.reason == "url_size_unknown"
    assert len(commands) == 1


def test_single_file_source_sizing_is_unchanged_by_multicomponent_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorization = authorize_public_url("https://example.test/watch/1", URLAccessMode.DIRECT)
    downloader = _downloader(tmp_path)
    commands: list[tuple[str, ...]] = []

    monkeypatch.setattr(acquisition, "revalidate_external_tool", lambda _tool: None)

    def single_file(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        commands.append(arguments)
        if "--dump-single-json" in arguments:
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout='{"url": "https://example.test/media.mp4", "filesize": 7}\n',
                stderr="",
            )
        staged_media = _staging_root(arguments) / "media.mp4"
        staged_media.parent.mkdir(parents=True, exist_ok=True)
        staged_media.write_bytes(b"public!")
        return subprocess.CompletedProcess(arguments, 0, stdout=f"{staged_media}\n", stderr="")

    monkeypatch.setattr(acquisition, "run_tool", single_file)

    artifact = acquisition.acquire_public_source(authorization, downloader, tmp_path)

    assert artifact.origin_kind == "public_url"
    assert commands[1][commands[1].index("--max-filesize") + 1] == "7"


def _staging_root(arguments: tuple[str, ...]) -> Path:
    return Path(
        next(value.removeprefix("home:") for value in arguments if value.startswith("home:"))
    )
