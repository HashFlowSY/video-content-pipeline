"""Phase 6's deterministic cue-bound semantic-boundary adjudication (ticket 04).

A text model proposes candidate cue-pair boundaries; it is never trusted to
define the final segmentation. This module adjudicates those candidates against
the revalidated PresentationCue inventory for one Part with stable, repeatable
rules:

* a final boundary occurs only between existing PresentationCues within one
  Part, so a candidate that leaves its Part, names an unknown cue, or inverts its
  cue pair is rejected rather than reinterpreted;
* overlapping technical-block context windows may surface the same cue span more
  than once, so cross-block candidates are deduplicated by complete cue identity
  before ownership is decided; and
* the surviving candidates become formal SemanticSegments only when they tile the
  Part exactly once. The adjudicator never invents a theme boundary: when no
  valid proposal tiles the Part it uses a single conservative SemanticSegment
  that owns every cue exactly once, records the reason, and marks the Part
  ``partial``.

Every discarded candidate — including one whose span was individually well-formed
but collectively broke coverage — is retained as a diagnostic so the evidence for
a boundary decision is never lost. The identities here are PresentationCue
identities: exactly-once ownership is a PresentationCue property, while the
NormalizedCue citations that support formal facts and titles are validated later
(ticket 05). See ``docs/PHASE_06_SPECIFICATION.md`` and the Text Analysis Context.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from video_content_pipeline.planning import PlanningDiagnostic

ADJUDICATED_ORIGIN = "adjudicated"
CONSERVATIVE_FALLBACK_ORIGIN = "conservative_fallback"
CONSERVATIVE_FALLBACK_REASON = "conservative_single_segment_fallback"

# The complete cue-boundary rejection vocabulary, anchored in one place so the
# adjudicator and its tests share a single source of truth (mirroring the
# diagnostic-reason collection in ``planning.py``).
BOUNDARY_CROSSES_PART = "boundary_crosses_part"
BOUNDARY_OUT_OF_RANGE = "boundary_out_of_range"
BOUNDARY_EMPTY = "boundary_empty"
BOUNDARY_DUPLICATE = "boundary_duplicate"
COVERAGE_BREAKING = "coverage_breaking"


class TextSegmentationError(ValueError):
    """A caller-contract violation in cue-bound adjudication input.

    This never signals rejected *model* input — invalid candidates are retained
    as diagnostics — only a malformed authoritative cue inventory, which is our
    own revalidated ground truth rather than untrusted generation.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class PartCueInventory:
    """The authoritative ordered PresentationCue identities for one Part.

    The identities are the revalidated ground truth against which model-proposed
    boundaries are adjudicated. For a Part with a valid Primary subtitle track
    they are ordered, unique, and non-empty; a subtitle-unavailable Part is never
    adjudicated (it is ``text_content=unavailable`` and handled in aggregation).
    """

    part_id: str
    cue_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.part_id:
            raise TextSegmentationError(
                "invalid_cue_inventory", "A cue inventory must name a Part."
            )
        if len(set(self.cue_ids)) != len(self.cue_ids):
            raise TextSegmentationError(
                "invalid_cue_inventory", "Cue inventory identities must be unique."
            )


@dataclass(frozen=True)
class ProposedSegment:
    """A model-proposed cue-pair boundary for one Part, before adjudication.

    ``start_cue_id`` and ``end_cue_id`` name the inclusive PresentationCue span a
    technical context block proposes as one segment. ``technical_block_id``
    records the overlapping context window that surfaced the candidate; two blocks
    proposing the same span are deduplicated by their complete cue identity.
    """

    part_id: str
    start_cue_id: str
    end_cue_id: str
    technical_block_id: str | None = None

    @property
    def cue_identity(self) -> tuple[str, str]:
        return (self.start_cue_id, self.end_cue_id)


@dataclass(frozen=True)
class AdjudicatedSegment:
    """A formal cue-bound SemanticSegment with exactly-once cue ownership."""

    part_id: str
    ordinal: int
    cue_ids: tuple[str, ...]
    origin: str

    def as_json(self) -> dict[str, object]:
        return {
            "part_id": self.part_id,
            "ordinal": self.ordinal,
            "origin": self.origin,
            "cue_ids": list(self.cue_ids),
        }


@dataclass(frozen=True)
class PartAdjudication:
    """The deterministic adjudication outcome for one Part.

    ``segments`` own every inventory cue exactly once in Part order. A
    ``used_fallback`` outcome carries a single conservative segment and a
    ``fallback`` reason; ``rejected`` retains every discarded candidate so the
    evidence for a boundary decision is never lost.
    """

    part_id: str
    segments: tuple[AdjudicatedSegment, ...]
    used_fallback: bool
    fallback: PlanningDiagnostic | None
    rejected: tuple[PlanningDiagnostic, ...]

    @property
    def partial(self) -> bool:
        """Whether the conservative fallback lowers this Part to ``partial``."""

        return self.used_fallback

    def as_json(self) -> dict[str, object]:
        return {
            "part_id": self.part_id,
            "used_fallback": self.used_fallback,
            "fallback": self.fallback.as_json() if self.fallback is not None else None,
            "segments": [segment.as_json() for segment in self.segments],
            "rejected": [diagnostic.as_json() for diagnostic in self.rejected],
        }


@dataclass(frozen=True)
class _Accepted:
    """A well-formed candidate placed by its resolved Part-order cue span."""

    start: int
    end: int
    candidate: ProposedSegment


