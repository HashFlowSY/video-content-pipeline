"""Phase 7's local enhancement path: Gate-checked interval replacement (ticket 07).

``vcp enhance`` upgrades a subtitle-priority result on exactly the passages the
user names -- a Part, a time range, or specific problem cues -- without paying for
a full re-transcription. Its target scope comes only from the user (``--part``,
``--range``, ``--cue``); automatic suspicious-interval discovery belongs to the
full-ASR ``transcribe`` path alone. Inside each user-named interval ASR cues
replace the subtitle *display layer* only after passing the same adoption-style
timing gates a full run uses (ticket 04); on gate failure the original subtitle
cues stay with a recorded reason (ADR 0045). Merging is interval-grained, never
cue-level interleaved: an interval is wholly ASR or wholly subtitle. Every enhanced
cue carries ``subtitle_track`` or ``asr`` provenance, the original cues remain
immutable evidence, and every replacement and rejection is written to the
correction log and its readable rendering.

An enhanced artifact never claims full verbatim completeness and never changes
``audio_completeness=not_verified``; only a complete ``transcribe`` run performs
the Audio-completeness upgrade. ASR text enters exactly as it does on the full
path -- through the versioned Controlled offline ASR adapter and output
projection -- so no model is downloaded or executed. See
``docs/PHASE_07_SPECIFICATION.md``, the Transcription Context, and ADR 0045.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
from typing import TypeGuard

from video_content_pipeline.coverage import StreamCoverage
from video_content_pipeline.evidence import (
    InputEvidence,
    input_evidence,
    validated_report_id,
    write_json_once,
    write_text_once,
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
    SubtitleReportError,
    subtitle_rules_fingerprint,
)
from video_content_pipeline.text_generation import cue_id
from video_content_pipeline.timecode import (
    ExactTime,
    HalfOpenInterval,
    TimeValidationError,
)
from video_content_pipeline.timeline import CollectionTimeline, TimelinePart
from video_content_pipeline.transcription import (
    RESOURCE_ENVELOPE_DECISION,
    AudioReportBinding,
    evaluate_asr_capabilities,
    transcription_resource_envelope_pause,
)
from video_content_pipeline.transcription_contracts import (
    ProjectedAsrCue,
    TranscriptionContractError,
    load_controlled_asr_fixture,
    project_asr_output,
    retain_restricted_raw_output,
    revalidate_asr_contracts,
)
from video_content_pipeline.transcription_gates import (
    CanonicalTimelineGateResult,
    RejectedAsrCue,
    TimingGateRuleset,
    TranscriptionGateError,
    gate_projected_cues,
    load_timing_gate_ruleset,
)


class EnhancementError(ValueError):
    """A local-enhancement failure that names a stable diagnostic reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


# --- Retained subtitle display layer ----------------------------------------


@dataclass(frozen=True)
class RetainedSubtitleCue:
    """One retained subtitle cue: the immutable display-layer evidence to enhance.

    ``interval`` is the cue's raw-PTS source interval and ``source_ordinal`` its
    Part-local order, so ``cue_identity`` reproduces the same stable identity the
    text pipeline adjudicates against.
    """

    part_id: str
    track_id: str
    source_ordinal: int
    interval: HalfOpenInterval
    text: str

    @property
    def cue_identity(self) -> str:
        return cue_id(self.part_id, self.track_id, self.source_ordinal)

    def as_json(self) -> dict[str, object]:
        return {
            "cue_id": self.cue_identity,
            "source_ordinal": self.source_ordinal,
            "interval": _interval_as_json(self.interval),
            "text": self.text,
        }


