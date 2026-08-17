from __future__ import annotations

import socket
import subprocess
import threading
from pathlib import Path
from urllib.parse import urlsplit

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

    artifact = acquisition.acquire_public_source(
        authorization, downloader, tmp_path, lambda _plan: True
    )

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


def test_resolved_media_hosts_are_disclosed_in_the_download_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorization = authorize_public_url(
        "https://example.test/watch/1?token=secret", URLAccessMode.DIRECT
    )
    downloader = _downloader(tmp_path)
    disclosed_plans: list[acquisition.MediaDownloadPlan] = []

    monkeypatch.setattr(acquisition, "revalidate_external_tool", lambda _tool: None)

    def dash_media_on_cdn_hosts(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        if "--dump-single-json" in arguments:
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout=(
                    '{"webpage_url": "https://example.test/watch/1", "duration": 61.5, '
                    '"requested_formats": ['
                    '{"url": "https://cdn-a.fake.test/video.m4s", "filesize": 10}, '
                    '{"url": "https://cdn-b.fake.test/audio.m4s", "filesize": 5, '
                    '"fragment_base_url": "https://frag.fake.test/segments/"}]}\n'
                ),
                stderr="",
            )
        staged_media = _staging_root(arguments) / "media.mp4"
        staged_media.parent.mkdir(parents=True, exist_ok=True)
        staged_media.write_bytes(b"public-dash-vid")  # exactly 15 bytes = 10 + 5
        return subprocess.CompletedProcess(arguments, 0, stdout=f"{staged_media}\n", stderr="")

    monkeypatch.setattr(acquisition, "run_tool", dash_media_on_cdn_hosts)

    def confirm(plan: acquisition.MediaDownloadPlan) -> bool:
        disclosed_plans.append(plan)
        return True

    artifact = acquisition.acquire_public_source(authorization, downloader, tmp_path, confirm)

    assert artifact.origin_kind == "public_url"
    assert len(disclosed_plans) == 1
    plan = disclosed_plans[0]
    assert plan.media_hosts == (
        "cdn-a.fake.test",
        "cdn-b.fake.test",
        "example.test",
        "frag.fake.test",
    )
    assert plan.byte_count == 15
    assert plan.duration_seconds == 61.5
    assert plan.planned_increment_bytes == 15 * 2 + 64 * 1024**2
    assert plan.required_free_bytes == plan.planned_increment_bytes + 1024**3
    assert plan.as_json()["media_hosts"] == list(plan.media_hosts)
    assert "secret" not in str(plan.as_json())


