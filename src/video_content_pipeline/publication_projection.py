"""The deterministic, versioned Publication projection.

The projection is the render layer that turns verified workspace evidence into
the plan §4 publication file names and formats — ``subtitles.<basis>.{srt,vtt}``,
``transcript.<basis>.{md,json}``, ``content-report.md``, ``segments.json`` and
``correction-log.json`` — selecting and recording a timing view (ADR 0026) per
exported artifact. It performs *no new analysis and no content change*: it only
selects among the evidence it is given, assigns each artifact its publication
name and timing view, records provenance and status, and hashes the bytes. The
staging assembly and the whole-directory atomic publish that consume this
projection are a later ticket; this layer never touches ``outputs/``.

Its inputs arrive through a seam — :class:`ProjectionEvidence`, the verified
evidence the run's in-process composition provides (offline tests build it
directly). Two orthogonal axes are recorded per artifact:

* **Timing view** (Phase 2 coordinate space): ``part_relative`` for per-Part
  exports under ``parts/<part-id>/``, ``collection_virtual`` for the
  collection-level exports assembled at the bundle root.
* **Timing basis** (ADR 0026): ``adopted_alignment`` where a Part's
  forced-alignment gates passed and an aligned rendering exists, otherwise the
  ``original`` subtitle/ASR timing. It applies only to the alignment-governed
  timed artifacts (subtitles and transcripts).

Determinism is the contract: the same verified inputs and the same projection
Stage version always produce byte-identical artifacts, emitted in sorted path
order. Missing upstream evidence yields an ``unavailable`` manifest entry with
no bytes — never a fabricated placeholder file. The projection carries its own
Stage version (:data:`PUBLICATION_PROJECTION_STAGE_VERSION`) participating in a
:class:`ProjectionInvalidationKey`; any behaviour change here must increment it,
mirroring the stage-version discipline of ``stage_dag`` (ADR 0052).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from video_content_pipeline.planning import RunPlan
from video_content_pipeline.run_choices import AsrMode

#: The manually incremented Stage version of the Publication projection. Any
#: behaviour change — a new artifact, a different selection rule, a format
#: change — must bump this, or a resumed run silently re-publishes bytes made by
#: the old behaviour (ADR 0052).
PUBLICATION_PROJECTION_STAGE_VERSION = 1

_KEY_SCHEMA_VERSION = 1


class PublicationProjectionError(ValueError):
    """A projection failure with an auditable machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _sha256_json(value: object) -> str:
    """Hash a value by its canonical JSON form (the repo's digest idiom)."""

    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ArtifactKind(StrEnum):
    """The logical family a projected artifact belongs to."""

    SUBTITLES = "subtitles"
    TRANSCRIPT = "transcript"
    CONTENT_REPORT = "content_report"
    SEGMENTS = "segments"
    CORRECTION_LOG = "correction_log"


class PublicationBasis(StrEnum):
    """The publication basis component of a subtitle/transcript file name."""

    SOURCE = "source"
    READABLE = "readable"
    ENHANCED = "enhanced"
    VERBATIM = "verbatim"


class TimingView(StrEnum):
    """The coordinate space of a coordinate-bearing artifact (Phase 2)."""

    PART_RELATIVE = "part_relative"
    COLLECTION_VIRTUAL = "collection_virtual"


class TimingBasis(StrEnum):
    """Whether an alignment-governed artifact uses adopted or original timing."""

    ORIGINAL = "original"
    ADOPTED_ALIGNMENT = "adopted_alignment"


class ArtifactStatus(StrEnum):
    """A projected artifact's manifest status."""

    VALID = "valid"
    PARTIAL = "partial"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


#: Artifact kinds that carry a timeline, so a timing view is recorded for them.
_COORDINATE_BEARING: frozenset[ArtifactKind] = frozenset(
    {ArtifactKind.SUBTITLES, ArtifactKind.TRANSCRIPT, ArtifactKind.SEGMENTS}
)

#: Artifact kinds whose timing basis is governed by ADR 0026 alignment gates.
_ALIGNMENT_GOVERNED: frozenset[ArtifactKind] = frozenset(
    {ArtifactKind.SUBTITLES, ArtifactKind.TRANSCRIPT}
)

