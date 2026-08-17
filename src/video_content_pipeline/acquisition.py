"""Host-controlled public URL acquisition through a pinned external downloader."""

from __future__ import annotations

import json
import select
import socket
import socketserver
import stat
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeGuard
from urllib.parse import urlsplit

from video_content_pipeline.external_tools import (
    ExternalToolError,
    PinnedExternalTool,
    revalidate_external_tool,
    run_tool,
)
from video_content_pipeline.source import (
    SourceArtifact,
    SourceIntakeError,
    calculate_disk_headroom,
    ensure_disk_headroom,
    snapshot_local_source,
)
from video_content_pipeline.url_policy import (
    RedactedSourceProvenance,
    URLAccessMode,
    URLAuthorization,
    URLPolicyError,
    disclose_destination_host,
    validate_destination,
)


class URLAcquisitionError(ValueError):
    """A public-source acquisition failure that contains no raw URL."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


DOWNLOAD_PLAN_CONFIRMATION_SIGNAL = "确认"


@dataclass(frozen=True)
class MediaDownloadPlan:
    """One download's plan-time disclosure: media hosts, size, duration, disk need.

    Confirming this plan authorizes exactly ``media_hosts`` for that single
    download (ADR 0057). The set lives only in this in-memory value; the next
    download of the same platform discloses and confirms its hosts again.
    ``duration_seconds`` is disclosure-only and may honestly be unknown; the
    byte count is the fail-closed quantity.
    """

    provenance: RedactedSourceProvenance
    media_hosts: tuple[str, ...]
    byte_count: int
    duration_seconds: float | None
    planned_increment_bytes: int
    required_free_bytes: int

    def as_json(self) -> dict[str, object]:
        return {
            "provenance": self.provenance.as_json(),
            "media_hosts": list(self.media_hosts),
            "byte_count": self.byte_count,
            "duration_seconds": self.duration_seconds,
            "planned_increment_bytes": self.planned_increment_bytes,
            "required_free_bytes": self.required_free_bytes,
        }


def acquire_public_source(
    authorization: URLAuthorization,
    downloader: PinnedExternalTool,
    project_root: Path,
    confirm_download_plan: Callable[[MediaDownloadPlan], bool],
) -> SourceArtifact:
    """Acquire one authorized URL after its download plan is confirmed.

    The downloader is queried in simulation mode first. Its structured metadata
    must resolve the media hosts and an exact byte count, and the resulting
    download plan must be confirmed, before the second, project-local download
    invocation is allowed to write anything. The download admits connections to
    exactly the confirmed host set (ADR 0057), fail-closed.
    """

    if downloader.tool_id != "yt-dlp":
        raise URLAcquisitionError(
            "url_downloader_invalid", "Public acquisition requires the pinned yt-dlp tool."
        )
    if authorization.mode is URLAccessMode.FILTERED:
        raise URLAcquisitionError(
            "filtered_mode_unavailable",
            "Filtered URL access needs a separately configured filtered transport.",
        )
    acquisition_root = project_root / "tmp" / "url-acquisition"
    cache_root = project_root / "cache" / "yt-dlp"
    acquisition_root.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)
    staging_root = acquisition_root / uuid.uuid4().hex

    try:
        revalidate_external_tool(downloader)
        metadata_result = _run_metadata_under_host_control(authorization, downloader, cache_root)
    except (ExternalToolError, OSError) as error:
        raise URLAcquisitionError(
            "url_downloader_unavailable", "The pinned downloader could not start."
        ) from error
    metadata = _parse_metadata(metadata_result)
    media_hosts = _resolve_media_hosts(authorization, metadata)
    byte_count = _metadata_byte_count(metadata)
    planned_increment = byte_count * 2 + 64 * 1024**2
    headroom = calculate_disk_headroom(planned_increment)
    plan = MediaDownloadPlan(
        provenance=authorization.provenance,
        media_hosts=media_hosts,
        byte_count=byte_count,
        duration_seconds=_metadata_duration(metadata),
        planned_increment_bytes=planned_increment,
        required_free_bytes=headroom.required_bytes,
    )
    if not confirm_download_plan(plan):
        raise URLAcquisitionError(
            "download_plan_unconfirmed",
            "The media download plan was not confirmed; no bytes were moved.",
        )
    try:
        ensure_disk_headroom(project_root, headroom)
        revalidate_external_tool(downloader)
        download_result = _run_download_under_host_control(
            authorization,
            downloader,
            cache_root,
            staging_root,
            byte_count,
            confirmed_media_hosts=frozenset(plan.media_hosts),
        )
    except SourceIntakeError:
        raise
    except (ExternalToolError, OSError) as error:
        raise URLAcquisitionError(
            "url_downloader_unavailable", "The pinned downloader could not start."
        ) from error
    media_path = _downloaded_media_path(download_result, staging_root, byte_count)
    try:
        return snapshot_local_source(media_path, project_root / "input", origin_kind="public_url")
    except SourceIntakeError:
        raise


def _metadata_command(
    authorization: URLAuthorization,
    downloader: PinnedExternalTool,
    cache_root: Path,
    proxy_url: str,
) -> tuple[str, ...]:
    return (
        str(downloader.path),
        "--no-config",
        "--no-plugin-dirs",
        "--no-cookies",
        "--no-cookies-from-browser",
        "--no-playlist",
        "--proxy",
        proxy_url,
        "--cache-dir",
        str(cache_root),
        "--dump-single-json",
        "--skip-download",
        authorization.raw_url,
    )


def _download_command(
    authorization: URLAuthorization,
    downloader: PinnedExternalTool,
    cache_root: Path,
    staging_root: Path,
    maximum_byte_count: int,
    proxy_url: str,
) -> tuple[str, ...]:
    return (
        str(downloader.path),
        "--no-config",
        "--no-plugin-dirs",
        "--no-cookies",
        "--no-cookies-from-browser",
        "--no-playlist",
        "--proxy",
        proxy_url,
        "--cache-dir",
        str(cache_root),
        "--paths",
        f"home:{staging_root}",
        "--paths",
        f"temp:{staging_root}",
        "--output",
        "media.%(ext)s",
        "--max-filesize",
        str(maximum_byte_count),
        "--print",
        "after_move:%(filepath)s",
        authorization.raw_url,
    )


def _parse_metadata(result: object) -> dict[str, object]:
    returncode = getattr(result, "returncode", None)
    stdout = getattr(result, "stdout", None)
    if returncode != 0 or not isinstance(stdout, str):
        raise URLAcquisitionError(
            "url_metadata_failed", "The downloader could not inspect the public source."
        )
    try:
        decoded = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise URLAcquisitionError(
            "url_metadata_invalid", "The downloader returned invalid public-source metadata."
        ) from error
    if not isinstance(decoded, dict):
        raise URLAcquisitionError(
            "url_metadata_invalid", "The downloader returned invalid public-source metadata."
        )
    return decoded


def _resolve_media_hosts(
    authorization: URLAuthorization, metadata: dict[str, object]
) -> tuple[str, ...]:
    """Resolve every declared media destination host for plan disclosure (ADR 0057)."""

    destinations = _metadata_destinations(metadata)
    if not destinations:
        raise URLAcquisitionError(
            "url_media_location_missing", "The downloader did not declare a public media location."
        )
    hosts = {disclose_destination_host(authorization, destination) for destination in destinations}
    return tuple(sorted(hosts))


_DESTINATION_KEYS = ("webpage_url", "original_url", "url", "manifest_url", "fragment_base_url")


def _metadata_destinations(metadata: dict[str, object]) -> tuple[str, ...]:
    values: list[str] = []
    for key in _DESTINATION_KEYS:
        value = metadata.get(key)
        if isinstance(value, str):
            values.append(value)
    requested_formats = metadata.get("requested_formats")
    if isinstance(requested_formats, list):
        for component in requested_formats:
            if not isinstance(component, dict):
                continue
            for key in _DESTINATION_KEYS:
                value = component.get(key)
                if isinstance(value, str):
                    values.append(value)
    return tuple(dict.fromkeys(values))


def _metadata_byte_count(metadata: dict[str, object]) -> int:
    requested_formats = metadata.get("requested_formats")
    if isinstance(requested_formats, list) and requested_formats:
        return _multicomponent_byte_count(requested_formats)
    value = metadata.get("filesize")
    if _is_positive_size(value):
        return value
    raise URLAcquisitionError(
        "url_size_unknown", "The downloader did not provide a positive source byte count."
    )


def _multicomponent_byte_count(components: list[object]) -> int:
    """Sum the exact byte counts of a split-stream (DASH) source's components.

    Every component must declare a positive integer ``filesize``; a missing or
    approximate size fails closed rather than being guessed, so disk-headroom
    planning is never based on an unknown total.
    """

    total = 0
    for component in components:
        value = component.get("filesize") if isinstance(component, dict) else None
        if not _is_positive_size(value):
            raise URLAcquisitionError(
                "url_size_unknown",
                "The downloader did not provide a positive source byte count.",
            )
        total += value
    return total


def _is_positive_size(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _metadata_duration(metadata: dict[str, object]) -> float | None:
    value = metadata.get("duration")
    if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
        return float(value)
    return None


def _downloaded_media_path(result: object, staging_root: Path, maximum_byte_count: int) -> Path:
    returncode = getattr(result, "returncode", None)
    stdout = getattr(result, "stdout", None)
    if returncode != 0 or not isinstance(stdout, str):
        raise URLAcquisitionError(
            "url_download_failed", "The downloader could not acquire the public source."
        )
    paths = [line.strip() for line in stdout.splitlines() if line.strip()]
    if len(paths) != 1:
        raise URLAcquisitionError(
            "url_download_output_invalid", "The downloader did not report one acquired media file."
        )
    try:
        path = Path(paths[0]).resolve(strict=True)
        root = staging_root.resolve(strict=True)
        path.relative_to(root)
        metadata = path.stat()
    except (OSError, ValueError) as error:
        raise URLAcquisitionError(
            "url_download_output_invalid", "The downloader reported an invalid acquired media file."
        ) from error
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise URLAcquisitionError(
            "url_download_output_invalid", "The downloader reported an invalid acquired media file."
        )
    if metadata.st_size != maximum_byte_count:
        raise URLAcquisitionError(
            "url_download_size_mismatch",
            "The acquired media does not match its authorized byte count.",
        )
    return path


class _HostAuthorizationProxy:
    """A local CONNECT/HTTP proxy that fails closed outside one URL authorization.

    ``confirmed_media_hosts`` widens admission to exactly one confirmed download
    plan's disclosed host set (ADR 0057); the metadata pass runs without it.
    """

    def __init__(
        self,
        authorization: URLAuthorization,
        confirmed_media_hosts: frozenset[str] = frozenset(),
    ) -> None:
        self.authorization = authorization
        self.confirmed_media_hosts = confirmed_media_hosts
        self.failure: URLPolicyError | None = None
        self._server: socketserver.ThreadingTCPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> _HostAuthorizationProxy:
        owner = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                try:
                    line = self.rfile.readline(8192).decode("ascii", errors="strict").rstrip("\r\n")
                    method, target, _version = line.split(" ", 2)
                    if method == "CONNECT":
                        host, port = _split_connect_target(target)
                        owner._authorize(host, "https")
                        upstream = socket.create_connection((host, port))
                        try:
                            self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                            self.wfile.flush()
                            _relay(self.connection, upstream)
                        finally:
                            upstream.close()
                        return
                    parsed = urlsplit(target)
                    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                        raise URLPolicyError(
                            "host_escalation", "The URL is outside the authorization."
                        )
                    owner._authorize(parsed.hostname, parsed.scheme)
                    port = parsed.port or (443 if parsed.scheme == "https" else 80)
                    upstream = socket.create_connection((parsed.hostname, port))
                    try:
                        upstream.sendall(
                            f"{method} {parsed.path or '/'}"
                            f"{'?' + parsed.query if parsed.query else ''} HTTP/1.1\r\n".encode()
                        )
                        while header := self.rfile.readline(8192):
                            upstream.sendall(header)
                            if header in {b"\r\n", b"\n"}:
                                break
                        _relay(self.connection, upstream)
                    finally:
                        upstream.close()
                except (OSError, UnicodeError, ValueError, URLPolicyError) as error:
                    if isinstance(error, URLPolicyError):
                        owner.failure = error
                    self.wfile.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                    self.wfile.flush()

        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        self._server = server
        self._thread = threading.Thread(
            target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
        )
        self._thread.start()
        return self

    @property
    def proxy_url(self) -> str:
        if self._server is None:
            raise RuntimeError("Host authorization proxy is not running.")
        host, port = self._server.server_address
        if isinstance(host, bytes):
            host = host.decode("ascii")
        return f"http://{host}:{port}"

    def _authorize(self, host: str, scheme: str) -> None:
        validate_destination(
            self.authorization,
            f"{scheme}://{host}/",
            confirmed_media_hosts=self.confirmed_media_hosts,
        )

    def __exit__(self, *_args: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join()


def _run_metadata_under_host_control(
    authorization: URLAuthorization,
    downloader: PinnedExternalTool,
    cache_root: Path,
) -> object:
    with _HostAuthorizationProxy(authorization) as proxy:
        result = run_tool(_metadata_command(authorization, downloader, cache_root, proxy.proxy_url))
    if proxy.failure is not None:
        raise proxy.failure
    return result


def _run_download_under_host_control(
    authorization: URLAuthorization,
    downloader: PinnedExternalTool,
    cache_root: Path,
    staging_root: Path,
    maximum_byte_count: int,
    *,
    confirmed_media_hosts: frozenset[str],
) -> object:
    with _HostAuthorizationProxy(authorization, confirmed_media_hosts) as proxy:
        result = run_tool(
            _download_command(
                authorization,
                downloader,
                cache_root,
                staging_root,
                maximum_byte_count,
                proxy.proxy_url,
            )
        )
    if proxy.failure is not None:
        raise proxy.failure
    return result


def _split_connect_target(target: str) -> tuple[str, int]:
    host, separator, port_text = target.rpartition(":")
    if not separator or not host or not port_text.isdigit():
        raise ValueError
    return host, int(port_text)


def _relay(client: socket.socket, upstream: socket.socket) -> None:
    sockets = (client, upstream)
    while True:
        readable, _, _ = select.select(sockets, (), ())
        for source in readable:
            destination = upstream if source is client else client
            payload = source.recv(64 * 1024)
            if not payload:
                return
            destination.sendall(payload)
