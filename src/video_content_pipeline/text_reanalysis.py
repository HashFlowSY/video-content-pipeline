"""Phase 7 Affected-Part re-analysis (ADR 0046): tickets 08 and 09.

ADR 0046 recomputes semantic analysis at Part granularity after transcription or
enhancement changes the cue evidence basis: affected Parts are regenerated
against the new basis and unaffected Parts are carried forward from a retained
prior report. This module owns the whole recomputation.

Ticket 08 supplies its two deterministic building blocks:

* ``load_text_analysis_report`` deserializes a retained
  ``text-analysis-report.json`` back into the aggregation domain objects --
  ``AvailablePart`` with its verified segments, ``Chapter``, and the
  ``CollectionSummary`` -- and records the report's own content hash
  (``source_evidence``) so a later carry-forward can pin exactly which prior
  report it reused. Loading is read-only and never mutates the retained report.

  Hash verification has two independent layers, and the docstrings name each for
  what it actually guarantees: (1) ``source_evidence`` is the sha256 of the whole
  report file, so it pins every deserialized field -- cue identities included --
  to exact bytes; and (2) the retained ``rendered_report`` hash is re-verified
  against a fresh rendering, a summary-level integrity check over the fields the
  renderer surfaces (statuses, counts, reasons). Content that the renderer does
  not surface is guarded instead by reconstruction invariants, not by (2).

* ``select_affected_parts`` compares a prior report's per-Part cue identities to a
  new cue basis and deterministically classifies each Part as *affected* (its cue
  identities changed, or the Part was added or removed) or *unaffected* (its cue
  identities are byte-for-byte the same, so its prior analysis may be reused).

Reconstruction is verified against the same invariants the generator enforced:
segment ownership is exactly-once and Part-local with contiguous ordinals, and
every chapter and collection citation names an existing segment. A malformed
field or a broken invariant is treated as a drifted report and rejected -- never
coerced, repaired, or silently dropped.

Ticket 09 (``reanalyze_text``) composes those blocks into one new immutable
text-analysis attempt: it derives the changed cue basis from a retained
enhancement report, classifies each Part, regenerates only the affected Parts
through the shared Controlled offline text adapter (``text_generation``) so they
obey every Phase 6 contract unchanged, carries the unaffected Parts forward with
an explicit provenance link to the prior report -- reusing their verified
identities, never copying their prose -- and recomputes chapters and the
collection summary over the combined set. The attempt owns a fresh workspace and
never overwrites the prior report. Only the ``enhanced`` cue basis is exercised in
this phase; the ``verbatim`` basis awaits a complete full-ASR run. See ADR 0046
and the Text Analysis Context (Affected-Part re-analysis, Carried-forward
analysis Part).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import TypeGuard

from video_content_pipeline.evidence import (
    InputEvidence,
    input_evidence,
    validated_report_id,
    write_json_once,
)
from video_content_pipeline.planning import (
    PlanningDiagnostic,
    PlanningError,
    RunPlan,
    confirmed_plan_matches,
    load_plan_report,
    load_run_plan,
    revalidate_confirmed_inspection_evidence,
)
from video_content_pipeline.source import SourceArtifact
from video_content_pipeline.text_aggregation import (
    AggregatableSegment,
    AvailablePart,
    Chapter,
    CollectionEntry,
    CollectionSummary,
    OmittedPart,
    ProposedCollectionEntry,
    SegmentRef,
    TextAggregationError,
    UnavailablePart,
    aggregate_collection,
)
from video_content_pipeline.text_analysis import record_restricted_raw_output
from video_content_pipeline.text_contracts import (
    TextContractError,
    project_text_model_output,
    render_text_analysis_markdown,
    revalidate_text_generation_contracts,
)
from video_content_pipeline.text_generation import (
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_PARTIAL,
    LoadedPart,
    PartGeneration,
    TextGenerationError,
    generate_part,
    index_result_parts,
    load_controlled_generation,
    proposed_collection_entries,
)
from video_content_pipeline.text_segmentation import CONSERVATIVE_FALLBACK_ORIGIN
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval, TimeValidationError

# A retained report can be carried forward only when it holds verified analysis.
# A ``failed``, paused, or adapter-unavailable report generated no segments, so
# there is nothing to reuse and the loader refuses it rather than returning empty
# domain objects that would read as a successful (but content-free) reuse.
LOADABLE_STATUSES = frozenset({"complete", "partial"})


class Disposition(StrEnum):
    """Whether a Part's prior analysis may be reused or must be regenerated."""

    AFFECTED = "affected"
    UNAFFECTED = "unaffected"


class ClassificationReason(StrEnum):
    """Why a Part received its affected/unaffected disposition."""

    CHANGED = "cue_identities_changed"
    UNCHANGED = "cue_identities_unchanged"
    ADDED = "part_added"
    REMOVED = "part_removed"


