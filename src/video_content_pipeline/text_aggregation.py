"""Phase 6's Part-local chapter and collection-summary aggregation (ticket 06).

Ticket 04 turned model-proposed cue boundaries into formal SemanticSegments and
ticket 05 validated each segment's evidence-bound content. This module lifts those
verified segments into the two higher-level structures the report exposes, keeping
the same discipline: a model may propose an organization, but deterministic rules
decide what becomes formal, and every discarded proposal is retained as a
diagnostic so the evidence for a decision is never lost.

* A **chapter** is an optional consecutive sequence of verified SemanticSegments
  from one Part. ``adjudicate_part_chapters`` accepts a proposal only when it names
  this Part, cites existing segments, encloses a non-empty consecutive run, and
  does not overlap an already-accepted chapter. Chapters never cross a Part
  boundary and need not tile the Part; a Part may have no chapters at all.
* A **collection summary** may cite verified segments from *multiple* Parts while
  retaining each segment's Part identity. ``aggregate_collection`` validates every
  cited segment reference, declares each subtitle-unavailable Part's retained
  CollectionVirtualTime range and reason as an omitted range rather than inventing
  content, and preserves every conservative-fallback or unavailable Part as a
  limitation so the collection is reported ``partial``.

Source-language boundaries are preserved, not normalized: a chapter retains the
union of its member segments' source languages (including mixed Chinese/English),
deduplicated and sorted for determinism but never reduced to a single language or
translated, and a collection entry keeps each cited segment's Part identity instead
of merging Parts. Translation and any language normalization remain out of scope.
See ``docs/PHASE_06_SPECIFICATION.md`` and the Text Analysis Context.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from video_content_pipeline.planning import PlanningDiagnostic
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval

# The chapter-rejection vocabulary, anchored here so the adjudicator and its tests
# share one source of truth (mirroring ``text_segmentation``'s boundary reasons).
CHAPTER_CROSSES_PART = "chapter_crosses_part"
CHAPTER_UNKNOWN_SEGMENT = "chapter_unknown_segment"
CHAPTER_EMPTY = "chapter_empty"
CHAPTER_OVERLAP = "chapter_overlap"

# The collection-entry rejection vocabulary.
COLLECTION_ENTRY_EMPTY = "collection_entry_empty"
COLLECTION_ENTRY_UNKNOWN_SEGMENT = "collection_entry_unknown_segment"
COLLECTION_ENTRY_CITES_UNAVAILABLE_PART = "collection_entry_cites_unavailable_part"

# The limitation vocabulary preserved on the collection summary.
TEXT_CONTENT_UNAVAILABLE = "text_content_unavailable"
CONSERVATIVE_FALLBACK_LIMITATION = "conservative_single_segment_fallback"


class TextAggregationError(ValueError):
    """A caller-contract violation in aggregation input.

    Like ``text_segmentation``, this never signals rejected *model* input — invalid
    chapter and collection proposals are retained as diagnostics — only a malformed
    authoritative Part inventory, which is our own revalidated ground truth.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class AggregatableSegment:
    """One verified SemanticSegment identity offered for Part-local aggregation.

    ``source_languages`` records the segment's retained source languages (for
    example ``("zh",)`` or ``("en", "zh")`` for mixed Chinese/English). A chapter
    retains the union of its members' languages, deduplicated and sorted for a
    deterministic result, never normalized to a single language or translated.
    """

    part_id: str
    ordinal: int
    source_languages: tuple[str, ...] = ()


@dataclass(frozen=True)
class AvailablePart:
    """A Part with a valid Primary subtitle track and its verified segments.

    ``segments`` are the Part's verified SemanticSegments in Part order, with
    strictly ascending ordinals starting at zero (the ordinals ``text_segmentation``
    assigns). ``used_fallback`` records the ticket 04 conservative single-segment
    fallback, which the collection preserves as a limitation.
    """

    part_id: str
    segments: tuple[AggregatableSegment, ...]
    used_fallback: bool = False

    def __post_init__(self) -> None:
        if not self.part_id:
            raise TextAggregationError("invalid_available_part", "A Part must be named.")
        if not self.segments:
            raise TextAggregationError(
                "invalid_available_part",
                f"Available Part {self.part_id} has no verified SemanticSegments.",
            )
        expected = list(range(len(self.segments)))
        if [segment.ordinal for segment in self.segments] != expected or any(
            segment.part_id != self.part_id for segment in self.segments
        ):
            raise TextAggregationError(
                "invalid_available_part",
                f"Part {self.part_id} segments must be Part-local and ordinal 0..n-1 in order.",
            )

    @property
    def ordinals(self) -> frozenset[int]:
        return frozenset(segment.ordinal for segment in self.segments)


