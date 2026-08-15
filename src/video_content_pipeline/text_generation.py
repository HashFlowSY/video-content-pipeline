"""Phase 6's Controlled offline text adapter and generation orchestration (ticket 08).

Tickets 04-07 built the deterministic pieces of evidence-bound text analysis and
retained them behind independent contracts: the cue-bound boundary adjudicator
(``text_segmentation``), the evidence-bound content validator (``text_content``),
the Part-local aggregator (``text_aggregation``), and the versioned generation and
projection contracts (``text_contracts``). This module is the capstone that
composes them into one auditable ``GeneratedAnalysis`` and the Controlled offline
text adapter that drives it.

The composition keeps the same discipline throughout: authoritative cue
inventories come only from retained subtitle evidence (never from model output),
model-proposed boundaries and content are adjudicated and validated against that
evidence, and every rejected boundary, unsupported content item, and Part
limitation is retained as a diagnostic so the evidence for a decision is never
lost. The Controlled offline text adapter is not a model asset: it reads a
hash-pinned synthetic output fixture bound to a hash-pinned input-cue manifest, so
generation is fully deterministic and offline, and it can never earn a real-model
quality qualification.

Cue identity here is a synthesized, Part-local, stable string
``"<part_id>:<track_id>:<source_ordinal>"`` derived from the retained
``source-candidate.json`` (which stores only ``source_ordinal``, ``text``, and a
raw-PTS interval per cue). Segmentation owns PresentationCues by this identity and
content cites the same identities as its NormalizedCue basis, keeping every formal
fact evidence-bound to its own segment and Part. See
``docs/PHASE_06_SPECIFICATION.md`` and the Text Analysis Context.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TypeGuard

from video_content_pipeline.evidence import InputEvidence, input_evidence
from video_content_pipeline.planning import PlanningDiagnostic
from video_content_pipeline.text_aggregation import (
    AggregatableSegment,
    AvailablePart,
    Chapter,
    CollectionSummary,
    ProposedChapter,
    ProposedCollectionEntry,
    SegmentRef,
    UnavailablePart,
    adjudicate_part_chapters,
    aggregate_collection,
)
from video_content_pipeline.text_content import (
    SegmentCitationBasis,
    VerifiedSegmentContent,
    validate_segment_content,
)
from video_content_pipeline.text_segmentation import (
    PartCueInventory,
    ProposedSegment,
    adjudicate_part_segments,
)
from video_content_pipeline.timecode import HalfOpenInterval

# The report statuses this orchestration can conclude. ``complete``/``partial``
# match the spec's formal statuses; ``failed`` is concluded only when no verified
# SemanticSegment exists after generation (a whole-projection failure is decided
# earlier by the caller against ``text_contracts``).
STATUS_COMPLETE = "complete"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"

_NO_VERIFIED_SEGMENT = "no_verified_segment"


class TextGenerationError(ValueError):
    """A malformed authoritative input to controlled generation.

    Like ``text_segmentation`` and ``text_aggregation``, this never signals
    rejected *model* input — invalid model proposals are retained as diagnostics —
    only a malformed authoritative cue inventory, which is our own revalidated
    ground truth rather than untrusted generation.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def cue_id(part_id: str, track_id: str, source_ordinal: int) -> str:
    """Synthesize the stable Part-local cue identity used across text analysis."""

    return f"{part_id}:{track_id}:{source_ordinal}"


def input_cue_manifest_document(
    tracks: Sequence[tuple[str, int, str]],
) -> dict[str, object]:
    """Build the canonical input-cue manifest bound to one controlled generation.

    ``tracks`` are the revalidated selected Primary tracks as
    ``(source_id, stream_index, source_candidate_sha256)``. The manifest pins each
    Part's cue evidence by its content hash, so binding a controlled fixture to the
    manifest identity transitively binds it to the exact retained cues.
    """

    return {
        "schema_version": 1,
        "track_count": len(tracks),
        "tracks": [
            {"source_id": source_id, "stream_index": stream_index, "sha256": sha256_hex}
            for source_id, stream_index, sha256_hex in tracks
        ],
    }