class TextReanalysisError(ValueError):
    """A rejected retained report or selection input with a machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class LoadedPartAnalysis:
    """One reconstructed available Part with its retained verified analysis.

    ``part`` is the aggregation-level ``AvailablePart`` (its verified segments as
    ``AggregatableSegment`` identities) and ``cue_ids`` are the Part's owned cue
    identities in Part order -- the basis the affected-Part selector keys on.
    """

    part: AvailablePart
    cue_ids: tuple[str, ...]


@dataclass(frozen=True)
class LoadedTextAnalysisReport:
    """A retained text-analysis report deserialized back into domain objects.

    ``source_evidence`` records the retained report's whole-file content hash: it
    pins every deserialized field to exact bytes, so a later carry-forward can
    cite precisely which prior report -- and which content -- it reused.
    """

    report_id: str
    plan_id: str
    subtitle_report_id: str
    status: str
    source_evidence: InputEvidence
    parts: tuple[LoadedPartAnalysis, ...]
    chapters: tuple[Chapter, ...]
    collection_summary: CollectionSummary | None

    @property
    def part_cue_bases(self) -> dict[str, tuple[str, ...]]:
        """The per-available-Part ordered cue identities the selector keys on."""

        return {loaded.part.part_id: loaded.cue_ids for loaded in self.parts}

    @property
    def omitted_parts(self) -> tuple[OmittedPart, ...]:
        """The subtitle-unavailable Parts declared by the collection summary."""

        if self.collection_summary is None:
            return ()
        return self.collection_summary.omitted_parts


def load_text_analysis_report(report_path: Path) -> LoadedTextAnalysisReport:
    """Deserialize a retained ``text-analysis-report.json`` into domain objects.

    The report is read once (read-only) and its whole-file content hash recorded
    as ``source_evidence``. Its retained rendition hash is re-verified against a
    fresh rendering (a summary-level integrity check), segments are grouped into
    ``AvailablePart`` objects with their reconstructed cue ownership, and chapters
    and the collection summary are reconstructed and checked against those
    segments. Any unreadable or wrong-status report, and any malformed field or
    broken reconstruction invariant, raises ``TextReanalysisError``.
    """

    evidence = _read_report_evidence(report_path)
    document = _read_report_document(report_path)
    report_id = document.get("report_id")
    if not isinstance(report_id, str) or not report_id:
        raise TextReanalysisError(
            "text_analysis_report_unloadable", "Retained report omits its report identity."
        )
    status = document.get("status")
    if not isinstance(status, str):
        raise TextReanalysisError(
            "text_analysis_report_unloadable", "Retained report omits its status."
        )
    if status not in LOADABLE_STATUSES:
        raise TextReanalysisError(
            "text_analysis_report_not_loadable",
            f"A {status!r} report holds no verified analysis to carry forward.",
        )
    _verify_rendition_hash(document)

    parts = _reconstruct_parts(document)
    known_ordinals = {loaded.part.part_id: loaded.part.ordinals for loaded in parts}
    chapters = _reconstruct_chapters(document, known_ordinals)
    collection_summary = _reconstruct_collection(document, known_ordinals)

    return LoadedTextAnalysisReport(
        report_id=report_id,
        plan_id=_required_str(document, "plan_id"),
        subtitle_report_id=_required_str(document, "subtitle_report_id"),
        status=status,
        source_evidence=evidence,
        parts=parts,
        chapters=chapters,
        collection_summary=collection_summary,
    )


def _read_report_evidence(report_path: Path) -> InputEvidence:
    try:
        return input_evidence(report_path)
    except OSError as error:
        raise TextReanalysisError(
            "text_analysis_report_unloadable", "Retained report cannot be read."
        ) from error


def _read_report_document(report_path: Path) -> Mapping[str, object]:
    try:
        decoded = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TextReanalysisError(
            "text_analysis_report_unloadable", "Retained report cannot be read."
        ) from error
    if not isinstance(decoded, Mapping):
        raise TextReanalysisError(
            "text_analysis_report_unloadable", "Retained report is not a JSON object."
        )
    return decoded


def _verify_rendition_hash(document: Mapping[str, object]) -> None:
    """Re-verify the retained rendition hash against a fresh rendering.

    Production binds ``rendered_report.sha256`` from a deterministic rendering of
    the report content, and the renderer ignores the ``rendered_report`` field
    itself, so re-rendering the loaded document reproduces the same hash for an
    untouched report. This is a summary-level integrity check: it catches drift in
    the fields the renderer surfaces (statuses, counts, reasons). Content the
    renderer does not surface is guarded instead by the reconstruction invariants
    and pinned as bytes by ``source_evidence``.
    """

    rendered = document.get("rendered_report")
    if not isinstance(rendered, Mapping):
        raise TextReanalysisError(
            "text_analysis_report_unloadable", "Retained report omits its rendition hash."
        )
    recorded = rendered.get("sha256")
    if not isinstance(recorded, str) or not recorded:
        raise TextReanalysisError(
            "text_analysis_report_unloadable", "Retained report omits its rendition hash."
        )
    if render_text_analysis_markdown(document).sha256 != recorded:
        raise TextReanalysisError(
            "text_analysis_report_drifted",
            "Retained report content no longer matches its recorded rendition hash.",
        )


def _reconstruct_parts(document: Mapping[str, object]) -> tuple[LoadedPartAnalysis, ...]:
    """Group the report's verified segments back into available Parts, in order.

    Parts appear in first-segment order; within a Part, segments are grouped by
    their retained ordinal. ``AvailablePart`` enforces contiguous Part-local
    ordinals, and cue ownership is checked to be exactly-once across the Part; a
    violation is a drifted report, not a repairable condition.
    """

    order: list[str] = []
    grouped: dict[str, list[Mapping[str, object]]] = {}
    for raw_segment in _object_sequence(document.get("segments"), "segment"):
        part_id = raw_segment.get("part_id")
        if not isinstance(part_id, str) or not part_id:
            raise TextReanalysisError(
                "text_analysis_report_drifted", "A retained segment omits its Part identity."
            )
        if part_id not in grouped:
            grouped[part_id] = []
            order.append(part_id)
        grouped[part_id].append(raw_segment)

    return tuple(_reconstruct_part(part_id, grouped[part_id]) for part_id in order)


def _reconstruct_part(
    part_id: str, raw_segments: Sequence[Mapping[str, object]]
) -> LoadedPartAnalysis:
    ordered = sorted(raw_segments, key=_segment_ordinal)
    aggregatable: list[AggregatableSegment] = []
    cue_ids: list[str] = []
    seen_cues: set[str] = set()
    used_fallback = False
    for raw_segment in ordered:
        aggregatable.append(
            AggregatableSegment(
                part_id=part_id,
                ordinal=_segment_ordinal(raw_segment),
                source_languages=_string_list(
                    raw_segment.get("source_languages"), "source language"
                ),
            )
        )
        for cue in _string_list(raw_segment.get("cue_ids"), "cue identity"):
            if cue in seen_cues:
                raise TextReanalysisError(
                    "text_analysis_report_drifted",
                    f"Part {part_id} cue {cue!r} is owned by more than one segment.",
                )
            seen_cues.add(cue)
            cue_ids.append(cue)
        if raw_segment.get("origin") == CONSERVATIVE_FALLBACK_ORIGIN:
            used_fallback = True
    try:
        part = AvailablePart(
            part_id=part_id, segments=tuple(aggregatable), used_fallback=used_fallback
        )
    except TextAggregationError as error:
        raise TextReanalysisError(
            "text_analysis_report_drifted",
            f"Part {part_id} segments are not contiguous Part-local ordinals.",
        ) from error
    return LoadedPartAnalysis(part=part, cue_ids=tuple(cue_ids))


def _reconstruct_chapters(
    document: Mapping[str, object], known_ordinals: Mapping[str, frozenset[int]]
) -> tuple[Chapter, ...]:
    chapters: list[Chapter] = []
    for raw_chapter in _object_sequence(document.get("chapters"), "chapter"):
        part_id = raw_chapter.get("part_id")
        ordinal = raw_chapter.get("ordinal")
        title = raw_chapter.get("title")
        segment_ordinals = _ordinal_list(raw_chapter.get("segment_ordinals"), "chapter citation")
        if (
            not isinstance(part_id, str)
            or not _is_ordinal(ordinal)
            or (title is not None and not isinstance(title, str))
        ):
            raise TextReanalysisError(
                "text_analysis_report_drifted", "A retained chapter has an invalid identity."
            )
        available = known_ordinals.get(part_id)
        if available is None or not set(segment_ordinals) <= available:
            raise TextReanalysisError(
                "text_analysis_report_drifted",
                f"A retained chapter for Part {part_id} cites an unknown segment.",
            )
        chapters.append(
            Chapter(
                part_id=part_id,
                ordinal=ordinal,
                title=title,
                segment_ordinals=segment_ordinals,
                source_languages=_string_list(
                    raw_chapter.get("source_languages"), "source language"
                ),
            )
        )
    return tuple(chapters)


def _reconstruct_collection(
    document: Mapping[str, object], known_ordinals: Mapping[str, frozenset[int]]
) -> CollectionSummary | None:
    raw_summary = document.get("collection_summary")
    if raw_summary is None:
        return None
    if not isinstance(raw_summary, Mapping):
        raise TextReanalysisError(
            "text_analysis_report_drifted", "Retained collection summary is not an object."
        )
    return CollectionSummary(
        part_ids=_string_list(raw_summary.get("part_ids"), "collection Part identity"),
        entries=tuple(
            _reconstruct_entry(entry, known_ordinals)
            for entry in _object_sequence(raw_summary.get("entries"), "collection entry")
        ),
        omitted_parts=tuple(
            _reconstruct_omitted(item)
            for item in _object_sequence(raw_summary.get("omitted_parts"), "omitted Part")
        ),
        limitations=_reconstruct_diagnostics(raw_summary.get("limitations"), "limitation"),
        rejected=_reconstruct_diagnostics(raw_summary.get("rejected"), "rejected entry"),
    )


def _reconstruct_entry(
    raw_entry: Mapping[str, object], known_ordinals: Mapping[str, frozenset[int]]
) -> CollectionEntry:
    text = raw_entry.get("text")
    if text is not None and not isinstance(text, str):
        raise TextReanalysisError(
            "text_analysis_report_drifted", "A retained collection entry has invalid text."
        )
    refs: list[SegmentRef] = []
    for raw_ref in _object_sequence(raw_entry.get("segment_refs"), "collection citation"):
        part_id = raw_ref.get("part_id")
        ordinal = raw_ref.get("ordinal")
        if not isinstance(part_id, str) or not _is_ordinal(ordinal):
            raise TextReanalysisError(
                "text_analysis_report_drifted", "A retained collection citation is malformed."
            )
        available = known_ordinals.get(part_id)
        if available is None or ordinal not in available:
            raise TextReanalysisError(
                "text_analysis_report_drifted",
                f"A retained collection entry cites unknown segment {part_id}:{ordinal}.",
            )
        refs.append(SegmentRef(part_id=part_id, ordinal=ordinal))
    return CollectionEntry(segment_refs=tuple(refs), text=text)


def _reconstruct_omitted(raw_omitted: Mapping[str, object]) -> OmittedPart:
    part_id = raw_omitted.get("part_id")
    reason = raw_omitted.get("reason")
    if not isinstance(part_id, str) or not isinstance(reason, str) or not part_id or not reason:
        raise TextReanalysisError(
            "text_analysis_report_drifted", "A retained omitted Part is malformed."
        )
    return OmittedPart(
        part_id=part_id,
        reason=reason,
        virtual_time_range=_reconstruct_interval(raw_omitted.get("virtual_time_range")),
    )


def _reconstruct_diagnostics(value: object, label: str) -> tuple[PlanningDiagnostic, ...]:
    diagnostics: list[PlanningDiagnostic] = []
    for raw in _object_sequence(value, label):
        reason = raw.get("reason")
        message = raw.get("message")
        if not isinstance(reason, str) or not isinstance(message, str):
            raise TextReanalysisError(
                "text_analysis_report_drifted", f"A retained {label} is malformed."
            )
        diagnostics.append(PlanningDiagnostic(reason, message))
    return tuple(diagnostics)


def _reconstruct_interval(value: object) -> HalfOpenInterval:
    if not isinstance(value, Mapping):
        raise TextReanalysisError(
            "text_analysis_report_drifted", "A retained omitted Part omits its time range."
        )
    try:
        return HalfOpenInterval(
            start=_reconstruct_time(value.get("start")),
            end=_reconstruct_time(value.get("end")),
        )
    except TimeValidationError as error:
        raise TextReanalysisError(
            "text_analysis_report_drifted", "A retained omitted Part has an invalid time range."
        ) from error


def _reconstruct_time(value: object) -> ExactTime:
    numerator = value.get("numerator") if isinstance(value, Mapping) else None
    denominator = value.get("denominator") if isinstance(value, Mapping) else None
    if not _is_int(numerator) or not _is_int(denominator) or denominator == 0:
        raise TextReanalysisError(
            "text_analysis_report_drifted", "A retained time endpoint is malformed."
        )
    return ExactTime(numerator, denominator)


# --- Affected-Part selection -------------------------------------------------


@dataclass(frozen=True)
class PartClassification:
    """One Part's deterministic affected/unaffected disposition and its reason."""

    part_id: str
    disposition: Disposition
    reason: ClassificationReason

    def as_json(self) -> dict[str, object]:
        return {
            "part_id": self.part_id,
            "disposition": self.disposition.value,
            "reason": self.reason.value,
        }