@dataclass(frozen=True)
class UnavailablePart:
    """A Part without a valid Primary subtitle track: ``text_content=unavailable``.

    It invents no segment or fact. Its retained CollectionVirtualTime range and
    reason are declared as an omitted range during collection aggregation.
    """

    part_id: str
    reason: str
    virtual_time_range: HalfOpenInterval

    def __post_init__(self) -> None:
        if not self.part_id:
            raise TextAggregationError("invalid_unavailable_part", "A Part must be named.")
        if not self.reason:
            raise TextAggregationError(
                "invalid_unavailable_part",
                f"Unavailable Part {self.part_id} must record a reason.",
            )


@dataclass(frozen=True)
class ProposedChapter:
    """A model-proposed consecutive segment span for one Part, before adjudication."""

    part_id: str
    start_ordinal: int
    end_ordinal: int
    title: str | None = None


@dataclass(frozen=True)
class Chapter:
    """A formal Part-local chapter over a consecutive verified-segment run.

    A chapter cites its member segments through ``segment_ordinals``. Unlike a
    SemanticSegment, a chapter has only one provenance — it is always the
    adjudicated survivor of a proposal — so it carries no ``origin`` discriminator.
    """

    part_id: str
    ordinal: int
    title: str | None
    segment_ordinals: tuple[int, ...]
    source_languages: tuple[str, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "part_id": self.part_id,
            "ordinal": self.ordinal,
            "title": self.title,
            "segment_ordinals": list(self.segment_ordinals),
            "source_languages": list(self.source_languages),
        }


@dataclass(frozen=True)
class PartChapters:
    """The deterministic chapter outcome for one Part.

    ``chapters`` are Part-local, non-overlapping, and ordered by their first
    segment; ``rejected`` retains every discarded proposal so the evidence for a
    chapter decision is never lost.
    """

    part_id: str
    chapters: tuple[Chapter, ...]
    rejected: tuple[PlanningDiagnostic, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "part_id": self.part_id,
            "chapters": [chapter.as_json() for chapter in self.chapters],
            "rejected": [diagnostic.as_json() for diagnostic in self.rejected],
        }


@dataclass(frozen=True)
class SegmentRef:
    """A collection-summary citation to one verified segment, retaining its Part."""

    part_id: str
    ordinal: int

    def as_json(self) -> dict[str, object]:
        return {"part_id": self.part_id, "ordinal": self.ordinal}


@dataclass(frozen=True)
class ProposedCollectionEntry:
    """A model-proposed collection-summary entry citing verified segments."""

    segment_refs: tuple[SegmentRef, ...]
    text: str | None = None


@dataclass(frozen=True)
class CollectionEntry:
    """A verified collection-summary entry that retains each cited Part identity."""

    segment_refs: tuple[SegmentRef, ...]
    text: str | None

    @property
    def part_ids(self) -> tuple[str, ...]:
        """The distinct Parts this entry cites, in first-citation order."""

        ordered: list[str] = []
        for ref in self.segment_refs:
            if ref.part_id not in ordered:
                ordered.append(ref.part_id)
        return tuple(ordered)

    def as_json(self) -> dict[str, object]:
        return {
            "text": self.text,
            "segment_refs": [ref.as_json() for ref in self.segment_refs],
        }


@dataclass(frozen=True)
class OmittedPart:
    """A subtitle-unavailable Part declared with its retained range and reason."""

    part_id: str
    reason: str
    virtual_time_range: HalfOpenInterval

    @classmethod
    def from_unavailable(cls, part: UnavailablePart) -> OmittedPart:
        """Declare a subtitle-unavailable Part's retained range as an omission."""

        return cls(part.part_id, part.reason, part.virtual_time_range)

    def as_json(self) -> dict[str, object]:
        return {
            "part_id": self.part_id,
            "reason": self.reason,
            "virtual_time_range": {
                "start": _exact_time_as_json(self.virtual_time_range.start),
                "end": _exact_time_as_json(self.virtual_time_range.end),
            },
        }