def input_cue_manifest_sha256(document: Mapping[str, object]) -> str:
    """Return the canonical content identity of an input-cue manifest document."""

    return sha256(json.dumps(document, sort_keys=True).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LoadedPart:
    """One available Part's authoritative ordered cue inventory.

    ``cue_ids`` are the revalidated ground truth against which model-proposed
    boundaries and citations are adjudicated. They are Part-local and ordered by
    source ordinal.
    """

    part_id: str
    track_id: str
    cue_ids: tuple[str, ...]


@dataclass(frozen=True)
class UnavailablePartInfo:
    """A Part without a valid Primary subtitle track: ``text_content=unavailable``.

    Its retained CollectionVirtualTime range and reason are declared as an omitted
    range in the collection summary; no segment or fact is invented for it.
    """

    part_id: str
    reason: str
    virtual_time_range: HalfOpenInterval


@dataclass(frozen=True)
class ControlledGeneration:
    """A hash-pinned synthetic output fixture bound to an input-cue manifest.

    The Controlled offline text adapter is not a model asset. It returns exactly
    the retained ``raw_output`` when the actual input-cue manifest identity matches
    ``input_fixture_sha256``; both hashes are recorded so a future real-model
    boundary can prove precisely which fixed input produced which fixed output.
    """

    raw_output: bytes
    output_fixture: InputEvidence
    input_fixture_sha256: str


@dataclass(frozen=True)
class GeneratedSegment:
    """One verified SemanticSegment: cue ownership plus its validated content."""

    part_id: str
    ordinal: int
    origin: str
    cue_ids: tuple[str, ...]
    source_languages: tuple[str, ...]
    content: VerifiedSegmentContent

    def as_json(self) -> dict[str, object]:
        content = self.content.as_json()
        return {
            "part_id": self.part_id,
            "ordinal": self.ordinal,
            "origin": self.origin,
            "cue_ids": list(self.cue_ids),
            "source_languages": list(self.source_languages),
            "title": content["title"],
            "details": content["details"],
            "questions_and_answers": content["questions_and_answers"],
            "people": content["people"],
            "contradictions": content["contradictions"],
            "unresolved_questions": content["unresolved_questions"],
            "content_diagnostics": content["diagnostics"],
        }


@dataclass(frozen=True)
class GeneratedAnalysis:
    """The deterministic outcome of one controlled-generation attempt.

    ``segments`` and ``chapters`` are Part-ordered; ``collection_summary`` retains
    Part identity across the collection. ``diagnostics`` aggregates every retained
    boundary rejection, conservative fallback, chapter rejection, and collection
    limitation, and ``unsupported_item_count`` counts pruned content items so the
    readable report can summarize them without dumping generated text.
    """

    status: str
    segments: tuple[GeneratedSegment, ...]
    chapters: tuple[Chapter, ...]
    collection_summary: CollectionSummary | None
    diagnostics: tuple[PlanningDiagnostic, ...]
    unsupported_item_count: int


def load_cue_inventory(
    source_candidate_path: Path, *, part_id: str, stream_index: int
) -> LoadedPart:
    """Load one Part's authoritative ordered cue identities from retained evidence.

    The retained ``source-candidate.json`` stores each accepted cue's
    ``source_ordinal`` in Part order. This reads that ground truth and synthesizes
    the Part-local cue identities the text pipeline adjudicates against. A malformed
    or unexpected candidate raises ``TextGenerationError`` because the cue inventory
    is our own revalidated evidence, not untrusted model output.
    """

    track_id = f"stream-{stream_index}"
    try:
        decoded = json.loads(source_candidate_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TextGenerationError(
            "cue_inventory_invalid", "Subtitle source candidate cannot be read."
        ) from error
    if not isinstance(decoded, Mapping) or decoded.get("schema_version") != 1:
        raise TextGenerationError(
            "cue_inventory_invalid", "Subtitle source candidate has an invalid schema."
        )
    raw_cues = decoded.get("cues")
    if not isinstance(raw_cues, list):
        raise TextGenerationError(
            "cue_inventory_invalid", "Subtitle source candidate omits a cue list."
        )
    ordinals: list[int] = []
    for raw_cue in raw_cues:
        if not isinstance(raw_cue, Mapping):
            raise TextGenerationError("cue_inventory_invalid", "A subtitle cue is not an object.")
        ordinal = raw_cue.get("source_ordinal")
        if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 0:
            raise TextGenerationError(
                "cue_inventory_invalid", "A subtitle cue omits a valid source ordinal."
            )
        ordinals.append(ordinal)
    if len(set(ordinals)) != len(ordinals):
        raise TextGenerationError(
            "cue_inventory_invalid", "Subtitle cue source ordinals are not unique."
        )
    return LoadedPart(
        part_id=part_id,
        track_id=track_id,
        cue_ids=tuple(cue_id(part_id, track_id, ordinal) for ordinal in ordinals),
    )


def load_controlled_generation(
    adapter_document: Mapping[str, object], project_root: Path, input_fixture_sha256: str
) -> ControlledGeneration | None:
    """Load the Controlled offline text adapter's bound synthetic output fixture.

    A ``generation`` block is optional: without it, no controlled adapter generates
    and the caller retains ``controlled_adapter_unavailable`` unchanged. With it,
    the block must name the project-relative output fixture and its hash, and the
    input-fixture hash it was authored for. The fixture bytes are hash-verified and
    returned verbatim; the caller compares ``input_fixture_sha256`` to the actual
    input-cue manifest to prove the fixture matches these revalidated cues.
    """

    generation = adapter_document.get("generation")
    if generation is None:
        return None
    if not isinstance(generation, Mapping):
        raise TextGenerationError(
            "controlled_generation_invalid", "Controlled adapter generation block is malformed."
        )
    relative = generation.get("output_fixture_path")
    expected_output_sha = generation.get("output_fixture_sha256")
    bound_input_sha = generation.get("input_fixture_sha256")
    if (
        not isinstance(relative, str)
        or not relative
        or not isinstance(expected_output_sha, str)
        or not isinstance(bound_input_sha, str)
    ):
        raise TextGenerationError(
            "controlled_generation_invalid",
            "Controlled adapter generation block omits a fixture path or hash.",
        )
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise TextGenerationError(
            "controlled_generation_invalid",
            "Controlled adapter output fixture path must be project-relative.",
        )
    fixture_path = project_root / relative
    try:
        raw_output = fixture_path.read_bytes()
    except OSError as error:
        raise TextGenerationError(
            "controlled_generation_invalid", "Controlled adapter output fixture cannot be read."
        ) from error
    if sha256(raw_output).hexdigest() != expected_output_sha:
        raise TextGenerationError(
            "controlled_generation_invalid",
            "Controlled adapter output fixture hash no longer matches its identity.",
        )
    return ControlledGeneration(
        raw_output=raw_output,
        output_fixture=input_evidence(fixture_path),
        input_fixture_sha256=bound_input_sha,
    )


def generate_analysis(
    available: Sequence[LoadedPart],
    unavailable: Sequence[UnavailablePartInfo],
    result: Mapping[str, object],
) -> GeneratedAnalysis:
    """Compose adjudication, content validation, and aggregation for one attempt.

    Each available Part's model-proposed boundaries are adjudicated into formal
    SemanticSegments (ticket 04); each segment's proposed content is validated
    against its own cue basis (ticket 05); the Part's chapters and the collection
    summary are aggregated (ticket 06). Every rejection and limitation is retained.
    The status is ``complete`` only when every available Part tiled without the
    conservative fallback and no Part is unavailable; a fallback or unavailable Part
    lowers it to ``partial``; the absence of any verified segment is ``failed``.
    """

    result_parts = _index_result_parts(result)
    segments: list[GeneratedSegment] = []
    chapters: list[Chapter] = []
    diagnostics: list[PlanningDiagnostic] = []
    unsupported = 0
    used_fallback_anywhere = False
    aggregatable_parts: list[AvailablePart | UnavailablePart] = []

    for part in available:
        part_result = result_parts.get(part.part_id, {})
        part_segments, part_diags, part_unsupported, used_fallback = _generate_part_segments(
            part, part_result
        )
        segments.extend(part_segments)
        diagnostics.extend(part_diags)
        unsupported += part_unsupported
        used_fallback_anywhere = used_fallback_anywhere or used_fallback

        available_part = AvailablePart(
            part_id=part.part_id,
            segments=tuple(
                AggregatableSegment(
                    part_id=segment.part_id,
                    ordinal=segment.ordinal,
                    source_languages=segment.source_languages,
                )
                for segment in part_segments
            ),
            used_fallback=used_fallback,
        )
        aggregatable_parts.append(available_part)
        part_chapters = adjudicate_part_chapters(
            available_part, _proposed_chapters(part.part_id, part_result)
        )
        chapters.extend(part_chapters.chapters)
        diagnostics.extend(part_chapters.rejected)

    for missing in unavailable:
        aggregatable_parts.append(
            UnavailablePart(
                part_id=missing.part_id,
                reason=missing.reason,
                virtual_time_range=missing.virtual_time_range,
            )
        )

    collection_summary = aggregate_collection(
        aggregatable_parts, _proposed_collection_entries(result)
    )
    diagnostics.extend(collection_summary.rejected)
    diagnostics.extend(collection_summary.limitations)

    status = _conclude_status(
        has_segments=bool(segments),
        used_fallback=used_fallback_anywhere,
        has_unavailable=bool(unavailable),
    )
    if status == STATUS_FAILED:
        diagnostics.append(
            PlanningDiagnostic(
                _NO_VERIFIED_SEGMENT,
                "No available Part produced a verified SemanticSegment.",
            )
        )
    return GeneratedAnalysis(
        status=status,
        segments=tuple(segments),
        chapters=tuple(chapters),
        collection_summary=collection_summary if aggregatable_parts else None,
        diagnostics=tuple(diagnostics),
        unsupported_item_count=unsupported,
    )


def _generate_part_segments(
    part: LoadedPart, part_result: Mapping[str, object]
) -> tuple[list[GeneratedSegment], list[PlanningDiagnostic], int, bool]:
    """Adjudicate one Part's boundaries and validate each segment's content."""

    raw_segments = _as_list(part_result.get("segments"))
    adjudication = adjudicate_part_segments(
        PartCueInventory(part_id=part.part_id, cue_ids=part.cue_ids),
        _proposed_segments(part.part_id, raw_segments),
    )
    diagnostics: list[PlanningDiagnostic] = list(adjudication.rejected)
    if adjudication.fallback is not None:
        diagnostics.append(adjudication.fallback)

    generated: list[GeneratedSegment] = []
    unsupported = 0
    for segment in adjudication.segments:
        raw_content = _segment_content(raw_segments, segment.ordinal)
        basis = SegmentCitationBasis(
            part_id=part.part_id,
            segment_ordinal=segment.ordinal,
            normalized_cue_ids=frozenset(segment.cue_ids),
        )
        content = validate_segment_content(raw_content, basis)
        unsupported += len(content.diagnostics)
        generated.append(
            GeneratedSegment(
                part_id=segment.part_id,
                ordinal=segment.ordinal,
                origin=segment.origin,
                cue_ids=segment.cue_ids,
                source_languages=_source_languages(raw_segments, segment.ordinal),
                content=content,
            )
        )
    return generated, diagnostics, unsupported, adjudication.used_fallback


def _index_result_parts(result: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    indexed: dict[str, Mapping[str, object]] = {}
    for raw_part in _as_list(result.get("parts")):
        if not isinstance(raw_part, Mapping):
            continue
        part_id = raw_part.get("part_id")
        if isinstance(part_id, str) and part_id:
            indexed[part_id] = raw_part
    return indexed


def _proposed_segments(part_id: str, raw_segments: list[object]) -> tuple[ProposedSegment, ...]:
    proposed: list[ProposedSegment] = []
    for raw_segment in raw_segments:
        if not isinstance(raw_segment, Mapping):
            continue
        boundary = raw_segment.get("boundary")
        if not isinstance(boundary, Mapping):
            continue
        start = boundary.get("start_cue_id")
        end = boundary.get("end_cue_id")
        if not isinstance(start, str) or not isinstance(end, str):
            continue
        block = boundary.get("technical_block_id")
        proposed.append(
            ProposedSegment(
                part_id=part_id,
                start_cue_id=start,
                end_cue_id=end,
                technical_block_id=block if isinstance(block, str) else None,
            )
        )
    return tuple(proposed)


def _segment_content(raw_segments: list[object], ordinal: int) -> object:
    if ordinal < len(raw_segments):
        raw_segment = raw_segments[ordinal]
        if isinstance(raw_segment, Mapping):
            return raw_segment.get("content")
    return {}


def _source_languages(raw_segments: list[object], ordinal: int) -> tuple[str, ...]:
    if ordinal < len(raw_segments):
        raw_segment = raw_segments[ordinal]
        if isinstance(raw_segment, Mapping):
            languages = raw_segment.get("source_languages")
            if isinstance(languages, list):
                return tuple(
                    sorted({language for language in languages if isinstance(language, str)})
                )
    return ()


def _proposed_chapters(
    part_id: str, part_result: Mapping[str, object]
) -> tuple[ProposedChapter, ...]:
    proposed: list[ProposedChapter] = []
    for raw_chapter in _as_list(part_result.get("chapters")):
        if not isinstance(raw_chapter, Mapping):
            continue
        start = raw_chapter.get("start_ordinal")
        end = raw_chapter.get("end_ordinal")
        if not _is_ordinal(start) or not _is_ordinal(end):
            continue
        title = raw_chapter.get("title")
        proposed.append(
            ProposedChapter(
                part_id=part_id,
                start_ordinal=start,
                end_ordinal=end,
                title=title if isinstance(title, str) else None,
            )
        )
    return tuple(proposed)


def _proposed_collection_entries(
    result: Mapping[str, object],
) -> tuple[ProposedCollectionEntry, ...]:
    summary = result.get("collection_summary")
    if not isinstance(summary, Mapping):
        return ()
    entries: list[ProposedCollectionEntry] = []
    for raw_entry in _as_list(summary.get("entries")):
        if not isinstance(raw_entry, Mapping):
            continue
        refs: list[SegmentRef] = []
        for raw_ref in _as_list(raw_entry.get("segment_refs")):
            if not isinstance(raw_ref, Mapping):
                continue
            part_id = raw_ref.get("part_id")
            ordinal = raw_ref.get("ordinal")
            if isinstance(part_id, str) and part_id and _is_ordinal(ordinal):
                refs.append(SegmentRef(part_id=part_id, ordinal=ordinal))
        text = raw_entry.get("text")
        entries.append(
            ProposedCollectionEntry(
                segment_refs=tuple(refs),
                text=text if isinstance(text, str) else None,
            )
        )
    return tuple(entries)


def _conclude_status(*, has_segments: bool, used_fallback: bool, has_unavailable: bool) -> str:
    if not has_segments:
        return STATUS_FAILED
    if used_fallback or has_unavailable:
        return STATUS_PARTIAL
    return STATUS_COMPLETE


def _is_ordinal(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _as_list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


__all__ = [
    "ControlledGeneration",
    "GeneratedAnalysis",
    "GeneratedSegment",
    "LoadedPart",
    "STATUS_COMPLETE",
    "STATUS_FAILED",
    "STATUS_PARTIAL",
    "TextGenerationError",
    "UnavailablePartInfo",
    "cue_id",
    "generate_analysis",
    "input_cue_manifest_document",
    "input_cue_manifest_sha256",
    "load_controlled_generation",
    "load_cue_inventory",
]
