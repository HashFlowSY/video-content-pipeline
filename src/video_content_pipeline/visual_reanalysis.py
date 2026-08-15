"""Phase 8 ticket 07: consume visual-text evidence in Affected-Part re-analysis.

ADR 0046 recomputes semantic analysis at Part granularity: affected Parts are
regenerated against a changed evidence basis and unaffected Parts are carried
forward from a retained prior report. Phase 7 (``text_reanalysis``) drove that
recomputation from an enhancement report's changed cue basis. This module adds
the second driver ADR 0047/0049 asked for: a retained visual-text report becomes
an Optional visual-text context input to a *new* re-analysis attempt, so chapters
and summaries can reflect on-screen evidence without re-running unaffected Parts.

The wiring keeps the same discipline as every other text-analysis step -- a model
proposes, the deterministic core adjudicates against retained evidence, and every
visual fact traces to retained OCR evidence:

* the retained visual-text report is loaded and revalidated (whole-file hash plus
  its bound plan identity) before use, and affected Parts are exactly the Parts
  carrying new visual evidence (``load_visual_text_report`` /
  ``select_visually_affected_parts``);
* each Part's Visual page changes participate as candidate boundary evidence in
  the same deterministic cue-bound adjudication the text model's boundaries feed
  (``visual_boundary_candidates`` -> ``text_generation.generate_part``'s
  ``extra_boundaries``);
* every admitted page-text OCR fact is owned by exactly one formal SemanticSegment
  (``assign_page_facts``), and a cited page fact appears only where classified
  page-text evidence exists (ADR 0049 -- visual-text classifies, text-analysis
  cites); the absence of visual evidence never blocks subtitle-derived claims; and
* chapters and the collection summary are recomputed over the combined
  regenerated-plus-carried-forward set through the Phase 6/7 composition, in a
  fresh immutable workspace that never overwrites the prior report.

The Host-read comment upgrade (ADR 0049, ticket 08) also lives here, because it is
the one cross-modal fact decision the phase permits: a background-UI comment the
host read aloud or explicitly selected becomes formal evidence only after a
deterministic comparison with the retained cue text (``host_read_upgrade``). The
visual-side report is never mutated; the upgrade record lives in this text-analysis
report and cites both the OCR evidence item and the supporting cues. Visual
page-change times and OCR item PTS are compared on the same retained raw-PTS axis as
the subtitle cue intervals; cross-stream clock reconciliation on real media is
deferred with the rest of the real-OCR work. See ``docs/PHASE_08_SPECIFICATION.md``,
ADR 0046-0049, and the Text Analysis and Visual-Text Contexts.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Protocol, TypeGuard, TypeVar

from video_content_pipeline.enhancement import RetainedSubtitleCue, load_retained_subtitle_cues
from video_content_pipeline.evidence import (
    InputEvidence,
    input_evidence,
    validated_report_id,
    write_json_once,
)
from video_content_pipeline.host_read_upgrade import (
    BackgroundUiComment,
    CueText,
    HostReadUpgrade,
    evaluate_host_read_upgrades,
    load_host_read_upgrade_ruleset,
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
from video_content_pipeline.subtitle_pipeline import (
    CandidateState,
    SubtitleCandidate,
    SubtitleCandidateReport,
)
from video_content_pipeline.text_aggregation import (
    CollectionSummary,
    ProposedCollectionEntry,
    TextAggregationError,
)
from video_content_pipeline.text_analysis import record_restricted_raw_output
from video_content_pipeline.text_contracts import (
    TextContractError,
    project_text_model_output,
    render_text_analysis_markdown,
    revalidate_text_generation_contracts,
)
from video_content_pipeline.text_generation import (
    STATUS_FAILED,
    LoadedPart,
    PartGeneration,
    TextGenerationError,
    generate_part,
    index_result_parts,
    load_controlled_generation,
    proposed_collection_entries,
)
from video_content_pipeline.text_reanalysis import (
    PROVENANCE_REGENERATED,
    CarriedForwardPart,
    LoadedTextAnalysisReport,
    TextReanalysisError,
    carry_forward_parts,
    combined_part_order,
    compose_reanalysis,
    load_text_analysis_report,
)
from video_content_pipeline.text_segmentation import ProposedSegment
from video_content_pipeline.timecode import ExactTime, TimeValidationError

# The origin marker a Visual page-change boundary candidate carries into the
# cue-bound adjudicator, so an auditor can tell a page-change-derived candidate
# apart from a text-model-proposed one.
VISUAL_PAGE_CHANGE_ORIGIN = "visual_page_change"

# The cue-basis driver this attempt records in provenance -- the sibling
# enhancement re-analysis records ``enhanced``; a visual attempt is driven by the
# retained visual-text evidence, never by a changed cue basis.
CUE_BASIS_VISUAL_TEXT = "visual_text"

# A retained visual-text report holds classified evidence only in these terminal
# states; ``partial`` includes the OCR-declined page-index-only report (which
# simply carries no page facts). Any other state -- acquisition-gated, paused, or
# failed -- produced no classified evidence to consume.
VISUAL_EVIDENCE_STATUSES = frozenset({"complete", "partial"})

# The one page-category (ADR 0049) whose OCR items become cited page facts; the
# other categories and excluded platform noise never enter formal content here.
_PAGE_TEXT_CATEGORY = "page_text"

# The category whose items are the *candidates* for the Host-read comment upgrade
# (ticket 08). A background-UI item is never evidence until text-analysis upgrades
# it, and it never widens Affected-Part selection on its own (that stays driven by
# page-text facts and page changes).
_BACKGROUND_UI_CATEGORY = "background_ui"


class VisualReanalysisError(ValueError):
    """A rejected visual re-analysis input with a machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


# --- Retained visual-text report (Optional visual-text context) --------------