#: The subtitle bases each ASR mode publishes (plan §7). No mode ever emits
#: another mode's bases — this table is the single place that mapping lives.
_MODE_SUBTITLE_BASES: Mapping[AsrMode, tuple[PublicationBasis, ...]] = {
    AsrMode.SUBTITLE_FIRST: (PublicationBasis.SOURCE, PublicationBasis.READABLE),
    AsrMode.FULL_ASR: (PublicationBasis.VERBATIM, PublicationBasis.READABLE),
    AsrMode.ENHANCEMENT: (PublicationBasis.ENHANCED,),
}

#: The transcript basis each mode publishes (``transcript.<basis>.*``, plan §7).
_MODE_TRANSCRIPT_BASIS: Mapping[AsrMode, PublicationBasis] = {
    AsrMode.SUBTITLE_FIRST: PublicationBasis.SOURCE,
    AsrMode.FULL_ASR: PublicationBasis.VERBATIM,
    AsrMode.ENHANCEMENT: PublicationBasis.ENHANCED,
}


def expected_subtitle_bases(mode: AsrMode) -> tuple[PublicationBasis, ...]:
    """Return the subtitle bases ``mode`` publishes, in canonical order."""

    return _MODE_SUBTITLE_BASES[mode]


def transcript_basis(mode: AsrMode) -> PublicationBasis:
    """Return the transcript basis ``mode`` publishes."""

    return _MODE_TRANSCRIPT_BASIS[mode]


# --- Evidence seam ----------------------------------------------------------


@dataclass(frozen=True)
class TimedArtifactEvidence:
    """Verified rendering(s) of one timed artifact in one coordinate space.

    ``original`` and ``adopted_alignment`` are the artifact's bytes already
    rendered by upstream in this scope's coordinate space, with original timing
    and with adopted forced-alignment timing respectively; either may be absent.
    ``adopted_gates_passed`` records whether ADR 0026's global validity gates
    passed for this scope, which is the sole condition under which the adopted
    rendering may be published. ``provenance`` carries the artifact's recorded
    provenance verbatim (for example enhancement's per-cue source markers).
    """

    original: str | None = None
    adopted_alignment: str | None = None
    adopted_gates_passed: bool = False
    partial: bool = False
    invalid: bool = False
    provenance: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PlainArtifactEvidence:
    """Verified rendering of a non-timed collection document."""

    content: str | None = None
    partial: bool = False
    invalid: bool = False
    provenance: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ProjectionEvidence:
    """All verified evidence the projection selects among.

    The projection never reads the filesystem; it selects only from what this
    structure holds. An absent map entry or ``None`` field means the artifact is
    unavailable and is recorded as such — no placeholder file is fabricated.
    """

    part_subtitles: Mapping[tuple[str, PublicationBasis], TimedArtifactEvidence] = field(
        default_factory=dict
    )
    collection_subtitles: Mapping[PublicationBasis, TimedArtifactEvidence] = field(
        default_factory=dict
    )
    collection_transcript: TimedArtifactEvidence | None = None
    content_report: PlainArtifactEvidence | None = None
    segments: PlainArtifactEvidence | None = None
    correction_log: PlainArtifactEvidence | None = None


# --- Projected artifacts ----------------------------------------------------


@dataclass(frozen=True)
class ProjectedArtifact:
    """One artifact the projection resolved to a publication path.

    ``content`` is ``None`` exactly when ``status`` is ``unavailable``; in that
    case ``sha256`` is ``None`` too and no file is ever written. ``timing_view``
    is recorded for coordinate-bearing artifacts; ``timing_basis`` only for the
    alignment-governed ones (ADR 0026).
    """

    path: str
    kind: ArtifactKind
    status: ArtifactStatus
    content: str | None
    sha256: str | None
    timing_view: TimingView | None
    timing_basis: TimingBasis | None
    provenance: Mapping[str, object]

    def as_manifest_entry(self) -> dict[str, object]:
        """Render the manifest record staging (a later ticket) publishes."""

        return {
            "path": self.path,
            "kind": self.kind.value,
            "status": self.status.value,
            "sha256": self.sha256,
            "timing_view": self.timing_view.value if self.timing_view else None,
            "timing_basis": self.timing_basis.value if self.timing_basis else None,
            "provenance": dict(self.provenance),
        }