@dataclass(frozen=True)
class AffectedPartSelection:
    """The deterministic classification of every Part across two cue bases."""

    classifications: tuple[PartClassification, ...]

    @property
    def affected(self) -> tuple[str, ...]:
        return tuple(
            item.part_id
            for item in self.classifications
            if item.disposition is Disposition.AFFECTED
        )

    @property
    def unaffected(self) -> tuple[str, ...]:
        return tuple(
            item.part_id
            for item in self.classifications
            if item.disposition is Disposition.UNAFFECTED
        )

    def classification(self, part_id: str) -> PartClassification:
        for item in self.classifications:
            if item.part_id == part_id:
                return item
        raise KeyError(part_id)

    def as_json(self) -> dict[str, object]:
        return {
            "classifications": [item.as_json() for item in self.classifications],
            "affected": list(self.affected),
            "unaffected": list(self.unaffected),
        }


def select_affected_parts(
    prior_cue_bases: Mapping[str, Sequence[str]],
    new_cue_bases: Mapping[str, Sequence[str]],
) -> AffectedPartSelection:
    """Classify each Part as affected or unaffected across two cue bases.

    A Part whose ordered cue identities are byte-for-byte identical in both bases is
    *unaffected* -- its prior analysis may be carried forward. Any other Part is
    *affected*: its cue identities changed (including a reordering), or it was added
    or removed between the two bases, so its prior analysis is never reused as-is.
    Classifications are ordered by Part identity for a stable, auditable record.
    """

    classifications: list[PartClassification] = []
    for part_id in sorted(set(prior_cue_bases) | set(new_cue_bases)):
        in_prior = part_id in prior_cue_bases
        in_new = part_id in new_cue_bases
        if in_prior and in_new:
            if tuple(prior_cue_bases[part_id]) == tuple(new_cue_bases[part_id]):
                classifications.append(
                    PartClassification(
                        part_id, Disposition.UNAFFECTED, ClassificationReason.UNCHANGED
                    )
                )
            else:
                classifications.append(
                    PartClassification(
                        part_id, Disposition.AFFECTED, ClassificationReason.CHANGED
                    )
                )
        elif in_new:
            classifications.append(
                PartClassification(part_id, Disposition.AFFECTED, ClassificationReason.ADDED)
            )
        else:
            classifications.append(
                PartClassification(part_id, Disposition.AFFECTED, ClassificationReason.REMOVED)
            )
    return AffectedPartSelection(classifications=tuple(classifications))


# --- Carried-forward analysis Parts (ADR 0046) -------------------------------

# The two provenance markers every re-analysis segment and chapter carries, so an
# auditor can tell a freshly regenerated Part apart from a reused prior one.
PROVENANCE_REGENERATED = "regenerated"
PROVENANCE_CARRIED_FORWARD = "carried_forward"