@dataclass(frozen=True)
class VisualPageFact:
    """One admitted page-text OCR fact, traceable to its Part, page, and PTS.

    It is the citeable unit a formal SemanticSegment owns: the verbatim on-screen
    text with the Part-local ``visual_page_id`` and the retained raw-PTS at which
    it was read, so every visual fact traces to retained OCR evidence.
    """

    part_id: str
    visual_page_id: str
    pts: ExactTime
    text: str
    confidence: float

    def as_json(self) -> dict[str, object]:
        return {
            "visual_page_id": self.visual_page_id,
            "pts": {"numerator": self.pts.numerator, "denominator": self.pts.denominator},
            "text": self.text,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class VisualPartEvidence:
    """The new visual evidence a single Part carries into re-analysis.

    ``page_facts`` are the admitted page-text OCR facts in read order;
    ``page_change_times`` are the internal Visual page-change boundary times (every
    page appearance start except the Part's earliest), the candidate boundary
    evidence page changes contribute to adjudication.
    """

    part_id: str
    page_facts: tuple[VisualPageFact, ...]
    page_change_times: tuple[ExactTime, ...]
    background_ui_comments: tuple[BackgroundUiComment, ...] = ()

    @property
    def has_visual_evidence(self) -> bool:
        """Whether this Part carries any new visual evidence to re-analyze.

        Background-UI comments are deliberately *not* counted here: they are only
        candidates for the Host-read comment upgrade, which enriches an already
        affected Part rather than widening Affected-Part selection (ADR 0049).
        """

        return bool(self.page_facts) or bool(self.page_change_times)


@dataclass(frozen=True)
class LoadedVisualText:
    """A retained visual-text report deserialized into the evidence it contributes.

    ``source_evidence`` records the whole-file content hash, pinning every
    deserialized field to exact bytes so the re-analysis can cite precisely which
    visual report -- and which evidence -- it consumed.
    """

    report_id: str
    plan_id: str
    status: str
    source_evidence: InputEvidence
    parts: tuple[VisualPartEvidence, ...]

    @property
    def parts_by_id(self) -> dict[str, VisualPartEvidence]:
        return {evidence.part_id: evidence for evidence in self.parts}


def load_visual_text_report(report_path: Path) -> LoadedVisualText:
    """Deserialize and revalidate a retained visual-text report before use.

    The report is read once (read-only), its whole-file content hash recorded, and
    its terminal status checked to hold classified evidence. The page-text OCR
    items are read from the versioned ``classification`` block (page-text only, per
    ADR 0049) and the Visual page-change times from the ``page_index`` appearance
    records. An unreadable report, a non-loadable status, or a malformed field
    raises ``VisualReanalysisError``; nothing in the retained report is mutated.
    """

    evidence = _read_report_evidence(report_path)
    document = _read_report_document(report_path)
    report_id = _required_str(document, "report_id")
    plan_id = _required_str(document, "plan_id")
    status = document.get("status")
    if not isinstance(status, str):
        raise VisualReanalysisError(
            "visual_text_report_unloadable", "Retained visual-text report omits its status."
        )
    if status not in VISUAL_EVIDENCE_STATUSES:
        raise VisualReanalysisError(
            "visual_text_report_not_loadable",
            f"A {status!r} visual-text report holds no classified evidence to consume.",
        )

    facts_by_part = _page_text_facts_by_part(document)
    changes_by_part = _page_change_times_by_part(document)
    comments_by_part = _background_ui_comments_by_part(document)
    part_ids = (
        _page_index_part_ids(document)
        | set(facts_by_part)
        | set(changes_by_part)
        | set(comments_by_part)
    )
    parts = tuple(
        VisualPartEvidence(
            part_id=part_id,
            page_facts=facts_by_part.get(part_id, ()),
            page_change_times=changes_by_part.get(part_id, ()),
            background_ui_comments=comments_by_part.get(part_id, ()),
        )
        for part_id in sorted(part_ids)
    )
    return LoadedVisualText(
        report_id=report_id,
        plan_id=plan_id,
        status=status,
        source_evidence=evidence,
        parts=parts,
    )


def _page_text_facts_by_part(
    document: Mapping[str, object],
) -> dict[str, tuple[VisualPageFact, ...]]:
    """Read every Part's admitted page-text OCR facts from the classification block."""

    classification = document.get("classification")
    if classification is None:
        return {}
    if not isinstance(classification, Mapping):
        raise VisualReanalysisError(
            "visual_text_report_drifted", "Retained classification block is not an object."
        )
    facts: dict[str, tuple[VisualPageFact, ...]] = {}
    for raw_part in _object_sequence(classification.get("parts"), "classification Part"):
        part_id = _required_str(raw_part, "part_id")
        part_facts: list[VisualPageFact] = []
        for raw_item in _object_sequence(raw_part.get("classified"), "classified item"):
            if raw_item.get("category") != _PAGE_TEXT_CATEGORY:
                continue
            part_facts.append(_page_fact(part_id, raw_item))
        if part_facts:
            facts[part_id] = tuple(part_facts)
    return facts


def _page_fact(part_id: str, raw_item: Mapping[str, object]) -> VisualPageFact:
    text, confidence = _text_and_confidence(raw_item, "page-text")
    return VisualPageFact(
        part_id=part_id,
        visual_page_id=_required_str(raw_item, "visual_page_id"),
        pts=_time_from_json(raw_item.get("pts")),
        text=text,
        confidence=confidence,
    )


def _background_ui_comments_by_part(
    document: Mapping[str, object],
) -> dict[str, tuple[BackgroundUiComment, ...]]:
    """Read every Part's classified background-UI items -- upgrade candidates (ticket 08).

    These are read from the same versioned ``classification`` block as page-text
    facts but are *not* evidence: they are the only items the Host-read comment
    upgrade may promote, and only when cross-modal comparison with cue text confirms
    the host read or selected them (ADR 0049). Everything else classification
    produced is ignored here.
    """

    classification = document.get("classification")
    if classification is None:
        return {}
    if not isinstance(classification, Mapping):
        raise VisualReanalysisError(
            "visual_text_report_drifted", "Retained classification block is not an object."
        )
    comments: dict[str, tuple[BackgroundUiComment, ...]] = {}
    for raw_part in _object_sequence(classification.get("parts"), "classification Part"):
        part_id = _required_str(raw_part, "part_id")
        part_comments: list[BackgroundUiComment] = []
        for raw_item in _object_sequence(raw_part.get("classified"), "classified item"):
            if raw_item.get("category") != _BACKGROUND_UI_CATEGORY:
                continue
            part_comments.append(_background_ui_comment(part_id, raw_item))
        if part_comments:
            comments[part_id] = tuple(part_comments)
    return comments


def _background_ui_comment(part_id: str, raw_item: Mapping[str, object]) -> BackgroundUiComment:
    text, confidence = _text_and_confidence(raw_item, "background-UI")
    return BackgroundUiComment(
        part_id=part_id,
        visual_page_id=_required_str(raw_item, "visual_page_id"),
        pts=_time_from_json(raw_item.get("pts")),
        text=text,
        confidence=confidence,
    )


def _text_and_confidence(raw_item: Mapping[str, object], label: str) -> tuple[str, float]:
    text = raw_item.get("text")
    confidence = raw_item.get("confidence")
    if not isinstance(text, str) or not _is_real(confidence):
        raise VisualReanalysisError(
            "visual_text_report_drifted",
            f"A classified {label} item omits text or confidence.",
        )
    return text, float(confidence)


def _page_change_times_by_part(
    document: Mapping[str, object],
) -> dict[str, tuple[ExactTime, ...]]:
    """Read every Part's internal page-change times from its page-index appearances.

    Every page appearance start is a candidate boundary except the Part's earliest
    -- the Part's own beginning is not an internal boundary. A returning page's
    reappearance start is retained, so "the speaker returned to this slide" is
    boundary evidence.
    """

    page_index = document.get("page_index")
    if not isinstance(page_index, Mapping):
        return {}
    changes: dict[str, tuple[ExactTime, ...]] = {}
    for raw_part in _object_sequence(page_index.get("parts"), "page-index Part with changes"):
        part_id = _required_str(raw_part, "part_id")
        starts: list[ExactTime] = []
        for raw_page in _object_sequence(raw_part.get("pages"), "visual page"):
            for raw_appearance in _object_sequence(raw_page.get("appearances"), "page appearance"):
                starts.append(_time_from_json(raw_appearance.get("start")))
        if len(starts) <= 1:
            continue
        ordered = sorted(set(starts), key=lambda time: time.as_fraction())
        internal = tuple(ordered[1:])  # drop the Part's earliest appearance start
        if internal:
            changes[part_id] = internal
    return changes


def _page_index_part_ids(document: Mapping[str, object]) -> set[str]:
    """Return every Part named in the page index, evidence-bearing or not."""

    page_index = document.get("page_index")
    if not isinstance(page_index, Mapping):
        return set()
    return {
        _required_str(raw_part, "part_id")
        for raw_part in _object_sequence(page_index.get("parts"), "page-index Part")
    }


def select_visually_affected_parts(
    visual: LoadedVisualText, available_part_ids: Collection[str]
) -> tuple[str, ...]:
    """Return the available prior Parts that carry new visual evidence, in order.

    An affected Part is one that both carries new visual evidence (page-text facts
    or page changes) and is an available Part of the prior report -- only such a
    Part has a cue basis to regenerate against. A Part with visual evidence but no
    prior cue basis, and an available Part with no visual evidence, are left to be
    carried forward. Selection is ordered by Part identity for a stable record.
    """

    available = set(available_part_ids)
    return tuple(
        evidence.part_id
        for evidence in sorted(visual.parts, key=lambda evidence: evidence.part_id)
        if evidence.has_visual_evidence and evidence.part_id in available
    )


# --- Visual page-change boundary candidates (AC2) ----------------------------


@dataclass(frozen=True)
class TimedCue:
    """One Part cue identity paired with its retained raw-PTS start time, in order."""

    cue_id: str
    start: ExactTime


def visual_boundary_candidates(
    part_id: str, change_times: Sequence[ExactTime], cues: Sequence[TimedCue]
) -> tuple[ProposedSegment, ...]:
    """Derive candidate cue-pair boundaries from a Part's Visual page changes.

    Each page-change time splits the Part before the first cue whose start is at or
    after it; the resulting cut points partition the ordered cues into consecutive
    cue-pair spans. Those spans are returned as ``ProposedSegment`` candidates
    tagged with the Visual page-change origin, so page changes participate as
    candidate boundary evidence in the same deterministic adjudication the text
    model's boundaries feed. A change before the first cue or after the last cue
    contributes no interior split; with no interior split the Part contributes no
    candidate at all, so page changes never override the text model's boundaries
    with a spurious single-segment tiling.

    Because adjudication accepts a candidate set only when it tiles the Part exactly
    once, these candidates *reinforce* the text model when the two agree (or when the
    model proposes none, letting the page changes drive segmentation) and *defer*
    conservatively when the two disagree -- a disagreeing union cannot tile, so the
    Part falls back to one conservative segment. That conservative resolution is the
    Phase 6 adjudication contract, left unchanged; page changes never coerce a
    boundary the evidence cannot jointly support.
    """

    if not cues:
        return ()
    cut_positions: set[int] = set()
    for change in change_times:
        position = _first_cue_at_or_after(cues, change)
        if 0 < position < len(cues):
            cut_positions.add(position)
    if not cut_positions:
        return ()
    bounds = sorted(cut_positions)
    starts = [0, *bounds]
    ends = [*bounds, len(cues)]
    return tuple(
        ProposedSegment(
            part_id=part_id,
            start_cue_id=cues[start].cue_id,
            end_cue_id=cues[end - 1].cue_id,
            technical_block_id=VISUAL_PAGE_CHANGE_ORIGIN,
        )
        for start, end in zip(starts, ends, strict=True)
        if start < end
    )


def _first_cue_at_or_after(cues: Sequence[TimedCue], time: ExactTime) -> int:
    target = time.as_fraction()
    for position, cue in enumerate(cues):
        if cue.start.as_fraction() >= target:
            return position
    return len(cues)


# --- Exactly-once page-fact ownership (AC3/AC4) ------------------------------


class _HasPts(Protocol):
    """A visual item ownable by segment time: it exposes a raw-PTS read time."""

    @property
    def pts(self) -> ExactTime: ...


_PtsItemT = TypeVar("_PtsItemT", bound=_HasPts)


def _assign_by_pts(
    items: Sequence[_PtsItemT], segment_starts: Sequence[tuple[int, ExactTime]]
) -> dict[int, tuple[_PtsItemT, ...]]:
    """Assign each PTS-bearing item to exactly one segment, by segment start time.

    An item is owned by the segment in effect at its PTS -- the last segment whose
    start time is at or before the item -- and an item before the first segment
    falls to the first. Because the segments tile the Part in start order, every
    item is owned by exactly one segment. Segments with no owned item do not appear
    in the result. Returns a map from segment ordinal to its owned items, in the
    items' original order.
    """

    ordered = sorted(segment_starts, key=lambda item: item[1].as_fraction())
    owned: dict[int, list[_PtsItemT]] = {}
    if not ordered:
        return {}
    for item in items:
        target = item.pts.as_fraction()
        chosen = ordered[0][0]
        for ordinal, start in ordered:
            if start.as_fraction() <= target:
                chosen = ordinal
            else:
                break
        owned.setdefault(chosen, []).append(item)
    return {ordinal: tuple(members) for ordinal, members in owned.items()}


def assign_page_facts(
    facts: Sequence[VisualPageFact], segment_starts: Sequence[tuple[int, ExactTime]]
) -> dict[int, tuple[VisualPageFact, ...]]:
    """Assign each admitted page-text fact to exactly one segment, by segment time.

    A thin, named wrapper over ``_assign_by_pts`` for page facts specifically; the
    Host-read comment upgrade owns its upgraded comments through the same helper, so
    every visual item -- page fact or upgraded comment -- is owned by exactly one
    segment under identical rules.
    """

    return _assign_by_pts(facts, segment_starts)


# --- The visual re-analysis input manifest (regeneration binding) ------------


def visual_reanalysis_manifest_document(
    affected_bases: Mapping[str, Sequence[str]],
    *,
    prior_report_id: str,
    visual_report_id: str,
) -> dict[str, object]:
    """Build the canonical manifest binding one controlled visual re-analysis.

    It pins the retained prior report, the visual-text report that supplied the new
    evidence, and every affected Part's ordered cue identities (the cue basis is
    unchanged -- visual evidence never rewrites cues). Binding a controlled text
    fixture to this manifest hash binds its fixed output to exactly this
    regeneration over exactly these Parts.
    """

    return {
        "schema_version": 1,
        "prior_report_id": prior_report_id,
        "visual_report_id": visual_report_id,
        "affected_parts": [
            {"part_id": part_id, "cue_ids": list(affected_bases[part_id])}
            for part_id in sorted(affected_bases)
        ],
    }


def visual_reanalysis_manifest_sha256(document: Mapping[str, object]) -> str:
    """Return the canonical content identity of a visual re-analysis manifest."""

    return sha256(json.dumps(document, sort_keys=True).encode("utf-8")).hexdigest()


# --- The visual re-analysis report -------------------------------------------


@dataclass(frozen=True)
class VisualReanalysisReport:
    """Immutable machine-readable result of one visual Affected-Part re-analysis."""

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
    visual_report_id: str
    visual_report_evidence: InputEvidence | None
    affected_part_ids: tuple[str, ...]
    carried_forward: tuple[CarriedForwardPart, ...]
    regenerated_part_ids: tuple[str, ...]
    segments: tuple[dict[str, object], ...]
    chapters: tuple[dict[str, object], ...]
    collection_summary: CollectionSummary | None
    unsupported_item_count: int
    visual_fact_count: int
    host_read_upgrades: tuple[HostReadUpgrade, ...]
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
            "attempt_kind": "visual_affected_part_reanalysis",
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
                "visual_text_report": {
                    "report_id": self.visual_report_id,
                    **(
                        self.visual_report_evidence.as_json()
                        if self.visual_report_evidence is not None
                        else {}
                    ),
                },
            },
            "audio_completeness": "not_verified",
            "reanalysis": {
                "cue_basis_source": CUE_BASIS_VISUAL_TEXT,
                "prior_report_id": self.prior_report_id,
                "prior_report_sha256": (
                    self.prior_report_evidence.sha256
                    if self.prior_report_evidence is not None
                    else None
                ),
                "visual_report_id": self.visual_report_id,
                "affected_parts": list(self.affected_part_ids),
                "regenerated_parts": list(self.regenerated_part_ids),
                "carried_forward_parts": [
                    carried.provenance_json() for carried in self.carried_forward
                ],
                "visual_fact_count": self.visual_fact_count,
                "host_read_upgrades": [upgrade.as_json() for upgrade in self.host_read_upgrades],
                "host_read_upgrade_count": len(self.host_read_upgrades),
            },
            "segments": [dict(segment) for segment in self.segments],
            "chapters": [dict(chapter) for chapter in self.chapters],
            "collection_summary": (
                self.collection_summary.as_json() if self.collection_summary is not None else None
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
                "frame_extraction": "not_attempted",
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
class _VisualReanalysisInputs:
    plan: RunPlan
    plan_path: Path
    subtitle_report_id: str
    subtitle_path: Path
    prior: LoadedTextAnalysisReport
    visual: LoadedVisualText
    visual_path: Path
    cues_by_part: dict[str, tuple[RetainedSubtitleCue, ...]]


@dataclass
class _VisualReanalysisBuilder:
    report_id: str
    plan_id: str
    subtitle_report_id: str
    prior_report_id: str
    visual_report_id: str
    workspace_path: Path
    report_path: Path
    status: str = STATUS_FAILED
    plan_evidence: InputEvidence | None = None
    subtitle_evidence: InputEvidence | None = None
    prior_report_evidence: InputEvidence | None = None
    visual_report_evidence: InputEvidence | None = None
    affected_part_ids: tuple[str, ...] = ()
    carried_forward: tuple[CarriedForwardPart, ...] = ()
    regenerated_part_ids: tuple[str, ...] = ()
    segments: tuple[dict[str, object], ...] = ()
    chapters: tuple[dict[str, object], ...] = ()
    collection_summary: CollectionSummary | None = None
    unsupported_item_count: int = 0
    visual_fact_count: int = 0
    host_read_upgrades: tuple[HostReadUpgrade, ...] = ()
    contract_identity: dict[str, object] | None = None
    restricted_raw_output: dict[str, object] | None = None
    diagnostics: tuple[PlanningDiagnostic, ...] = ()

    def bind_inputs(self, inputs: _VisualReanalysisInputs) -> None:
        self.plan_id = inputs.plan.plan_id
        self.subtitle_report_id = inputs.subtitle_report_id
        self.prior_report_id = inputs.prior.report_id
        self.visual_report_id = inputs.visual.report_id
        self.plan_evidence = input_evidence(inputs.plan_path)
        self.subtitle_evidence = input_evidence(inputs.subtitle_path)
        self.prior_report_evidence = inputs.prior.source_evidence
        self.visual_report_evidence = inputs.visual.source_evidence

    def fail(self, error: Exception) -> None:
        self.status = STATUS_FAILED
        self.affected_part_ids = ()
        self.carried_forward = ()
        self.regenerated_part_ids = ()
        self.segments = ()
        self.chapters = ()
        self.collection_summary = None
        self.unsupported_item_count = 0
        self.visual_fact_count = 0
        self.host_read_upgrades = ()
        self.contract_identity = None
        self.restricted_raw_output = None
        self.diagnostics = (
            PlanningDiagnostic(
                getattr(error, "reason", "visual_reanalysis_input_invalid"), str(error)
            ),
        )

    def build(self) -> VisualReanalysisReport:
        return VisualReanalysisReport(
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
            visual_report_id=self.visual_report_id,
            visual_report_evidence=self.visual_report_evidence,
            affected_part_ids=self.affected_part_ids,
            carried_forward=self.carried_forward,
            regenerated_part_ids=self.regenerated_part_ids,
            segments=self.segments,
            chapters=self.chapters,
            collection_summary=self.collection_summary,
            unsupported_item_count=self.unsupported_item_count,
            visual_fact_count=self.visual_fact_count,
            host_read_upgrades=self.host_read_upgrades,
            contract_identity=self.contract_identity,
            restricted_raw_output=self.restricted_raw_output,
            rendered_report=None,
            diagnostics=self.diagnostics,
        )


def reanalyze_text_with_visual(
    plan_id: str,
    subtitle_report_id: str,
    prior_report_id: str,
    visual_report_id: str,
    project_root: Path,
) -> dict[str, object]:
    """Create one immutable visual Affected-Part re-analysis report (ADR 0046/0047).

    After ``vcp visual-text`` retains classified on-screen evidence, this starts a
    new immutable text-analysis attempt: it revalidates the confirmed RunPlan, the
    subtitle report, the retained prior text-analysis report, and the retained
    visual-text report (by identity and content hash); selects the affected Parts
    as exactly the available Parts carrying new visual evidence; regenerates each
    through the Controlled offline text adapter with its Visual page changes as
    candidate boundary evidence and its admitted page-text facts owned exactly once
    by the resulting segments; carries the unaffected Parts forward with a
    provenance link to the prior report; and recomputes chapters and the collection
    over the combined set. The attempt owns a fresh workspace and never overwrites
    the prior report, so there is no automatic retry.
    """

    report_id = uuid.uuid4().hex
    workspace_path = project_root / "work" / "visual-reanalysis-reports" / report_id
    report_path = workspace_path / "visual-reanalysis-report.json"
    builder = _VisualReanalysisBuilder(
        report_id=report_id,
        plan_id=plan_id,
        subtitle_report_id=subtitle_report_id,
        prior_report_id=prior_report_id,
        visual_report_id=visual_report_id,
        workspace_path=workspace_path,
        report_path=report_path,
    )
    try:
        inputs = _revalidate_inputs(
            plan_id, subtitle_report_id, prior_report_id, visual_report_id, project_root
        )
        builder.bind_inputs(inputs)
        _execute_reanalysis(builder, inputs, project_root)
    except (
        VisualReanalysisError,
        TextReanalysisError,
        TextContractError,
        TextGenerationError,
        TextAggregationError,
        PlanningError,
        TimeValidationError,
        OSError,
        ValueError,
    ) as error:
        builder.fail(error)

    report = _render_and_bind_markdown(builder.build())
    _write_reanalysis_json(report_path, report.as_json())
    return {"status": report.status, "report": report.as_json()}


def _execute_reanalysis(
    builder: _VisualReanalysisBuilder,
    inputs: _VisualReanalysisInputs,
    project_root: Path,
) -> None:
    """Select affected Parts, regenerate them with visual evidence, and recompose."""

    prior = inputs.prior
    available_ids = tuple(loaded.part.part_id for loaded in prior.parts)
    affected = select_visually_affected_parts(inputs.visual, available_ids)
    builder.affected_part_ids = affected
    unaffected = tuple(part_id for part_id in available_ids if part_id not in set(affected))

    carried_forward = carry_forward_parts(prior, unaffected)
    builder.carried_forward = carried_forward

    regeneration = _AffectedRegeneration(
        parts=(),
        page_facts_by_segment={},
        upgrades_by_segment={},
        upgrades=(),
        proposed_entries=_prior_proposed_entries(prior),
    )
    if affected:
        regeneration = _regenerate_affected(builder, inputs, project_root, affected)
    builder.regenerated_part_ids = tuple(part.part_id for part in regeneration.parts)

    order = combined_part_order(
        prior, builder.regenerated_part_ids, carried_forward, prior.omitted_parts
    )
    composition = compose_reanalysis(
        regenerated=regeneration.parts,
        carried_forward=carried_forward,
        omitted_parts=prior.omitted_parts,
        proposed_entries=regeneration.proposed_entries,
        part_order=order,
    )
    segments = _attach_visual_evidence(
        composition.segments,
        regeneration.page_facts_by_segment,
        regeneration.upgrades_by_segment,
    )
    builder.status = composition.status
    builder.segments = segments
    builder.chapters = composition.chapters
    builder.collection_summary = composition.collection_summary
    builder.unsupported_item_count = composition.unsupported_item_count
    builder.visual_fact_count = sum(
        len(facts) for facts in regeneration.page_facts_by_segment.values()
    )
    builder.host_read_upgrades = regeneration.upgrades
    builder.diagnostics = composition.diagnostics


@dataclass
class _AffectedRegeneration:
    """The evidence one visual re-analysis pass produced over the affected Parts."""

    parts: tuple[PartGeneration, ...]
    page_facts_by_segment: dict[tuple[str, int], tuple[VisualPageFact, ...]]
    upgrades_by_segment: dict[tuple[str, int], tuple[HostReadUpgrade, ...]]
    upgrades: tuple[HostReadUpgrade, ...]
    proposed_entries: tuple[ProposedCollectionEntry, ...]


def _regenerate_affected(
    builder: _VisualReanalysisBuilder,
    inputs: _VisualReanalysisInputs,
    project_root: Path,
    affected: Sequence[str],
) -> _AffectedRegeneration:
    """Regenerate each affected Part with its visual evidence in play.

    The controlled text fixture must be bound to a visual re-analysis manifest that
    pins exactly these Parts and their (unchanged) cue basis; its retained output is
    projected through the versioned schema, and each affected Part is regenerated by
    the shared per-Part generation -- with its Visual page changes passed as extra
    candidate boundaries -- so it obeys every Phase 6 contract. Each Part's admitted
    page-text facts are then owned exactly once by the resulting segments, and its
    background-UI comments are run through the versioned Host-read comment upgrade
    (ADR 0049); any comment the host read or selected becomes an upgraded fact owned
    exactly once too. A missing fixture, an invalid projection, or a malformed
    upgrade ruleset fails the attempt before any evidence composes.
    """

    upgrade_rules = load_host_read_upgrade_ruleset(project_root)
    prior_bases = inputs.prior.part_cue_bases
    affected_bases = {part_id: prior_bases[part_id] for part_id in affected}
    contracts = revalidate_text_generation_contracts(project_root)
    manifest_document = visual_reanalysis_manifest_document(
        affected_bases,
        prior_report_id=inputs.prior.report_id,
        visual_report_id=inputs.visual.report_id,
    )
    manifest_sha = visual_reanalysis_manifest_sha256(manifest_document)
    controlled = load_controlled_generation(
        contracts.controlled_adapter.document, project_root, manifest_sha
    )
    if controlled is None:
        raise VisualReanalysisError(
            "visual_reanalysis_regeneration_unavailable",
            "No Controlled offline text adapter fixture is bound to this visual re-analysis.",
        )
    if controlled.input_fixture_sha256 != manifest_sha:
        raise VisualReanalysisError(
            "visual_reanalysis_input_mismatch",
            "Controlled text fixture is not bound to these affected visual re-analysis Parts.",
        )
    manifest_path = builder.workspace_path / "provenance" / "visual-reanalysis-manifest.json"
    _write_reanalysis_json(manifest_path, manifest_document)
    builder.restricted_raw_output = record_restricted_raw_output(
        builder.workspace_path, "visual-reanalysis-generation", controlled.raw_output
    ).as_json()
    projection = project_text_model_output(
        _decode_generation_output(controlled.raw_output), contracts
    )
    if projection.projection is None:
        message = (
            projection.diagnostic.message
            if projection.diagnostic is not None
            else "The controlled visual re-analysis output is invalid."
        )
        raise VisualReanalysisError("model_output_invalid", message)
    result = projection.projection.get("result")
    result_mapping = result if isinstance(result, Mapping) else {}
    result_parts = index_result_parts(result_mapping)

    visual_by_part = inputs.visual.parts_by_id
    regenerated: list[PartGeneration] = []
    page_facts_by_segment: dict[tuple[str, int], tuple[VisualPageFact, ...]] = {}
    upgrades_by_segment: dict[tuple[str, int], tuple[HostReadUpgrade, ...]] = {}
    upgrades: list[HostReadUpgrade] = []
    for part_id in affected:
        cue_ids = prior_bases[part_id]
        retained_cues = inputs.cues_by_part.get(part_id, ())
        timed = _timed_cues(part_id, cue_ids, retained_cues)
        evidence = visual_by_part[part_id]
        candidates = visual_boundary_candidates(part_id, evidence.page_change_times, timed)
        part = generate_part(
            LoadedPart(part_id=part_id, track_id="visual-reanalysis", cue_ids=cue_ids),
            result_parts.get(part_id, {}),
            extra_boundaries=candidates,
        )
        regenerated.append(part)
        _own_items(part, timed, evidence.page_facts, page_facts_by_segment)
        part_upgrades = evaluate_host_read_upgrades(
            comments=evidence.background_ui_comments,
            cues=_cue_texts(retained_cues),
            rules=upgrade_rules,
        )
        upgrades.extend(part_upgrades)
        _own_items(part, timed, part_upgrades, upgrades_by_segment)

    builder.contract_identity = {
        "text_generation_contracts": contracts.as_json(),
        "input_manifest": {
            **input_evidence(manifest_path).as_json(),
            "sha256": manifest_sha,
        },
        "controlled_adapter_identity": contracts.controlled_adapter.version,
        "host_read_upgrade_rules": upgrade_rules.as_json(),
    }
    return _AffectedRegeneration(
        parts=tuple(regenerated),
        page_facts_by_segment=page_facts_by_segment,
        upgrades_by_segment=upgrades_by_segment,
        upgrades=tuple(upgrades),
        proposed_entries=proposed_collection_entries(result_mapping),
    )


def _own_items(
    part: PartGeneration,
    timed: Sequence[TimedCue],
    items: Sequence[_PtsItemT],
    sink: dict[tuple[str, int], tuple[_PtsItemT, ...]],
) -> None:
    """Own one Part's PTS-bearing visual items by exactly one of its segments.

    An item is owned by the segment in effect at its PTS (``_assign_by_pts``). Should
    a Part's cues carry no retained timing at all -- a drift condition that never
    arises in the offline synthetic phase -- every item still falls to the Part's
    first segment rather than being silently dropped, preserving both exactly-once
    ownership and full retention. Both page-text facts and upgraded Host-read
    comments flow through this one owner, so every visual item is owned identically.
    """

    if not items or not part.segments:
        return
    start_by_cue = {cue.cue_id: cue.start for cue in timed}
    segment_starts: list[tuple[int, ExactTime]] = []
    for segment in part.segments:
        starts = [start_by_cue[cue_id] for cue_id in segment.cue_ids if cue_id in start_by_cue]
        if starts:
            segment_starts.append((segment.ordinal, min(starts, key=lambda t: t.as_fraction())))
    if not segment_starts:
        sink[(part.part_id, part.segments[0].ordinal)] = tuple(items)
        return
    for ordinal, owned in _assign_by_pts(items, segment_starts).items():
        sink[(part.part_id, ordinal)] = owned


def _attach_visual_evidence(
    segments: Sequence[dict[str, object]],
    page_facts_by_segment: Mapping[tuple[str, int], tuple[VisualPageFact, ...]],
    upgrades_by_segment: Mapping[tuple[str, int], tuple[HostReadUpgrade, ...]],
) -> tuple[dict[str, object], ...]:
    """Attach each regenerated segment's owned visual evidence to its record.

    Only a freshly regenerated segment can own new visual evidence; a carried-forward
    segment keeps its retained content untouched. Both the cited page facts and the
    upgraded Host-read comments ride beside the segment's cue-bound content and never
    displace subtitle-derived fields, so the renderer's rendition is unchanged.
    """

    attached: list[dict[str, object]] = []
    for segment in segments:
        part_id = segment.get("part_id")
        ordinal = segment.get("ordinal")
        record = dict(segment)
        if (
            segment.get("provenance") == PROVENANCE_REGENERATED
            and isinstance(part_id, str)
            and isinstance(ordinal, int)
        ):
            facts = page_facts_by_segment.get((part_id, ordinal), ())
            record["visual_page_facts"] = [fact.as_json() for fact in facts]
            upgrades = upgrades_by_segment.get((part_id, ordinal), ())
            record["host_read_comments"] = [upgrade.as_json() for upgrade in upgrades]
        attached.append(record)
    return tuple(attached)


def _timed_cues(
    part_id: str, cue_ids: Sequence[str], retained: Sequence[RetainedSubtitleCue]
) -> tuple[TimedCue, ...]:
    """Pair each of a Part's ordered cue identities with its retained start time.

    Cue identities come from the prior report (the cue basis is unchanged); their
    start times come from the retained subtitle evidence. A cue whose start time is
    unavailable is dropped from the timing view -- it simply contributes no
    page-change split and owns no fact by time -- rather than failing the attempt.
    """

    start_by_id = {cue.cue_identity: cue.interval.start for cue in retained}
    return tuple(
        TimedCue(cue_id=cue_id, start=start_by_id[cue_id])
        for cue_id in cue_ids
        if cue_id in start_by_id
    )


def _cue_texts(retained: Sequence[RetainedSubtitleCue]) -> tuple[CueText, ...]:
    """Project retained subtitle cues into the identity/interval/text the upgrade reads.

    The Host-read comment upgrade needs each cue's verbatim text and full raw-PTS
    interval (not just its start) to test whether a nearby cue read a background-UI
    comment, so it consumes this richer view rather than the boundary-only
    ``TimedCue``. Cue identities are the same the prior report's segments own.
    """

    return tuple(
        CueText(
            cue_id=cue.cue_identity,
            start=cue.interval.start,
            end=cue.interval.end,
            text=cue.text,
        )
        for cue in retained
    )


def _prior_proposed_entries(
    prior: LoadedTextAnalysisReport,
) -> tuple[ProposedCollectionEntry, ...]:
    """Re-propose the prior collection entries when no Part is regenerated."""

    if prior.collection_summary is None:
        return ()
    return tuple(
        ProposedCollectionEntry(segment_refs=entry.segment_refs, text=entry.text)
        for entry in prior.collection_summary.entries
    )


# --- Input revalidation ------------------------------------------------------


def _revalidate_inputs(
    plan_id: str,
    subtitle_report_id: str,
    prior_report_id: str,
    visual_report_id: str,
    project_root: Path,
) -> _VisualReanalysisInputs:
    """Revalidate every bound input before a visual re-analysis attempt proceeds.

    The confirmed RunPlan and its inspection evidence, the subtitle report identity,
    the retained prior text-analysis report (hash-verified), and the retained
    visual-text report (hash-verified) must all agree on the plan and subtitle
    identities; any drift or mismatch blocks the attempt as ``failed`` before
    regeneration. Each affected Part's retained subtitle cue timing is loaded from
    the confirmed subtitle report so page changes and facts align to real cues.
    """

    plan_path = project_root / "plans" / plan_id / "run-plan.json"
    plan = load_run_plan(plan_path)
    if plan.plan_id != plan_id:
        raise VisualReanalysisError(
            "run_plan_not_confirmed", "RunPlan identity does not match the requested plan ID."
        )
    confirmed_report = load_plan_report(
        project_root / "plans" / "reports" / plan.report_id / "plan-report.json"
    )
    if not confirmed_plan_matches(confirmed_report, plan):
        raise VisualReanalysisError(
            "run_plan_not_confirmed", "RunPlan evidence does not match a confirmed PlanReport."
        )
    revalidate_confirmed_inspection_evidence(
        confirmed_report,
        plan,
        drift_error=lambda: VisualReanalysisError(
            "inspection_evidence_changed",
            "PlanReport inspection evidence no longer matches the confirmed RunPlan.",
        ),
    )
    subtitle_id = validated_report_id(
        subtitle_report_id,
        invalid_error=lambda: VisualReanalysisError(
            "subtitle_report_invalid", "Subtitle candidate report ID must be a UUID."
        ),
    )
    subtitle_path = _subtitle_report_path(project_root, plan.source_artifacts, subtitle_id)
    subtitle_report = _load_subtitle_report(subtitle_path, subtitle_id, plan.plan_id)

    prior_path = _prior_report_path(project_root, prior_report_id)
    prior = _load_prior_report(prior_path)
    if prior.plan_id != plan.plan_id or prior.subtitle_report_id != subtitle_id:
        raise VisualReanalysisError(
            "visual_reanalysis_report_mismatch",
            "Prior text-analysis report does not belong to this RunPlan and subtitle report.",
        )

    visual_path = _visual_report_path(project_root, visual_report_id)
    visual = load_visual_text_report(visual_path)
    if visual.plan_id != plan.plan_id:
        raise VisualReanalysisError(
            "visual_reanalysis_report_mismatch",
            "Visual-text report does not belong to this RunPlan.",
        )

    available_ids = {loaded.part.part_id for loaded in prior.parts}
    affected = select_visually_affected_parts(visual, available_ids)
    cues_by_part = _load_affected_cues(plan, subtitle_report, affected)

    return _VisualReanalysisInputs(
        plan=plan,
        plan_path=plan_path,
        subtitle_report_id=subtitle_id,
        subtitle_path=subtitle_path,
        prior=prior,
        visual=visual,
        visual_path=visual_path,
        cues_by_part=cues_by_part,
    )


def _load_affected_cues(
    plan: RunPlan, report: SubtitleCandidateReport, affected: Sequence[str]
) -> dict[str, tuple[RetainedSubtitleCue, ...]]:
    """Load each affected Part's retained subtitle cue timing (identity + interval).

    Mirrors the enhancement track revalidation: the Primary subtitle candidate per
    Part is located in the confirmed subtitle report, its retained source-candidate
    evidence is hash-verified, and its cues are loaded with intervals. The cue
    identities match the prior report's cue basis exactly, so page changes and OCR
    facts align to the same cues the segments own.
    """

    plan_part_ids = {artifact.source_id for artifact in plan.source_artifacts}
    selections = {selection.source_id: selection.stream_index for selection in report.selections}
    cues_by_part: dict[str, tuple[RetainedSubtitleCue, ...]] = {}
    for part_id in affected:
        if part_id not in plan_part_ids:
            raise VisualReanalysisError(
                "visual_reanalysis_part_unknown",
                f"Affected Part {part_id!r} is not a Part of this RunPlan.",
            )
        valid = [
            candidate
            for candidate in report.candidates
            if candidate.source_id == part_id and candidate.state is CandidateState.VALID
        ]
        selected = _selected_candidate(part_id, valid, selections.get(part_id))
        if selected.source_candidate_path is None or selected.source_candidate_sha256 is None:
            raise VisualReanalysisError(
                "visual_reanalysis_track_changed",
                f"Affected Part {part_id!r} has incomplete retained subtitle evidence.",
            )
        candidate_path = Path(selected.source_candidate_path)
        if input_evidence(candidate_path).sha256 != selected.source_candidate_sha256:
            raise VisualReanalysisError(
                "visual_reanalysis_track_changed",
                f"Affected Part {part_id!r} subtitle evidence hash no longer matches.",
            )
        cues_by_part[part_id] = load_retained_subtitle_cues(
            candidate_path, part_id=part_id, stream_index=selected.stream_index
        )
    return cues_by_part


def _selected_candidate(
    part_id: str, valid: list[SubtitleCandidate], selected_stream_index: int | None
) -> SubtitleCandidate:
    if not valid:
        raise VisualReanalysisError(
            "visual_reanalysis_part_unavailable",
            f"Affected Part {part_id!r} has no valid Primary subtitle track.",
        )
    if len(valid) == 1:
        return valid[0]
    match = next(
        (candidate for candidate in valid if candidate.stream_index == selected_stream_index), None
    )
    if match is None:
        raise VisualReanalysisError(
            "visual_reanalysis_selection_unresolved",
            f"Affected Part {part_id!r} has multiple valid tracks without a retained selection.",
        )
    return match


def _load_prior_report(prior_path: Path) -> LoadedTextAnalysisReport:
    try:
        return load_text_analysis_report(prior_path)
    except TextReanalysisError as error:
        raise VisualReanalysisError(error.reason, error.message) from error


def _load_subtitle_report(path: Path, subtitle_id: str, plan_id: str) -> SubtitleCandidateReport:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VisualReanalysisError(
            "subtitle_report_invalid", "Subtitle candidate report cannot be read."
        ) from error
    if not isinstance(decoded, Mapping) or decoded.get("report_id") != subtitle_id:
        raise VisualReanalysisError(
            "subtitle_report_mismatch", "Subtitle candidate report identity does not match."
        )
    if decoded.get("plan_id") != plan_id:
        raise VisualReanalysisError(
            "subtitle_report_mismatch", "Subtitle candidate report does not belong to this RunPlan."
        )
    return SubtitleCandidateReport.from_json(decoded, path)


def _prior_report_path(project_root: Path, report_id: str) -> Path:
    validated_id = validated_report_id(
        report_id,
        invalid_error=lambda: VisualReanalysisError(
            "text_analysis_report_invalid", "Text analysis report ID must be a UUID."
        ),
    )
    return (
        project_root / "work" / "text-analysis-reports" / validated_id / "text-analysis-report.json"
    )


def _visual_report_path(project_root: Path, report_id: str) -> Path:
    validated_id = validated_report_id(
        report_id,
        invalid_error=lambda: VisualReanalysisError(
            "visual_text_report_invalid", "Visual-text report ID must be a UUID."
        ),
    )
    return project_root / "work" / "visual-text-reports" / validated_id / "visual-report.json"


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


def _render_and_bind_markdown(report: VisualReanalysisReport) -> VisualReanalysisReport:
    """Render the deterministic Markdown rendition and bind its version and hash."""

    rendition = render_text_analysis_markdown(report.as_json())
    markdown_path = report.workspace_path / "visual-reanalysis-report.md"
    if markdown_path.exists():
        if markdown_path.read_text(encoding="utf-8") != rendition.text:
            raise VisualReanalysisError(
                "visual_reanalysis_report_conflict",
                f"Immutable Markdown rendition differs: {markdown_path}",
            )
    else:
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(rendition.text, encoding="utf-8")
    rendered_report = dict(rendition.as_json())
    rendered_report["path"] = markdown_path.as_posix()
    return replace(report, rendered_report=rendered_report)


# --- Small typed readers -----------------------------------------------------


def _read_report_evidence(report_path: Path) -> InputEvidence:
    try:
        return input_evidence(report_path)
    except OSError as error:
        raise VisualReanalysisError(
            "visual_text_report_unloadable", "Retained visual-text report cannot be read."
        ) from error


def _read_report_document(report_path: Path) -> Mapping[str, object]:
    try:
        decoded = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VisualReanalysisError(
            "visual_text_report_unloadable", "Retained visual-text report cannot be read."
        ) from error
    if not isinstance(decoded, Mapping):
        raise VisualReanalysisError(
            "visual_text_report_unloadable", "Retained visual-text report is not a JSON object."
        )
    return decoded


def _required_str(document: Mapping[str, object], field: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise VisualReanalysisError(
            "visual_text_report_unloadable", f"Retained visual-text report omits its {field}."
        )
    return value


def _object_sequence(value: object, label: str) -> tuple[Mapping[str, object], ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise VisualReanalysisError(
            "visual_text_report_drifted", f"A retained {label} list is malformed."
        )
    items: list[Mapping[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise VisualReanalysisError(
                "visual_text_report_drifted", f"A retained {label} is not an object."
            )
        items.append(item)
    return tuple(items)


def _time_from_json(value: object) -> ExactTime:
    numerator = value.get("numerator") if isinstance(value, Mapping) else None
    denominator = value.get("denominator") if isinstance(value, Mapping) else None
    if not _is_int(numerator) or not _is_int(denominator) or denominator == 0:
        raise VisualReanalysisError(
            "visual_text_report_drifted", "A retained time value is malformed."
        )
    return ExactTime(numerator, denominator)


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
        conflict_error=lambda message: VisualReanalysisError(
            "visual_reanalysis_report_conflict", message
        ),
    )


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_real(value: object) -> TypeGuard[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool)