def test_declined_download_plan_stops_before_any_download_and_carries_no_authority_forward(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorization = authorize_public_url(
        "https://example.test/watch/1?token=secret", URLAccessMode.DIRECT
    )
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(acquisition, "revalidate_external_tool", lambda _tool: None)

    def cdn_media(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        if "--dump-single-json" not in arguments:
            raise AssertionError("a declined download plan must never start a download")
        return subprocess.CompletedProcess(
            arguments,
            0,
            stdout='{"url": "https://cdn.fake.test/media.mp4", "filesize": 12}\n',
            stderr="",
        )

    monkeypatch.setattr(acquisition, "run_tool", cdn_media)

    with pytest.raises(acquisition.URLAcquisitionError) as error:
        acquisition.acquire_public_source(
            authorization, _downloader(tmp_path), tmp_path, lambda _plan: False
        )

    assert error.value.reason == "download_plan_unconfirmed"
    assert len(calls) == 1
    assert "secret" not in str(error.value)
    assert not list((tmp_path / "input").glob("*/media"))

    # The earlier disclosure leaves nothing behind: a repeat of the same
    # download is re-disclosed and blocks again without fresh confirmation.
    with pytest.raises(acquisition.URLAcquisitionError) as repeat_error:
        acquisition.acquire_public_source(
            authorization, _downloader(tmp_path), tmp_path, lambda _plan: False
        )

    assert repeat_error.value.reason == "download_plan_unconfirmed"


def test_mid_download_connection_to_an_undisclosed_host_fails_closed_as_host_escalation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorization = authorize_public_url("https://example.test/watch/1", URLAccessMode.DIRECT)
    downloader = _downloader(tmp_path)

    monkeypatch.setattr(acquisition, "revalidate_external_tool", lambda _tool: None)

    def redirecting_downloader(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        if "--dump-single-json" in arguments:
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout='{"url": "https://cdn.fake.test/media.mp4", "filesize": 12}\n',
                stderr="",
            )
        response = _proxy_connect(_proxy_url(arguments), "undisclosed.fake.test", 443)
        assert "403" in response
        return subprocess.CompletedProcess(arguments, 1, stdout="", stderr="")

    monkeypatch.setattr(acquisition, "run_tool", redirecting_downloader)

    with pytest.raises(URLPolicyError) as error:
        acquisition.acquire_public_source(authorization, downloader, tmp_path, lambda _plan: True)

    assert error.value.reason == "host_escalation"


def test_download_proxy_admits_a_confirmed_media_host_beyond_the_page_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorization = authorize_public_url("https://example.test/watch/1", URLAccessMode.DIRECT)
    downloader = _downloader(tmp_path)

    monkeypatch.setattr(acquisition, "revalidate_external_tool", lambda _tool: None)

    with socket.create_server(("127.0.0.1", 0)) as upstream:
        upstream_port = upstream.getsockname()[1]

        def serve_one_upstream_connection() -> None:
            connection, _address = upstream.accept()
            with connection:
                if connection.recv(4) == b"ping":
                    connection.sendall(b"pong")

        upstream_thread = threading.Thread(target=serve_one_upstream_connection, daemon=True)
        upstream_thread.start()

        def tunneling_downloader(arguments: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
            if "--dump-single-json" in arguments:
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    stdout='{"url": "https://localhost/media.mp4", "filesize": 12}\n',
                    stderr="",
                )
            response = _proxy_connect(
                _proxy_url(arguments), "localhost", upstream_port, payload=b"ping"
            )
            assert "200" in response.splitlines()[0]
            assert response.endswith("pong")
            staged_media = _staging_root(arguments) / "media.mp4"
            staged_media.parent.mkdir(parents=True, exist_ok=True)
            staged_media.write_bytes(b"tunneled-med")  # exactly 12 bytes
            return subprocess.CompletedProcess(arguments, 0, stdout=f"{staged_media}\n", stderr="")

        monkeypatch.setattr(acquisition, "run_tool", tunneling_downloader)

        artifact = acquisition.acquire_public_source(
            authorization, downloader, tmp_path, lambda _plan: True
        )
        upstream_thread.join(timeout=5)

    assert artifact.origin_kind == "public_url"
    assert artifact.media_path.read_bytes() == b"tunneled-med"


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
        acquisition.acquire_public_source(
            authorization, _downloader(tmp_path), tmp_path, _never_confirm
        )

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
        acquisition.acquire_public_source(authorization, downloader, tmp_path, lambda _plan: True)

    assert error.value.reason == "url_download_size_mismatch"
    assert not list((tmp_path / "input").glob("*/media"))


def test_filtered_mode_never_falls_back_to_direct_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authorization = authorize_public_url("https://example.test/watch/1", URLAccessMode.FILTERED)
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(acquisition, "run_tool", lambda arguments: calls.append(arguments))

    with pytest.raises(acquisition.URLAcquisitionError) as error:
        acquisition.acquire_public_source(
            authorization, _downloader(tmp_path), tmp_path, _never_confirm
        )

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

    first_artifact = acquisition.acquire_public_source(
        first, downloader, tmp_path, lambda _plan: True
    )
    second_artifact = acquisition.acquire_public_source(
        second, downloader, tmp_path, lambda _plan: True
    )

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

    artifact = acquisition.acquire_public_source(
        authorization, downloader, tmp_path, lambda _plan: True
    )

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
        acquisition.acquire_public_source(authorization, downloader, tmp_path, _never_confirm)

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

    artifact = acquisition.acquire_public_source(
        authorization, downloader, tmp_path, lambda _plan: True
    )

    assert artifact.origin_kind == "public_url"
    assert commands[1][commands[1].index("--max-filesize") + 1] == "7"


def _staging_root(arguments: tuple[str, ...]) -> Path:
    return Path(
        next(value.removeprefix("home:") for value in arguments if value.startswith("home:"))
    )


def _proxy_url(arguments: tuple[str, ...]) -> str:
    return arguments[arguments.index("--proxy") + 1]


def _proxy_connect(proxy_url: str, host: str, port: int, payload: bytes | None = None) -> str:
    """Issue one CONNECT through the acquisition proxy and return what came back."""

    parsed = urlsplit(proxy_url)
    assert parsed.hostname is not None and parsed.port is not None
    with socket.create_connection((parsed.hostname, parsed.port), timeout=5) as connection:
        connection.settimeout(5)
        connection.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\n\r\n".encode("ascii"))
        received = connection.recv(1024)
        if payload is not None and b"200" in received.splitlines()[0]:
            connection.sendall(payload)
            received += connection.recv(1024)
        return received.decode("ascii", errors="replace")


def _never_confirm(_plan: acquisition.MediaDownloadPlan) -> bool:
    raise AssertionError("the download plan must not reach confirmation")