# The cue-basis a re-analysis regenerated against. Only ``enhanced`` is exercised in
# this phase; ``verbatim`` is reserved for a complete full-ASR run's cue basis.
CUE_BASIS_ENHANCED = "enhanced"


@dataclass(frozen=True)
class CarriedForwardPart:
    """A Part whose verified prior analysis is reused, linked to its source report.

    The reused analysis is referenced through ``source_report_id`` and its content
    hash, never copied prose: only the Part's verified segment and chapter
    *identities* (ordinals and source languages) are carried into the new attempt so
    chapters and the collection can be recomputed over the combined set. The full
    validated content stays authoritative in the linked prior report.
    """

    part_id: str
    source_report_id: str
    source_report_sha256: str
    part: AvailablePart
    chapters: tuple[Chapter, ...]
    used_fallback: bool

    def provenance_json(self) -> dict[str, object]:
        return {
            "part_id": self.part_id,
            "source_report_id": self.source_report_id,
            "source_report_sha256": self.source_report_sha256,
            "segment_count": len(self.part.segments),
            "chapter_count": len(self.chapters),
            "used_fallback": self.used_fallback,
        }


def carry_forward_parts(
    prior: LoadedTextAnalysisReport, unaffected_ids: Sequence[str]
) -> tuple[CarriedForwardPart, ...]:
    """Carry each unaffected available Part forward with a link to its source report.

    Every carried-forward Part references the retained prior report by id and
    content hash, reusing its verified segment and chapter identities without
    re-running the text model or copying its prose. An unaffected id that is not an
    available Part of the prior report (for example a still-omitted Part) has no
    analysis to reuse and is skipped here; the collection recomputation keeps its
    omission declared.
    """

    parts_by_id = {loaded.part.part_id: loaded for loaded in prior.parts}
    chapters_by_part: dict[str, list[Chapter]] = {}
    for chapter in prior.chapters:
        chapters_by_part.setdefault(chapter.part_id, []).append(chapter)
    carried: list[CarriedForwardPart] = []
    for part_id in unaffected_ids:
        loaded = parts_by_id.get(part_id)
        if loaded is None:
            continue
        carried.append(
            CarriedForwardPart(
                part_id=part_id,
                source_report_id=prior.report_id,
                source_report_sha256=prior.source_evidence.sha256,
                part=loaded.part,
                chapters=tuple(chapters_by_part.get(part_id, ())),
                used_fallback=loaded.part.used_fallback,
            )
        )
    return tuple(carried)


# --- New cue basis derivation -------------------------------------------------


def enhancement_report_cue_bases(
    document: Mapping[str, object],
) -> dict[str, tuple[str, ...]]:
    """Read each enhanced Part's ordered cue identities from a retained enhancement report.

    The enhancement report's ``enhanced_parts`` records, per Part, the display-layer
    cues after Gate-checked interval replacement (ADR 0045), each carrying its
    ``cue_ref`` transcription-provenance identity (an original ``subtitle_track`` cue
    or an ``asr`` cue). Those identities in display order are the enhanced Part's new
    cue basis. A malformed enhanced Part or cue is a rejected input, never silently
    dropped, because a dropped cue would misclassify the Part as unaffected.
    """

    enhanced_parts = document.get("enhanced_parts")
    if not isinstance(enhanced_parts, list):
        raise TextReanalysisError(
            "enhancement_report_invalid", "Enhancement report omits its enhanced Parts."
        )
    bases: dict[str, tuple[str, ...]] = {}
    for raw_part in enhanced_parts:
        if not isinstance(raw_part, Mapping):
            raise TextReanalysisError(
                "enhancement_report_invalid", "An enhanced Part is not an object."
            )
        part_id = raw_part.get("part_id")
        cues = raw_part.get("cues")
        if not isinstance(part_id, str) or not part_id or not isinstance(cues, list):
            raise TextReanalysisError(
                "enhancement_report_invalid", "An enhanced Part omits its identity or cues."
            )
        cue_ids: list[str] = []
        for raw_cue in cues:
            if not isinstance(raw_cue, Mapping):
                raise TextReanalysisError(
                    "enhancement_report_invalid", "An enhanced cue is not an object."
                )
            cue_ref = raw_cue.get("cue_ref")
            if not isinstance(cue_ref, str) or not cue_ref:
                raise TextReanalysisError(
                    "enhancement_report_invalid", "An enhanced cue omits its provenance identity."
                )
            cue_ids.append(cue_ref)
        bases[part_id] = tuple(cue_ids)
    return bases


def combined_new_cue_bases(
    prior_bases: Mapping[str, Sequence[str]],
    changed_bases: Mapping[str, Sequence[str]],
) -> dict[str, tuple[str, ...]]:
    """Overlay a changed cue basis onto the prior report's per-Part cue bases.

    Parts the change did not touch keep their prior cue identities, so the
    affected-Part selector marks them unaffected and they are carried forward; a
    changed Part takes its new identities and is regenerated.
    """

    combined = {part_id: tuple(cues) for part_id, cues in prior_bases.items()}
    for part_id, cues in changed_bases.items():
        combined[part_id] = tuple(cues)
    return combined


# --- Combined re-analysis composition ----------------------------------------


@dataclass(frozen=True)
class ReanalysisComposition:
    """The recomputed content of a re-analysis attempt over the combined Part set."""

    status: str
    segments: tuple[dict[str, object], ...]
    chapters: tuple[dict[str, object], ...]
    collection_summary: CollectionSummary | None
    diagnostics: tuple[PlanningDiagnostic, ...]
    unsupported_item_count: int


def combined_part_order(
    prior: LoadedTextAnalysisReport,
    regenerated_ids: Sequence[str],
    carried_forward: Sequence[CarriedForwardPart],
    omitted_parts: Sequence[OmittedPart],
) -> tuple[str, ...]:
    """Order every Part of the combined set, preserving the prior report's order.

    The prior collection's Part order is preserved for continuity; any Part present
    only after the change (an added regenerated Part) is appended in stable
    identity order. The order governs the recomputed segment, chapter, and
    collection layout so the new report is deterministic.
    """

    if prior.collection_summary is not None:
        prior_order = list(prior.collection_summary.part_ids)
    else:
        prior_order = [loaded.part.part_id for loaded in prior.parts]
        prior_order += [omitted.part_id for omitted in prior.omitted_parts]
    present = set(regenerated_ids)
    present |= {carried.part_id for carried in carried_forward}
    present |= {omitted.part_id for omitted in omitted_parts}
    order = [part_id for part_id in prior_order if part_id in present]
    seen = set(prior_order)
    order += sorted(part_id for part_id in present if part_id not in seen)
    return tuple(order)