def load_retained_subtitle_cues(
    source_candidate_path: Path, *, part_id: str, stream_index: int
) -> tuple[RetainedSubtitleCue, ...]:
    """Load one Part's retained subtitle cues -- text and raw-PTS intervals -- in order.

    The retained ``source-candidate.json`` stores each accepted cue's
    ``source_ordinal``, ``text``, and raw-PTS interval. This is our own revalidated
    evidence rather than untrusted model output, so a malformed candidate raises
    ``enhancement_cue_basis_invalid``. Cues are returned in retained order; their
    identities are the ground truth ``--cue`` selectors resolve against.
    """

    track_id = f"stream-{stream_index}"
    try:
        decoded = json.loads(source_candidate_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EnhancementError(
            "enhancement_cue_basis_invalid", "Subtitle source candidate cannot be read."
        ) from error
    if not isinstance(decoded, Mapping) or decoded.get("schema_version") != 1:
        raise EnhancementError(
            "enhancement_cue_basis_invalid", "Subtitle source candidate has an invalid schema."
        )
    raw_cues = decoded.get("cues")
    if not isinstance(raw_cues, list):
        raise EnhancementError(
            "enhancement_cue_basis_invalid", "Subtitle source candidate omits a cue list."
        )
    cues: list[RetainedSubtitleCue] = []
    seen: set[int] = set()
    for raw_cue in raw_cues:
        if not isinstance(raw_cue, Mapping):
            raise EnhancementError(
                "enhancement_cue_basis_invalid", "A subtitle cue is not an object."
            )
        ordinal = raw_cue.get("source_ordinal")
        text = raw_cue.get("text")
        if not _is_non_negative_int(ordinal) or ordinal in seen:
            raise EnhancementError(
                "enhancement_cue_basis_invalid", "A subtitle cue omits a unique source ordinal."
            )
        if not isinstance(text, str):
            raise EnhancementError(
                "enhancement_cue_basis_invalid", "A subtitle cue omits its text."
            )
        seen.add(ordinal)
        cues.append(
            RetainedSubtitleCue(
                part_id=part_id,
                track_id=track_id,
                source_ordinal=ordinal,
                interval=_interval_from_json(raw_cue.get("raw_pts_interval")),
                text=text,
            )
        )
    cues.sort(key=lambda cue: (cue.interval.start.as_fraction(), cue.source_ordinal))
    return tuple(cues)


def part_coverage_envelope(cues: Sequence[RetainedSubtitleCue]) -> HalfOpenInterval:
    """Return the Part's retained caption-coverage envelope from its cues.

    The envelope spans the earliest cue start to the latest cue end. It is the
    retained coverage a ``--range`` selector and the timing gates are validated
    against; an ASR cue outside it is rejected as out of coverage.
    """

    if not cues:
        raise EnhancementError(
            "enhancement_scope_empty_part", "A named Part has no retained subtitle cues to enhance."
        )
    start = min(cue.interval.start for cue in cues)
    end = max(cue.interval.end for cue in cues)
    return HalfOpenInterval(start, end)


# --- Enhancement scope (user-named Parts, ranges, and cues) -----------------


@dataclass(frozen=True)
class PartSelector:
    """The whole named Part is the enhancement interval."""

    part_id: str


@dataclass(frozen=True)
class RangeSelector:
    """A user-named raw-PTS time range within a Part."""

    part_id: str
    start: ExactTime
    end: ExactTime


@dataclass(frozen=True)
class CueSelector:
    """A user-named retained cue (by its Part-local source ordinal)."""

    part_id: str
    source_ordinal: int


EnhancementSelector = PartSelector | RangeSelector | CueSelector


def parse_selectors(
    part_values: Sequence[str],
    range_values: Sequence[str],
    cue_values: Sequence[str],
) -> tuple[EnhancementSelector, ...]:
    """Parse ``--part``/``--range``/``--cue`` CLI values into typed selectors.

    ``--part`` is ``<part-id>``; ``--range`` is ``<part-id>:<start>-<end>`` in
    decimal seconds; ``--cue`` is ``<part-id>:<source-ordinal>``. At least one
    selector is required -- the enhancement scope is never empty and never
    defaulted. A malformed selector raises ``enhancement_selector_invalid``.
    """

    selectors: list[EnhancementSelector] = []
    for value in part_values:
        part_id = value.strip()
        if not part_id:
            raise EnhancementError("enhancement_selector_invalid", "A --part selector is empty.")
        selectors.append(PartSelector(part_id))
    for value in range_values:
        selectors.append(_parse_range_selector(value))
    for value in cue_values:
        selectors.append(_parse_cue_selector(value))
    if not selectors:
        raise EnhancementError(
            "enhancement_scope_missing",
            "vcp enhance requires at least one --part, --range, or --cue selector.",
        )
    return tuple(selectors)


def _parse_range_selector(value: str) -> RangeSelector:
    part_id, _, span = value.partition(":")
    if not part_id or "-" not in span:
        raise EnhancementError(
            "enhancement_selector_invalid",
            "A --range selector must be <part-id>:<start>-<end> in seconds.",
        )
    start_text, _, end_text = span.partition("-")
    return RangeSelector(part_id, _seconds_to_exact(start_text), _seconds_to_exact(end_text))


def _parse_cue_selector(value: str) -> CueSelector:
    part_id, sep, cue_token = value.partition(":")
    # ``<part-id>:<cue-id>`` accepts either the bare Part-local source ordinal
    # (``part-1:3``) or the full retained cue identity (``part-1:part-1:stream-1:3``);
    # the ordinal is its trailing segment, revalidated against retained cues later.
    ordinal_text = cue_token.rsplit(":", 1)[-1].strip()
    if not part_id or not sep or not ordinal_text.isdigit():
        raise EnhancementError(
            "enhancement_selector_invalid",
            "A --cue selector must be <part-id>:<cue-id> naming a retained cue ordinal.",
        )
    return CueSelector(part_id, int(ordinal_text))


def _seconds_to_exact(text: str) -> ExactTime:
    try:
        fraction = Fraction(text.strip())
    except (ValueError, ZeroDivisionError) as error:
        raise EnhancementError(
            "enhancement_selector_invalid", f"A --range bound {text!r} is not a number."
        ) from error
    return ExactTime(fraction.numerator, fraction.denominator)


@dataclass(frozen=True)
class PartEnhancementScope:
    """The merged raw-PTS intervals to enhance within one Part, in start order."""

    part_id: str
    intervals: tuple[HalfOpenInterval, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "part_id": self.part_id,
            "intervals": [_interval_as_json(interval) for interval in self.intervals],
        }


def resolve_enhancement_scope(
    selectors: Sequence[EnhancementSelector],
    *,
    cues_by_part: Mapping[str, tuple[RetainedSubtitleCue, ...]],
) -> tuple[PartEnhancementScope, ...]:
    """Revalidate every selector against retained cue identities and stream coverage.

    Each selector must name a Part that has a retained subtitle cue basis; a range
    must be a positive half-open interval within that Part's retained coverage
    envelope; a cue must name a retained source ordinal. The resolved intervals are
    merged per Part into the minimal non-overlapping cover so overlapping selectors
    enhance a passage once. An unresolvable selector raises a stable reason -- the
    scope is validated whole before any ASR runs.
    """

    intervals_by_part: dict[str, list[HalfOpenInterval]] = {}
    order: list[str] = []
    for selector in selectors:
        part_id = selector.part_id
        cues = cues_by_part.get(part_id)
        if cues is None:
            raise EnhancementError(
                "enhancement_part_unknown",
                f"Enhancement Part {part_id!r} has no retained subtitle cue basis.",
            )
        interval = _selector_interval(selector, cues)
        if part_id not in intervals_by_part:
            intervals_by_part[part_id] = []
            order.append(part_id)
        intervals_by_part[part_id].append(interval)
    return tuple(
        PartEnhancementScope(part_id, _merge_intervals(intervals_by_part[part_id]))
        for part_id in order
    )


def _selector_interval(
    selector: EnhancementSelector, cues: tuple[RetainedSubtitleCue, ...]
) -> HalfOpenInterval:
    envelope = part_coverage_envelope(cues)
    if isinstance(selector, PartSelector):
        return envelope
    if isinstance(selector, RangeSelector):
        try:
            interval = HalfOpenInterval(selector.start, selector.end)
        except TimeValidationError as error:
            raise EnhancementError(
                "enhancement_range_invalid", "A --range must be a positive half-open interval."
            ) from error
        if (
            interval.start.as_fraction() < envelope.start.as_fraction()
            or interval.end.as_fraction() > envelope.end.as_fraction()
        ):
            raise EnhancementError(
                "enhancement_range_out_of_coverage",
                "A --range falls outside the Part's retained subtitle coverage.",
            )
        return interval
    match = next((cue for cue in cues if cue.source_ordinal == selector.source_ordinal), None)
    if match is None:
        raise EnhancementError(
            "enhancement_cue_unknown",
            f"Enhancement cue ordinal {selector.source_ordinal} is not a retained cue.",
        )
    return match.interval


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


# --- Gate-checked interval replacement (ADR 0045) ---------------------------

# The two Cue-level transcription provenance values every enhanced cue carries.
PROVENANCE_SUBTITLE_TRACK = "subtitle_track"
PROVENANCE_ASR = "asr"

# The correction-log entry kinds the gate-checked replacement records: an interval
# whose display layer was replaced by ASR, or one kept original on a gate failure.
CORRECTION_REPLACEMENT = "replacement"
CORRECTION_REJECTION = "rejection"


@dataclass(frozen=True)
class EnhancedCue:
    """One cue in the enhanced display layer, with its transcription provenance."""

    provenance: str
    interval: HalfOpenInterval
    text: str
    cue_ref: str

    def as_json(self) -> dict[str, object]:
        return {
            "provenance": self.provenance,
            "interval": _interval_as_json(self.interval),
            "text": self.text,
            "cue_ref": self.cue_ref,
        }


@dataclass(frozen=True)
class CorrectionEntry:
    """One recorded replacement or rejection over a single enhancement interval."""

    part_id: str
    interval: HalfOpenInterval
    kind: str
    reason: str
    replaced_cue_ids: tuple[str, ...]
    asr_cue_ordinals: tuple[int, ...]
    gate_reasons: tuple[str, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "part_id": self.part_id,
            "interval": _interval_as_json(self.interval),
            "kind": self.kind,
            "reason": self.reason,
            "replaced_cue_ids": list(self.replaced_cue_ids),
            "asr_cue_ordinals": list(self.asr_cue_ordinals),
            "gate_reasons": list(self.gate_reasons),
        }


@dataclass(frozen=True)
class EnhancedPart:
    """The deterministic enhanced cue basis for one Part plus its correction log."""

    part_id: str
    cues: tuple[EnhancedCue, ...]
    corrections: tuple[CorrectionEntry, ...]

    def replaced_interval_count(self) -> int:
        return sum(1 for entry in self.corrections if entry.kind == CORRECTION_REPLACEMENT)

    def rejected_interval_count(self) -> int:
        return sum(1 for entry in self.corrections if entry.kind == CORRECTION_REJECTION)

    def as_json(self) -> dict[str, object]:
        return {
            "part_id": self.part_id,
            "cues": [cue.as_json() for cue in self.cues],
            "corrections": [entry.as_json() for entry in self.corrections],
        }


def gate_checked_interval_replacement(
    *,
    part_id: str,
    retained_cues: Sequence[RetainedSubtitleCue],
    enhancement_intervals: Sequence[HalfOpenInterval],
    gate_result: CanonicalTimelineGateResult,
) -> EnhancedPart:
    """Merge ASR cues into user intervals by interval-grained gate-checked replacement.

    For each enhancement interval, the ASR cues targeting it (their raw interval
    inside it) replace the subtitle cues that lie wholly inside it *only* if every
    targeting ASR cue was admitted by the gates and at least one exists; a gate
    rejection or an absent ASR candidate keeps the original subtitle cues with a
    recorded reason (ADR 0045). Replacement is interval-grained: an interval is
    wholly ASR or wholly subtitle, never cue-level interleaved. A subtitle cue that
    straddles the interval boundary extends beyond the user-named scope, so it is
    kept unchanged rather than dropped; only cues fully within a replaced interval
    are removed from the display layer (they survive as immutable correction-log
    evidence). Every interval outcome is recorded in the correction log.
    """

    admitted = tuple(gate_result.admitted)
    rejected = tuple(gate_result.rejected)
    corrections: list[CorrectionEntry] = []
    asr_cues: list[EnhancedCue] = []
    replaced_intervals: list[HalfOpenInterval] = []

    for interval in enhancement_intervals:
        targeting_admitted = tuple(cue for cue in admitted if _within(cue.raw_interval, interval))
        targeting_rejected = tuple(cue for cue in rejected if _within(cue.raw_interval, interval))
        # Only cues lying wholly inside the interval are in the user's replacement
        # scope; a cue straddling the boundary reaches beyond it and is kept.
        enclosed = tuple(cue for cue in retained_cues if _within(cue.interval, interval))
        if targeting_rejected or not targeting_admitted:
            corrections.append(_rejection_entry(part_id, interval, enclosed, targeting_rejected))
            continue
        replaced_intervals.append(interval)
        for cue in targeting_admitted:
            asr_cues.append(
                EnhancedCue(
                    provenance=PROVENANCE_ASR,
                    interval=cue.raw_interval,
                    text=cue.text,
                    cue_ref=f"{part_id}:asr:{cue.ordinal}",
                )
            )
        corrections.append(
            CorrectionEntry(
                part_id=part_id,
                interval=interval,
                kind=CORRECTION_REPLACEMENT,
                reason="asr_cues_passed_gates",
                replaced_cue_ids=tuple(cue.cue_identity for cue in enclosed),
                asr_cue_ordinals=tuple(cue.ordinal for cue in targeting_admitted),
                gate_reasons=(),
            )
        )

    kept = tuple(
        EnhancedCue(
            provenance=PROVENANCE_SUBTITLE_TRACK,
            interval=cue.interval,
            text=cue.text,
            cue_ref=cue.cue_identity,
        )
        for cue in retained_cues
        if not any(_within(cue.interval, interval) for interval in replaced_intervals)
    )
    cues = tuple(
        sorted(
            (*kept, *asr_cues),
            key=lambda cue: (cue.interval.start.as_fraction(), cue.interval.end.as_fraction()),
        )
    )
    return EnhancedPart(part_id=part_id, cues=cues, corrections=tuple(corrections))


def _rejection_entry(
    part_id: str,
    interval: HalfOpenInterval,
    enclosed: tuple[RetainedSubtitleCue, ...],
    targeting_rejected: tuple[RejectedAsrCue, ...],
) -> CorrectionEntry:
    if targeting_rejected:
        reason = "asr_cues_failed_gates"
        gate_reasons = tuple(cue.reason for cue in targeting_rejected)
    else:
        reason = "no_asr_candidate"
        gate_reasons = ()
    return CorrectionEntry(
        part_id=part_id,
        interval=interval,
        kind=CORRECTION_REJECTION,
        reason=reason,
        replaced_cue_ids=tuple(cue.cue_identity for cue in enclosed),
        asr_cue_ordinals=(),
        gate_reasons=gate_reasons,
    )


def _within(inner: HalfOpenInterval, outer: HalfOpenInterval) -> bool:
    return (
        inner.start.as_fraction() >= outer.start.as_fraction()
        and inner.end.as_fraction() <= outer.end.as_fraction()
    )


# --- Readable correction report ---------------------------------------------


def render_correction_report(parts: Sequence[EnhancedPart]) -> str:
    """Render the correction log as readable Chinese prose (report prose defaults to Chinese)."""

    lines = ["# 增强修正报告", ""]
    for part in parts:
        lines.append(f"## 片段 {part.part_id}")
        if not part.corrections:
            lines.append("- 无修正记录。")
            lines.append("")
            continue
        for entry in part.corrections:
            window = _interval_seconds_text(entry.interval)
            if entry.kind == CORRECTION_REPLACEMENT:
                lines.append(
                    f"- 替换 区间 {window}：ASR 通过时序门禁，替换 "
                    f"{len(entry.replaced_cue_ids)} 条字幕显示层。"
                )
            else:
                detail = "、".join(entry.gate_reasons) if entry.gate_reasons else "无 ASR 候选"
                lines.append(f"- 保留 区间 {window}：原字幕保持不变（原因：{detail}）。")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- The enhance command boundary -------------------------------------------


class EnhancementReportStatus(StrEnum):
    """The recorded outcome of one enhancement attempt.

    ``complete`` retains enhanced artifacts with no gate-failure fallback;
    ``partial`` retains enhanced artifacts where at least one interval fell back to
    the original subtitle cues on a gate failure or absent candidate. ``failed``
    retains revalidation drift, an invalid scope, or an invalid projection before
    any enhanced artifact exists. ``resource_envelope_exceeded`` is a resumable
    decision pause, and ``model_acquisition_required`` is the terminal outcome when
    no Controlled offline ASR adapter fixture and no eligible model exist.
    """

    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    RESOURCE_ENVELOPE_EXCEEDED = "resource_envelope_exceeded"
    MODEL_ACQUISITION_REQUIRED = "model_acquisition_required"


@dataclass(frozen=True)
class SelectedEnhancementTrack:
    """One revalidated Primary subtitle track a named Part enhances against."""

    part_id: str
    stream_index: int
    source_candidate_sha256: str


@dataclass
class _EnhancementInputs:
    """The fully revalidated inputs threaded from revalidation into execution."""

    plan: RunPlan
    plan_path: Path
    subtitle_report: SubtitleCandidateReport
    subtitle_path: Path
    subtitle_rules_fingerprint: str
    tracks: tuple[SelectedEnhancementTrack, ...]
    cues_by_part: dict[str, tuple[RetainedSubtitleCue, ...]]
    audio_binding: AudioReportBinding
    audio_evidence: InputEvidence | None
    scope: tuple[PartEnhancementScope, ...]


def enhancement_input_manifest_document(
    subtitle_report_id: str,
    tracks: Sequence[SelectedEnhancementTrack],
    scope: Sequence[PartEnhancementScope],
) -> dict[str, object]:
    """Build the canonical input manifest binding one controlled enhancement request.

    It pins the retained subtitle report, each enhanced Part's Primary track by its
    cue-evidence hash, and the resolved enhancement intervals. Binding a controlled
    fixture to this manifest hash transitively binds its fixed ASR output to exactly
    this scope over exactly these revalidated cues.
    """

    ordered_tracks = sorted(tracks, key=lambda track: (track.part_id, track.stream_index))
    ordered_scope = sorted(scope, key=lambda part: part.part_id)
    return {
        "schema_version": 1,
        "subtitle_report_id": subtitle_report_id,
        "tracks": [
            {
                "part_id": track.part_id,
                "stream_index": track.stream_index,
                "sha256": track.source_candidate_sha256,
            }
            for track in ordered_tracks
        ],
        "scope": [part.as_json() for part in ordered_scope],
    }


def enhancement_input_manifest_sha256(document: Mapping[str, object]) -> str:
    """Return the canonical content identity of an enhancement input manifest."""

    return sha256(json.dumps(document, sort_keys=True).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EnhancementReport:
    """Immutable machine-readable result of one enhancement attempt."""

    report_id: str
    plan_id: str
    subtitle_report_id: str
    status: EnhancementReportStatus
    workspace_path: Path
    report_path: Path
    plan_evidence: InputEvidence | None
    subtitle_evidence: InputEvidence | None
    audio_evidence: InputEvidence | None
    resumed_from_report: InputEvidence | None
    resumed_from_report_id: str | None
    resumption_decision: str | None
    audio_binding: AudioReportBinding
    scope: tuple[PartEnhancementScope, ...]
    enhanced_parts: tuple[EnhancedPart, ...]
    artifacts: dict[str, object]
    contract_identity: dict[str, object] | None
    required_decision: dict[str, object] | None
    diagnostics: tuple[PlanningDiagnostic, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "report_id": self.report_id,
            "plan_id": self.plan_id,
            "subtitle_report_id": self.subtitle_report_id,
            "status": self.status.value,
            "workspace_path": self.workspace_path.as_posix(),
            "report_path": self.report_path.as_posix(),
            "input_evidence": {
                "run_plan": _evidence_json(self.plan_evidence),
                "subtitle_candidate_report": _evidence_json(self.subtitle_evidence),
                "audio_analysis_report": _evidence_json(self.audio_evidence),
                "resumed_from_report": _evidence_json(self.resumed_from_report),
                "resumed_from_report_id": self.resumed_from_report_id,
                "resumption_decision": self.resumption_decision,
            },
            "audio_analysis": self.audio_binding.as_json(),
            "audio_completeness": "not_verified",
            "verbatim_completeness_claimed": False,
            "scope": [part.as_json() for part in self.scope],
            "enhanced_parts": [part.as_json() for part in self.enhanced_parts],
            "artifacts": self.artifacts,
            "contract_identity": self.contract_identity,
            "required_decision": self.required_decision,
            "diagnostics": [diagnostic.as_json() for diagnostic in self.diagnostics],
            "guarantees": {
                "model_acquisition": "not_attempted",
                "model_execution": "not_attempted",
                "network_access": "not_attempted",
                "outputs_publication": "not_attempted",
            },
        }


def enhance(
    plan_id: str,
    subtitle_report_id: str,
    project_root: Path,
    *,
    part_selectors: Sequence[str] = (),
    range_selectors: Sequence[str] = (),
    cue_selectors: Sequence[str] = (),
    audio_report_id: str | None = None,
    resumed_from_report: InputEvidence | None = None,
    resumed_from_report_id: str | None = None,
    resumption_decision: str | None = None,
) -> dict[str, object]:
    """Create one immutable enhancement report by Gate-checked interval replacement.

    The attempt exactly revalidates the confirmed RunPlan and SourceArtifact hashes,
    the retained subtitle report and its rules, the optional Audio analysis report,
    and every user-named Part, range, and cue against retained cue identities and
    stream coverage. It then runs the Controlled offline ASR adapter over the scope,
    gates the projected cues, and replaces the display layer interval by interval --
    keeping the originals with a recorded reason on gate failure. The enhanced
    artifacts never claim verbatim completeness and never change
    ``audio_completeness``. Any drift, invalid scope, or invalid projection retains a
    ``failed`` report; each attempt owns a fresh workspace and never overwrites prior
    evidence.
    """

    report_id = uuid.uuid4().hex
    workspace_path = project_root / "work" / "enhancement-reports" / report_id
    report_path = workspace_path / "enhancement-report.json"
    builder = _ReportBuilder(
        report_id=report_id,
        plan_id=plan_id,
        subtitle_report_id=subtitle_report_id,
        workspace_path=workspace_path,
        report_path=report_path,
        resumed_from_report=resumed_from_report,
        resumed_from_report_id=resumed_from_report_id,
        resumption_decision=resumption_decision,
    )
    try:
        selectors = parse_selectors(part_selectors, range_selectors, cue_selectors)
        inputs = _revalidate_inputs(
            plan_id, subtitle_report_id, audio_report_id, selectors, project_root
        )
        builder.bind_inputs(inputs)
        _execute(builder, inputs, project_root, resumption_decision)
    except (
        EnhancementError,
        PlanningError,
        SubtitleReportError,
        TranscriptionContractError,
        TranscriptionGateError,
        OSError,
        ValueError,
    ) as error:
        builder.fail(error)

    report = builder.build()
    write_json_once(
        report_path,
        report.as_json(),
        conflict_error=lambda message: EnhancementError("enhancement_report_conflict", message),
    )
    return {"status": report.status.value, "report": report.as_json()}


def resume_enhancement(
    report_id: str, decision: str | None, project_root: Path
) -> dict[str, object]:
    """Resume one retained enhancement decision pause from an explicit user decision.

    Resumption never auto-resumes and never changes identity-bound inputs: it
    requires an explicit report ID and an explicit decision, and it may continue
    only a retained ``resource_envelope_exceeded`` pause (continued with
    ``resource_configuration_changed``). It starts a fresh attempt from the retained
    plan, subtitle, audio, and scope identities and never overwrites the paused
    report, so there is no automatic retry.
    """

    prior_path = _enhancement_report_path(project_root, report_id)
    try:
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EnhancementError(
            "enhancement_report_invalid", "Enhancement report cannot be read."
        ) from error
    if not isinstance(prior, Mapping) or prior.get("report_id") != report_id:
        raise EnhancementError("enhancement_report_invalid", "Enhancement report is invalid.")
    if decision is None:
        raise EnhancementError(
            "enhancement_resume_invalid", "Resume requires an explicit user decision."
        )
    if not _is_resource_envelope_pause(prior):
        raise EnhancementError(
            "enhancement_resume_invalid",
            "Only a retained enhancement decision pause can be resumed.",
        )
    if decision != RESOURCE_ENVELOPE_DECISION:
        raise EnhancementError(
            "enhancement_resume_invalid",
            "A resource-envelope pause requires --decision resource_configuration_changed.",
        )
    plan_id, subtitle_report_id, audio_report_id, selectors = _resumed_request(prior)
    return enhance(
        plan_id,
        subtitle_report_id,
        project_root,
        part_selectors=selectors["part"],
        range_selectors=selectors["range"],
        cue_selectors=selectors["cue"],
        audio_report_id=audio_report_id,
        resumed_from_report=input_evidence(prior_path),
        resumed_from_report_id=report_id,
        resumption_decision=decision,
    )


# --- Report assembly --------------------------------------------------------


@dataclass
class _ReportBuilder:
    report_id: str
    plan_id: str
    subtitle_report_id: str
    workspace_path: Path
    report_path: Path
    resumed_from_report: InputEvidence | None
    resumed_from_report_id: str | None
    resumption_decision: str | None
    status: EnhancementReportStatus = EnhancementReportStatus.FAILED
    plan_evidence: InputEvidence | None = None
    subtitle_evidence: InputEvidence | None = None
    audio_evidence: InputEvidence | None = None
    audio_binding: AudioReportBinding = field(
        default_factory=lambda: AudioReportBinding("not_available")
    )
    scope: tuple[PartEnhancementScope, ...] = ()
    enhanced_parts: tuple[EnhancedPart, ...] = ()
    artifacts: dict[str, object] = field(default_factory=dict)
    contract_identity: dict[str, object] | None = None
    required_decision: dict[str, object] | None = None
    diagnostics: tuple[PlanningDiagnostic, ...] = ()

    def bind_inputs(self, inputs: _EnhancementInputs) -> None:
        self.plan_id = inputs.plan.plan_id
        self.subtitle_report_id = inputs.subtitle_report.report_id
        self.plan_evidence = input_evidence(inputs.plan_path)
        self.subtitle_evidence = input_evidence(inputs.subtitle_path)
        self.audio_evidence = inputs.audio_evidence
        self.audio_binding = inputs.audio_binding
        self.scope = inputs.scope

    def fail(self, error: Exception) -> None:
        self.status = EnhancementReportStatus.FAILED
        self.enhanced_parts = ()
        self.artifacts = {}
        self.contract_identity = None
        self.required_decision = None
        self.diagnostics = (
            PlanningDiagnostic(getattr(error, "reason", "enhancement_input_invalid"), str(error)),
        )

    def build(self) -> EnhancementReport:
        return EnhancementReport(
            report_id=self.report_id,
            plan_id=self.plan_id,
            subtitle_report_id=self.subtitle_report_id,
            status=self.status,
            workspace_path=self.workspace_path,
            report_path=self.report_path,
            plan_evidence=self.plan_evidence,
            subtitle_evidence=self.subtitle_evidence,
            audio_evidence=self.audio_evidence,
            resumed_from_report=self.resumed_from_report,
            resumed_from_report_id=self.resumed_from_report_id,
            resumption_decision=self.resumption_decision,
            audio_binding=self.audio_binding,
            scope=self.scope,
            enhanced_parts=self.enhanced_parts,
            artifacts=self.artifacts,
            contract_identity=self.contract_identity,
            required_decision=self.required_decision,
            diagnostics=self.diagnostics,
        )


def _execute(
    builder: _ReportBuilder,
    inputs: _EnhancementInputs,
    project_root: Path,
    resumption_decision: str | None,
) -> None:
    """Run capability evaluation, controlled ASR, gates, and interval replacement."""

    capability_report = evaluate_asr_capabilities(project_root)
    envelope_pause = transcription_resource_envelope_pause(capability_report)
    if envelope_pause is not None:
        builder.status = EnhancementReportStatus.RESOURCE_ENVELOPE_EXCEEDED
        builder.required_decision = {
            "reason": "resource_envelope_exceeded",
            "decision": RESOURCE_ENVELOPE_DECISION,
        }
        builder.diagnostics = (
            PlanningDiagnostic(
                "resource_envelope_exceeded",
                "A conservative ASR resource estimate exceeds the 24 GiB envelope; reconfigure "
                "rather than silently change model, quantization, or batch.",
            ),
        )
        return

    contracts = revalidate_asr_contracts(project_root)
    manifest_document = enhancement_input_manifest_document(
        inputs.subtitle_report.report_id, inputs.tracks, inputs.scope
    )
    manifest_sha = enhancement_input_manifest_sha256(manifest_document)
    fixture = load_controlled_asr_fixture(contracts.controlled_adapter.document, project_root)
    if fixture is None:
        builder.status = EnhancementReportStatus.MODEL_ACQUISITION_REQUIRED
        builder.diagnostics = (
            PlanningDiagnostic(
                "model_acquisition_required",
                "No Controlled offline ASR adapter fixture and no acquired offline ASR model is "
                "available for enhancement.",
            ),
        )
        return
    if fixture.input_fixture_sha256 != manifest_sha:
        raise EnhancementError(
            "enhancement_fixture_input_mismatch",
            "Controlled ASR adapter fixture is not bound to this revalidated enhancement scope.",
        )

    manifest_path = builder.workspace_path / "provenance" / "input-manifest.json"
    write_json_once(
        manifest_path,
        manifest_document,
        conflict_error=lambda message: EnhancementError("enhancement_report_conflict", message),
    )
    raw_pointer = retain_restricted_raw_output(
        fixture.raw_output,
        builder.workspace_path,
        capability=fixture.capability,
        label="enhancement",
    )
    projection = project_asr_output(_decode_output(fixture.raw_output), contracts)
    if projection.state != "projected":
        message = (
            projection.diagnostic.message
            if projection.diagnostic is not None
            else ("The controlled ASR output is invalid.")
        )
        raise EnhancementError("model_output_invalid", message)

    gate_rules = load_timing_gate_ruleset(project_root)
    enhanced_parts = tuple(
        _enhance_part(part, projection.cues, inputs.cues_by_part[part.part_id], gate_rules)
        for part in inputs.scope
    )
    builder.enhanced_parts = enhanced_parts
    builder.contract_identity = {
        "projection_schema": contracts.projection_schema.as_json(),
        "controlled_adapter": contracts.controlled_adapter.as_json(),
        "input_manifest": {**input_evidence(manifest_path).as_json(), "sha256": manifest_sha},
        "timing_gate_version": gate_rules.version,
        "capability": fixture.capability,
    }
    builder.artifacts = _write_enhancement_artifacts(
        builder.workspace_path, enhanced_parts, raw_pointer.as_json()
    )
    rejected = sum(part.rejected_interval_count() for part in enhanced_parts)
    builder.status = (
        EnhancementReportStatus.PARTIAL if rejected else EnhancementReportStatus.COMPLETE
    )
    builder.diagnostics = ()


def _enhance_part(
    scope: PartEnhancementScope,
    all_cues: Sequence[ProjectedAsrCue],
    retained_cues: tuple[RetainedSubtitleCue, ...],
    gate_rules: TimingGateRuleset,
) -> EnhancedPart:
    """Gate this Part's ASR cues onto its canonical timeline, then replace intervals."""

    envelope = part_coverage_envelope(retained_cues)
    part_cues = tuple(cue for cue in all_cues if _within(cue.interval, envelope))
    gate_result = gate_projected_cues(
        part_id=scope.part_id,
        cues=part_cues,
        part_coverage=StreamCoverage(coverage=envelope, gaps=(), diagnostics=()),
        timeline=CollectionTimeline(parts=(TimelinePart(scope.part_id, envelope),)),
        rules=gate_rules,
    )
    return gate_checked_interval_replacement(
        part_id=scope.part_id,
        retained_cues=retained_cues,
        enhancement_intervals=scope.intervals,
        gate_result=gate_result,
    )


def _write_enhancement_artifacts(
    workspace_path: Path,
    enhanced_parts: Sequence[EnhancedPart],
    raw_output_pointer: dict[str, object],
) -> dict[str, object]:
    """Write the enhanced artifacts, correction log, and readable correction report.

    Each artifact is immutable and stays in the workspace; publication is a later,
    separately authorized stage. The enhanced subtitle and transcript artifacts
    carry per-cue provenance and never claim verbatim completeness.
    """

    artifacts_dir = workspace_path / "artifacts"
    subtitles_document = {
        "schema_version": 1,
        "artifact_class": "subtitles.enhanced",
        "verbatim_completeness_claimed": False,
        "parts": [
            {"part_id": part.part_id, "cues": [cue.as_json() for cue in part.cues]}
            for part in enhanced_parts
        ],
    }
    transcript_document = {
        "schema_version": 1,
        "artifact_class": "transcript.enhanced",
        "verbatim_completeness_claimed": False,
        "parts": [
            {
                "part_id": part.part_id,
                "text": "\n".join(cue.text for cue in part.cues),
            }
            for part in enhanced_parts
        ],
    }
    correction_log_document = {
        "schema_version": 1,
        "parts": [part.as_json() for part in enhanced_parts],
    }
    subtitles_path = artifacts_dir / "subtitles.enhanced.json"
    transcript_path = artifacts_dir / "transcript.enhanced.json"
    correction_log_path = artifacts_dir / "correction-log.json"
    correction_report_path = artifacts_dir / "correction-report.md"
    _write_json_once(subtitles_path, subtitles_document)
    _write_json_once(transcript_path, transcript_document)
    _write_json_once(correction_log_path, correction_log_document)
    write_text_once(
        correction_report_path,
        render_correction_report(enhanced_parts),
        conflict_error=lambda message: EnhancementError("enhancement_report_conflict", message),
    )
    return {
        "subtitles_enhanced": input_evidence(subtitles_path).as_json(),
        "transcript_enhanced": input_evidence(transcript_path).as_json(),
        "correction_log": input_evidence(correction_log_path).as_json(),
        "correction_report": input_evidence(correction_report_path).as_json(),
        "restricted_raw_output": raw_output_pointer,
    }


# --- Revalidation -----------------------------------------------------------


def _revalidate_inputs(
    plan_id: str,
    subtitle_report_id: str,
    audio_report_id: str | None,
    selectors: Sequence[EnhancementSelector],
    project_root: Path,
) -> _EnhancementInputs:
    plan_path = project_root / "plans" / plan_id / "run-plan.json"
    plan = load_run_plan(plan_path)
    if plan.plan_id != plan_id:
        raise EnhancementError(
            "run_plan_not_confirmed", "RunPlan identity does not match the requested plan ID."
        )
    confirmed_report = load_plan_report(
        project_root / "plans" / "reports" / plan.report_id / "plan-report.json"
    )
    if not confirmed_plan_matches(confirmed_report, plan):
        raise EnhancementError(
            "run_plan_not_confirmed", "RunPlan evidence does not match a confirmed PlanReport."
        )
    revalidate_confirmed_inspection_evidence(
        confirmed_report,
        plan,
        drift_error=lambda: EnhancementError(
            "inspection_evidence_changed",
            "PlanReport inspection evidence no longer matches the confirmed RunPlan.",
        ),
    )
    expected_subtitle_id = _validated_subtitle_report_id(subtitle_report_id)
    subtitle_path = _subtitle_report_path(project_root, plan.source_artifacts, expected_subtitle_id)
    subtitle_report = _load_subtitle_report(subtitle_path)
    if subtitle_report.report_id != expected_subtitle_id or subtitle_report.plan_id != plan.plan_id:
        raise EnhancementError(
            "subtitle_report_mismatch",
            "Subtitle candidate report does not belong to this RunPlan.",
        )
    rules_fingerprint = _revalidate_subtitle_rules(subtitle_report, project_root)

    named_parts = {selector.part_id for selector in selectors}
    tracks, cues_by_part = _revalidate_named_tracks(plan, subtitle_report, named_parts)
    scope = resolve_enhancement_scope(selectors, cues_by_part=cues_by_part)

    audio_binding = AudioReportBinding("not_available")
    audio_evidence: InputEvidence | None = None
    if audio_report_id is not None:
        audio_evidence, audio_binding = _bind_audio_report(
            project_root, audio_report_id, plan.plan_id, subtitle_report.report_id
        )
    return _EnhancementInputs(
        plan=plan,
        plan_path=plan_path,
        subtitle_report=subtitle_report,
        subtitle_path=subtitle_path,
        subtitle_rules_fingerprint=rules_fingerprint,
        tracks=tracks,
        cues_by_part=cues_by_part,
        audio_binding=audio_binding,
        audio_evidence=audio_evidence,
        scope=scope,
    )


def _revalidate_named_tracks(
    plan: RunPlan, report: SubtitleCandidateReport, named_parts: set[str]
) -> tuple[tuple[SelectedEnhancementTrack, ...], dict[str, tuple[RetainedSubtitleCue, ...]]]:
    """Revalidate each named Part's Primary subtitle track and load its cue basis."""

    plan_part_ids = {artifact.source_id for artifact in plan.source_artifacts}
    selections = {selection.source_id: selection.stream_index for selection in report.selections}
    tracks: list[SelectedEnhancementTrack] = []
    cues_by_part: dict[str, tuple[RetainedSubtitleCue, ...]] = {}
    for part_id in sorted(named_parts):
        if part_id not in plan_part_ids:
            raise EnhancementError(
                "enhancement_part_unknown",
                f"Enhancement Part {part_id!r} is not a Part of this RunPlan.",
            )
        valid = [
            candidate
            for candidate in report.candidates
            if candidate.source_id == part_id and candidate.state is CandidateState.VALID
        ]
        selected = _selected_candidate(valid, selections.get(part_id))
        if selected.source_candidate_path is None or selected.source_candidate_sha256 is None:
            raise EnhancementError(
                "enhancement_track_changed",
                f"Enhancement Part {part_id!r} has incomplete retained subtitle evidence.",
            )
        candidate_path = Path(selected.source_candidate_path)
        # Hash through ``input_evidence`` (which routes to ``evidence.sha256_file``)
        # so the read is of retained subtitle evidence, never of source media.
        if input_evidence(candidate_path).sha256 != selected.source_candidate_sha256:
            raise EnhancementError(
                "enhancement_track_changed",
                f"Enhancement Part {part_id!r} subtitle evidence hash no longer matches.",
            )
        cues_by_part[part_id] = load_retained_subtitle_cues(
            candidate_path, part_id=part_id, stream_index=selected.stream_index
        )
        tracks.append(
            SelectedEnhancementTrack(
                part_id=part_id,
                stream_index=selected.stream_index,
                source_candidate_sha256=selected.source_candidate_sha256,
            )
        )
    return tuple(tracks), cues_by_part


def _selected_candidate(
    valid: list[SubtitleCandidate], selected_stream_index: int | None
) -> SubtitleCandidate:
    if not valid:
        raise EnhancementError(
            "enhancement_part_unavailable",
            "A named enhancement Part has no valid Primary subtitle track.",
        )
    if len(valid) == 1:
        return valid[0]
    match = next(
        (candidate for candidate in valid if candidate.stream_index == selected_stream_index), None
    )
    if match is None:
        raise EnhancementError(
            "enhancement_selection_unresolved",
            "A named enhancement Part has multiple valid tracks without a retained selection.",
        )
    return match


# --- Shared helpers (mirroring the transcribe revalidation contract) --------


def _revalidate_subtitle_rules(report: SubtitleCandidateReport, project_root: Path) -> str:
    current = subtitle_rules_fingerprint(project_root)
    if report.subtitle_rules_fingerprint != current:
        raise EnhancementError(
            "subtitle_rules_changed",
            "Subtitle rules no longer match the retained candidate report.",
        )
    return current


def _bind_audio_report(
    project_root: Path, audio_report_id: str, plan_id: str, subtitle_report_id: str
) -> tuple[InputEvidence, AudioReportBinding]:
    validated_id = validated_report_id(
        audio_report_id,
        invalid_error=lambda: EnhancementError(
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
        raise EnhancementError(
            "audio_report_invalid", "Audio analysis report cannot be read."
        ) from error
    if not isinstance(decoded, Mapping) or decoded.get("report_id") != validated_id:
        raise EnhancementError("audio_report_invalid", "Audio analysis report is invalid.")
    if decoded.get("plan_id") != plan_id or decoded.get("subtitle_report_id") != subtitle_report_id:
        raise EnhancementError(
            "audio_report_mismatch",
            "Audio analysis report is not bound to this RunPlan and subtitle report.",
        )
    return input_evidence(audio_path), AudioReportBinding(
        "bound",
        report_id=validated_id,
        plan_id=plan_id,
        subtitle_report_id=subtitle_report_id,
    )


def _is_resource_envelope_pause(report: Mapping[str, object]) -> bool:
    required_decision = report.get("required_decision")
    return (
        report.get("status") == EnhancementReportStatus.RESOURCE_ENVELOPE_EXCEEDED.value
        and isinstance(required_decision, Mapping)
        and required_decision.get("reason") == "resource_envelope_exceeded"
    )


def _resumed_request(
    report: Mapping[str, object],
) -> tuple[str, str, str | None, dict[str, list[str]]]:
    plan_id = report.get("plan_id")
    subtitle_report_id = report.get("subtitle_report_id")
    if not isinstance(plan_id, str) or not isinstance(subtitle_report_id, str):
        raise EnhancementError(
            "enhancement_report_invalid", "Paused report omits its identity-bound inputs."
        )
    audio = report.get("audio_analysis")
    audio_report_id = audio.get("report_id") if isinstance(audio, Mapping) else None
    return (
        plan_id,
        subtitle_report_id,
        audio_report_id if isinstance(audio_report_id, str) else None,
        _resumed_selectors(report.get("scope")),
    )


def _resumed_selectors(scope: object) -> dict[str, list[str]]:
    """Rebuild explicit ``--range`` selectors from a paused report's resolved scope.

    A paused report retains its resolved intervals, so the resume reconstructs the
    exact same scope as explicit range selectors -- the identity-bound inputs are
    never re-derived from anything but the retained report.
    """

    ranges: list[str] = []
    if isinstance(scope, list):
        for part in scope:
            if not isinstance(part, Mapping):
                continue
            part_id = part.get("part_id")
            intervals = part.get("intervals")
            if not isinstance(part_id, str) or not isinstance(intervals, list):
                continue
            for interval in intervals:
                ranges.append(f"{part_id}:{_interval_range_text(interval)}")
    return {"part": [], "range": ranges, "cue": []}


# --- Path resolution and small serializers ----------------------------------


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


def _load_subtitle_report(path: Path) -> SubtitleCandidateReport:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EnhancementError(
            "subtitle_report_invalid", "Subtitle candidate report cannot be read."
        ) from error
    return SubtitleCandidateReport.from_json(decoded, path)


def _validated_subtitle_report_id(value: str) -> str:
    return validated_report_id(
        value,
        invalid_error=lambda: EnhancementError(
            "subtitle_report_invalid", "Subtitle candidate report ID must be a UUID."
        ),
    )


def _enhancement_report_path(project_root: Path, report_id: str) -> Path:
    validated_id = validated_report_id(
        report_id,
        invalid_error=lambda: EnhancementError(
            "enhancement_report_invalid", "Enhancement report ID must be a UUID."
        ),
    )
    return project_root / "work" / "enhancement-reports" / validated_id / "enhancement-report.json"


def _decode_output(raw_output: bytes) -> object:
    try:
        return json.loads(raw_output)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _write_json_once(path: Path, payload: object) -> None:
    write_json_once(
        path,
        payload,
        conflict_error=lambda message: EnhancementError("enhancement_report_conflict", message),
    )


def _evidence_json(evidence_record: InputEvidence | None) -> dict[str, object] | None:
    return evidence_record.as_json() if evidence_record is not None else None


def _interval_from_json(value: object) -> HalfOpenInterval:
    if not isinstance(value, Mapping):
        raise EnhancementError(
            "enhancement_cue_basis_invalid", "A subtitle cue omits its raw-PTS interval."
        )
    return HalfOpenInterval(
        _exact_time_from_json(value.get("start")), _exact_time_from_json(value.get("end"))
    )


def _exact_time_from_json(value: object) -> ExactTime:
    if not isinstance(value, Mapping):
        raise EnhancementError(
            "enhancement_cue_basis_invalid", "A subtitle cue time is not an object."
        )
    numerator = value.get("numerator")
    denominator = value.get("denominator")
    if not _is_int(numerator) or not _is_int(denominator) or denominator <= 0:
        raise EnhancementError("enhancement_cue_basis_invalid", "A subtitle cue time is malformed.")
    return ExactTime(numerator, denominator)


def _interval_as_json(interval: HalfOpenInterval) -> dict[str, object]:
    return {"start": _time_as_json(interval.start), "end": _time_as_json(interval.end)}


def _time_as_json(value: ExactTime) -> dict[str, int]:
    return {"numerator": value.numerator, "denominator": value.denominator}


def _interval_seconds_text(interval: HalfOpenInterval) -> str:
    return f"[{_seconds_text(interval.start)}, {_seconds_text(interval.end)})"


def _interval_range_text(interval: object) -> str:
    if not isinstance(interval, Mapping):
        raise EnhancementError(
            "enhancement_report_invalid", "A paused scope interval is malformed."
        )
    start = _seconds_text(_exact_time_from_json(interval.get("start")))
    end = _seconds_text(_exact_time_from_json(interval.get("end")))
    return f"{start}-{end}"


def _seconds_text(value: ExactTime) -> str:
    # Exact rational rendering (``5`` or ``7/2``), never float division: the resume
    # path re-parses this text through ``Fraction`` to rebuild the identity-bound
    # ``--range`` scope, so a lossy ``0.3333...`` would drift off the retained interval.
    return str(value.as_fraction())


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_non_negative_int(value: object) -> TypeGuard[int]:
    return _is_int(value) and value >= 0


__all__ = [
    "CORRECTION_REJECTION",
    "CORRECTION_REPLACEMENT",
    "PROVENANCE_ASR",
    "PROVENANCE_SUBTITLE_TRACK",
    "CorrectionEntry",
    "CueSelector",
    "EnhancedCue",
    "EnhancedPart",
    "EnhancementError",
    "EnhancementReport",
    "EnhancementReportStatus",
    "PartEnhancementScope",
    "PartSelector",
    "RangeSelector",
    "RetainedSubtitleCue",
    "SelectedEnhancementTrack",
    "enhance",
    "enhancement_input_manifest_document",
    "enhancement_input_manifest_sha256",
    "gate_checked_interval_replacement",
    "load_retained_subtitle_cues",
    "parse_selectors",
    "part_coverage_envelope",
    "render_correction_report",
    "resolve_enhancement_scope",
    "resume_enhancement",
]