@dataclass(frozen=True)
class CollectionSummary:
    """The deterministic cross-Part aggregation outcome.

    ``entries`` cite verified segments across Parts with Part identity retained;
    ``omitted_parts`` declares every subtitle-unavailable Part's range and reason;
    ``limitations`` preserves each unavailable or conservative-fallback Part; and
    ``rejected`` retains every discarded entry proposal. ``part_ids`` retains the
    ordered identity of every Part, available or not.
    """

    part_ids: tuple[str, ...]
    entries: tuple[CollectionEntry, ...]
    omitted_parts: tuple[OmittedPart, ...]
    limitations: tuple[PlanningDiagnostic, ...]
    rejected: tuple[PlanningDiagnostic, ...]

    @property
    def partial(self) -> bool:
        """Whether an omitted or conservative-fallback Part lowers the collection."""

        return bool(self.limitations)

    def as_json(self) -> dict[str, object]:
        return {
            "part_ids": list(self.part_ids),
            "partial": self.partial,
            "entries": [entry.as_json() for entry in self.entries],
            "omitted_parts": [omitted.as_json() for omitted in self.omitted_parts],
            "limitations": [diagnostic.as_json() for diagnostic in self.limitations],
            "rejected": [diagnostic.as_json() for diagnostic in self.rejected],
        }


def adjudicate_part_chapters(
    part: AvailablePart, proposed: Sequence[ProposedChapter]
) -> PartChapters:
    """Adjudicate one Part's model-proposed chapters into formal Part-local chapters.

    A proposal is accepted only when it names this Part, encloses a non-empty
    consecutive run of existing verified segments, and does not overlap an
    already-accepted chapter. Proposals are considered in input order (first-wins on
    overlap); the survivors are then ordered by their first segment and assigned
    chapter ordinals. Every rejection is retained as a diagnostic, and chapters need
    not cover every segment.
    """

    by_ordinal = {segment.ordinal: segment for segment in part.segments}
    rejected: list[PlanningDiagnostic] = []
    accepted: list[ProposedChapter] = []
    claimed: set[int] = set()

    for candidate in proposed:
        rejection = _chapter_rejection(candidate, part, by_ordinal, claimed)
        if rejection is not None:
            rejected.append(rejection)
            continue
        claimed.update(range(candidate.start_ordinal, candidate.end_ordinal + 1))
        accepted.append(candidate)

    ordered = sorted(accepted, key=lambda candidate: candidate.start_ordinal)
    chapters = tuple(
        _build_chapter(candidate, ordinal, by_ordinal)
        for ordinal, candidate in enumerate(ordered)
    )
    return PartChapters(part_id=part.part_id, chapters=chapters, rejected=tuple(rejected))


def _chapter_rejection(
    candidate: ProposedChapter,
    part: AvailablePart,
    by_ordinal: dict[int, AggregatableSegment],
    claimed: set[int],
) -> PlanningDiagnostic | None:
    """Return a rejection diagnostic for an invalid chapter proposal, else ``None``."""

    if candidate.part_id != part.part_id:
        return PlanningDiagnostic(
            CHAPTER_CROSSES_PART,
            f"Chapter names Part {candidate.part_id}, not {part.part_id}; "
            "a chapter never crosses a Part boundary.",
        )
    if candidate.start_ordinal > candidate.end_ordinal:
        return PlanningDiagnostic(
            CHAPTER_EMPTY,
            "Chapter span does not enclose a non-empty consecutive segment run.",
        )
    span = range(candidate.start_ordinal, candidate.end_ordinal + 1)
    if any(ordinal not in by_ordinal for ordinal in span):
        return PlanningDiagnostic(
            CHAPTER_UNKNOWN_SEGMENT,
            "Chapter cites a segment ordinal this Part does not own.",
        )
    if any(ordinal in claimed for ordinal in span):
        return PlanningDiagnostic(
            CHAPTER_OVERLAP,
            "Chapter overlaps an already-accepted chapter in this Part.",
        )
    return None


