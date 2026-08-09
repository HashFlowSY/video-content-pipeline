"""Explicit URL authority and manual collection rules for Phase 3."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import SplitResult, urlsplit


class URLPolicyError(ValueError):
    """A URL authority failure with a machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class URLAccessMode(StrEnum):
    """The two non-interchangeable public-URL authorization scopes."""

    FILTERED = "filtered"
    DIRECT = "direct"


COLLECTION_CLOSURE_SIGNAL = "结束"


@dataclass(frozen=True)
class RedactedSourceProvenance:
    """The persistent form of a URL without credentials, query, or fragment."""

    scheme: str
    host: str
    path: str
    transport_integrity_verified: bool

    def as_json(self) -> dict[str, object]:
        return {
            "scheme": self.scheme,
            "host": self.host,
            "path": self.path,
            "transport_integrity_verified": self.transport_integrity_verified,
        }


@dataclass(frozen=True)
class URLAuthorizationEvidence:
    """Persistent authorization evidence that deliberately excludes the raw URL."""

    mode: URLAccessMode
    provenance: RedactedSourceProvenance

    def as_json(self) -> dict[str, object]:
        return {"mode": self.mode.value, "provenance": self.provenance.as_json()}

    @classmethod
    def from_json(cls, value: object) -> URLAuthorizationEvidence:
        if not isinstance(value, dict):
            raise ValueError("URL authorization evidence must be an object.")
        mode = value.get("mode")
        provenance = value.get("provenance")
        if not isinstance(mode, str) or not isinstance(provenance, dict):
            raise ValueError("URL authorization evidence has an invalid schema.")
        scheme = provenance.get("scheme")
        host = provenance.get("host")
        path = provenance.get("path")
        integrity = provenance.get("transport_integrity_verified")
        if (
            not isinstance(scheme, str)
            or not isinstance(host, str)
            or not isinstance(path, str)
            or not isinstance(integrity, bool)
        ):
            raise ValueError("Redacted source provenance has an invalid schema.")
        return cls(URLAccessMode(mode), RedactedSourceProvenance(scheme, host, path, integrity))


@dataclass(frozen=True)
class URLAuthorization:
    """Process-local raw URL plus its safe persistent policy evidence."""

    raw_url: str
    mode: URLAccessMode
    provenance: RedactedSourceProvenance

    @property
    def host(self) -> str:
        return self.provenance.host

    @property
    def evidence(self) -> URLAuthorizationEvidence:
        return URLAuthorizationEvidence(self.mode, self.provenance)


def authorize_public_url(
    raw_url: str, mode: URLAccessMode, *, allow_insecure_http: bool = False
) -> URLAuthorization:
    """Validate an explicitly supplied public URL without accessing the network."""

    if not isinstance(mode, URLAccessMode):
        raise URLPolicyError(
            "url_mode_invalid", "A public URL requires the filtered or direct access mode."
        )
    parsed = _parse_public_url(raw_url)
    if parsed.scheme == "http" and not allow_insecure_http:
        raise URLPolicyError(
            "insecure_http_not_authorized",
            "An HTTP source requires explicit insecure-HTTP authorization.",
        )
    return URLAuthorization(
        raw_url=raw_url,
        mode=mode,
        provenance=RedactedSourceProvenance(
            scheme=parsed.scheme,
            host=parsed.hostname or "",
            path=parsed.path or "/",
            transport_integrity_verified=parsed.scheme == "https",
        ),
    )


def redact_url(raw_url: str) -> RedactedSourceProvenance:
    """Return persistent provenance and drop query and fragment unconditionally."""

    parsed = _parse_public_url(raw_url)
    return RedactedSourceProvenance(
        scheme=parsed.scheme,
        host=parsed.hostname or "",
        path=parsed.path or "/",
        transport_integrity_verified=parsed.scheme == "https",
    )


def validate_destination(authority: URLAuthorization, destination_url: str) -> None:
    """Reject redirects and discovered hosts outside the original authorization."""

    destination = _parse_public_url(destination_url)
    if destination.hostname != authority.host:
        raise URLPolicyError(
            "host_escalation", "The URL would access a host outside the authorization."
        )
    if authority.provenance.scheme == "https" and destination.scheme != "https":
        raise URLPolicyError("https_downgrade", "The URL would downgrade an HTTPS authorization.")


@dataclass
class ManualCollectionSession:
    """An in-memory, ordered URL collection that is inert until closure."""

    mode: URLAccessMode
    allow_insecure_http: bool = False
    _entries: list[URLAuthorization] = field(default_factory=list)
    _closed: bool = False
    _insecure_http_authorizations_remaining: int = field(init=False)

    def __post_init__(self) -> None:
        self._insecure_http_authorizations_remaining = int(self.allow_insecure_http)

    @property
    def entries(self) -> tuple[URLAuthorization, ...]:
        """Return ordered, process-local authorizations accumulated before closure."""

        return tuple(self._entries)

    def append(self, raw_url: str) -> URLAuthorization:
        """Append one ordered link after local policy and duplicate validation."""

        if self._closed:
            raise URLPolicyError(
                "collection_closed", "A closed collection cannot accept another URL."
            )
        if any(entry.raw_url == raw_url for entry in self._entries):
            raise URLPolicyError("duplicate_url", "A manual collection cannot repeat one URL.")
        authorization = authorize_public_url(
            raw_url,
            self.mode,
            allow_insecure_http=self._insecure_http_authorizations_remaining > 0,
        )
        if authorization.provenance.scheme == "http":
            self._insecure_http_authorizations_remaining -= 1
        self._entries.append(authorization)
        return authorization

    def close(self, signal: str) -> tuple[URLAuthorization, ...]:
        """Freeze an ordered collection only after the user supplies `结束`."""

        if signal != COLLECTION_CLOSURE_SIGNAL:
            raise URLPolicyError(
                "collection_closure_required", "Manual collection closure requires `结束`."
            )
        if not self._entries:
            raise URLPolicyError("collection_empty", "A manual collection needs at least one URL.")
        self._closed = True
        return tuple(self._entries)


def _parse_public_url(raw_url: str) -> SplitResult:
    parsed = urlsplit(raw_url)
    if parsed.scheme not in {"http", "https"}:
        raise URLPolicyError("url_scheme_invalid", "A public source must use HTTP or HTTPS.")
    if not parsed.hostname:
        raise URLPolicyError("url_host_missing", "A public source must include a host.")
    if parsed.username is not None or parsed.password is not None:
        raise URLPolicyError(
            "url_credentials_forbidden", "A public source URL must not contain credentials."
        )
    return parsed