def compose_reanalysis(
    *,
    regenerated: Sequence[PartGeneration],
    carried_forward: Sequence[CarriedForwardPart],
    omitted_parts: Sequence[OmittedPart],
    proposed_entries: Sequence[ProposedCollectionEntry],
    part_order: Sequence[str],
) -> ReanalysisComposition:
    """Recompute chapters and the collection over regenerated plus carried-forward Parts.

    Segments and chapters are laid out in ``part_order``: a regenerated Part
    contributes its freshly adjudicated segments and chapters (provenance
    ``regenerated``); a carried-forward Part contributes its reused identities
    linked to the source report (provenance ``carried_forward``). Every
    subtitle-unavailable Part stays declared as an omitted range. The collection
    summary is recomputed over the whole combined set, and the status follows the
    same rule a fresh analysis uses -- ``partial`` on any conservative fallback or
    omitted Part, ``failed`` only when no verified segment survives.
    """

    regenerated_by_id = {part.part_id: part for part in regenerated}
    carried_by_id = {carried.part_id: carried for carried in carried_forward}
    omitted_by_id = {omitted.part_id: omitted for omitted in omitted_parts}

    aggregatable: list[AvailablePart | UnavailablePart] = []
    for part_id in part_order:
        if part_id in regenerated_by_id:
            aggregatable.append(regenerated_by_id[part_id].available_part)
        elif part_id in carried_by_id:
            aggregatable.append(carried_by_id[part_id].part)
        elif part_id in omitted_by_id:
            omitted = omitted_by_id[part_id]
            aggregatable.append(
                UnavailablePart(
                    part_id=omitted.part_id,
                    reason=omitted.reason,
                    virtual_time_range=omitted.virtual_time_range,
                )
            )
    collection_summary = (
        aggregate_collection(aggregatable, proposed_entries) if aggregatable else None
    )

    segments: list[dict[str, object]] = []
    chapters: list[dict[str, object]] = []
    diagnostics: list[PlanningDiagnostic] = []
    unsupported = 0
    used_fallback = False
    for part_id in part_order:
        if part_id in regenerated_by_id:
            part = regenerated_by_id[part_id]
            diagnostics.extend(part.diagnostics)
            unsupported += part.unsupported_item_count
            used_fallback = used_fallback or part.used_fallback
            for segment in part.segments:
                segments.append({**segment.as_json(), "provenance": PROVENANCE_REGENERATED})
            for chapter in part.chapters:
                chapters.append({**chapter.as_json(), "provenance": PROVENANCE_REGENERATED})
        elif part_id in carried_by_id:
            carried = carried_by_id[part_id]
            used_fallback = used_fallback or carried.used_fallback
            for aggregatable_segment in carried.part.segments:
                segments.append(_carried_segment_json(aggregatable_segment, carried))
            for chapter in carried.chapters:
                chapters.append(
                    {
                        **chapter.as_json(),
                        "provenance": PROVENANCE_CARRIED_FORWARD,
                        "source_report_id": carried.source_report_id,
                    }
                )

    if collection_summary is not None:
        diagnostics.extend(collection_summary.rejected)
        diagnostics.extend(collection_summary.limitations)

    status = _conclude_reanalysis_status(
        has_segments=bool(segments),
        used_fallback=used_fallback,
        has_omitted=bool(omitted_parts),
    )
    if status == STATUS_FAILED:
        diagnostics.append(
            PlanningDiagnostic(
                "no_verified_segment",
                "No regenerated or carried-forward Part produced a verified SemanticSegment.",
            )
        )
    return ReanalysisComposition(
        status=status,
        segments=tuple(segments),
        chapters=tuple(chapters),
        collection_summary=collection_summary,
        diagnostics=tuple(diagnostics),
        unsupported_item_count=unsupported,
    )


def _carried_segment_json(
    segment: AggregatableSegment, carried: CarriedForwardPart
) -> dict[str, object]:
    """Serialize one carried-forward segment as an identity linked to its source report."""

    return {
        "part_id": segment.part_id,
        "ordinal": segment.ordinal,
        "source_languages": list(segment.source_languages),
        "provenance": PROVENANCE_CARRIED_FORWARD,
        "source_report_id": carried.source_report_id,
    }


def _conclude_reanalysis_status(
    *, has_segments: bool, used_fallback: bool, has_omitted: bool
) -> str:
    if not has_segments:
        return STATUS_FAILED
    if used_fallback or has_omitted:
        return STATUS_PARTIAL
    return STATUS_COMPLETE


# --- The re-analysis input-cue manifest (regeneration binding) ----------------


def reanalysis_input_cue_manifest_document(
    affected_bases: Mapping[str, Sequence[str]],
    *,
    prior_report_id: str,
    enhancement_report_id: str,
) -> dict[str, object]:
    """Build the canonical manifest binding one controlled re-analysis regeneration.

    It pins the retained prior report, the enhancement report that changed the cue
    basis, and every affected Part's ordered new cue identities. Binding a
    controlled text fixture to this manifest hash transitively binds its fixed
    output to exactly this regeneration over exactly these changed cues.
    """

    return {
        "schema_version": 1,
        "prior_report_id": prior_report_id,
        "enhancement_report_id": enhancement_report_id,
        "affected_parts": [
            {"part_id": part_id, "cue_ids": list(affected_bases[part_id])}
            for part_id in sorted(affected_bases)
        ],
    }


def reanalysis_input_cue_manifest_sha256(document: Mapping[str, object]) -> str:
    """Return the canonical content identity of a re-analysis input-cue manifest."""

    return sha256(json.dumps(document, sort_keys=True).encode("utf-8")).hexdigest()


# --- The re-analysis report ---------------------------------------------------


@dataclass(frozen=True)
class ReanalysisReport:
    """Immutable machine-readable result of one Affected-Part re-analysis attempt."""

    report_id: str
    plan_id: str
    subtitle_report_id: str
    status: str
    workspace_path: Path
    report_path: Path
    plan_evidence: InputEvidence | None
    subtitle_evidence: InputEvidence | None
    prior_report_id: str
    prior_report_evidence: InputEvidence | None
    enhancement_report_id: str
    enhancement_report_evidence: InputEvidence | None
    cue_basis_source: str
    selection: AffectedPartSelection | None
    regenerated_part_ids: tuple[str, ...]
    carried_forward: tuple[CarriedForwardPart, ...]
    segments: tuple[dict[str, object], ...]
    chapters: tuple[dict[str, object], ...]
    collection_summary: CollectionSummary | None
    unsupported_item_count: int
    contract_identity: dict[str, object] | None
    restricted_raw_output: dict[str, object] | None
    rendered_report: dict[str, object] | None
    diagnostics: tuple[PlanningDiagnostic, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "report_id": self.report_id,
            "plan_id": self.plan_id,
            "subtitle_report_id": self.subtitle_report_id,
            "status": self.status,
            "attempt_kind": "affected_part_reanalysis",
            "workspace_path": self.workspace_path.as_posix(),
            "report_path": self.report_path.as_posix(),
            "input_evidence": {
                "run_plan": _evidence_json(self.plan_evidence),
                "subtitle_candidate_report": _evidence_json(self.subtitle_evidence),
                "prior_text_analysis_report": {
                    "report_id": self.prior_report_id,
                    **(
                        self.prior_report_evidence.as_json()
                        if self.prior_report_evidence is not None
                        else {}
                    ),
                },
                "enhancement_report": {
                    "report_id": self.enhancement_report_id,
                    **(
                        self.enhancement_report_evidence.as_json()
                        if self.enhancement_report_evidence is not None
                        else {}
                    ),
                },
            },
            "audio_completeness": "not_verified",
            "reanalysis": {
                "cue_basis_source": self.cue_basis_source,
                "prior_report_id": self.prior_report_id,
                "prior_report_sha256": (
                    self.prior_report_evidence.sha256
                    if self.prior_report_evidence is not None
                    else None
                ),
                "classifications": (
                    self.selection.as_json()["classifications"]
                    if self.selection is not None
                    else []
                ),
                "regenerated_parts": list(self.regenerated_part_ids),
                "carried_forward_parts": [
                    carried.provenance_json() for carried in self.carried_forward
                ],
            },
            "segments": [dict(segment) for segment in self.segments],
            "chapters": [dict(chapter) for chapter in self.chapters],
            "collection_summary": (
                self.collection_summary.as_json()
                if self.collection_summary is not None
                else None
            ),
            "unsupported_item_count": self.unsupported_item_count,
            "contract_identity": self.contract_identity,
            "restricted_raw_output": self.restricted_raw_output,
            "required_decision": None,
            "rendered_report": self.rendered_report,
            "diagnostics": [diagnostic.as_json() for diagnostic in self.diagnostics],
            "guarantees": {
                "asr_or_ocr": "not_attempted",
                "external_knowledge": "not_used",
                "model_acquisition": "not_attempted",
                "model_execution": "not_attempted",
                "network_access": "not_attempted",
                "outputs_publication": "not_attempted",
                "run_plan_mutation": "not_attempted",
                "subtitle_artifact_mutation": "not_attempted",
                "translation": "not_attempted",
                "user_media_access": "not_attempted",
            },
        }


