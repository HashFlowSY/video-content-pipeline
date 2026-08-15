"""Phase 7 ticket 08 retained-report loading and affected-Part selection.

ADR 0046 recomputes semantic analysis at Part granularity after transcription or
enhancement changes the cue evidence basis: affected Parts are regenerated
against the new basis and unaffected Parts are carried forward from a retained
prior report. That recomputation (ticket 09) needs two deterministic
text-analysis capabilities, both supplied here:

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
coerced, repaired, or silently dropped. See ADR 0046 and the Text Analysis
Context (Affected-Part re-analysis, Carried-forward analysis Part).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TypeGuard

from video_content_pipeline.evidence import InputEvidence, input_evidence
from video_content_pipeline.planning import PlanningDiagnostic
from video_content_pipeline.text_aggregation import (
    AggregatableSegment,
    AvailablePart,
    Chapter,
    CollectionEntry,
    CollectionSummary,
    OmittedPart,
    SegmentRef,
    TextAggregationError,
)
from video_content_pipeline.text_contracts import render_text_analysis_markdown
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