def adjudicate_part_segments(
    inventory: PartCueInventory, proposed: Sequence[ProposedSegment]
) -> PartAdjudication:
    """Adjudicate one Part's model-proposed boundaries into formal segments.

    Candidates are filtered to this Part, deduplicated by complete cue identity,
    and validated for range and non-emptiness; every rejection is retained as a
    diagnostic. The survivors become formal segments only if they tile the Part
    exactly once. Otherwise every survivor is retained as a coverage-breaking
    diagnostic and the Part falls back to one conservative segment that owns every
    cue exactly once and records the reason.
    """

    if not inventory.cue_ids:
        raise TextSegmentationError(
            "empty_cue_inventory",
            f"Part {inventory.part_id} has no PresentationCues to adjudicate.",
        )

    index_of = {cue_id: position for position, cue_id in enumerate(inventory.cue_ids)}
    rejected: list[PlanningDiagnostic] = []
    accepted: list[_Accepted] = []
    seen: set[tuple[str, str]] = set()

    for candidate in proposed:
        rejection = _rejection_for(candidate, inventory, index_of, seen)
        if rejection is not None:
            rejected.append(rejection)
            continue
        seen.add(candidate.cue_identity)
        accepted.append(
            _Accepted(index_of[candidate.start_cue_id], index_of[candidate.end_cue_id], candidate)
        )

    tiling = _tiling(accepted, len(inventory.cue_ids))
    if tiling is None:
        rejected.extend(_coverage_breaking(accepted))
        return _fallback(inventory, tuple(rejected))
    segments = tuple(
        AdjudicatedSegment(
            part_id=inventory.part_id,
            ordinal=ordinal,
            cue_ids=inventory.cue_ids[placed.start : placed.end + 1],
            origin=ADJUDICATED_ORIGIN,
        )
        for ordinal, placed in enumerate(tiling)
    )
    return PartAdjudication(
        part_id=inventory.part_id,
        segments=segments,
        used_fallback=False,
        fallback=None,
        rejected=tuple(rejected),
    )


def _rejection_for(
    candidate: ProposedSegment,
    inventory: PartCueInventory,
    index_of: dict[str, int],
    seen: set[tuple[str, str]],
) -> PlanningDiagnostic | None:
    """Return a rejection diagnostic for an invalid candidate, else ``None``."""

    if candidate.part_id != inventory.part_id:
        return PlanningDiagnostic(
            BOUNDARY_CROSSES_PART,
            f"Candidate names Part {candidate.part_id}, not {inventory.part_id}.",
        )
    if candidate.start_cue_id not in index_of or candidate.end_cue_id not in index_of:
        return PlanningDiagnostic(
            BOUNDARY_OUT_OF_RANGE,
            "Candidate cue pair is not entirely within the Part's PresentationCues.",
        )
    if index_of[candidate.start_cue_id] > index_of[candidate.end_cue_id]:
        return PlanningDiagnostic(
            BOUNDARY_EMPTY,
            "Candidate cue pair does not enclose a non-empty PresentationCue span.",
        )
    if candidate.cue_identity in seen:
        return PlanningDiagnostic(
            BOUNDARY_DUPLICATE,
            "Overlapping technical blocks proposed an identical cue span.",
        )
    return None


def _tiling(accepted: list[_Accepted], cue_count: int) -> list[_Accepted] | None:
    """Return the ordered exact tiling of the Part, or ``None`` if impossible.

    A valid tiling starts at the first cue, ends at the last cue, and each
    consecutive span begins immediately after the previous one, so every cue is
    owned exactly once with no gap or overlap. Any gap, overlap, or empty set
    yields ``None`` so the caller falls back conservatively rather than inventing
    a boundary.
    """

    if not accepted:
        return None
    ordered = sorted(accepted, key=lambda placed: (placed.start, placed.end))
    expected_start = 0
    for placed in ordered:
        if placed.start != expected_start:
            return None
        expected_start = placed.end + 1
    if expected_start != cue_count:
        return None
    return ordered


def _coverage_breaking(accepted: list[_Accepted]) -> list[PlanningDiagnostic]:
    """Retain every well-formed-but-discarded candidate as coverage-breaking.

    When the survivors cannot tile the Part exactly, the whole proposal is
    discarded in favour of the conservative fallback. Each survivor is recorded in
    Part order so no boundary evidence is lost, mirroring the individual-rejection
    diagnostics.
    """

    return [
        PlanningDiagnostic(
            COVERAGE_BREAKING,
            "Candidate cue span "
            f"[{placed.candidate.start_cue_id}, {placed.candidate.end_cue_id}] "
            "did not combine into an exact single-ownership tiling of the Part.",
        )
        for placed in sorted(accepted, key=lambda placed: (placed.start, placed.end))
    ]


def _fallback(
    inventory: PartCueInventory, rejected: tuple[PlanningDiagnostic, ...]
) -> PartAdjudication:
    """Build the conservative single-segment outcome for one Part."""

    segment = AdjudicatedSegment(
        part_id=inventory.part_id,
        ordinal=0,
        cue_ids=inventory.cue_ids,
        origin=CONSERVATIVE_FALLBACK_ORIGIN,
    )
    fallback = PlanningDiagnostic(
        CONSERVATIVE_FALLBACK_REASON,
        f"No valid cue-pair boundary tiled Part {inventory.part_id}; "
        "retained one conservative segment owning every cue exactly once.",
    )
    return PartAdjudication(
        part_id=inventory.part_id,
        segments=(segment,),
        used_fallback=True,
        fallback=fallback,
        rejected=rejected,
    )