@dataclass
class _ReanalysisInputs:
    """The revalidated inputs threaded from revalidation into regeneration."""

    plan: RunPlan
    plan_path: Path
    subtitle_report_id: str
    subtitle_path: Path
    prior: LoadedTextAnalysisReport
    prior_path: Path
    enhancement_document: Mapping[str, object]
    enhancement_path: Path


@dataclass
class _ReanalysisBuilder:
    """Accumulates the re-analysis report fields as revalidation and regeneration run."""

    report_id: str
    plan_id: str
    subtitle_report_id: str
    prior_report_id: str
    enhancement_report_id: str
    workspace_path: Path
    report_path: Path
    status: str = STATUS_FAILED
    plan_evidence: InputEvidence | None = None
    subtitle_evidence: InputEvidence | None = None
    prior_report_evidence: InputEvidence | None = None
    enhancement_report_evidence: InputEvidence | None = None
    cue_basis_source: str = CUE_BASIS_ENHANCED
    selection: AffectedPartSelection | None = None
    regenerated_part_ids: tuple[str, ...] = ()
    carried_forward: tuple[CarriedForwardPart, ...] = ()
    segments: tuple[dict[str, object], ...] = ()
    chapters: tuple[dict[str, object], ...] = ()
    collection_summary: CollectionSummary | None = None
    unsupported_item_count: int = 0
    contract_identity: dict[str, object] | None = None
    restricted_raw_output: dict[str, object] | None = None
    diagnostics: tuple[PlanningDiagnostic, ...] = ()

    def bind_inputs(self, inputs: _ReanalysisInputs) -> None:
        self.plan_id = inputs.plan.plan_id
        self.subtitle_report_id = inputs.subtitle_report_id
        self.prior_report_id = inputs.prior.report_id
        self.plan_evidence = input_evidence(inputs.plan_path)
        self.subtitle_evidence = input_evidence(inputs.subtitle_path)
        self.prior_report_evidence = inputs.prior.source_evidence
        self.enhancement_report_evidence = input_evidence(inputs.enhancement_path)

    def fail(self, error: Exception) -> None:
        self.status = STATUS_FAILED
        self.selection = None
        self.regenerated_part_ids = ()
        self.carried_forward = ()
        self.segments = ()
        self.chapters = ()
        self.collection_summary = None
        self.unsupported_item_count = 0
        self.contract_identity = None
        self.restricted_raw_output = None
        self.diagnostics = (
            PlanningDiagnostic(getattr(error, "reason", "reanalysis_input_invalid"), str(error)),
        )

    def build(self) -> ReanalysisReport:
        return ReanalysisReport(
            report_id=self.report_id,
            plan_id=self.plan_id,
            subtitle_report_id=self.subtitle_report_id,
            status=self.status,
            workspace_path=self.workspace_path,
            report_path=self.report_path,
            plan_evidence=self.plan_evidence,
            subtitle_evidence=self.subtitle_evidence,
            prior_report_id=self.prior_report_id,
            prior_report_evidence=self.prior_report_evidence,
            enhancement_report_id=self.enhancement_report_id,
            enhancement_report_evidence=self.enhancement_report_evidence,
            cue_basis_source=self.cue_basis_source,
            selection=self.selection,
            regenerated_part_ids=self.regenerated_part_ids,
            carried_forward=self.carried_forward,
            segments=self.segments,
            chapters=self.chapters,
            collection_summary=self.collection_summary,
            unsupported_item_count=self.unsupported_item_count,
            contract_identity=self.contract_identity,
            restricted_raw_output=self.restricted_raw_output,
            rendered_report=None,
            diagnostics=self.diagnostics,
        )


def reanalyze_text(
    plan_id: str,
    subtitle_report_id: str,
    prior_report_id: str,
    enhancement_report_id: str,
    project_root: Path,
) -> dict[str, object]:
    """Create one immutable Affected-Part re-analysis report (ADR 0046).

    After ``vcp enhance`` changes the cue basis, this starts a new immutable
    text-analysis attempt: it revalidates the confirmed RunPlan, the subtitle
    report, the retained prior text-analysis report, and the enhancement report;
    classifies each Part affected or unaffected by comparing cue identities;
    regenerates only affected Parts through the Controlled offline text adapter over
    their changed cue basis; carries unaffected Parts forward with an explicit
    provenance link to the prior report; and recomputes chapters and the collection
    summary over the combined set. The attempt owns a fresh workspace and never
    overwrites the prior report, so there is no automatic retry.
    """

    report_id = uuid.uuid4().hex
    workspace_path = project_root / "work" / "reanalysis-reports" / report_id
    report_path = workspace_path / "reanalysis-report.json"
    builder = _ReanalysisBuilder(
        report_id=report_id,
        plan_id=plan_id,
        subtitle_report_id=subtitle_report_id,
        prior_report_id=prior_report_id,
        enhancement_report_id=enhancement_report_id,
        workspace_path=workspace_path,
        report_path=report_path,
    )
    try:
        inputs = _revalidate_reanalysis_inputs(
            plan_id,
            subtitle_report_id,
            prior_report_id,
            enhancement_report_id,
            project_root,
        )
        builder.bind_inputs(inputs)
        _execute_reanalysis(builder, inputs, project_root)
    except (
        TextReanalysisError,
        TextContractError,
        TextGenerationError,
        TextAggregationError,
        PlanningError,
        OSError,
        ValueError,
    ) as error:
        builder.fail(error)

    report = _render_and_bind_reanalysis_markdown(builder.build())
    _write_reanalysis_json(report_path, report.as_json())
    return {"status": report.status, "report": report.as_json()}