@dataclass(frozen=True)
class ProjectionResult:
    """The projection's deterministic output over one plan's evidence."""

    artifacts: tuple[ProjectedArtifact, ...]
    stage_version: int

    def manifest_entries(self) -> list[dict[str, object]]:
        """Return every artifact's manifest record in sorted path order."""

        return [artifact.as_manifest_entry() for artifact in self.artifacts]

    def digest(self) -> str:
        """Return the stable digest of the whole projection.

        Taken over the manifest entries (the persisted form), so the digest and
        what staging publishes can never drift apart, and byte-identical inputs
        yield an identical digest — the determinism contract's witness.
        """

        return _sha256_json(
            {"stage_version": self.stage_version, "artifacts": self.manifest_entries()}
        )


# --- Invalidation key -------------------------------------------------------


@dataclass(frozen=True)
class ProjectionInvalidationKey:
    """The Publication projection's invalidation key (ADR 0052).

    It folds the upstream evidence fingerprint, the hash of the projection's
    configuration subset (the ASR mode that fixes the artifact set), and the
    projection Stage version, so a version bump or an evidence change re-keys the
    projection while identical runs share a key.
    """

    stage_version: int
    input_hashes: tuple[str, ...]
    config_subset_hash: str

    def digest(self) -> str:
        return _sha256_json(self.as_json())

    def as_json(self) -> dict[str, object]:
        return {
            "schema_version": _KEY_SCHEMA_VERSION,
            "stage": "publication_projection",
            "stage_version": self.stage_version,
            "input_hashes": list(self.input_hashes),
            "config_subset_hash": self.config_subset_hash,
        }

    @classmethod
    def from_json(cls, value: object) -> ProjectionInvalidationKey:
        if not isinstance(value, Mapping):
            raise PublicationProjectionError(
                "invalidation_key_invalid", "A key must be a JSON object."
            )
        if value.get("schema_version") != _KEY_SCHEMA_VERSION:
            raise PublicationProjectionError(
                "invalidation_key_invalid",
                f"Invalidation key schema_version must be {_KEY_SCHEMA_VERSION}.",
            )
        version = value.get("stage_version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise PublicationProjectionError(
                "invalidation_key_invalid", "stage_version must be an integer."
            )
        raw_hashes = value.get("input_hashes")
        if not isinstance(raw_hashes, Sequence) or isinstance(raw_hashes, str | bytes):
            raise PublicationProjectionError(
                "invalidation_key_invalid", "input_hashes must be a list."
            )
        hashes = tuple(_required_hash(item) for item in raw_hashes)
        config_hash = value.get("config_subset_hash")
        if not isinstance(config_hash, str):
            raise PublicationProjectionError(
                "invalidation_key_invalid", "config_subset_hash must be a string."
            )
        return cls(stage_version=version, input_hashes=hashes, config_subset_hash=config_hash)


