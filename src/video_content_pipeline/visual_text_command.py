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

from video_content_pipeline.evidence import InputEvidence, input_evidence, write_json_once
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
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval, TimeValidationError
from video_content_pipeline.visual_text import (
    VisualTextCapabilityReport,
    VisualTextError,
    evaluate_ocr_capabilities,
    offline_guarantees,
)

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
    return tuple(
        selector for selector in selectors if not isinstance(selector, AllScope)
    )


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
    exists; ``model_acquisition_required`` is the terminal outcome when no eligible
    OCR capability is locally available. ``complete`` and ``partial`` are produced
    once detection, the OCR pause, and OCR land in later tickets.
    """

    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    MODEL_ACQUISITION_REQUIRED = "model_acquisition_required"


@dataclass
class _VisualTextInputs:
    """The fully revalidated inputs threaded from revalidation into finalization."""

    plan: RunPlan
    plan_path: Path
    confirmed_report_path: Path
    rule_versions: VisualTextRuleVersions
    scope: tuple[PartVisualScope, ...]
    limitations: tuple[PlanningDiagnostic, ...]
    capability_report: VisualTextCapabilityReport


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
    rule_versions: VisualTextRuleVersions | None
    scope: tuple[PartVisualScope, ...]
    capability: VisualTextCapabilityReport | None
    limitations: tuple[PlanningDiagnostic, ...]
    diagnostics: tuple[PlanningDiagnostic, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "report_id": self.report_id,
            "plan_id": self.plan_id,
            "status": self.status.value,
            "workspace_path": self.workspace_path.as_posix(),
            "report_path": self.report_path.as_posix(),
            "input_evidence": {
                "run_plan": _evidence_json(self.plan_evidence),
                "confirmed_plan_report": _evidence_json(self.confirmed_report_evidence),
            },
            "scope": {
                "requested": self.scope_mode,
                "parts": [part.as_json() for part in self.scope],
            },
            "capability": self.capability.as_json() if self.capability is not None else None,
            "rule_versions": (
                self.rule_versions.as_json() if self.rule_versions is not None else None
            ),
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
    status: VisualTextReportStatus = VisualTextReportStatus.FAILED
    plan_evidence: InputEvidence | None = None
    confirmed_report_evidence: InputEvidence | None = None
    rule_versions: VisualTextRuleVersions | None = None
    scope: tuple[PartVisualScope, ...] = ()
    capability: VisualTextCapabilityReport | None = None
    limitations: tuple[PlanningDiagnostic, ...] = ()
    diagnostics: tuple[PlanningDiagnostic, ...] = ()

    def bind(self, inputs: _VisualTextInputs) -> None:
        self.plan_id = inputs.plan.plan_id
        self.plan_evidence = input_evidence(inputs.plan_path)
        self.confirmed_report_evidence = input_evidence(inputs.confirmed_report_path)
        self.rule_versions = inputs.rule_versions
        self.scope = inputs.scope
        self.limitations = inputs.limitations
        self.capability = inputs.capability_report

    def fail(self, error: Exception) -> None:
        self.status = VisualTextReportStatus.FAILED
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
            rule_versions=self.rule_versions,
            scope=self.scope,
            capability=self.capability,
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
) -> dict[str, object]:
    """Run one immutable visual-text attempt over an explicitly given scope.

    Scope is parsed before anything else: an unscoped invocation raises
    ``visual_text_scope_missing`` and no workspace is created. With a scope, the
    attempt mints a fresh Immutable visual-text workspace, revalidates the confirmed
    RunPlan and SourceArtifact hashes, the retained inspection evidence, the
    versioned rules, and every named Part and range against retained Part identities
    and actual video coverage, then evaluates the OCR capability. Any drift or
    invalid scope retains a ``failed`` report with a structured reason; with no
    eligible OCR capability the terminal outcome is ``model_acquisition_required``.
    Each attempt owns a fresh workspace and never overwrites prior evidence.
    """

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
    )
    try:
        inputs = _revalidate_inputs(plan_id, selectors, project_root)
        builder.bind(inputs)
        _finalize(builder, inputs)
    except (VisualTextError, PlanningError, OSError, ValueError) as error:
        builder.fail(error)

    report = builder.build()
    write_json_once(
        report_path,
        report.as_json(),
        conflict_error=lambda message: VisualTextError("visual_text_report_conflict", message),
    )
    return {"status": report.status.value, "report": report.as_json()}


def _revalidate_inputs(
    plan_id: str,
    selectors: Sequence[VisualTextScopeSelector],
    project_root: Path,
) -> _VisualTextInputs:
    plan_path = project_root / "plans" / plan_id / "run-plan.json"
    plan = load_run_plan(plan_path)
    if plan.plan_id != plan_id:
        raise VisualTextError(
            "run_plan_not_confirmed", "RunPlan identity does not match the requested plan ID."
        )
    confirmed_report_path = (
        project_root / "plans" / "reports" / plan.report_id / "plan-report.json"
    )
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
    coverage_by_part = part_video_coverage(confirmed_report)
    plan_part_ids = {artifact.source_id for artifact in plan.source_artifacts}
    scope = resolve_visual_text_scope(
        selectors, plan_part_ids=plan_part_ids, coverage_by_part=coverage_by_part
    )
    limitations = _uncovered_part_limitations(selectors, plan_part_ids, coverage_by_part)
    capability_report = evaluate_ocr_capabilities(project_root)
    return _VisualTextInputs(
        plan=plan,
        plan_path=plan_path,
        confirmed_report_path=confirmed_report_path,
        rule_versions=rule_versions,
        scope=scope,
        limitations=limitations,
        capability_report=capability_report,
    )


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


def _finalize(builder: _ReportBuilder, inputs: _VisualTextInputs) -> None:
    """Derive the terminal status from the OCR capability once revalidation has passed.

    Detection, the OCR resource confirmation pause, and OCR arrive in later tickets;
    at this boundary the outcome follows the evaluated capability directly. With no
    eligible, acquired OCR capability that is the Model-acquisition-required
    visual-text result, carrying its retained scope and rule versions but no visual
    evidence. Deriving the status from ``capability_report.result`` -- rather than
    asserting it -- keeps the boundary honest as later tickets add capability states.
    """

    builder.status = VisualTextReportStatus(inputs.capability_report.result)


# --- Serialization helpers --------------------------------------------------


def _time_as_json(time: ExactTime) -> dict[str, int]:
    return {"numerator": time.numerator, "denominator": time.denominator}


def _evidence_json(evidence: InputEvidence | None) -> dict[str, object] | None:
    return evidence.as_json() if evidence is not None else None