def _execute_reanalysis(
    builder: _ReanalysisBuilder, inputs: _ReanalysisInputs, project_root: Path
) -> None:
    """Classify Parts, regenerate the affected ones, and recompose the report."""

    prior = inputs.prior
    changed_bases = enhancement_report_cue_bases(inputs.enhancement_document)
    new_bases = combined_new_cue_bases(prior.part_cue_bases, changed_bases)
    selection = select_affected_parts(prior.part_cue_bases, new_bases)
    builder.selection = selection
    builder.cue_basis_source = CUE_BASIS_ENHANCED

    carried_forward = carry_forward_parts(prior, selection.unaffected)
    builder.carried_forward = carried_forward

    affected_available = tuple(part_id for part_id in selection.affected if part_id in new_bases)
    proposed_entries: tuple[ProposedCollectionEntry, ...] = _prior_proposed_entries(prior)
    regenerated: tuple[PartGeneration, ...] = ()
    if affected_available:
        regenerated, proposed_entries = _regenerate_affected(
            builder, inputs, project_root, affected_available, new_bases
        )
    builder.regenerated_part_ids = tuple(part.part_id for part in regenerated)

    order = combined_part_order(
        prior, builder.regenerated_part_ids, carried_forward, prior.omitted_parts
    )
    composition = compose_reanalysis(
        regenerated=regenerated,
        carried_forward=carried_forward,
        omitted_parts=prior.omitted_parts,
        proposed_entries=proposed_entries,
        part_order=order,
    )
    builder.status = composition.status
    builder.segments = composition.segments
    builder.chapters = composition.chapters
    builder.collection_summary = composition.collection_summary
    builder.unsupported_item_count = composition.unsupported_item_count
    builder.diagnostics = composition.diagnostics


def _regenerate_affected(
    builder: _ReanalysisBuilder,
    inputs: _ReanalysisInputs,
    project_root: Path,
    affected_available: Sequence[str],
    new_bases: Mapping[str, tuple[str, ...]],
) -> tuple[tuple[PartGeneration, ...], tuple[ProposedCollectionEntry, ...]]:
    """Regenerate affected Parts through the Controlled offline text adapter.

    The controlled fixture must be bound to a re-analysis input-cue manifest that
    pins exactly these changed cues; its retained output is projected through the
    versioned schema and each affected Part is regenerated by the shared per-Part
    generation so it obeys every Phase 6 contract. The raw output stays restricted
    audit evidence. A missing fixture or an invalid projection fails the attempt
    before any regenerated evidence is composed.
    """

    contracts = revalidate_text_generation_contracts(project_root)
    manifest_document = reanalysis_input_cue_manifest_document(
        {part_id: new_bases[part_id] for part_id in affected_available},
        prior_report_id=inputs.prior.report_id,
        enhancement_report_id=builder.enhancement_report_id,
    )
    manifest_sha = reanalysis_input_cue_manifest_sha256(manifest_document)
    controlled = load_controlled_generation(
        contracts.controlled_adapter.document, project_root, manifest_sha
    )
    if controlled is None:
        raise TextReanalysisError(
            "reanalysis_regeneration_unavailable",
            "No Controlled offline text adapter fixture is bound to this re-analysis regeneration.",
        )
    if controlled.input_fixture_sha256 != manifest_sha:
        raise TextReanalysisError(
            "reanalysis_input_mismatch",
            "Controlled text fixture is not bound to these changed re-analysis cues.",
        )
    manifest_path = builder.workspace_path / "provenance" / "reanalysis-input-cue-manifest.json"
    _write_reanalysis_json(manifest_path, manifest_document)
    builder.restricted_raw_output = record_restricted_raw_output(
        builder.workspace_path, "reanalysis-generation", controlled.raw_output
    ).as_json()
    projection = project_text_model_output(
        _decode_generation_output(controlled.raw_output), contracts
    )
    if projection.projection is None:
        message = (
            projection.diagnostic.message
            if projection.diagnostic is not None
            else "The controlled re-analysis output is invalid."
        )
        raise TextReanalysisError("model_output_invalid", message)
    result = projection.projection.get("result")
    result_mapping = result if isinstance(result, Mapping) else {}
    result_parts = index_result_parts(result_mapping)
    regenerated = tuple(
        generate_part(
            LoadedPart(part_id=part_id, track_id="reanalysis", cue_ids=new_bases[part_id]),
            result_parts.get(part_id, {}),
        )
        for part_id in affected_available
    )
    builder.contract_identity = {
        "text_generation_contracts": contracts.as_json(),
        "input_cue_manifest": {
            **input_evidence(manifest_path).as_json(),
            "sha256": manifest_sha,
        },
        "controlled_adapter_identity": contracts.controlled_adapter.version,
    }
    return regenerated, proposed_collection_entries(result_mapping)


def _prior_proposed_entries(
    prior: LoadedTextAnalysisReport,
) -> tuple[ProposedCollectionEntry, ...]:
    """Re-propose the prior collection entries when no Part is regenerated.

    With nothing regenerated the collection is still recomputed, so the prior
    verified entries become the proposals and ``aggregate_collection`` revalidates
    each citation against the carried-forward segments.
    """

    if prior.collection_summary is None:
        return ()
    return tuple(
        ProposedCollectionEntry(segment_refs=entry.segment_refs, text=entry.text)
        for entry in prior.collection_summary.entries
    )


def _prior_text_analysis_report_path(project_root: Path, report_id: str) -> Path:
    validated_id = validated_report_id(
        report_id,
        invalid_error=lambda: TextReanalysisError(
            "text_analysis_report_invalid", "Text analysis report ID must be a UUID."
        ),
    )
    return (
        project_root / "work" / "text-analysis-reports" / validated_id / "text-analysis-report.json"
    )


