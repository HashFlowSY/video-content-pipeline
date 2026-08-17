"""Visual-Text Context: the explicit visual-text command boundary (Phase 8).

Ticket 02 establishes the sole start boundary for a visual-text attempt. ``vcp
visual-text <plan-id>`` runs on an explicitly given scope (``--all``, named
Parts, or Part-relative second ranges); an unscoped invocation is an error and
creates no workspace. Before any work, the attempt exactly revalidates the
confirmed RunPlan and SourceArtifact hashes (never reading source media), the
retained inspection evidence, the versioned detection/sampling/classification
rule identities, and every named Part and range against retained Part identities
and actual video-stream coverage. Any drift blocks the attempt with a structured
reason. Each attempt owns a fresh Immutable visual-text workspace and an
authoritative ``visual-report.json`` that never overwrites prior evidence.

Detection, sampling, OCR, and classification arrive in later tickets; this ticket
delivers the boundary, scope revalidation, and the immutable report, so the
terminal outcomes here are a ``failed`` report on drift or invalid scope and the
Model-acquisition-required visual-text result when no OCR capability exists. No
model is downloaded or executed and no frame of user media is extracted.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from hashlib import sha256
from pathlib import Path

from video_content_pipeline.evidence import (
    InputEvidence,
    input_evidence,
    validated_report_id,
    write_json_once,
)
from video_content_pipeline.inspection import PlanInspectionEvidence
from video_content_pipeline.planning import (
    PlanningDiagnostic,
    PlanningError,
    PlanReport,
    RunPlan,
    confirmed_plan_matches,
    load_plan_report,
    load_run_plan,
    revalidate_confirmed_inspection_evidence,
)
from video_content_pipeline.probe import project_probe_document
from video_content_pipeline.real_engine_adapter import (
    RealEngineSelection,
    dispatch_real_stage,
)
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval, TimeValidationError
from video_content_pipeline.visual_page_index import (
    FrameMetricFixture,
    OcrResourcePlan,
    OcrResourcePolicy,
    PageIndexRules,
    PartPageIndex,
    build_part_page_index,
    frames_in_scope,
    load_frame_metric_fixture,
    load_ocr_resource_policy,
    load_page_index_rules,
    plan_ocr_resources,
)
from video_content_pipeline.visual_text import (
    VisualTextCapabilityReport,
    VisualTextError,
    evaluate_ocr_capabilities,
    offline_guarantees,
)
from video_content_pipeline.visual_text_classification import (
    ClassificationRuleset,
    PartClassificationResult,
    classify_ocr_items,
    load_classification_ruleset,
)
from video_content_pipeline.visual_text_contracts import (
    ProjectedOcrItem,
    load_controlled_ocr_fixture,
    ocr_input_manifest_document,
    ocr_input_manifest_sha256,
    project_ocr_output,
    retain_restricted_ocr_output,
    revalidate_ocr_contracts,
)
from video_content_pipeline.visual_text_gates import (
    GatedOcrItem,
    OcrItemGateResult,
    RejectedOcrItem,
    gate_ocr_items,
)
from video_content_pipeline.visual_text_suspicion import (
    AudioActivityRegion,
    EmbeddedMediaRuleset,
    PartEmbeddedMediaResult,
    audio_activity_regions,
    detect_embedded_media,
    load_embedded_media_ruleset,
)

# The explicit decisions that continue a retained visual-text decision pause. The
# affirmative OCR decision runs OCR; the declining decision keeps the page index at
# zero cost; the resource-envelope decision follows the Phase 7 resume convention.
OCR_RESOURCE_CONFIRMATION_DECISION = "ocr_resource_confirmed"
OCR_DECLINE_DECISION = "ocr_declined"
RESOURCE_ENVELOPE_DECISION = "resource_configuration_changed"

# The recorded pause reasons carried in a report's ``required_decision`` block.
_OCR_RESOURCE_CONFIRMATION_REASON = "ocr_resource_confirmation"
_RESOURCE_ENVELOPE_REASON = "resource_envelope_exceeded"

# The invalidation reason recorded when untrusted OCR output fails the projection.
_MODEL_OUTPUT_INVALID = "model_output_invalid"
# The rejection reason for an OCR item naming a Part with no page index this attempt.
_OCR_ITEM_UNKNOWN_PART = "ocr_item_unknown_part"


# --- Scope selectors --------------------------------------------------------


@dataclass(frozen=True)
class AllScope:
    """Select every retained Part with determinate video coverage."""


@dataclass(frozen=True)
class PartScope:
    """Select one whole named Part."""

    part_id: str


@dataclass(frozen=True)
class RangeScope:
    """Select a Part-relative second range within one named Part."""

    part_id: str
    start: ExactTime
    end: ExactTime


VisualTextScopeSelector = AllScope | PartScope | RangeScope


def parse_visual_text_scope(
    all_parts: bool,
    part_values: Sequence[str],
    range_values: Sequence[str],
) -> tuple[VisualTextScopeSelector, ...]:
    """Parse ``--all``/``--part``/``--range`` into typed selectors, or reject an empty scope.

    ``--all`` selects the whole collection and cannot be combined with a specific
    Part or range. ``--part`` is ``<part-id>``; ``--range`` is
    ``<part-id>:<start>-<end>`` in Part-relative decimal seconds. An invocation with
    no scope argument raises ``visual_text_scope_missing`` -- the scope is never
    defaulted -- so the caller can refuse it before any workspace exists.
    """

    if all_parts and (part_values or range_values):
        raise VisualTextError(
            "visual_text_scope_invalid",
            "--all cannot be combined with --part or --range.",
        )
    if all_parts:
        return (AllScope(),)
    selectors: list[VisualTextScopeSelector] = []
    for value in part_values:
        part_id = value.strip()
        if not part_id:
            raise VisualTextError("visual_text_selector_invalid", "A --part selector is empty.")
        selectors.append(PartScope(part_id))
    for value in range_values:
        selectors.append(_parse_range_selector(value))
    if not selectors:
        raise VisualTextError(
            "visual_text_scope_missing",
            "vcp visual-text requires an explicit --all, --part, or --range scope.",
        )
    return tuple(selectors)


def _parse_range_selector(value: str) -> RangeScope:
    part_id, sep, span = value.partition(":")
    if not part_id or not sep or "-" not in span:
        raise VisualTextError(
            "visual_text_selector_invalid",
            "A --range selector must be <part-id>:<start>-<end> in Part-relative seconds.",
        )
    start_text, _, end_text = span.partition("-")
    return RangeScope(part_id, _seconds_to_exact(start_text), _seconds_to_exact(end_text))


def _seconds_to_exact(text: str) -> ExactTime:
    try:
        fraction = Fraction(text.strip())
    except (ValueError, ZeroDivisionError) as error:
        raise VisualTextError(
            "visual_text_selector_invalid", f"A --range bound {text!r} is not a number."
        ) from error
    return ExactTime(fraction.numerator, fraction.denominator)


# --- Resolved scope ---------------------------------------------------------


@dataclass(frozen=True)
class PartVisualScope:
    """The merged Part-relative intervals a visual-text attempt covers in one Part."""

    part_id: str
    coverage_start: ExactTime
    coverage_duration: ExactTime
    intervals: tuple[HalfOpenInterval, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "part_id": self.part_id,
            "coverage_start": _time_as_json(self.coverage_start),
            "coverage_duration": _time_as_json(self.coverage_duration),
            "intervals": [
                {"start": _time_as_json(interval.start), "end": _time_as_json(interval.end)}
                for interval in self.intervals
            ],
        }


def resolve_visual_text_scope(
    selectors: Sequence[VisualTextScopeSelector],
    *,
    plan_part_ids: set[str],
    coverage_by_part: Mapping[str, HalfOpenInterval],
) -> tuple[PartVisualScope, ...]:
    """Revalidate every selector against retained Part identities and video coverage.

    ``--all`` expands to every Part with determinate video coverage; a named Part
    must be a Part of this RunPlan and carry determinate video coverage; a range
    must be a positive half-open interval inside ``[0, duration]`` on the
    Part-relative clock. Resolved intervals are merged per Part into the minimal
    non-overlapping cover so overlapping selectors sample a passage once. Every
    failure raises a stable reason -- the scope is validated whole before any frame
    work.
    """

    intervals_by_part: dict[str, list[HalfOpenInterval]] = {}
    order: list[str] = []
    for selector in _expanded_selectors(selectors, coverage_by_part):
        part_id = selector.part_id
        coverage = _part_coverage(part_id, plan_part_ids, coverage_by_part)
        interval = _selector_interval(selector, coverage)
        if part_id not in intervals_by_part:
            intervals_by_part[part_id] = []
            order.append(part_id)
        intervals_by_part[part_id].append(interval)
    return tuple(
        PartVisualScope(
            part_id=part_id,
            coverage_start=coverage_by_part[part_id].start,
            coverage_duration=_duration(coverage_by_part[part_id]),
            intervals=_merge_intervals(intervals_by_part[part_id]),
        )
        for part_id in order
    )


def _expanded_selectors(
    selectors: Sequence[VisualTextScopeSelector],
    coverage_by_part: Mapping[str, HalfOpenInterval],
) -> tuple[PartScope | RangeScope, ...]:
    if any(isinstance(selector, AllScope) for selector in selectors):
        covered = tuple(PartScope(part_id) for part_id in sorted(coverage_by_part))
        if not covered:
            raise VisualTextError(
                "visual_text_scope_empty",
                "No retained Part has determinate video coverage to sample.",
            )
        return covered
    return tuple(selector for selector in selectors if not isinstance(selector, AllScope))


def _part_coverage(
    part_id: str,
    plan_part_ids: set[str],
    coverage_by_part: Mapping[str, HalfOpenInterval],
) -> HalfOpenInterval:
    if part_id not in plan_part_ids:
        raise VisualTextError(
            "visual_text_part_unknown",
            f"Visual-text Part {part_id!r} is not a Part of this RunPlan.",
        )
    coverage = coverage_by_part.get(part_id)
    if coverage is None:
        raise VisualTextError(
            "visual_text_part_uncovered",
            f"Visual-text Part {part_id!r} has no determinate video coverage.",
        )
    return coverage


def _selector_interval(
    selector: PartScope | RangeScope, coverage: HalfOpenInterval
) -> HalfOpenInterval:
    duration = _duration(coverage)
    if isinstance(selector, PartScope):
        return HalfOpenInterval(ExactTime(0), duration)
    try:
        interval = HalfOpenInterval(selector.start, selector.end)
    except TimeValidationError as error:
        raise VisualTextError(
            "visual_text_range_invalid", "A --range must be a positive half-open interval."
        ) from error
    if interval.start.as_fraction() < 0 or interval.end.as_fraction() > duration.as_fraction():
        raise VisualTextError(
            "visual_text_range_out_of_coverage",
            "A --range falls outside the Part's determinate video coverage.",
        )
    return interval


def _merge_intervals(intervals: Sequence[HalfOpenInterval]) -> tuple[HalfOpenInterval, ...]:
    """Return the minimal non-overlapping cover of ``intervals``, sorted by start."""

    ordered = sorted(
        intervals, key=lambda interval: (interval.start.as_fraction(), interval.end.as_fraction())
    )
    merged: list[HalfOpenInterval] = []
    for interval in ordered:
        if merged and interval.start.as_fraction() <= merged[-1].end.as_fraction():
            if interval.end.as_fraction() > merged[-1].end.as_fraction():
                merged[-1] = HalfOpenInterval(merged[-1].start, interval.end)
            continue
        merged.append(interval)
    return tuple(merged)


def _duration(coverage: HalfOpenInterval) -> ExactTime:
    return coverage.end - coverage.start


# --- Per-Part video coverage ------------------------------------------------


def part_video_coverage(confirmed_report: PlanReport) -> dict[str, HalfOpenInterval]:
    """Return each Part's determinate video-stream coverage from confirmed evidence.

    Visual-text samples video, so a Part's clock is its first video stream's
    observed coverage envelope (raw PTS). A Part with no video stream, or one whose
    video coverage is indeterminate, is simply absent -- named-scope revalidation
    then reports it as uncovered rather than guessing a range.
    """

    coverage: dict[str, HalfOpenInterval] = {}
    for evidence in confirmed_report.inspection_evidence:
        interval = _video_coverage_interval(evidence)
        if interval is not None:
            coverage[evidence.source_id] = interval
    return coverage


def _video_coverage_interval(evidence: PlanInspectionEvidence) -> HalfOpenInterval | None:
    if evidence.structural_document is None:
        return None
    projection = project_probe_document(evidence.structural_document).projection
    if projection is None:
        return None
    coverage_by_stream = dict(evidence.coverage_by_stream)
    for stream in projection.streams:
        if stream.codec_type != "video":
            continue
        stream_coverage = coverage_by_stream.get(stream.index)
        if stream_coverage is None or stream_coverage.coverage is None:
            return None
        return stream_coverage.coverage
    return None


# --- Versioned rule identities ----------------------------------------------


@dataclass(frozen=True)
class VisualTextRuleVersions:
    """The versioned detection/sampling/classification rule identities and fingerprint."""

    detection: str
    sampling: str
    classification: str
    fingerprint: str

    def as_json(self) -> dict[str, object]:
        return {
            "detection": self.detection,
            "sampling": self.sampling,
            "classification": self.classification,
            "fingerprint": self.fingerprint,
        }


def load_visual_text_rule_versions(project_root: Path) -> VisualTextRuleVersions:
    """Load the versioned visual-text rules, or reject a missing or malformed file.

    Detection, sampling, and classification are deterministic and versioned (ADR
    0047). The command records these identities in provenance so every selection
    decision replays; a missing or malformed rules file raises
    ``visual_text_rules_invalid`` before any attempt proceeds.
    """

    path = project_root / "config" / "visual-text" / "rules.json"
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VisualTextError(
            "visual_text_rules_invalid", f"Visual-text rules cannot be read: {path}"
        ) from error
    if not isinstance(decoded, Mapping):
        raise VisualTextError("visual_text_rules_invalid", "Visual-text rules must be an object.")
    detection = _rule_version(decoded, "detection")
    sampling = _rule_version(decoded, "sampling")
    classification = _rule_version(decoded, "classification")
    fingerprint = sha256(
        json.dumps(decoded, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return VisualTextRuleVersions(detection, sampling, classification, fingerprint)


def _rule_version(document: Mapping[str, object], key: str) -> str:
    section = document.get(key)
    if not isinstance(section, Mapping):
        raise VisualTextError(
            "visual_text_rules_invalid", f"Visual-text rules need a {key!r} object."
        )
    version = section.get("version")
    if not isinstance(version, str) or not version:
        raise VisualTextError(
            "visual_text_rules_invalid", f"Visual-text {key!r} rules need a version string."
        )
    return version


# --- Report -----------------------------------------------------------------


class VisualTextReportStatus(StrEnum):
    """The recorded outcome of one visual-text attempt.

    ``failed`` retains revalidation drift or an invalid scope before any evidence
    exists. ``awaiting_ocr_resource_confirmation`` and ``resource_envelope_exceeded``
    are the immutable decision pauses that stop the attempt after detection: the
    first presents the OCR resource plan for an explicit affirmative decision, the
    second reports a plan over the approved envelope. ``partial`` retains a page
    index after the user declined OCR (zero visual facts), and
    ``model_acquisition_required`` is the terminal outcome once OCR is authorized but
    no eligible OCR capability is locally available. ``complete`` arrives once OCR
    executes in a later ticket.
    """

    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    AWAITING_OCR_RESOURCE_CONFIRMATION = "awaiting_ocr_resource_confirmation"
    RESOURCE_ENVELOPE_EXCEEDED = "resource_envelope_exceeded"
    MODEL_ACQUISITION_REQUIRED = "model_acquisition_required"


@dataclass
class _VisualTextInputs:
    """The fully revalidated inputs threaded from revalidation into finalization."""

    plan: RunPlan
    plan_path: Path
    confirmed_report_path: Path
    rule_versions: VisualTextRuleVersions
    page_index_rules: PageIndexRules
    ocr_resource_policy: OcrResourcePolicy
    classification_rules: ClassificationRuleset
    embedded_media_rules: EmbeddedMediaRuleset
    scope: tuple[PartVisualScope, ...]
    limitations: tuple[PlanningDiagnostic, ...]
    capability_report: VisualTextCapabilityReport
    audio_report_id: str | None
    audio_report_evidence: InputEvidence | None
    # None means no Audio analysis report was supplied (picture-only suspicion basis);
    # a mapping means a revalidated report was supplied (picture-plus-audio), keyed by
    # Part with that Part's active-voice regions (empty for a model-gated report).
    audio_regions_by_part: Mapping[str, tuple[AudioActivityRegion, ...]] | None


@dataclass(frozen=True)
class _PartFrameInventory:
    """A retained per-Part page index paired with its workspace inventory pointer.

    The full Retained frame inventory is written to a workspace-internal artifact
    (never a formal output); the report embeds the pages, appearance records, and
    frame records together with the artifact's hash pointer and the hash-pinned
    frame-metric fixture evidence.
    """

    index: PartPageIndex
    fixture_evidence: InputEvidence
    inventory: InputEvidence

    def as_json(self) -> dict[str, object]:
        return {
            **self.index.as_json(),
            "frame_metric_fixture": self.fixture_evidence.as_json(),
            "inventory_artifact": {
                "path": self.inventory.path.as_posix(),
                "sha256": self.inventory.sha256,
                "byte_count": self.inventory.byte_count,
                "published": False,
            },
        }


@dataclass(frozen=True)
class VisualTextReport:
    """Immutable machine-readable result of one visual-text attempt."""

    report_id: str
    plan_id: str
    status: VisualTextReportStatus
    scope_mode: str
    workspace_path: Path
    report_path: Path
    plan_evidence: InputEvidence | None
    confirmed_report_evidence: InputEvidence | None
    resumed_from_report: InputEvidence | None
    resumed_from_report_id: str | None
    resumption_decision: str | None
    audio_report_id: str | None
    audio_report_evidence: InputEvidence | None
    rule_versions: VisualTextRuleVersions | None
    scope: tuple[PartVisualScope, ...]
    capability: VisualTextCapabilityReport | None
    page_index: tuple[_PartFrameInventory, ...]
    ocr_resource_plan: OcrResourcePlan | None
    ocr_evidence: dict[str, object] | None
    classification: dict[str, object] | None
    suspected_embedded_media: dict[str, object] | None
    required_decision: dict[str, object] | None
    limitations: tuple[PlanningDiagnostic, ...]
    diagnostics: tuple[PlanningDiagnostic, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "report_id": self.report_id,
            "plan_id": self.plan_id,
            "status": self.status.value,
            "workspace_path": self.workspace_path.as_posix(),
            "report_path": self.report_path.as_posix(),
            "audio_report_id": self.audio_report_id,
            "input_evidence": {
                "run_plan": _evidence_json(self.plan_evidence),
                "confirmed_plan_report": _evidence_json(self.confirmed_report_evidence),
                "resumed_from_report": _evidence_json(self.resumed_from_report),
                "resumed_from_report_id": self.resumed_from_report_id,
                "resumption_decision": self.resumption_decision,
                "audio_analysis_report": _evidence_json(self.audio_report_evidence),
            },
            "scope": {
                "requested": self.scope_mode,
                "parts": [part.as_json() for part in self.scope],
            },
            "capability": self.capability.as_json() if self.capability is not None else None,
            "rule_versions": (
                self.rule_versions.as_json() if self.rule_versions is not None else None
            ),
            "page_index": {
                "parts": [inventory.as_json() for inventory in self.page_index],
            },
            "ocr_resource": (
                self.ocr_resource_plan.as_json() if self.ocr_resource_plan is not None else None
            ),
            "ocr_evidence": self.ocr_evidence,
            "classification": self.classification,
            "suspected_embedded_media": self.suspected_embedded_media,
            "required_decision": self.required_decision,
            "limitations": [limitation.as_json() for limitation in self.limitations],
            "diagnostics": [diagnostic.as_json() for diagnostic in self.diagnostics],
            "guarantees": offline_guarantees(),
        }


@dataclass
class _ReportBuilder:
    report_id: str
    plan_id: str
    scope_mode: str
    workspace_path: Path
    report_path: Path
    resumed_from_report: InputEvidence | None = None
    resumed_from_report_id: str | None = None
    resumption_decision: str | None = None
    audio_report_id: str | None = None
    audio_report_evidence: InputEvidence | None = None
    status: VisualTextReportStatus = VisualTextReportStatus.FAILED
    plan_evidence: InputEvidence | None = None
    confirmed_report_evidence: InputEvidence | None = None
    rule_versions: VisualTextRuleVersions | None = None
    scope: tuple[PartVisualScope, ...] = ()
    capability: VisualTextCapabilityReport | None = None
    page_index: tuple[_PartFrameInventory, ...] = ()
    ocr_resource_plan: OcrResourcePlan | None = None
    ocr_evidence: dict[str, object] | None = None
    classification: dict[str, object] | None = None
    suspected_embedded_media: dict[str, object] | None = None
    required_decision: dict[str, object] | None = None
    limitations: tuple[PlanningDiagnostic, ...] = ()
    diagnostics: tuple[PlanningDiagnostic, ...] = ()

    def bind(self, inputs: _VisualTextInputs) -> None:
        self.plan_id = inputs.plan.plan_id
        self.plan_evidence = input_evidence(inputs.plan_path)
        self.confirmed_report_evidence = input_evidence(inputs.confirmed_report_path)
        self.audio_report_id = inputs.audio_report_id
        self.audio_report_evidence = inputs.audio_report_evidence
        self.rule_versions = inputs.rule_versions
        self.scope = inputs.scope
        self.limitations = inputs.limitations
        self.capability = inputs.capability_report

    def fail(self, error: Exception) -> None:
        self.status = VisualTextReportStatus.FAILED
        self.ocr_resource_plan = None
        self.required_decision = None
        self.diagnostics = (
            PlanningDiagnostic(getattr(error, "reason", "visual_text_input_invalid"), str(error)),
        )

    def build(self) -> VisualTextReport:
        return VisualTextReport(
            report_id=self.report_id,
            plan_id=self.plan_id,
            status=self.status,
            scope_mode=self.scope_mode,
            workspace_path=self.workspace_path,
            report_path=self.report_path,
            plan_evidence=self.plan_evidence,
            confirmed_report_evidence=self.confirmed_report_evidence,
            resumed_from_report=self.resumed_from_report,
            resumed_from_report_id=self.resumed_from_report_id,
            resumption_decision=self.resumption_decision,
            audio_report_id=self.audio_report_id,
            audio_report_evidence=self.audio_report_evidence,
            rule_versions=self.rule_versions,
            scope=self.scope,
            capability=self.capability,
            page_index=self.page_index,
            ocr_resource_plan=self.ocr_resource_plan,
            ocr_evidence=self.ocr_evidence,
            classification=self.classification,
            suspected_embedded_media=self.suspected_embedded_media,
            required_decision=self.required_decision,
            limitations=self.limitations,
            diagnostics=self.diagnostics,
        )


def run_visual_text(
    plan_id: str,
    project_root: Path,
    *,
    all_parts: bool = False,
    part_selectors: Sequence[str] = (),
    range_selectors: Sequence[str] = (),
    audio_report_id: str | None = None,
    resumed_from_report: InputEvidence | None = None,
    resumed_from_report_id: str | None = None,
    resumption_decision: str | None = None,
    real_engines: RealEngineSelection | None = None,
) -> dict[str, object]:
    """Run one immutable visual-text attempt over an explicitly given scope.

    Scope is parsed before anything else: an unscoped invocation raises
    ``visual_text_scope_missing`` and no workspace is created. With a scope, the
    attempt mints a fresh Immutable visual-text workspace, revalidates the confirmed
    RunPlan and SourceArtifact hashes, the retained inspection evidence, the
    versioned rules, and every named Part and range against retained Part identities
    and actual video coverage, then builds the deterministic page index. After
    detection the attempt plans OCR resources: with frames to recognize it stops at
    the OCR resource confirmation pause (or the Visual-text resource-envelope pause
    when the plan exceeds the approved envelope), and OCR runs only after
    ``resume-visual-text`` records an explicit affirmative decision. A declining
    decision retains the page index with zero visual facts; an affirmative decision
    with no eligible OCR capability reaches ``model_acquisition_required``. Any drift
    or invalid scope retains a ``failed`` report. Each attempt owns a fresh workspace
    and never overwrites prior evidence, so there is no automatic retry.

    Once the page index exists the attempt also marks Suspected embedded-media intervals
    from the picture (basis picture-plus-audio when an optional ``audio_report_id`` is
    supplied and revalidated, picture-only otherwise); and after an affirmative OCR
    decision produces gated evidence, each admitted item is classified deterministically
    as page text, speaker supplement, or background UI, with platform noise retained as
    non-evidence and low-confidence items marked ``classification_uncertain``.

    ``real_engines`` is run composition's real-adapter selection (Phase 12 ticket
    06): ``None`` on every automated-test run (the controlled offline OCR path
    below), and the acquired real OCR engine when set, reached through
    :func:`~video_content_pipeline.real_engine_adapter.dispatch_real_stage`.
    """

    if real_engines is not None:
        return dispatch_real_stage(real_engines, stage="visual_text")
    selectors = parse_visual_text_scope(all_parts, part_selectors, range_selectors)
    scope_mode = "all" if any(isinstance(s, AllScope) for s in selectors) else "explicit"
    report_id = uuid.uuid4().hex
    workspace_path = project_root / "work" / "visual-text-reports" / report_id
    report_path = workspace_path / "visual-report.json"
    builder = _ReportBuilder(
        report_id=report_id,
        plan_id=plan_id,
        scope_mode=scope_mode,
        workspace_path=workspace_path,
        report_path=report_path,
        resumed_from_report=resumed_from_report,
        resumed_from_report_id=resumed_from_report_id,
        resumption_decision=resumption_decision,
        audio_report_id=audio_report_id,
    )
    try:
        inputs = _revalidate_inputs(plan_id, selectors, project_root, audio_report_id)
        builder.bind(inputs)
        _finalize(builder, inputs, project_root)
    except (VisualTextError, PlanningError, OSError, ValueError) as error:
        builder.fail(error)

    report = builder.build()
    write_json_once(
        report_path,
        report.as_json(),
        conflict_error=lambda message: VisualTextError("visual_text_report_conflict", message),
    )
    return {"status": report.status.value, "report": report.as_json()}


def resume_visual_text(
    report_id: str, decision: str | None, project_root: Path
) -> dict[str, object]:
    """Resume one retained visual-text decision pause from an explicit user decision.

    Resumption never auto-resumes and never changes identity-bound inputs: it
    requires an explicit report ID and an explicit decision, and it may continue only
    a retained report whose decision pause it recognizes -- the OCR resource
    confirmation pause (continued with ``ocr_resource_confirmed`` to run OCR or
    ``ocr_declined`` to keep the page index at zero cost) or the Visual-text
    resource-envelope pause (continued with ``resource_configuration_changed``). A
    resume starts a fresh attempt from the retained plan and scope identities and
    never overwrites the paused report, so there is no automatic retry.
    """

    prior_path = _visual_text_report_path(project_root, report_id)
    try:
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VisualTextError(
            "visual_text_report_invalid", "Visual-text report cannot be read."
        ) from error
    if not isinstance(prior, Mapping) or prior.get("report_id") != report_id:
        raise VisualTextError("visual_text_report_invalid", "Visual-text report is invalid.")
    if decision is None:
        raise VisualTextError(
            "visual_text_resume_invalid", "Resume requires an explicit user decision."
        )
    pause_reason = _resumable_pause_reason(prior)
    if pause_reason is None:
        raise VisualTextError(
            "visual_text_resume_invalid",
            "Only a retained visual-text decision pause can be resumed.",
        )
    _reject_mismatched_decision(pause_reason, decision)
    plan_id, all_parts, range_selectors, audio_report_id = _resumed_request(prior)
    return run_visual_text(
        plan_id,
        project_root,
        all_parts=all_parts,
        range_selectors=range_selectors,
        audio_report_id=audio_report_id,
        resumed_from_report=input_evidence(prior_path),
        resumed_from_report_id=report_id,
        resumption_decision=decision,
    )


def _reject_mismatched_decision(pause_reason: str, decision: str) -> None:
    """Reject a decision that does not match the retained pause it claims to continue."""

    if pause_reason == _OCR_RESOURCE_CONFIRMATION_REASON and decision not in (
        OCR_RESOURCE_CONFIRMATION_DECISION,
        OCR_DECLINE_DECISION,
    ):
        raise VisualTextError(
            "visual_text_resume_invalid",
            "An OCR resource confirmation pause requires --decision ocr_resource_confirmed or "
            "ocr_declined.",
        )
    if pause_reason == _RESOURCE_ENVELOPE_REASON and decision != RESOURCE_ENVELOPE_DECISION:
        raise VisualTextError(
            "visual_text_resume_invalid",
            "A resource-envelope pause requires --decision resource_configuration_changed.",
        )


def _resumable_pause_reason(report: Mapping[str, object]) -> str | None:
    """Return the resumable decision-pause reason of a retained report, if any."""

    required_decision = report.get("required_decision")
    if not isinstance(required_decision, Mapping):
        return None
    reason = required_decision.get("reason")
    status = report.get("status")
    if (
        status == VisualTextReportStatus.AWAITING_OCR_RESOURCE_CONFIRMATION.value
        and reason == _OCR_RESOURCE_CONFIRMATION_REASON
    ):
        return _OCR_RESOURCE_CONFIRMATION_REASON
    if (
        status == VisualTextReportStatus.RESOURCE_ENVELOPE_EXCEEDED.value
        and reason == _RESOURCE_ENVELOPE_REASON
    ):
        return _RESOURCE_ENVELOPE_REASON
    return None


def _resumed_request(report: Mapping[str, object]) -> tuple[str, bool, tuple[str, ...], str | None]:
    """Read the identity-bound plan, scope, and audio report from a paused report.

    The paused report retains its resolved scope intervals, so the resume rebuilds
    the exact same scope as explicit ``--range`` selectors -- the identity-bound
    inputs are never re-derived from anything but the retained report. An ``--all``
    request is replayed as ranges over the same retained coverage, which resolves to
    the same Parts. The optional supplied Audio analysis report is an identity-bound
    input too, so its report ID is replayed and revalidated afresh. A malformed or
    empty retained scope is rejected rather than silently narrowed.
    """

    plan_id = report.get("plan_id")
    if not isinstance(plan_id, str) or not plan_id:
        raise VisualTextError(
            "visual_text_report_invalid", "Paused report omits its identity-bound plan ID."
        )
    scope = report.get("scope")
    parts = scope.get("parts") if isinstance(scope, Mapping) else None
    if not isinstance(parts, list) or not parts:
        raise VisualTextError(
            "visual_text_report_invalid", "Paused report omits its retained scope."
        )
    ranges = tuple(_scope_range_selectors(parts))
    audio_report_id = report.get("audio_report_id")
    if audio_report_id is not None and not isinstance(audio_report_id, str):
        raise VisualTextError(
            "visual_text_report_invalid", "Paused report has a malformed audio report identity."
        )
    return plan_id, False, ranges, audio_report_id


def _scope_range_selectors(parts: Sequence[object]) -> list[str]:
    selectors: list[str] = []
    for part in parts:
        if not isinstance(part, Mapping):
            raise VisualTextError("visual_text_report_invalid", "A paused scope Part is malformed.")
        part_id = part.get("part_id")
        intervals = part.get("intervals")
        if not isinstance(part_id, str) or not part_id or not isinstance(intervals, list):
            raise VisualTextError("visual_text_report_invalid", "A paused scope Part is malformed.")
        for interval in intervals:
            selectors.append(f"{part_id}:{_interval_range_text(interval)}")
    if not selectors:
        raise VisualTextError(
            "visual_text_report_invalid", "Paused report retains no scope interval."
        )
    return selectors


def _interval_range_text(interval: object) -> str:
    if not isinstance(interval, Mapping):
        raise VisualTextError("visual_text_report_invalid", "A paused scope interval is malformed.")
    start = _seconds_text(_exact_time_from_json(interval.get("start")))
    end = _seconds_text(_exact_time_from_json(interval.get("end")))
    return f"{start}-{end}"


def _exact_time_from_json(value: object) -> ExactTime:
    if not isinstance(value, Mapping):
        raise VisualTextError("visual_text_report_invalid", "A paused scope time is malformed.")
    numerator = value.get("numerator")
    denominator = value.get("denominator")
    if (
        not isinstance(numerator, int)
        or isinstance(numerator, bool)
        or not isinstance(denominator, int)
        or isinstance(denominator, bool)
        or denominator <= 0
    ):
        raise VisualTextError("visual_text_report_invalid", "A paused scope time is malformed.")
    return ExactTime(numerator, denominator)


def _seconds_text(value: ExactTime) -> str:
    # Exact rational rendering (``5`` or ``7/2``), never float division: the resume
    # path re-parses this through ``Fraction`` to rebuild the identity-bound --range
    # scope, so a lossy ``0.3333...`` would drift off the retained interval.
    return str(value.as_fraction())


def _visual_text_report_path(project_root: Path, report_id: str) -> Path:
    validated = validated_report_id(
        report_id,
        invalid_error=lambda: VisualTextError(
            "visual_text_report_invalid", "Visual-text report ID must be a UUID."
        ),
    )
    return project_root / "work" / "visual-text-reports" / validated / "visual-report.json"


def _revalidate_inputs(
    plan_id: str,
    selectors: Sequence[VisualTextScopeSelector],
    project_root: Path,
    audio_report_id: str | None,
) -> _VisualTextInputs:
    plan_path = project_root / "plans" / plan_id / "run-plan.json"
    plan = load_run_plan(plan_path)
    if plan.plan_id != plan_id:
        raise VisualTextError(
            "run_plan_not_confirmed", "RunPlan identity does not match the requested plan ID."
        )
    confirmed_report_path = project_root / "plans" / "reports" / plan.report_id / "plan-report.json"
    confirmed_report = load_plan_report(confirmed_report_path)
    if not confirmed_plan_matches(confirmed_report, plan):
        raise VisualTextError(
            "run_plan_not_confirmed", "RunPlan evidence does not match a confirmed PlanReport."
        )
    revalidate_confirmed_inspection_evidence(
        confirmed_report,
        plan,
        drift_error=lambda: VisualTextError(
            "inspection_evidence_changed",
            "PlanReport inspection evidence no longer matches the confirmed RunPlan.",
        ),
    )
    rule_versions = load_visual_text_rule_versions(project_root)
    page_index_rules = load_page_index_rules(project_root)
    ocr_resource_policy = load_ocr_resource_policy(project_root)
    classification_rules = load_classification_ruleset(project_root)
    embedded_media_rules = load_embedded_media_ruleset(project_root)
    coverage_by_part = part_video_coverage(confirmed_report)
    plan_part_ids = {artifact.source_id for artifact in plan.source_artifacts}
    scope = resolve_visual_text_scope(
        selectors, plan_part_ids=plan_part_ids, coverage_by_part=coverage_by_part
    )
    limitations = _uncovered_part_limitations(selectors, plan_part_ids, coverage_by_part)
    capability_report = evaluate_ocr_capabilities(project_root)
    audio_evidence, audio_regions_by_part = _bind_audio_report(
        project_root, audio_report_id, plan.plan_id, [part.part_id for part in scope]
    )
    return _VisualTextInputs(
        plan=plan,
        plan_path=plan_path,
        confirmed_report_path=confirmed_report_path,
        rule_versions=rule_versions,
        page_index_rules=page_index_rules,
        ocr_resource_policy=ocr_resource_policy,
        classification_rules=classification_rules,
        embedded_media_rules=embedded_media_rules,
        scope=scope,
        limitations=limitations,
        capability_report=capability_report,
        audio_report_id=audio_report_id,
        audio_report_evidence=audio_evidence,
        audio_regions_by_part=audio_regions_by_part,
    )


def _bind_audio_report(
    project_root: Path,
    audio_report_id: str | None,
    plan_id: str,
    scoped_part_ids: Sequence[str],
) -> tuple[InputEvidence | None, Mapping[str, tuple[AudioActivityRegion, ...]] | None]:
    """Revalidate an optional supplied Audio analysis report before its evidence is used.

    An Audio analysis report is optional and used only by embedded-media suspicion (an
    optional informing context). When one is supplied it is revalidated exactly -- hash
    evidence plus bound input identities: it must exist, name itself, and be bound to
    this RunPlan -- before any of its evidence is read, so a mismatched report blocks
    the attempt rather than silently informing a marker. Its active-voice regions are
    then lifted per scoped Part (a model-gated report legitimately carries none). With
    no report supplied the result is ``(None, None)`` and suspicion falls back to the
    picture-only basis.
    """

    if audio_report_id is None:
        return None, None
    validated_id = validated_report_id(
        audio_report_id,
        invalid_error=lambda: VisualTextError(
            "audio_report_invalid", "Audio analysis report ID must be a UUID."
        ),
    )
    audio_path = (
        project_root
        / "work"
        / "audio-analysis-reports"
        / validated_id
        / "audio-analysis-report.json"
    )
    try:
        decoded = json.loads(audio_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VisualTextError(
            "audio_report_invalid", "Audio analysis report cannot be read."
        ) from error
    if not isinstance(decoded, Mapping) or decoded.get("report_id") != validated_id:
        raise VisualTextError("audio_report_invalid", "Audio analysis report is invalid.")
    if decoded.get("plan_id") != plan_id:
        raise VisualTextError(
            "audio_report_mismatch", "Audio analysis report is not bound to this RunPlan."
        )
    regions_by_part = {
        part_id: audio_activity_regions(decoded, part_id) for part_id in scoped_part_ids
    }
    return input_evidence(audio_path), regions_by_part


def _uncovered_part_limitations(
    selectors: Sequence[VisualTextScopeSelector],
    plan_part_ids: set[str],
    coverage_by_part: Mapping[str, HalfOpenInterval],
) -> tuple[PlanningDiagnostic, ...]:
    """Record Parts an ``--all`` sweep skipped because they lack video coverage.

    Named scope rejects an uncovered Part outright; a whole-collection sweep instead
    samples what it can and records each skipped Part as an auditable limitation
    rather than silently dropping it.
    """

    if not any(isinstance(selector, AllScope) for selector in selectors):
        return ()
    return tuple(
        PlanningDiagnostic(
            "visual_text_part_uncovered",
            f"Part {part_id!r} was skipped: no determinate video coverage.",
        )
        for part_id in sorted(plan_part_ids - set(coverage_by_part))
    )


def _finalize(builder: _ReportBuilder, inputs: _VisualTextInputs, project_root: Path) -> None:
    """Build the deterministic page index, plan OCR resources, then derive the status.

    Detection and sampling are deterministic and carry no model capability, so the
    Part-local page index is always built when frame metrics exist. Each Part in scope
    consumes its hash-pinned frame-metric fixture; a Part with no fixture records an
    auditable limitation rather than a silent gap, and a fixture naming stale rule
    versions is rule drift that blocks the attempt. With the index built, the
    conservative OCR resource plan is derived and the terminal status follows the two
    internal gates.
    """

    builder.page_index, absences = _build_page_index(inputs, project_root, builder.workspace_path)
    builder.limitations = inputs.limitations + absences
    builder.suspected_embedded_media = _detect_embedded_media(builder, inputs)
    plan = plan_ocr_resources(
        tuple(inventory.index for inventory in builder.page_index), inputs.ocr_resource_policy
    )
    builder.ocr_resource_plan = plan
    _derive_terminal_status(builder, inputs, plan, project_root)


def _detect_embedded_media(
    builder: _ReportBuilder, inputs: _VisualTextInputs
) -> dict[str, object] | None:
    """Mark suspected embedded-media intervals from the picture, once per scoped Part.

    Suspicion is picture-derived (a sustained transition-frame run) and so is produced
    whenever a page index exists -- independently of the OCR decision, exactly like the
    page index it is retained beside. The basis follows the revalidated audio binding:
    picture-plus-audio when a report was supplied, picture-only otherwise. With no page
    index there is nothing to mark and the block is absent.
    """

    if not builder.page_index:
        return None
    rules = inputs.embedded_media_rules
    regions_by_part = inputs.audio_regions_by_part
    parts: list[PartEmbeddedMediaResult] = []
    for inventory in builder.page_index:
        part_id = inventory.index.part_id
        audio_regions = None if regions_by_part is None else regions_by_part.get(part_id, ())
        parts.append(
            detect_embedded_media(
                part_id=part_id,
                index=inventory.index,
                rules=rules,
                audio_regions=audio_regions,
            )
        )
    return _versioned_part_block(
        rules.version, rules.calibration_required, [part.as_json() for part in parts]
    )


def _versioned_part_block(
    version: str, calibration_required: bool, parts: Sequence[dict[str, object]]
) -> dict[str, object]:
    """Wrap per-Part results in the shared versioned evidence-block envelope.

    Both the classification and the embedded-media suspicion blocks record their
    versioned ``calibration_required`` rule identity beside a list of per-Part results,
    so the two callers build the same envelope through here.
    """

    return {"version": version, "calibration_required": calibration_required, "parts": list(parts)}


def _derive_terminal_status(
    builder: _ReportBuilder,
    inputs: _VisualTextInputs,
    plan: OcrResourcePlan,
    project_root: Path,
) -> None:
    """Choose the status from the OCR resource plan and any explicit resume decision.

    The two internal gates sequence as: nothing to recognize resolves straight to the
    capability outcome; a plan over the approved envelope is the immutable Visual-text
    resource-envelope pause (never silently altering candidate, resolution, or batch);
    a declining decision retains the page index as ``partial`` with zero visual facts;
    an affirmative decision authorizes OCR, which runs the Controlled offline OCR
    adapter (``_execute_ocr``) to produce gated evidence -- or, with no controlled
    adapter and no eligible model, reaches ``model_acquisition_required`` with the page
    index retained; and any other attempt (a fresh run, or a resource-envelope resume
    that now fits) stops at the OCR resource confirmation pause. The plan is
    deterministic, so a resume rebuilds the same page index and re-plans the same OCR
    work.
    """

    capability_status = VisualTextReportStatus(inputs.capability_report.result)
    decision = builder.resumption_decision
    if plan.selected_frame_count == 0:
        builder.status = capability_status
        return
    if not plan.within_envelope:
        builder.status = VisualTextReportStatus.RESOURCE_ENVELOPE_EXCEEDED
        builder.required_decision = {
            "reason": _RESOURCE_ENVELOPE_REASON,
            "decision": RESOURCE_ENVELOPE_DECISION,
        }
        builder.diagnostics = (
            PlanningDiagnostic(
                _RESOURCE_ENVELOPE_REASON,
                "The planned OCR run exceeds the approved resource envelope; reconfigure rather "
                "than silently change candidate, resolution, or batch.",
            ),
        )
        return
    if decision == OCR_DECLINE_DECISION:
        builder.status = VisualTextReportStatus.PARTIAL
        return
    if decision == OCR_RESOURCE_CONFIRMATION_DECISION:
        _execute_ocr(builder, inputs, capability_status, project_root)
        return
    builder.status = VisualTextReportStatus.AWAITING_OCR_RESOURCE_CONFIRMATION
    builder.required_decision = {
        "reason": _OCR_RESOURCE_CONFIRMATION_REASON,
        "decision": OCR_RESOURCE_CONFIRMATION_DECISION,
    }


@dataclass(frozen=True)
class _OcrEvidence:
    """The projected-and-gated OCR evidence block embedded in a report.

    ``state`` is ``projected`` for gated evidence or ``model_output_invalid`` when an
    untrusted output failed the projection; ``contract`` records the versioned adapter
    and projection identities plus the restricted raw-output pointer; ``parts`` carries
    each Part's admitted and rejected items; ``orphan_rejections`` retains items naming
    a Part with no page index this attempt.
    """

    state: str
    contract: dict[str, object]
    parts: tuple[OcrItemGateResult, ...]
    orphan_rejections: tuple[RejectedOcrItem, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "state": self.state,
            "contract": self.contract,
            "parts": [result.as_json() for result in self.parts],
            "rejected_items": [item.as_json() for item in self.orphan_rejections],
        }


def _execute_ocr(
    builder: _ReportBuilder,
    inputs: _VisualTextInputs,
    capability_status: VisualTextReportStatus,
    project_root: Path,
) -> None:
    """Run the Controlled offline OCR adapter and gate its output into evidence.

    An affirmative OCR decision reaches here with a within-envelope plan and at
    least one selected representative. OCR text enters only through the versioned
    projection: the two versioned contract identities are revalidated, the bound
    controlled fixture (if any) is loaded and proven to match exactly the selected
    frames, its raw output is retained as restricted audit evidence and projected,
    and every projected item is gated against its Part's coverage and page
    appearance records. With no controlled adapter fixture and no eligible model the
    attempt acquisition-gates with the page index retained; an untrusted, malformed
    output invalidates the whole attempt (``model_output_invalid`` -> ``failed``)
    with the raw output kept as restricted audit evidence; otherwise the admitted
    and rejected items become the report's OCR evidence, ``partial`` when any item
    was rejected and ``complete`` when none was. The controlled adapter is not a
    model asset, so the offline model-execution guarantee is preserved.
    """

    contracts = revalidate_ocr_contracts(project_root)
    fixture = load_controlled_ocr_fixture(contracts, project_root)
    if fixture is None:
        builder.status = capability_status
        return

    selections = _selected_representatives(builder.page_index)
    manifest_document = ocr_input_manifest_document(inputs.plan.plan_id, selections)
    manifest_sha = ocr_input_manifest_sha256(manifest_document)
    if fixture.input_fixture_sha256 != manifest_sha:
        raise VisualTextError(
            "visual_text_fixture_input_mismatch",
            "Controlled OCR adapter fixture is not bound to this attempt's selected frames.",
        )
    manifest_path = builder.workspace_path / "provenance" / "ocr-input-manifest.json"
    write_json_once(
        manifest_path,
        manifest_document,
        conflict_error=lambda message: VisualTextError("visual_text_report_conflict", message),
    )
    # The raw output is retained as restricted audit evidence *before* projection, so
    # an invalid output leaves a diagnostic trail rather than vanishing.
    raw_pointer = retain_restricted_ocr_output(
        fixture.raw_output,
        builder.workspace_path,
        capability=fixture.capability,
        label="visual-text",
    )
    contract_identity = {
        **contracts.as_json(),
        "capability": fixture.capability,
        "input_manifest": {**input_evidence(manifest_path).as_json(), "sha256": manifest_sha},
        "restricted_raw_output": raw_pointer.as_json(),
    }

    projection = project_ocr_output(_decode_ocr_output(fixture.raw_output), contracts)
    if projection.state != "projected":
        message = (
            projection.diagnostic.message
            if projection.diagnostic is not None
            else "The controlled OCR output is invalid."
        )
        builder.status = VisualTextReportStatus.FAILED
        builder.ocr_evidence = _OcrEvidence(
            _MODEL_OUTPUT_INVALID, contract_identity, (), ()
        ).as_json()
        builder.diagnostics = (PlanningDiagnostic(_MODEL_OUTPUT_INVALID, message),)
        return

    gate_results, orphans = _gate_projected_items(
        builder.page_index, inputs.scope, projection.items
    )
    builder.ocr_evidence = _OcrEvidence(
        "projected", contract_identity, tuple(gate_results), tuple(orphans)
    ).as_json()
    builder.classification = _classify_admitted_items(gate_results, inputs.classification_rules)
    rejected_count = sum(len(result.rejected) for result in gate_results) + len(orphans)
    builder.status = (
        VisualTextReportStatus.PARTIAL if rejected_count else VisualTextReportStatus.COMPLETE
    )


def _classify_admitted_items(
    gate_results: Sequence[OcrItemGateResult], rules: ClassificationRuleset
) -> dict[str, object]:
    """Classify each Part's admitted OCR evidence items with the versioned rules.

    Classification runs only over admitted (gated) items -- a rejected item never
    became evidence, so it is never classified. Each Part is classified independently;
    platform-noise items are partitioned out as retained non-evidence and every other
    item receives a page category or ``classification_uncertain``.
    """

    parts: list[PartClassificationResult] = []
    for result in gate_results:
        admitted: Sequence[GatedOcrItem] = result.admitted
        parts.append(classify_ocr_items(part_id=result.part_id, items=admitted, rules=rules))
    return _versioned_part_block(
        rules.version, rules.calibration_required, [part.as_json() for part in parts]
    )


def _selected_representatives(
    page_index: Sequence[_PartFrameInventory],
) -> list[tuple[str, str, ExactTime, str]]:
    """List every page's OCR representative across the scoped Parts, canonically bound.

    Each entry is ``(part_id, visual_page_id, selected_pts, content_fingerprint)`` --
    exactly the frames OCR will read -- so the input manifest hash binds the
    controlled fixture to precisely what detection and sampling selected.
    """

    selections: list[tuple[str, str, ExactTime, str]] = []
    for inventory in page_index:
        for page in inventory.index.pages:
            if page.selected_frame_pts is not None:
                selections.append(
                    (
                        inventory.index.part_id,
                        page.visual_page_id,
                        page.selected_frame_pts,
                        page.content_fingerprint,
                    )
                )
    return selections


def _gate_projected_items(
    page_index: Sequence[_PartFrameInventory],
    scope: Sequence[PartVisualScope],
    items: Sequence[ProjectedOcrItem],
) -> tuple[list[OcrItemGateResult], list[RejectedOcrItem]]:
    """Gate every projected item against the Part-local page index it names.

    Items are grouped by their projected Part; each scoped Part gates its own items
    against its coverage and page appearance records, and an item naming a Part with
    no page index in this attempt is rejected outright as ``ocr_item_unknown_part``
    rather than silently dropped.
    """

    coverage_by_part = {
        part_scope.part_id: HalfOpenInterval(ExactTime(0), part_scope.coverage_duration)
        for part_scope in scope
    }
    known_parts = {inventory.index.part_id for inventory in page_index}
    results: list[OcrItemGateResult] = []
    for inventory in page_index:
        part_id = inventory.index.part_id
        part_items = tuple(item for item in items if item.part_id == part_id)
        coverage = coverage_by_part.get(part_id)
        if coverage is None:
            # Every page-index Part is drawn from the resolved scope, so its coverage is
            # always present; a miss is an internal inconsistency, surfaced as a
            # structured failure rather than an unhandled KeyError that skips the gate.
            raise VisualTextError(
                "visual_text_coverage_missing",
                f"Part {part_id!r} has a page index but no resolved coverage to gate against.",
            )
        results.append(
            gate_ocr_items(
                part_id=part_id,
                items=part_items,
                page_index=inventory.index,
                coverage=coverage,
            )
        )
    orphans = [
        RejectedOcrItem(
            part_id=item.part_id,
            visual_page_id=item.visual_page_id,
            pts=item.pts,
            reason=_OCR_ITEM_UNKNOWN_PART,
            message="Item names a Part with no page index in this attempt.",
        )
        for item in items
        if item.part_id not in known_parts
    ]
    return results, orphans


def _decode_ocr_output(raw_output: bytes) -> object:
    try:
        return json.loads(raw_output)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _build_page_index(
    inputs: _VisualTextInputs, project_root: Path, workspace_path: Path
) -> tuple[tuple[_PartFrameInventory, ...], tuple[PlanningDiagnostic, ...]]:
    """Load fixtures and build one deterministic page index per Part in scope.

    Fixtures are validated whole before any inventory is written: a stale fixture
    (naming detection or sampling rule versions other than the loaded rules) raises
    ``visual_text_frame_metrics_stale`` and blocks the attempt before a single
    artifact lands, so a ``failed`` attempt never leaves a partial inventory. Parts
    without a fixture are recorded as limitations and simply produce no index.
    """

    rules = inputs.page_index_rules
    loaded: list[tuple[PartVisualScope, FrameMetricFixture]] = []
    absences: list[PlanningDiagnostic] = []
    for part_scope in inputs.scope:
        fixture_path = _frame_metric_fixture_path(project_root, part_scope.part_id)
        if not fixture_path.exists():
            absences.append(
                PlanningDiagnostic(
                    "visual_text_frame_metrics_absent",
                    f"Part {part_scope.part_id!r} has no frame-metric fixture; "
                    "no page index was built.",
                )
            )
            continue
        fixture = load_frame_metric_fixture(fixture_path, part_scope.part_id)
        if (
            fixture.detection_version != rules.detection_version
            or fixture.sampling_version != rules.sampling_version
        ):
            raise VisualTextError(
                "visual_text_frame_metrics_stale",
                f"Frame-metric fixture for Part {part_scope.part_id!r} names stale rule versions.",
            )
        loaded.append((part_scope, fixture))

    inventories = tuple(
        _write_part_inventory(part_scope, fixture, rules, workspace_path)
        for part_scope, fixture in loaded
    )
    return inventories, tuple(absences)


def _write_part_inventory(
    part_scope: PartVisualScope,
    fixture: FrameMetricFixture,
    rules: PageIndexRules,
    workspace_path: Path,
) -> _PartFrameInventory:
    """Build one Part's page index and write its Retained frame inventory to the workspace.

    Only the frames inside the resolved scope intervals are indexed. The full
    inventory is written once as a workspace-internal artifact (an Unpublished
    internal frame set); its hash pointer is returned for the report.
    """

    scoped = frames_in_scope(fixture.frames, part_scope.intervals)
    index = build_part_page_index(part_scope.part_id, scoped, rules)
    inventory_path = workspace_path / "page-index" / part_scope.part_id / "retained-frames.json"
    write_json_once(
        inventory_path,
        index.as_json(),
        conflict_error=lambda message: VisualTextError("visual_text_report_conflict", message),
    )
    # Hash the written artifact rather than re-serializing the payload, so the pointer
    # cannot drift from write_json_once's canonical on-disk format.
    return _PartFrameInventory(
        index=index,
        fixture_evidence=fixture.evidence,
        inventory=input_evidence(inventory_path),
    )


def _frame_metric_fixture_path(project_root: Path, part_id: str) -> Path:
    """Locate a Part's hash-pinned synthetic frame-metric fixture.

    The fixture stands in for pinned-ffmpeg extraction plus deterministic metric
    computation; offline verification supplies it here and no frame of user media is
    read. A real future path would instead produce these metrics under the workspace.
    """

    return project_root / "input" / "visual-text-frame-metrics" / f"{part_id}.json"


# --- Serialization helpers --------------------------------------------------


def _time_as_json(time: ExactTime) -> dict[str, int]:
    return {"numerator": time.numerator, "denominator": time.denominator}


def _evidence_json(evidence: InputEvidence | None) -> dict[str, object] | None:
    return evidence.as_json() if evidence is not None else None