def _build_chapter(
    candidate: ProposedChapter, ordinal: int, by_ordinal: dict[int, AggregatableSegment]
) -> Chapter:
    span = tuple(range(candidate.start_ordinal, candidate.end_ordinal + 1))
    languages = sorted(
        {
            language
            for segment_ordinal in span
            for language in by_ordinal[segment_ordinal].source_languages
        }
    )
    return Chapter(
        part_id=candidate.part_id,
        ordinal=ordinal,
        title=candidate.title,
        segment_ordinals=span,
        source_languages=tuple(languages),
    )


def aggregate_collection(
    parts: Sequence[AvailablePart | UnavailablePart],
    proposed: Sequence[ProposedCollectionEntry],
) -> CollectionSummary:
    """Aggregate verified Parts into a cross-Part collection summary.

    Every cited segment reference is validated against the available Parts:
    a reference to an unknown segment, a subtitle-unavailable Part, or an empty
    entry is rejected and retained as a diagnostic. Each subtitle-unavailable Part
    is declared as an omitted range and preserved as a limitation, and every
    conservative-fallback Part is preserved as a limitation, so the collection is
    reported ``partial`` whenever content was withheld. Cited Part identity is never
    merged.
    """

    part_ids = tuple(part.part_id for part in parts)
    if len(set(part_ids)) != len(part_ids):
        raise TextAggregationError(
            "duplicate_part_id", "Collection Part identifiers must be unique."
        )

    available = {part.part_id: part for part in parts if isinstance(part, AvailablePart)}
    unavailable_ids = {part.part_id for part in parts if isinstance(part, UnavailablePart)}

    entries: list[CollectionEntry] = []
    rejected: list[PlanningDiagnostic] = []
    for candidate in proposed:
        entry, rejection = _validate_entry(candidate, available, unavailable_ids)
        if rejection is not None:
            rejected.append(rejection)
            continue
        assert entry is not None
        entries.append(entry)

    omitted_parts = tuple(
        OmittedPart.from_unavailable(part)
        for part in parts
        if isinstance(part, UnavailablePart)
    )
    limitations = _limitations(parts)
    return CollectionSummary(
        part_ids=part_ids,
        entries=tuple(entries),
        omitted_parts=omitted_parts,
        limitations=limitations,
        rejected=tuple(rejected),
    )


def _validate_entry(
    candidate: ProposedCollectionEntry,
    available: dict[str, AvailablePart],
    unavailable_ids: set[str],
) -> tuple[CollectionEntry | None, PlanningDiagnostic | None]:
    """Validate one collection entry's segment references against the Parts."""

    if not candidate.segment_refs:
        return None, PlanningDiagnostic(
            COLLECTION_ENTRY_EMPTY, "A collection entry must cite at least one verified segment."
        )
    for ref in candidate.segment_refs:
        if ref.part_id in unavailable_ids:
            return None, PlanningDiagnostic(
                COLLECTION_ENTRY_CITES_UNAVAILABLE_PART,
                f"Entry cites Part {ref.part_id}, which is text_content=unavailable.",
            )
        part = available.get(ref.part_id)
        if part is None or ref.ordinal not in part.ordinals:
            return None, PlanningDiagnostic(
                COLLECTION_ENTRY_UNKNOWN_SEGMENT,
                f"Entry cites segment {ref.part_id}:{ref.ordinal}, "
                "which is not a verified segment.",
            )
    return CollectionEntry(segment_refs=tuple(candidate.segment_refs), text=candidate.text), None


def _limitations(
    parts: Sequence[AvailablePart | UnavailablePart],
) -> tuple[PlanningDiagnostic, ...]:
    """Preserve every unavailable and conservative-fallback Part as a limitation."""

    limitations: list[PlanningDiagnostic] = []
    for part in parts:
        if isinstance(part, UnavailablePart):
            limitations.append(
                PlanningDiagnostic(
                    TEXT_CONTENT_UNAVAILABLE,
                    f"Part {part.part_id} has no valid Primary subtitle track ({part.reason}); "
                    "its collection range is declared omitted.",
                )
            )
        elif part.used_fallback:
            limitations.append(
                PlanningDiagnostic(
                    CONSERVATIVE_FALLBACK_LIMITATION,
                    f"Part {part.part_id} used the conservative single-segment fallback.",
                )
            )
    return tuple(limitations)


def _exact_time_as_json(value: ExactTime) -> dict[str, int]:
    """Serialize an exact CollectionVirtualTime endpoint in the codebase's form."""

    return {"numerator": value.numerator, "denominator": value.denominator}