def _revalidate_reanalysis_inputs(
    plan_id: str,
    subtitle_report_id: str,
    prior_report_id: str,
    enhancement_report_id: str,
    project_root: Path,
) -> _ReanalysisInputs:
    """Revalidate every bound input before a re-analysis attempt proceeds.

    The confirmed RunPlan and its inspection evidence, the subtitle report identity,
    the retained prior text-analysis report (loaded and hash-verified), and the
    enhancement report (loaded, hash-verified, and required to hold enhanced Parts)
    must all agree on the plan and subtitle identities; any drift or mismatch blocks
    the attempt as ``failed`` before regeneration.
    """

    plan_path = project_root / "plans" / plan_id / "run-plan.json"
    plan = load_run_plan(plan_path)
    if plan.plan_id != plan_id:
        raise TextReanalysisError(
            "run_plan_not_confirmed", "RunPlan identity does not match the requested plan ID."
        )
    confirmed_report = load_plan_report(
        project_root / "plans" / "reports" / plan.report_id / "plan-report.json"
    )
    if not confirmed_plan_matches(confirmed_report, plan):
        raise TextReanalysisError(
            "run_plan_not_confirmed", "RunPlan evidence does not match a confirmed PlanReport."
        )
    revalidate_confirmed_inspection_evidence(
        confirmed_report,
        plan,
        drift_error=lambda: TextReanalysisError(
            "inspection_evidence_changed",
            "PlanReport inspection evidence no longer matches the confirmed RunPlan.",
        ),
    )
    subtitle_id = validated_report_id(
        subtitle_report_id,
        invalid_error=lambda: TextReanalysisError(
            "subtitle_report_invalid", "Subtitle candidate report ID must be a UUID."
        ),
    )
    subtitle_path = _subtitle_report_path(project_root, plan.source_artifacts, subtitle_id)
    _revalidate_subtitle_identity(subtitle_path, subtitle_id, plan.plan_id)

    prior_path = _prior_text_analysis_report_path(project_root, prior_report_id)
    prior = load_text_analysis_report(prior_path)
    if prior.plan_id != plan.plan_id or prior.subtitle_report_id != subtitle_id:
        raise TextReanalysisError(
            "reanalysis_report_mismatch",
            "Prior text-analysis report does not belong to this RunPlan and subtitle report.",
        )

    enhancement_document, enhancement_path = _load_enhancement_report(
        project_root, enhancement_report_id
    )
    if (
        enhancement_document.get("plan_id") != plan.plan_id
        or enhancement_document.get("subtitle_report_id") != subtitle_id
    ):
        raise TextReanalysisError(
            "reanalysis_report_mismatch",
            "Enhancement report does not belong to this RunPlan and subtitle report.",
        )

    return _ReanalysisInputs(
        plan=plan,
        plan_path=plan_path,
        subtitle_report_id=subtitle_id,
        subtitle_path=subtitle_path,
        prior=prior,
        prior_path=prior_path,
        enhancement_document=enhancement_document,
        enhancement_path=enhancement_path,
    )


def _revalidate_subtitle_identity(path: Path, subtitle_id: str, plan_id: str) -> None:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TextReanalysisError(
            "subtitle_report_invalid", "Subtitle candidate report cannot be read."
        ) from error
    if not isinstance(decoded, Mapping) or decoded.get("report_id") != subtitle_id:
        raise TextReanalysisError(
            "subtitle_report_mismatch", "Subtitle candidate report identity does not match."
        )
    if decoded.get("plan_id") != plan_id:
        raise TextReanalysisError(
            "subtitle_report_mismatch", "Subtitle candidate report does not belong to this RunPlan."
        )


def _load_enhancement_report(
    project_root: Path, enhancement_report_id: str
) -> tuple[Mapping[str, object], Path]:
    validated_id = validated_report_id(
        enhancement_report_id,
        invalid_error=lambda: TextReanalysisError(
            "enhancement_report_invalid", "Enhancement report ID must be a UUID."
        ),
    )
    path = project_root / "work" / "enhancement-reports" / validated_id / "enhancement-report.json"
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TextReanalysisError(
            "enhancement_report_invalid", "Enhancement report cannot be read."
        ) from error
    if not isinstance(decoded, Mapping) or decoded.get("report_id") != validated_id:
        raise TextReanalysisError("enhancement_report_invalid", "Enhancement report is invalid.")
    if decoded.get("status") not in {STATUS_COMPLETE, STATUS_PARTIAL}:
        raise TextReanalysisError(
            "enhancement_report_not_loadable",
            "Only a complete or partial enhancement report changes a cue basis to re-analyze.",
        )
    return decoded, path


def _render_and_bind_reanalysis_markdown(report: ReanalysisReport) -> ReanalysisReport:
    """Render the deterministic Markdown rendition and bind its version and hash."""

    rendition = render_text_analysis_markdown(report.as_json())
    markdown_path = report.workspace_path / "reanalysis-report.md"
    if markdown_path.exists():
        if markdown_path.read_text(encoding="utf-8") != rendition.text:
            raise TextReanalysisError(
                "reanalysis_report_conflict",
                f"Immutable Markdown rendition differs: {markdown_path}",
            )
    else:
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(rendition.text, encoding="utf-8")
    rendered_report = dict(rendition.as_json())
    rendered_report["path"] = markdown_path.as_posix()
    return replace(report, rendered_report=rendered_report)


def _subtitle_report_path(
    project_root: Path, source_artifacts: tuple[SourceArtifact, ...], report_id: str
) -> Path:
    if len(source_artifacts) == 1:
        return (
            project_root
            / "work"
            / source_artifacts[0].source_id
            / report_id
            / "candidate-report.json"
        )
    return project_root / "work" / "subtitle-reports" / report_id / "report.json"


def _decode_generation_output(raw_output: bytes) -> object:
    try:
        return json.loads(raw_output)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _evidence_json(evidence_record: InputEvidence | None) -> dict[str, object] | None:
    return evidence_record.as_json() if evidence_record is not None else None


def _write_reanalysis_json(path: Path, payload: object) -> None:
    write_json_once(
        path,
        payload,
        conflict_error=lambda message: TextReanalysisError("reanalysis_report_conflict", message),
    )


# --- Small typed readers -----------------------------------------------------


def _required_str(document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise TextReanalysisError(
            "text_analysis_report_unloadable", f"Retained report omits its {field}."
        )
    return value


def _segment_ordinal(segment: Mapping[str, object]) -> int:
    ordinal = segment.get("ordinal")
    if not _is_ordinal(ordinal):
        raise TextReanalysisError(
            "text_analysis_report_drifted", "A retained segment omits a valid ordinal."
        )
    return ordinal


def _object_sequence(value: object, label: str) -> tuple[Mapping[str, object], ...]:
    """Read an optional list of JSON objects, rejecting any non-object element.

    An absent list is an empty tuple; a present-but-wrong-type list or a
    non-object element is a drifted report -- never a silently dropped item.
    """

    if value is None:
        return ()
    if not _is_json_list(value):
        raise TextReanalysisError(
            "text_analysis_report_drifted", f"A retained {label} list is malformed."
        )
    items: list[Mapping[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise TextReanalysisError(
                "text_analysis_report_drifted", f"A retained {label} is not an object."
            )
        items.append(item)
    return tuple(items)


def _string_list(value: object, label: str) -> tuple[str, ...]:
    """Read a required list of strings, rejecting a malformed element or type."""

    if not _is_json_list(value):
        raise TextReanalysisError(
            "text_analysis_report_drifted", f"A retained {label} list is malformed."
        )
    strings: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise TextReanalysisError(
                "text_analysis_report_drifted", f"A retained {label} is not a string."
            )
        strings.append(item)
    return tuple(strings)


def _ordinal_list(value: object, label: str) -> tuple[int, ...]:
    """Read a required list of non-negative ints, rejecting a malformed element."""

    if not _is_json_list(value):
        raise TextReanalysisError(
            "text_analysis_report_drifted", f"A retained {label} list is malformed."
        )
    ordinals: list[int] = []
    for item in value:
        if not _is_ordinal(item):
            raise TextReanalysisError(
                "text_analysis_report_drifted", f"A retained {label} is not a valid ordinal."
            )
        ordinals.append(item)
    return tuple(ordinals)


def _is_json_list(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes)


def _is_ordinal(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)
