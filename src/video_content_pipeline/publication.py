"""Staging, atomic publication, the RunBundle manifest, and the latest pointer.

This module is the publication mechanism (ADR 0051): it turns a deterministic
:class:`~video_content_pipeline.publication_projection.ProjectionResult` and the
Minimal RunBundle floor documents into a published RunBundle at
``outputs/<source-id>/<run-id>/``, and advances the per-source latest pointer.

The mechanism has exactly the shape ADR 0051 fixes:

* **Staging** — candidate artifacts and reports are assembled under
  ``work/<source-id>/<run-id>/staging/`` in the *final* RunBundle layout, each
  file's bytes hashed and recorded in ``manifest.json``. The manifest and the
  staged files must match in *both* directions (nothing extra on disk, nothing
  missing) before publication is allowed.
* **Same-filesystem precheck** — before assembly writes anything, the staging
  device is compared with the ``outputs/`` device (``st_dev`` equality). A
  mismatch is an error, never a silent fallback to copying — a cross-device
  rename would not be atomic.
* **Atomic publish** — publication is one whole-directory ``rename`` of the
  staging tree onto ``outputs/<source-id>/<run-id>/``. Either the whole bundle
  becomes visible or none of it does; an existing run directory is never
  overwritten.
* **Post-publish reverification** — every published file is re-hashed against
  the manifest. A discrepancy is recorded and surfaced (journaled through the
  caller's seam and returned in the outcome), never silently accepted; a bundle
  that fails reverification cannot advance the latest pointer.
* **Latest pointer** — ``outputs/<source-id>/latest.json`` names the recommended
  publishable run and stores a pointer only, never a copy. It advances for a
  ``complete`` / ``complete_with_warnings`` run or a run that published partial
  results, and never for a purely failed run.

The report documents themselves (their *content*) are produced by a later
ticket; this module takes them through the :class:`BundleDocument` seam so the
mechanism is provable offline with synthetic documents.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from video_content_pipeline.durable_io import (
    atomic_replace,
    durable_write,
    fsync_directory,
    to_utc_isoformat,
    utc_now,
)
from video_content_pipeline.orchestration import RunLayout
from video_content_pipeline.publication_projection import (
    ArtifactStatus,
    ProjectedArtifact,
    ProjectionResult,
    TimingBasis,
    TimingView,
)
from video_content_pipeline.run_state import RunStatus

#: The name of the RunBundle manifest at the bundle root. It lists every other
#: file but never itself (a document cannot record its own hash before it is
#: written), so it is the one on-disk path the bidirectional coverage check
#: exempts.
MANIFEST_FILENAME = "manifest.json"

#: The per-source latest pointer, a sibling of the run directories.
LATEST_FILENAME = "latest.json"

#: The manifest ``kind`` recorded for a Minimal RunBundle report document, as
#: opposed to a projected content artifact carrying an ``ArtifactKind``.
DOCUMENT_KIND = "document"

_MANIFEST_SCHEMA_VERSION = 1
_LATEST_SCHEMA_VERSION = 1

#: Run statuses whose bundle always advances the latest pointer (ADR/plan §2.5).
_ALWAYS_ELIGIBLE: frozenset[RunStatus] = frozenset(
    {RunStatus.COMPLETE, RunStatus.COMPLETE_WITH_WARNINGS}
)

#: Run statuses that advance the pointer only when partial results were
#: published; a purely failed run among them advances nothing.
_ELIGIBLE_IF_PARTIAL: frozenset[RunStatus] = frozenset({RunStatus.INCOMPLETE, RunStatus.CANCELLED})

#: Projected-artifact statuses that count as published content for the
#: partial-results eligibility rule.
_PUBLISHED_CONTENT: frozenset[ArtifactStatus] = frozenset(
    {ArtifactStatus.VALID, ArtifactStatus.PARTIAL}
)


class PublicationError(ValueError):
    """A publication failure with an auditable machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _sha256_text(text: str) -> str:
    """Hash text by its UTF-8 bytes — the bundle-wide file digest idiom."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_document(payload: Mapping[str, object]) -> str:
    """Render a published JSON document deterministically, with trailing newline.

    Uses the repo's persisted-document idiom (``sort_keys`` + ``indent=2``) so a
    manifest or latest pointer is byte-identical across equal runs.
    """

    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


# --- Inputs and records -----------------------------------------------------


@dataclass(frozen=True)
class BundleDocument:
    """A RunBundle report document supplied by the run: a path and its bytes.

    The Minimal RunBundle floor — ``processing-report.md``, ``run-inventory.json``,
    ``quality-report.md``, ``quality-report.json`` and the ``diagnostics/``
    entries — reaches publication through a sequence of these. The path is
    bundle-relative (POSIX, possibly nested); ``manifest.json`` is reserved.
    """

    path: str
    content: str


@dataclass(frozen=True)
class ManifestArtifact:
    """One RunBundle manifest entry: a file's path, status, and recorded hash.

    ``sha256`` is ``None`` exactly for an ``unavailable`` artifact — no file is
    published for it, so it is excluded from the on-disk coverage check. Timing
    fields are carried only for coordinate-bearing / alignment-governed content
    artifacts (ADR 0026); documents leave them ``None``.
    """

    path: str
    kind: str
    status: ArtifactStatus
    sha256: str | None
    timing_view: TimingView | None = None
    timing_basis: TimingBasis | None = None
    provenance: Mapping[str, object] = field(default_factory=dict)

    @classmethod
    def from_projected(cls, artifact: ProjectedArtifact) -> ManifestArtifact:
        return cls(
            path=artifact.path,
            kind=artifact.kind.value,
            status=artifact.status,
            sha256=artifact.sha256,
            timing_view=artifact.timing_view,
            timing_basis=artifact.timing_basis,
            provenance=dict(artifact.provenance),
        )

    @classmethod
    def for_document(cls, document: BundleDocument) -> ManifestArtifact:
        return cls(
            path=document.path,
            kind=DOCUMENT_KIND,
            status=ArtifactStatus.VALID,
            sha256=_sha256_text(document.content),
        )

    @property
    def has_file(self) -> bool:
        """Whether a file is expected on disk for this entry."""

        return self.sha256 is not None

    def as_json(self) -> dict[str, object]:
        return {
            "path": self.path,
            "kind": self.kind,
            "status": self.status.value,
            "sha256": self.sha256,
            "timing_view": self.timing_view.value if self.timing_view else None,
            "timing_basis": self.timing_basis.value if self.timing_basis else None,
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_json(cls, value: object) -> ManifestArtifact:
        if not isinstance(value, Mapping):
            raise PublicationError("manifest_invalid", "A manifest artifact must be an object.")
        path = _required_str(value, "path")
        kind = _required_str(value, "kind")
        status = _parse_status(value.get("status"))
        sha256 = value.get("sha256")
        if sha256 is not None and not isinstance(sha256, str):
            raise PublicationError("manifest_invalid", "sha256 must be a string or null.")
        provenance = value.get("provenance", {})
        if not isinstance(provenance, Mapping):
            raise PublicationError("manifest_invalid", "provenance must be an object.")
        return cls(
            path=path,
            kind=kind,
            status=status,
            sha256=sha256,
            timing_view=_parse_timing_view(value.get("timing_view")),
            timing_basis=_parse_timing_basis(value.get("timing_basis")),
            provenance=dict(provenance),
        )


@dataclass(frozen=True)
class RunBundleManifest:
    """The manifest listing every expected RunBundle file with status and hash.

    ``plan_id`` records the confirmed plan that produced the bundle, so a
    published RunBundle is self-describing: an Improvement run
    (:mod:`~video_content_pipeline.improve`) reads it to locate the source plan it
    derives a new plan from, without reading any workspace. The publication
    mechanism itself is plan-agnostic — the run loop supplies the identity — so it
    defaults to empty for the isolated mechanism unit tests that carry no plan.
    """

    source_id: str
    run_id: str
    run_status: RunStatus
    projection_stage_version: int
    artifacts: tuple[ManifestArtifact, ...]
    plan_id: str = ""
    schema_version: int = _MANIFEST_SCHEMA_VERSION

    def as_json(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_id": self.source_id,
            "run_id": self.run_id,
            "plan_id": self.plan_id,
            "run_status": self.run_status.value,
            "projection_stage_version": self.projection_stage_version,
            "artifacts": [artifact.as_json() for artifact in self.artifacts],
        }

    def to_text(self) -> str:
        return _canonical_document(self.as_json())

    @classmethod
    def from_json(cls, value: object) -> RunBundleManifest:
        if not isinstance(value, Mapping):
            raise PublicationError("manifest_invalid", "A manifest must be a JSON object.")
        if value.get("schema_version") != _MANIFEST_SCHEMA_VERSION:
            raise PublicationError(
                "manifest_invalid",
                f"Manifest schema_version must be {_MANIFEST_SCHEMA_VERSION}.",
            )
        raw_artifacts = value.get("artifacts")
        if not isinstance(raw_artifacts, Sequence) or isinstance(raw_artifacts, str | bytes):
            raise PublicationError("manifest_invalid", "artifacts must be a list.")
        version = value.get("projection_stage_version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise PublicationError(
                "manifest_invalid", "projection_stage_version must be an integer."
            )
        plan_id = value.get("plan_id", "")
        if not isinstance(plan_id, str):
            raise PublicationError("manifest_invalid", "plan_id must be a string.")
        return cls(
            source_id=_required_str(value, "source_id"),
            run_id=_required_str(value, "run_id"),
            run_status=_parse_run_status(value.get("run_status")),
            projection_stage_version=version,
            artifacts=tuple(ManifestArtifact.from_json(item) for item in raw_artifacts),
            plan_id=plan_id,
        )


@dataclass(frozen=True)
class VerificationDiscrepancy:
    """One way a published bundle failed to match its manifest."""

    path: str
    reason: str


@dataclass(frozen=True)
class PublicationVerification:
    """The outcome of re-hashing a bundle against its manifest."""

    verified: bool
    discrepancies: tuple[VerificationDiscrepancy, ...] = ()


@dataclass(frozen=True)
class LatestPointer:
    """The per-source latest pointer document — a pointer, never a copy."""

    source_id: str
    run_id: str
    run_status: RunStatus
    published_at: str
    schema_version: int = _LATEST_SCHEMA_VERSION

    def as_json(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_id": self.source_id,
            "run_id": self.run_id,
            "run_status": self.run_status.value,
            "published_at": self.published_at,
        }

    @classmethod
    def from_json(cls, value: object) -> LatestPointer:
        if not isinstance(value, Mapping):
            raise PublicationError("latest_invalid", "A latest pointer must be an object.")
        if value.get("schema_version") != _LATEST_SCHEMA_VERSION:
            raise PublicationError(
                "latest_invalid", f"Latest schema_version must be {_LATEST_SCHEMA_VERSION}."
            )
        return cls(
            source_id=_required_str(value, "source_id", reason="latest_invalid"),
            run_id=_required_str(value, "run_id", reason="latest_invalid"),
            run_status=_parse_run_status(value.get("run_status"), reason="latest_invalid"),
            published_at=_required_str(value, "published_at", reason="latest_invalid"),
        )


@dataclass(frozen=True)
class PublicationOutcome:
    """The result of publishing one RunBundle."""

    output_dir: Path
    manifest: RunBundleManifest
    verification: PublicationVerification
    latest_advanced: bool


# --- Manifest assembly ------------------------------------------------------


def build_run_bundle_manifest(
    *,
    source_id: str,
    run_id: str,
    run_status: RunStatus,
    projection: ProjectionResult,
    documents: Sequence[BundleDocument],
    plan_id: str = "",
) -> RunBundleManifest:
    """Merge the projected artifacts and report documents into one manifest.

    Every projected artifact is listed — including ``unavailable`` ones with no
    file — alongside a ``valid`` entry per report document. Paths must be unique
    and none may claim the reserved manifest name, and the entries are sorted by
    path so a manifest is byte-identical across equal runs.
    """

    entries: list[ManifestArtifact] = [
        ManifestArtifact.from_projected(artifact) for artifact in projection.artifacts
    ]
    for document in documents:
        if document.path == MANIFEST_FILENAME:
            raise PublicationError(
                "reserved_manifest_path",
                f"A document may not use the reserved path {MANIFEST_FILENAME!r}.",
            )
        entries.append(ManifestArtifact.for_document(document))

    seen: set[str] = set()
    for entry in entries:
        if entry.path in seen:
            raise PublicationError(
                "duplicate_artifact_path",
                f"The manifest lists {entry.path!r} more than once.",
            )
        seen.add(entry.path)

    entries.sort(key=lambda entry: entry.path)
    return RunBundleManifest(
        source_id=source_id,
        run_id=run_id,
        run_status=run_status,
        projection_stage_version=projection.stage_version,
        artifacts=tuple(entries),
        plan_id=plan_id,
    )


# --- Staging ----------------------------------------------------------------


def _outputs_root(layout: RunLayout) -> Path:
    return layout.project_root / "outputs"


def _device_of(path: Path) -> int:
    """Return the filesystem device id a path lives on (a narrow test seam)."""

    return os.stat(path).st_dev


def _require_same_filesystem(layout: RunLayout) -> None:
    """Fail fast unless staging and ``outputs/`` share one filesystem.

    Compared before assembly writes anything: the atomic publish is a rename of
    the staging tree onto a child of ``outputs/``, and a rename across devices is
    not atomic. A mismatch errors here rather than silently degrading to a copy.
    """

    outputs_root = _outputs_root(layout)
    outputs_root.mkdir(parents=True, exist_ok=True)
    layout.staging_dir.mkdir(parents=True, exist_ok=True)
    if _device_of(layout.staging_dir) != _device_of(outputs_root):
        raise PublicationError(
            "cross_device_publish",
            "Staging and outputs are on different filesystems; an atomic "
            "publish rename is impossible and copying is refused (ADR 0051).",
        )


def _write_bundle_file(root: Path, relative_path: str, content: str) -> None:
    destination = root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        durable_write(destination, content, flags=os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    except OSError as exc:
        # A full disk (ENOSPC) mid-assembly must surface as the module's typed
        # reason — the CLI's error contract catches ``PublicationError`` but not a
        # bare ``OSError`` — and it is safe to do so: staging is a scratch tree the
        # atomic publish rename has not yet exposed, so nothing partial is visible.
        raise PublicationError(
            "staging_write_failed", f"Writing staged bundle file {relative_path} failed."
        ) from exc


def assemble_staging(
    layout: RunLayout,
    *,
    run_status: RunStatus,
    projection: ProjectionResult,
    documents: Sequence[BundleDocument],
    plan_id: str = "",
) -> RunBundleManifest:
    """Assemble the RunBundle in staging and return its verified manifest.

    Runs the same-filesystem precheck first, writes each available projected
    artifact and every report document into ``staging/`` in final bundle layout,
    writes ``manifest.json``, then confirms staging matches the manifest in both
    directions. A mismatch here is an assembly bug and raises.
    """

    _require_same_filesystem(layout)
    manifest = build_run_bundle_manifest(
        source_id=layout.source_id,
        run_id=layout.run_id,
        run_status=run_status,
        projection=projection,
        documents=documents,
        plan_id=plan_id,
    )

    staging = layout.staging_dir
    for artifact in projection.artifacts:
        if artifact.content is not None:
            _write_bundle_file(staging, artifact.path, artifact.content)
    for document in documents:
        _write_bundle_file(staging, document.path, document.content)
    _write_bundle_file(staging, MANIFEST_FILENAME, manifest.to_text())

    verification = _verify_directory(staging, manifest)
    if not verification.verified:
        raise PublicationError(
            "staging_manifest_mismatch",
            "Staging did not match its manifest after assembly: "
            + ", ".join(f"{d.path} ({d.reason})" for d in verification.discrepancies),
        )
    return manifest


# --- Verification -----------------------------------------------------------


def _relative_files(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


def _verify_directory(root: Path, manifest: RunBundleManifest) -> PublicationVerification:
    """Re-hash ``root`` against ``manifest`` in both directions.

    Every entry with a recorded hash must be present with matching bytes; every
    file on disk (except the manifest itself) must be a listed entry. Missing
    files, hash mismatches, and unexpected files are all discrepancies.
    """

    discrepancies: list[VerificationDiscrepancy] = []
    expected: set[str] = set()
    for artifact in manifest.artifacts:
        if not artifact.has_file:
            continue
        expected.add(artifact.path)
        file_path = root / artifact.path
        if not file_path.is_file():
            discrepancies.append(VerificationDiscrepancy(artifact.path, "missing"))
            continue
        actual = _sha256_text(file_path.read_text(encoding="utf-8"))
        if actual != artifact.sha256:
            discrepancies.append(VerificationDiscrepancy(artifact.path, "hash_mismatch"))

    on_disk = _relative_files(root)
    on_disk.discard(MANIFEST_FILENAME)
    for extra in sorted(on_disk - expected):
        discrepancies.append(VerificationDiscrepancy(extra, "unexpected"))

    return PublicationVerification(verified=not discrepancies, discrepancies=tuple(discrepancies))


def read_run_bundle_manifest(bundle_dir: Path) -> RunBundleManifest:
    """Read and parse ``manifest.json`` from a bundle directory."""

    manifest_path = bundle_dir / MANIFEST_FILENAME
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PublicationError("manifest_missing", f"No manifest at {manifest_path}.") from exc
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PublicationError("manifest_invalid", "The manifest is not valid JSON.") from exc
    return RunBundleManifest.from_json(document)


def verify_published_bundle(bundle_dir: Path) -> PublicationVerification:
    """Re-hash a published bundle against its own ``manifest.json``.

    This is the hash-layer check ``vcp verify`` and the post-publish
    reverification share: it reads the manifest the bundle carries and confirms
    the files on disk still match it in both directions.
    """

    manifest = read_run_bundle_manifest(bundle_dir)
    return _verify_directory(bundle_dir, manifest)


# --- Latest pointer ---------------------------------------------------------


def latest_pointer_eligible(
    run_status: RunStatus,
    projection: ProjectionResult,
    verification: PublicationVerification,
) -> bool:
    """Whether this run's bundle may become the per-source latest pointer.

    A bundle that failed reverification is never eligible — the pointer must
    never name a corrupt bundle. Otherwise a ``complete`` /
    ``complete_with_warnings`` run is always eligible, an ``incomplete`` /
    ``cancelled`` run is eligible only if it published partial content, and a
    purely failed run is never eligible.
    """

    if not verification.verified:
        return False
    if run_status in _ALWAYS_ELIGIBLE:
        return True
    if run_status in _ELIGIBLE_IF_PARTIAL:
        return any(artifact.status in _PUBLISHED_CONTENT for artifact in projection.artifacts)
    return False


def read_latest_pointer(path: Path) -> LatestPointer | None:
    """Read the latest pointer at ``path``, or ``None`` if there is none."""

    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PublicationError("latest_invalid", "The latest pointer is not valid JSON.") from exc
    return LatestPointer.from_json(document)


def _advance_latest_pointer(layout: RunLayout, run_status: RunStatus, now: datetime) -> bool:
    """Point ``latest.json`` at this run, unless a newer run already owns it.

    Run ids sort chronologically, so a strictly greater existing pointer is a
    later run whose place this one must not take. Otherwise the pointer is
    replaced atomically with a fresh document that stores this run id only.
    """

    existing = read_latest_pointer(layout.latest_path)
    if existing is not None and existing.run_id > layout.run_id:
        return False
    pointer = LatestPointer(
        source_id=layout.source_id,
        run_id=layout.run_id,
        run_status=run_status,
        published_at=to_utc_isoformat(
            now, on_naive=lambda: PublicationError("naive_timestamp", "published_at must be aware.")
        ),
    )
    atomic_replace(layout.latest_path, _canonical_document(pointer.as_json()))
    return True


# --- Publication ------------------------------------------------------------


def publish_run_bundle(
    layout: RunLayout,
    *,
    run_status: RunStatus,
    projection: ProjectionResult,
    documents: Sequence[BundleDocument],
    plan_id: str = "",
    now: datetime | None = None,
    journal: Callable[[Mapping[str, object]], None] | None = None,
) -> PublicationOutcome:
    """Assemble, atomically publish, reverify, and advance the latest pointer.

    Staging is assembled and self-verified; the run bundle directory is refused
    if it already exists (a published run is never overwritten); the whole
    staging tree is renamed onto ``outputs/<source-id>/<run-id>/`` in one commit;
    every published file is re-hashed against the manifest. A reverification
    failure is journaled through ``journal`` and returned in the outcome — never
    silently accepted — and blocks the latest pointer from advancing.
    """

    manifest = assemble_staging(
        layout,
        run_status=run_status,
        projection=projection,
        documents=documents,
        plan_id=plan_id,
    )

    if layout.output_dir.exists():
        raise PublicationError(
            "run_already_published",
            f"A published RunBundle already exists at {layout.output_dir}.",
        )
    layout.source_output_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(layout.staging_dir, layout.output_dir)
    except OSError as exc:
        raise PublicationError(
            "publish_rename_failed",
            f"Publishing the RunBundle to {layout.output_dir} failed.",
        ) from exc
    try:
        fsync_directory(layout.source_output_dir)
    except OSError as exc:
        # The rename already committed the whole bundle; only the durability
        # flush of its directory entry failed. Surface the typed reason so the
        # CLI reports it cleanly rather than leaking a bare ``OSError``.
        raise PublicationError(
            "publish_fsync_failed",
            f"Flushing the published RunBundle at {layout.output_dir} failed.",
        ) from exc

    verification = verify_published_bundle(layout.output_dir)
    if not verification.verified and journal is not None:
        journal(
            {
                "event": "publication_verification_failed",
                "run_id": layout.run_id,
                "source_id": layout.source_id,
                "discrepancies": [
                    {"path": d.path, "reason": d.reason} for d in verification.discrepancies
                ],
            }
        )

    latest_advanced = False
    if latest_pointer_eligible(run_status, projection, verification):
        try:
            latest_advanced = _advance_latest_pointer(
                layout, run_status, now if now is not None else utc_now()
            )
        except OSError as exc:
            # ``_advance_latest_pointer`` writes ``latest.json`` through an atomic
            # temp-then-rename, so a full disk leaves the previous pointer (or
            # none) intact — never a torn one. Report the typed reason.
            raise PublicationError(
                "latest_pointer_write_failed",
                f"Advancing the latest pointer at {layout.latest_path} failed.",
            ) from exc

    return PublicationOutcome(
        output_dir=layout.output_dir,
        manifest=manifest,
        verification=verification,
        latest_advanced=latest_advanced,
    )


# --- JSON parsing helpers ---------------------------------------------------


def _required_str(
    document: Mapping[str, object], key: str, *, reason: str = "manifest_invalid"
) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise PublicationError(reason, f"{key} must be a non-empty string.")
    return value


def _enum_string(value: object, *, field_name: str, reason: str = "manifest_invalid") -> str:
    if not isinstance(value, str):
        raise PublicationError(reason, f"{field_name} must be a string.")
    return value


def _parse_status(value: object) -> ArtifactStatus:
    text = _enum_string(value, field_name="status")
    try:
        return ArtifactStatus(text)
    except ValueError as exc:
        raise PublicationError("manifest_invalid", f"Unknown artifact status {text!r}.") from exc


def _parse_run_status(value: object, *, reason: str = "manifest_invalid") -> RunStatus:
    text = _enum_string(value, field_name="run_status", reason=reason)
    try:
        return RunStatus(text)
    except ValueError as exc:
        raise PublicationError(reason, f"Unknown run status {text!r}.") from exc


def _parse_timing_view(value: object) -> TimingView | None:
    if value is None:
        return None
    text = _enum_string(value, field_name="timing_view")
    try:
        return TimingView(text)
    except ValueError as exc:
        raise PublicationError("manifest_invalid", f"Unknown timing view {text!r}.") from exc


def _parse_timing_basis(value: object) -> TimingBasis | None:
    if value is None:
        return None
    text = _enum_string(value, field_name="timing_basis")
    try:
        return TimingBasis(text)
    except ValueError as exc:
        raise PublicationError("manifest_invalid", f"Unknown timing basis {text!r}.") from exc