def _required_hash(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise PublicationProjectionError(
            "invalidation_key_invalid", "Each input hash must be a string."
        )
    return value


# --- Projection -------------------------------------------------------------


def _require_mode(plan: RunPlan) -> AsrMode:
    mode = plan.run_choices.asr_mode()
    if mode is None:
        raise PublicationProjectionError(
            "missing_asr_mode",
            "A confirmed plan must fix an ASR mode before it can be projected.",
        )
    return mode


def _part_ids(plan: RunPlan) -> tuple[str, ...]:
    ids = tuple(artifact.source_id for artifact in plan.source_artifacts)
    if not ids:
        raise PublicationProjectionError("empty_plan", "A run plan needs at least one Part.")
    if len(set(ids)) != len(ids):
        raise PublicationProjectionError(
            "duplicate_part", "A run plan's Part source-ids must be distinct."
        )
    return ids


def _select_timed(
    evidence: TimedArtifactEvidence | None,
) -> tuple[str | None, TimingBasis | None]:
    """Choose the timed rendering to publish and its ADR 0026 basis.

    The adopted-alignment rendering is used only where it exists *and* its gates
    passed; otherwise the original timing is used. Absent both, the artifact is
    unavailable and no basis is recorded.
    """

    if evidence is None:
        return None, None
    if evidence.adopted_alignment is not None and evidence.adopted_gates_passed:
        return evidence.adopted_alignment, TimingBasis.ADOPTED_ALIGNMENT
    if evidence.original is not None:
        return evidence.original, TimingBasis.ORIGINAL
    return None, None


def _status_for(content: str | None, *, partial: bool, invalid: bool) -> ArtifactStatus:
    if content is None:
        return ArtifactStatus.UNAVAILABLE
    if invalid:
        return ArtifactStatus.INVALID
    if partial:
        return ArtifactStatus.PARTIAL
    return ArtifactStatus.VALID


def _timed_artifact(
    *,
    path: str,
    kind: ArtifactKind,
    timing_view: TimingView,
    evidence: TimedArtifactEvidence | None,
) -> ProjectedArtifact:
    content, basis = _select_timed(evidence)
    status = _status_for(
        content,
        partial=evidence.partial if evidence else False,
        invalid=evidence.invalid if evidence else False,
    )
    recorded_basis = basis if kind in _ALIGNMENT_GOVERNED else None
    return ProjectedArtifact(
        path=path,
        kind=kind,
        status=status,
        content=content,
        sha256=_sha256_text(content) if content is not None else None,
        timing_view=timing_view if kind in _COORDINATE_BEARING else None,
        timing_basis=recorded_basis if status is not ArtifactStatus.UNAVAILABLE else None,
        provenance=dict(evidence.provenance) if evidence else {},
    )


def _plain_artifact(
    *,
    path: str,
    kind: ArtifactKind,
    evidence: PlainArtifactEvidence | None,
) -> ProjectedArtifact:
    content = evidence.content if evidence else None
    status = _status_for(
        content,
        partial=evidence.partial if evidence else False,
        invalid=evidence.invalid if evidence else False,
    )
    timing_view = TimingView.COLLECTION_VIRTUAL if kind in _COORDINATE_BEARING else None
    return ProjectedArtifact(
        path=path,
        kind=kind,
        status=status,
        content=content,
        sha256=_sha256_text(content) if content is not None else None,
        timing_view=timing_view,
        timing_basis=None,
        provenance=dict(evidence.provenance) if evidence else {},
    )


_SUBTITLE_EXTENSIONS = ("srt", "vtt")
_TRANSCRIPT_EXTENSIONS = ("md", "json")


def project_publication(plan: RunPlan, evidence: ProjectionEvidence) -> ProjectionResult:
    """Project verified evidence into the plan's publication artifacts.

    The set of expected artifacts is fixed entirely by the plan's ASR mode and
    Parts, so no mode ever fabricates another mode's artifacts. Each artifact is
    resolved to a publication path, a timing view, an ADR 0026 timing basis, a
    status, and a hash; missing evidence becomes an ``unavailable`` entry with no
    bytes. Artifacts are returned in sorted path order for byte-identical output.
    """

    mode = _require_mode(plan)
    parts = _part_ids(plan)
    bases = expected_subtitle_bases(mode)
    artifacts: list[ProjectedArtifact] = []

    # Per-Part subtitles (PartRelativeTime), under parts/<part-id>/.
    for part in parts:
        for basis in bases:
            for extension in _SUBTITLE_EXTENSIONS:
                artifacts.append(
                    _timed_artifact(
                        path=f"parts/{part}/subtitles.{basis.value}.{extension}",
                        kind=ArtifactKind.SUBTITLES,
                        timing_view=TimingView.PART_RELATIVE,
                        evidence=evidence.part_subtitles.get((part, basis)),
                    )
                )

    # Collection-level subtitles (CollectionVirtualTime), at the bundle root.
    for basis in bases:
        for extension in _SUBTITLE_EXTENSIONS:
            artifacts.append(
                _timed_artifact(
                    path=f"subtitles.{basis.value}.{extension}",
                    kind=ArtifactKind.SUBTITLES,
                    timing_view=TimingView.COLLECTION_VIRTUAL,
                    evidence=evidence.collection_subtitles.get(basis),
                )
            )

    # Collection-level transcript (CollectionVirtualTime).
    t_basis = transcript_basis(mode)
    for extension in _TRANSCRIPT_EXTENSIONS:
        artifacts.append(
            _timed_artifact(
                path=f"transcript.{t_basis.value}.{extension}",
                kind=ArtifactKind.TRANSCRIPT,
                timing_view=TimingView.COLLECTION_VIRTUAL,
                evidence=evidence.collection_transcript,
            )
        )

    # Collection-level documents.
    artifacts.append(
        _plain_artifact(
            path="content-report.md",
            kind=ArtifactKind.CONTENT_REPORT,
            evidence=evidence.content_report,
        )
    )
    artifacts.append(
        _plain_artifact(
            path="segments.json", kind=ArtifactKind.SEGMENTS, evidence=evidence.segments
        )
    )
    artifacts.append(
        _plain_artifact(
            path="correction-log.json",
            kind=ArtifactKind.CORRECTION_LOG,
            evidence=evidence.correction_log,
        )
    )

    artifacts.sort(key=lambda artifact: artifact.path)
    return ProjectionResult(
        artifacts=tuple(artifacts),
        stage_version=PUBLICATION_PROJECTION_STAGE_VERSION,
    )


# --- Invalidation -----------------------------------------------------------


def _timed_fingerprint(evidence: TimedArtifactEvidence | None) -> object:
    if evidence is None:
        return None
    return {
        "original": evidence.original,
        "adopted_alignment": evidence.adopted_alignment,
        "adopted_gates_passed": evidence.adopted_gates_passed,
        "partial": evidence.partial,
        "invalid": evidence.invalid,
        "provenance": dict(evidence.provenance),
    }


def _plain_fingerprint(evidence: PlainArtifactEvidence | None) -> object:
    if evidence is None:
        return None
    return {
        "content": evidence.content,
        "partial": evidence.partial,
        "invalid": evidence.invalid,
        "provenance": dict(evidence.provenance),
    }


def _evidence_fingerprint(evidence: ProjectionEvidence) -> str:
    part_subtitles = [
        [part, basis.value, _timed_fingerprint(value)]
        for (part, basis), value in sorted(
            evidence.part_subtitles.items(), key=lambda item: (item[0][0], item[0][1].value)
        )
    ]
    collection_subtitles = [
        [basis.value, _timed_fingerprint(value)]
        for basis, value in sorted(
            evidence.collection_subtitles.items(), key=lambda item: item[0].value
        )
    ]
    return _sha256_json(
        {
            "part_subtitles": part_subtitles,
            "collection_subtitles": collection_subtitles,
            "collection_transcript": _timed_fingerprint(evidence.collection_transcript),
            "content_report": _plain_fingerprint(evidence.content_report),
            "segments": _plain_fingerprint(evidence.segments),
            "correction_log": _plain_fingerprint(evidence.correction_log),
        }
    )


def projection_invalidation_key(
    plan: RunPlan, evidence: ProjectionEvidence
) -> ProjectionInvalidationKey:
    """Compute the Publication projection's invalidation key for a plan.

    The configuration subset is the ASR mode *and the ordered Part ids* — the two
    inputs that together fix the expected artifact set, so two plans that differ
    only in their Part set never share a key even when all their evidence is
    absent. The single input hash is the upstream evidence fingerprint. A
    projection version bump, a Part-set change, or any evidence change re-keys the
    projection (ADR 0052).
    """

    mode = _require_mode(plan)
    parts = _part_ids(plan)
    return ProjectionInvalidationKey(
        stage_version=PUBLICATION_PROJECTION_STAGE_VERSION,
        input_hashes=(_evidence_fingerprint(evidence),),
        config_subset_hash=_sha256_json({"asr_mode": mode.value, "parts": list(parts)}),
    )
