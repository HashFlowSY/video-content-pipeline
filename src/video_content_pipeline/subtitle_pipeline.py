"""Phase 4 retained processing for one verified embedded SRT subtitle track."""

from __future__ import annotations

import codecs
import hashlib
import json
import subprocess
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Literal

from video_content_pipeline.coverage import StreamCoverage
from video_content_pipeline.external_tools import (
    PinnedExternalTool,
    revalidate_external_tool,
    run_tool,
)
from video_content_pipeline.inspection import PlanInspectionEvidence, SubtitleTrackCandidate
from video_content_pipeline.planning import (
    PlanningDiagnostic,
    PlanState,
    RunPlan,
    load_plan_report,
    load_run_plan,
)
from video_content_pipeline.source import (
    SourceArtifact,
    SourceIntakeError,
    calculate_disk_headroom,
    ensure_disk_headroom,
    sha256_file,
)
from video_content_pipeline.subtitles import (
    FormatProjectionLoss,
    SubtitleTrack,
    accept_subtitle_track,
    readable_output,
    serialize_source_srt,
    serialize_source_vtt,
    serialize_vtt,
    source_srt_projection_losses,
)
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval


class CandidateState(StrEnum):
    """The retained eligibility state of one embedded subtitle candidate."""

    VALID = "valid"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"
    ENCODING_AMBIGUOUS = "encoding_ambiguous"
    INCOMPLETE = "incomplete"


class CandidateReportState(StrEnum):
    """The complete processing outcome of one immutable candidate report."""

    BLOCKED = "blocked"
    COMPLETED = "completed"
    AWAITING_SUBTITLE_SELECTION = "awaiting_subtitle_selection"


SUBTITLE_MAX_PAYLOAD_BYTES = 256 * 1024**2
SUBTITLE_EXTRACTION_TIMEOUT_SECONDS = 300


class SubtitleReportError(ValueError):
    """A malformed retained subtitle report with a machine-readable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class _AmbiguousEncoding(UnicodeError):
    """Raised when automatic decoding would require a guess."""


def _run_extraction(command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    """Run extraction with a bounded timeout while tolerating simple test doubles."""

    try:
        return run_tool(command, timeout_seconds=SUBTITLE_EXTRACTION_TIMEOUT_SECONDS)
    except TypeError as error:
        if "timeout_seconds" not in str(error):
            raise
        return run_tool(command)


def _decode_payload(payload: bytes, requested_decoder: str | None) -> tuple[str, str]:
    if requested_decoder is not None:
        return payload.decode(requested_decoder, errors="strict"), requested_decoder
    if payload.startswith(codecs.BOM_UTF8):
        return payload.decode("utf-8-sig", errors="strict"), "utf-8-sig"
    if payload.startswith(codecs.BOM_UTF16_LE) or payload.startswith(codecs.BOM_UTF16_BE):
        return payload.decode("utf-16", errors="strict"), "utf-16"
    try:
        return payload.decode("utf-8", errors="strict"), "utf-8"
    except UnicodeDecodeError as error:
        raise _AmbiguousEncoding() from error


@dataclass(frozen=True)
class RawPayloadEvidence:
    """Hash-recorded payload evidence retained even after a failed extraction."""

    path: str | None
    sha256: str | None
    byte_count: int | None


@dataclass(frozen=True)
class ExtractionFormat:
    """The FFmpeg conversion required to parse one supported text subtitle payload."""

    source_format: Literal["srt", "vtt"]
    ffmpeg_codec: Literal["copy", "srt"]
    ffmpeg_muxer: Literal["srt", "webvtt"]


@dataclass(frozen=True)
class CandidateArtifacts:
    """Immutable source and readable exports for one atomically accepted candidate."""

    source_vtt_path: str
    source_srt_path: str
    readable_vtt_path: str
    readable_corrections_path: str
    projection_losses: tuple[FormatProjectionLoss, ...]


@dataclass(frozen=True)
class SubtitleCandidate:
    """One retained extraction and atomic validation outcome."""

    source_id: str
    stream_index: int
    state: CandidateState
    source_format: Literal["srt", "vtt"] | None = None
    raw_payload_path: str | None = None
    raw_payload_sha256: str | None = None
    raw_payload_bytes: int | None = None
    source_candidate_path: str | None = None
    source_candidate_sha256: str | None = None
    source_vtt_path: str | None = None
    source_srt_path: str | None = None
    readable_vtt_path: str | None = None
    readable_corrections_path: str | None = None
    format_projection_losses: tuple[FormatProjectionLoss, ...] = ()
    cue_count: int | None = None
    coverage_start: dict[str, int] | None = None
    diagnostic: PlanningDiagnostic | None = None
    attempt_id: str | None = None
    codec: str | None = None
    decoder: str | None = None

    def as_json(self) -> dict[str, object]:
        result: dict[str, object] = {
            "source_id": self.source_id,
            "stream_index": self.stream_index,
            "state": self.state.value,
            "attempt_id": self.attempt_id,
            "codec": self.codec,
            "decoder": self.decoder,
            "source_format": self.source_format,
            "raw_payload_path": self.raw_payload_path,
            "raw_payload_sha256": self.raw_payload_sha256,
            "raw_payload_bytes": self.raw_payload_bytes,
            "source_candidate_path": self.source_candidate_path,
            "source_candidate_sha256": self.source_candidate_sha256,
            "source_vtt_path": self.source_vtt_path,
            "source_srt_path": self.source_srt_path,
            "readable_vtt_path": self.readable_vtt_path,
            "readable_corrections_path": self.readable_corrections_path,
            "format_projection_losses": [loss.as_json() for loss in self.format_projection_losses],
            "cue_count": self.cue_count,
            "coverage_start": self.coverage_start,
        }
        if self.diagnostic is not None:
            result["diagnostic"] = self.diagnostic.as_json()
        return result

    @classmethod
    def from_json(cls, value: object) -> SubtitleCandidate:
        """Load candidate evidence without altering its retained outcome."""

        if not isinstance(value, Mapping):
            raise SubtitleReportError("subtitle_report_invalid", "Candidate must be an object.")
        try:
            losses = value.get("format_projection_losses", [])
            if not isinstance(losses, list):
                raise ValueError("format projection losses must be a list")
            diagnostic = value.get("diagnostic")
            return cls(
                source_id=_required_string(value, "source_id"),
                stream_index=_required_nonnegative_int(value, "stream_index"),
                state=CandidateState(_required_string(value, "state")),
                attempt_id=_optional_string(value.get("attempt_id")),
                codec=_optional_string(value.get("codec")),
                decoder=_optional_string(value.get("decoder")),
                source_format=_optional_format(value.get("source_format")),
                raw_payload_path=_optional_string(value.get("raw_payload_path")),
                raw_payload_sha256=_optional_string(value.get("raw_payload_sha256")),
                raw_payload_bytes=_optional_nonnegative_int(value.get("raw_payload_bytes")),
                source_candidate_path=_optional_string(value.get("source_candidate_path")),
                source_candidate_sha256=_optional_string(value.get("source_candidate_sha256")),
                source_vtt_path=_optional_string(value.get("source_vtt_path")),
                source_srt_path=_optional_string(value.get("source_srt_path")),
                readable_vtt_path=_optional_string(value.get("readable_vtt_path")),
                readable_corrections_path=_optional_string(value.get("readable_corrections_path")),
                format_projection_losses=tuple(_projection_loss(loss) for loss in losses),
                cue_count=_optional_nonnegative_int(value.get("cue_count")),
                coverage_start=_optional_time(value.get("coverage_start")),
                diagnostic=_planning_diagnostic(diagnostic) if diagnostic is not None else None,
            )
        except (TypeError, ValueError) as error:
            raise SubtitleReportError(
                "subtitle_report_invalid", "Candidate report contains an invalid candidate."
            ) from error


@dataclass(frozen=True)
class SubtitleTrackSelection:
    """One explicit user choice retained independently from the immutable RunPlan."""

    source_id: str
    stream_index: int

    def as_json(self) -> dict[str, object]:
        return {"source_id": self.source_id, "stream_index": self.stream_index}


@dataclass(frozen=True)
class SubtitleCandidateReport:
    """Immutable result of one Phase 4 candidate processing attempt."""

    report_id: str
    plan_id: str
    state: CandidateReportState
    subtitle_rules_fingerprint: str | None
    candidates: tuple[SubtitleCandidate, ...]
    diagnostics: tuple[PlanningDiagnostic, ...]
    report_path: Path
    parent_report_id: str | None = None
    selections: tuple[SubtitleTrackSelection, ...] = ()

    def as_json(self) -> dict[str, object]:
        return {
            "report_id": self.report_id,
            "parent_report_id": self.parent_report_id,
            "plan_id": self.plan_id,
            "state": self.state.value,
            "subtitle_rules_fingerprint": self.subtitle_rules_fingerprint,
            "candidates": [candidate.as_json() for candidate in self.candidates],
            "diagnostics": [diagnostic.as_json() for diagnostic in self.diagnostics],
            "selections": [selection.as_json() for selection in self.selections],
            "report_path": self.report_path.as_posix(),
        }

    @classmethod
    def from_json(cls, value: object, report_path: Path) -> SubtitleCandidateReport:
        """Load an immutable report using its expected project-owned path."""

        if not isinstance(value, Mapping):
            raise SubtitleReportError(
                "subtitle_report_invalid", "Candidate report must be an object."
            )
        try:
            candidates = value.get("candidates")
            diagnostics = value.get("diagnostics")
            selections = value.get("selections", [])
            if not all(isinstance(items, list) for items in (candidates, diagnostics, selections)):
                raise ValueError("Candidate report collections must be lists")
            assert isinstance(candidates, list)
            assert isinstance(diagnostics, list)
            assert isinstance(selections, list)
            return cls(
                report_id=_required_string(value, "report_id"),
                plan_id=_required_string(value, "plan_id"),
                state=CandidateReportState(_required_string(value, "state")),
                subtitle_rules_fingerprint=_optional_string(
                    value.get("subtitle_rules_fingerprint")
                ),
                candidates=tuple(
                    SubtitleCandidate.from_json(candidate) for candidate in candidates
                ),
                diagnostics=tuple(_planning_diagnostic(diagnostic) for diagnostic in diagnostics),
                report_path=report_path,
                parent_report_id=_optional_string(value.get("parent_report_id")),
                selections=tuple(_selection_from_json(selection) for selection in selections),
            )
        except (TypeError, ValueError) as error:
            raise SubtitleReportError(
                "subtitle_report_invalid", "Candidate report has an invalid schema."
            ) from error


def process_subtitles(
    plan_id: str,
    project_root: Path,
    requested_decoders: tuple[str, ...] = (),
) -> dict[str, object]:
    """Revalidate one confirmed plan before retaining its supported subtitle candidates."""

    report_id = uuid.uuid4().hex
    try:
        plan = load_run_plan(project_root / "plans" / plan_id / "run-plan.json")
        diagnostics = _revalidate_plan(plan, project_root)
        if diagnostics:
            return _persist_blocked_report(project_root, plan_id, report_id, diagnostics)
        report = load_plan_report(
            project_root / "plans" / "reports" / plan.report_id / "plan-report.json"
        )
        if report.state is not PlanState.READY_FOR_CONFIRMATION or not _matches_plan(report, plan):
            return _persist_blocked_report(
                project_root,
                plan_id,
                report_id,
                (
                    PlanningDiagnostic(
                        "run_plan_not_confirmed", "RunPlan evidence does not match confirmation."
                    ),
                ),
            )
        rules_fingerprint = subtitle_rules_fingerprint(project_root)
        _ensure_subtitle_headroom(plan, report, project_root)
        decoders = _parse_decoders(requested_decoders)
    except SubtitleReportError as error:
        return _persist_blocked_report(
            project_root,
            plan_id,
            report_id,
            (PlanningDiagnostic(error.reason, str(error)),),
        )
    except SourceIntakeError as error:
        return _persist_blocked_report(
            project_root,
            plan_id,
            report_id,
            (PlanningDiagnostic(error.reason, str(error)),),
        )
    except (OSError, ValueError) as error:
        reason = getattr(error, "reason", "run_plan_not_confirmed")
        return _persist_blocked_report(
            project_root, plan_id, report_id, (PlanningDiagnostic(reason, str(error)),)
        )

    candidates: list[SubtitleCandidate] = []
    for artifact, evidence in zip(plan.source_artifacts, report.inspection_evidence, strict=True):
        candidates.extend(
            _extract_supported_candidates(
                artifact, evidence, plan, report_id, project_root, decoders
            )
        )
    decoder_diagnostics = _validate_decoder_targets(tuple(candidates), decoders)
    if decoder_diagnostics:
        report_path = _report_path(project_root, plan.source_artifacts, report_id)
        candidate_report = SubtitleCandidateReport(
            report_id,
            plan.plan_id,
            CandidateReportState.BLOCKED,
            rules_fingerprint,
            tuple(candidates),
            decoder_diagnostics,
            report_path,
        )
        _write_json_once(report_path, candidate_report.as_json())
        return {"status": "blocked", "report": candidate_report.as_json()}
    ambiguous_source_ids = _ambiguous_source_ids(candidates)
    state = _initial_report_state(candidates, ambiguous_source_ids)
    report_diagnostics = tuple(
        PlanningDiagnostic(
            "subtitle_selection_required",
            f"Part {source_id} has multiple valid embedded subtitle tracks.",
        )
        for source_id in ambiguous_source_ids
    )
    report_path = _report_path(project_root, plan.source_artifacts, report_id)
    candidate_report = SubtitleCandidateReport(
        report_id,
        plan.plan_id,
        state,
        rules_fingerprint,
        tuple(candidates),
        report_diagnostics,
        report_path,
    )
    _write_json_once(report_path, candidate_report.as_json())
    return {"status": state.value, "report": candidate_report.as_json()}


def resume_subtitles(
    plan_id: str,
    parent_report_id: str,
    requested_selections: tuple[str, ...],
    project_root: Path,
    requested_decoders: tuple[str, ...] = (),
) -> dict[str, object]:
    """Append an explicit choice to a retained ambiguous report after revalidation."""

    report_id = uuid.uuid4().hex
    try:
        plan = load_run_plan(project_root / "plans" / plan_id / "run-plan.json")
        diagnostics = _revalidate_plan(plan, project_root)
        if diagnostics:
            return _persist_blocked_report(project_root, plan_id, report_id, diagnostics)
        report = load_plan_report(
            project_root / "plans" / "reports" / plan.report_id / "plan-report.json"
        )
        if report.state is not PlanState.READY_FOR_CONFIRMATION or not _matches_plan(report, plan):
            return _persist_blocked_report(
                project_root,
                plan_id,
                report_id,
                (
                    PlanningDiagnostic(
                        "run_plan_not_confirmed", "RunPlan evidence does not match confirmation."
                    ),
                ),
            )
        expected_parent_report_id = _validated_report_id(parent_report_id)
        parent_path = _report_path(project_root, plan.source_artifacts, expected_parent_report_id)
        parent_report = _load_candidate_report(parent_path)
        if parent_report.report_id != parent_report_id or parent_report.plan_id != plan.plan_id:
            raise SubtitleReportError(
                "subtitle_report_mismatch", "Candidate report does not belong to this RunPlan."
            )
        if parent_report.state not in {
            CandidateReportState.AWAITING_SUBTITLE_SELECTION,
            CandidateReportState.BLOCKED,
        }:
            raise SubtitleReportError(
                "subtitle_selection_not_pending",
                "Only a pending subtitle report can be resumed.",
            )
        rules_fingerprint = subtitle_rules_fingerprint(project_root)
        if rules_fingerprint != parent_report.subtitle_rules_fingerprint:
            raise SubtitleReportError(
                "subtitle_rules_changed",
                "Subtitle rules no longer match the retained candidate report.",
            )
        _ensure_subtitle_headroom(plan, report, project_root)
        decoders = _parse_decoders(requested_decoders)
        decoder_diagnostics = _validate_decoder_targets(parent_report.candidates, decoders)
        if decoder_diagnostics:
            return _persist_blocked_report(project_root, plan_id, report_id, decoder_diagnostics)
        candidates = _resolve_decoder_candidates(
            parent_report.candidates,
            decoders,
            report,
        )
        selections = tuple(_parse_selection(value) for value in requested_selections)
        diagnostics = _validate_selections(candidates, selections)
        if diagnostics:
            return _persist_blocked_report(project_root, plan_id, report_id, diagnostics)
        ambiguous_source_ids = _ambiguous_source_ids(list(candidates))
        if ambiguous_source_ids and not selections:
            return _persist_blocked_report(
                project_root,
                plan_id,
                report_id,
                tuple(
                    PlanningDiagnostic(
                        "subtitle_selection_required",
                        f"Part {source_id} has multiple valid embedded subtitle tracks.",
                    )
                    for source_id in ambiguous_source_ids
                ),
            )
    except (OSError, ValueError) as error:
        reason = getattr(error, "reason", "subtitle_report_invalid")
        return _persist_blocked_report(
            project_root, plan_id, report_id, (PlanningDiagnostic(reason, str(error)),)
        )

    report_path = _report_path(project_root, plan.source_artifacts, report_id)
    resumed_state = (
        CandidateReportState.AWAITING_SUBTITLE_SELECTION
        if _ambiguous_source_ids(list(candidates)) and not selections
        else (
            CandidateReportState.COMPLETED
            if any(candidate.state is CandidateState.VALID for candidate in candidates)
            else CandidateReportState.BLOCKED
        )
    )
    resumed_report = SubtitleCandidateReport(
        report_id=report_id,
        plan_id=plan.plan_id,
        state=resumed_state,
        subtitle_rules_fingerprint=rules_fingerprint,
        candidates=candidates,
        diagnostics=(
            tuple(
                PlanningDiagnostic(
                    "subtitle_selection_required",
                    f"Part {source_id} has multiple valid embedded subtitle tracks.",
                )
                for source_id in _ambiguous_source_ids(list(candidates))
            )
            if resumed_state is CandidateReportState.AWAITING_SUBTITLE_SELECTION
            else ()
        ),
        report_path=report_path,
        parent_report_id=parent_report.report_id,
        selections=selections,
    )
    _write_json_once(report_path, resumed_report.as_json())
    return {"status": resumed_report.state.value, "report": resumed_report.as_json()}


def subtitle_rules_fingerprint(project_root: Path) -> str:
    """Validate and fingerprint the versioned, project-owned Phase 4 rules."""

    rules_path = project_root / "config" / "subtitle-rules.json"
    try:
        raw_rules = rules_path.read_bytes()
        decoded = json.loads(raw_rules)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("subtitle_rules_invalid: subtitle rules cannot be read.") from error
    if not isinstance(decoded, dict) or decoded.get("schema_version") != 1:
        raise ValueError("subtitle_rules_invalid: subtitle rules have an invalid schema.")
    return hashlib.sha256(raw_rules).hexdigest()


def _initial_report_state(
    candidates: list[SubtitleCandidate], ambiguous_source_ids: tuple[str, ...]
) -> CandidateReportState:
    if ambiguous_source_ids:
        return CandidateReportState.AWAITING_SUBTITLE_SELECTION
    if any(candidate.state is CandidateState.VALID for candidate in candidates):
        return CandidateReportState.COMPLETED
    return CandidateReportState.BLOCKED


def _parse_decoders(values: tuple[str, ...]) -> dict[tuple[str, int], str]:
    decoders: dict[tuple[str, int], str] = {}
    for value in values:
        source_id, separator, decoder_value = value.rpartition("=")
        if not separator or not source_id or not decoder_value:
            raise SubtitleReportError(
                "subtitle_decoder_invalid",
                "Decoders must use part-id=stream-index=encoding.",
            )
        part_id, separator, stream_text = source_id.rpartition("=")
        if not separator or not part_id or not stream_text:
            # Also accept the compact part-id=stream-index:encoding spelling.
            if ":" in decoder_value and separator:
                stream_text, decoder_value = decoder_value.split(":", 1)
                part_id = source_id
            else:
                part_id, separator, stream_text = source_id.rpartition(":")
        try:
            stream_index = int(stream_text)
        except ValueError as error:
            raise SubtitleReportError(
                "subtitle_decoder_invalid",
                "Decoder stream index must be a non-negative integer.",
            ) from error
        if stream_index < 0 or not part_id:
            raise SubtitleReportError(
                "subtitle_decoder_invalid",
                "Decoder stream index must be a non-negative integer.",
            )
        if (part_id, stream_index) in decoders:
            raise SubtitleReportError(
                "subtitle_decoder_duplicate",
                f"Decoder choice for {part_id} stream {stream_index} was repeated.",
            )
        try:
            decoder = codecs.lookup(decoder_value).name
        except LookupError as error:
            raise SubtitleReportError(
                "subtitle_decoder_invalid", f"Unknown subtitle decoder: {decoder_value}."
            ) from error
        decoders[(part_id, stream_index)] = decoder
    return decoders


def _ensure_subtitle_headroom(plan: RunPlan, report: object, project_root: Path) -> None:
    """Reserve the plan requirement plus one bounded payload per supported track."""

    evidence_values = getattr(report, "inspection_evidence", ())
    estimated_increment = 0
    for evidence in evidence_values:
        stream_indexes = tuple(
            candidate.stream_index
            for candidate in getattr(evidence, "subtitle_tracks", ())
            if _source_format(evidence, candidate) is not None
        )
        estimated_increment += _subtitle_packet_growth_estimate(evidence, stream_indexes)
    requirement = calculate_disk_headroom(estimated_increment)
    if plan.disk_headroom.required_bytes > requirement.required_bytes:
        requirement = plan.disk_headroom
    ensure_disk_headroom(project_root, requirement)


def _subtitle_packet_growth_estimate(
    evidence: PlanInspectionEvidence, stream_indexes: tuple[int, ...]
) -> int:
    if not stream_indexes:
        return 0
    document = evidence.coverage_document
    if document is None:
        return len(stream_indexes) * SUBTITLE_MAX_PAYLOAD_BYTES
    try:
        decoded = json.loads(document.raw_json)
    except json.JSONDecodeError:
        return len(stream_indexes) * SUBTITLE_MAX_PAYLOAD_BYTES
    packets = decoded.get("packets") if isinstance(decoded, dict) else None
    if not isinstance(packets, list):
        return len(stream_indexes) * SUBTITLE_MAX_PAYLOAD_BYTES
    packet_bytes = 0
    known_indexes = set(stream_indexes)
    for packet in packets:
        if not isinstance(packet, dict):
            continue
        raw_stream_index = packet.get("stream_index")
        if not isinstance(raw_stream_index, int | str) or isinstance(raw_stream_index, bool):
            continue
        try:
            stream_index = int(raw_stream_index)
        except ValueError:
            continue
        if stream_index not in known_indexes:
            continue
        size = packet.get("size")
        if not isinstance(size, int | str) or isinstance(size, bool):
            return len(stream_indexes) * SUBTITLE_MAX_PAYLOAD_BYTES
        try:
            packet_size = int(size)
        except (TypeError, ValueError):
            return len(stream_indexes) * SUBTITLE_MAX_PAYLOAD_BYTES
        if packet_size < 0:
            return len(stream_indexes) * SUBTITLE_MAX_PAYLOAD_BYTES
        packet_bytes += packet_size
    return min(packet_bytes, len(stream_indexes) * SUBTITLE_MAX_PAYLOAD_BYTES)


def _validate_decoder_targets(
    candidates: tuple[SubtitleCandidate, ...],
    decoders: dict[tuple[str, int], str],
) -> tuple[PlanningDiagnostic, ...]:
    candidates_by_key = {
        (candidate.source_id, candidate.stream_index): candidate for candidate in candidates
    }
    diagnostics: list[PlanningDiagnostic] = []
    for source_id, stream_index in decoders:
        candidate = candidates_by_key.get((source_id, stream_index))
        if candidate is None:
            diagnostics.append(
                PlanningDiagnostic(
                    "subtitle_decoder_invalid",
                    f"Part {source_id} does not have subtitle stream {stream_index}.",
                )
            )
        elif candidate.state is not CandidateState.ENCODING_AMBIGUOUS:
            diagnostics.append(
                PlanningDiagnostic(
                    "subtitle_decoder_not_required",
                    f"Part {source_id} stream {stream_index} is not encoding ambiguous.",
                )
            )
    return tuple(diagnostics)


def _ambiguous_source_ids(candidates: list[SubtitleCandidate]) -> tuple[str, ...]:
    valid_by_source: dict[str, int] = {}
    for candidate in candidates:
        if candidate.state is CandidateState.VALID:
            valid_by_source[candidate.source_id] = valid_by_source.get(candidate.source_id, 0) + 1
    return tuple(sorted(source_id for source_id, count in valid_by_source.items() if count > 1))


def _validated_report_id(value: str) -> str:
    try:
        return uuid.UUID(hex=value).hex
    except ValueError as error:
        raise SubtitleReportError(
            "subtitle_report_invalid", "Candidate report ID must be a UUID."
        ) from error


def _load_candidate_report(path: Path) -> SubtitleCandidateReport:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SubtitleReportError(
            "subtitle_report_invalid", "Candidate report cannot be read."
        ) from error
    return SubtitleCandidateReport.from_json(decoded, path)


def _parse_selection(value: str) -> SubtitleTrackSelection:
    source_id, separator, stream_index_text = value.rpartition("=")
    if not separator or not source_id or not stream_index_text:
        raise SubtitleReportError(
            "subtitle_selection_invalid", "Selections must use part-id=stream-index."
        )
    try:
        stream_index = int(stream_index_text)
    except ValueError as error:
        raise SubtitleReportError(
            "subtitle_selection_invalid", "Selection stream index must be a non-negative integer."
        ) from error
    if stream_index < 0:
        raise SubtitleReportError(
            "subtitle_selection_invalid", "Selection stream index must be a non-negative integer."
        )
    return SubtitleTrackSelection(source_id, stream_index)


def _validate_selections(
    candidates: tuple[SubtitleCandidate, ...], selections: tuple[SubtitleTrackSelection, ...]
) -> tuple[PlanningDiagnostic, ...]:
    ambiguous_source_ids = _ambiguous_source_ids(list(candidates))
    selected_by_source: dict[str, SubtitleTrackSelection] = {}
    diagnostics: list[PlanningDiagnostic] = []
    for selection in selections:
        if selection.source_id in selected_by_source:
            diagnostics.append(
                PlanningDiagnostic(
                    "subtitle_selection_duplicate",
                    f"Part {selection.source_id} has more than one selection.",
                )
            )
        selected_by_source[selection.source_id] = selection
    for source_id in ambiguous_source_ids:
        selected = selected_by_source.get(source_id)
        if selected is None:
            diagnostics.append(
                PlanningDiagnostic(
                    "subtitle_selection_missing",
                    f"Part {source_id} requires an explicit subtitle stream selection.",
                )
            )
            continue
        matching_candidate = next(
            (
                candidate
                for candidate in candidates
                if candidate.source_id == source_id
                and candidate.stream_index == selected.stream_index
            ),
            None,
        )
        if matching_candidate is None or matching_candidate.state is not CandidateState.VALID:
            diagnostics.append(
                PlanningDiagnostic(
                    "subtitle_selection_invalid",
                    f"Part {source_id} does not have a valid selected subtitle stream.",
                )
            )
    for source_id in selected_by_source:
        if source_id not in ambiguous_source_ids:
            diagnostics.append(
                PlanningDiagnostic(
                    "subtitle_selection_not_required",
                    f"Part {source_id} is not awaiting subtitle selection.",
                )
            )
    return tuple(diagnostics)


def _revalidate_plan(plan: RunPlan, project_root: Path) -> tuple[PlanningDiagnostic, ...]:
    diagnostics: list[PlanningDiagnostic] = []
    for artifact in plan.source_artifacts:
        try:
            digest, byte_count = sha256_file(artifact.media_path)
        except FileNotFoundError:
            diagnostics.append(
                PlanningDiagnostic("source_artifact_missing", str(artifact.media_path))
            )
            continue
        except OSError:
            diagnostics.append(
                PlanningDiagnostic(
                    "source_artifact_unavailable",
                    "A SourceArtifact can no longer be read for hash revalidation.",
                )
            )
            continue
        if digest != artifact.sha256 or byte_count != artifact.byte_count:
            diagnostics.append(
                PlanningDiagnostic(
                    "source_artifact_changed", "A SourceArtifact hash no longer matches."
                )
            )
    ffmpeg = _ffmpeg(plan)
    if ffmpeg is None:
        diagnostics.append(
            PlanningDiagnostic("ffmpeg_missing", "RunPlan has no pinned FFmpeg tool.")
        )
    else:
        try:
            revalidate_external_tool(ffmpeg)
        except (FileNotFoundError, ValueError) as error:
            diagnostics.append(PlanningDiagnostic("tool_identity_changed", str(error)))
    try:
        subtitle_rules_fingerprint(project_root)
    except ValueError as error:
        diagnostics.append(PlanningDiagnostic("subtitle_rules_invalid", str(error)))
    return tuple(diagnostics)


def _matches_plan(report: object, plan: RunPlan) -> bool:
    report_sources = getattr(report, "source_artifacts", ())
    report_tools = getattr(report, "tools", ())
    return report_sources == plan.source_artifacts and report_tools == plan.tools


def _extract_supported_candidates(
    artifact: SourceArtifact,
    evidence: PlanInspectionEvidence,
    plan: RunPlan,
    report_id: str,
    project_root: Path,
    decoders: dict[tuple[str, int], str],
) -> tuple[SubtitleCandidate, ...]:
    candidates: list[SubtitleCandidate] = []
    for candidate in evidence.subtitle_tracks:
        codec = _candidate_codec(evidence, candidate)
        extraction_format = _source_format(evidence, candidate)
        if not candidate.available or candidate.origin != "embedded" or extraction_format is None:
            candidates.append(
                SubtitleCandidate(
                    artifact.source_id,
                    candidate.stream_index,
                    CandidateState.UNAVAILABLE,
                    attempt_id=report_id,
                    codec=codec,
                    diagnostic=PlanningDiagnostic(
                        "subtitle_format_unsupported",
                        (
                            "Embedded subtitle codec "
                            f"{codec or 'unknown'} is unavailable "
                            "in Phase 4; no OCR or approximate conversion is allowed."
                        ),
                    ),
                )
            )
            continue
        candidates.append(
            _extract_candidate_with_resource_retention(
                artifact,
                candidate,
                codec or "unknown",
                extraction_format,
                evidence.coverage_by_stream,
                _ffmpeg(plan),
                report_id,
                project_root,
                decoders.get((artifact.source_id, candidate.stream_index)),
            )
        )
    return tuple(candidates)


def _source_format(
    evidence: PlanInspectionEvidence, candidate: SubtitleTrackCandidate
) -> ExtractionFormat | None:
    if evidence.structural_document is None:
        return None
    try:
        decoded = json.loads(evidence.structural_document.raw_json)
    except json.JSONDecodeError:
        return None
    streams = decoded.get("streams") if isinstance(decoded, dict) else None
    if not isinstance(streams, list):
        return None
    for stream in streams:
        if (
            isinstance(stream, dict)
            and stream.get("codec_type") == "subtitle"
            and stream.get("index") == candidate.stream_index
        ):
            codec = stream.get("codec_name")
            if codec in {"subrip", "srt"}:
                return ExtractionFormat("srt", "copy", "srt")
            if codec == "webvtt":
                return ExtractionFormat("vtt", "copy", "webvtt")
            if codec == "mov_text":
                return ExtractionFormat("srt", "srt", "srt")
    return None


def _candidate_codec(
    evidence: PlanInspectionEvidence, candidate: SubtitleTrackCandidate
) -> str | None:
    if evidence.structural_document is None:
        return None
    try:
        decoded = json.loads(evidence.structural_document.raw_json)
    except json.JSONDecodeError:
        return None
    streams = decoded.get("streams") if isinstance(decoded, dict) else None
    if not isinstance(streams, list):
        return None
    for stream in streams:
        if (
            isinstance(stream, dict)
            and stream.get("codec_type") == "subtitle"
            and stream.get("index") == candidate.stream_index
        ):
            codec = stream.get("codec_name")
            return codec if isinstance(codec, str) else None
    return None


def _extract_candidate(
    artifact: SourceArtifact,
    candidate: SubtitleTrackCandidate,
    codec: str,
    extraction_format: ExtractionFormat,
    coverage_by_stream: tuple[tuple[int, StreamCoverage], ...],
    ffmpeg: PinnedExternalTool | None,
    report_id: str,
    project_root: Path,
    requested_decoder: str | None,
) -> SubtitleCandidate:
    if ffmpeg is None:
        raise AssertionError("Plan revalidation must require FFmpeg before extraction.")
    coverage = _playback_coverage(coverage_by_stream)
    if coverage is None:
        return SubtitleCandidate(
            artifact.source_id,
            candidate.stream_index,
            CandidateState.INVALID,
            attempt_id=report_id,
            codec=codec,
            diagnostic=PlanningDiagnostic(
                "coverage_indeterminate", "Subtitle validation requires playable stream coverage."
            ),
        )
    workspace = project_root / "work" / artifact.source_id / report_id
    source_format = extraction_format.source_format
    raw_payload = workspace / f"stream-{candidate.stream_index}.payload.{source_format}"
    raw_payload.parent.mkdir(parents=True, exist_ok=True)
    command = (
        str(ffmpeg.path),
        "-v",
        "error",
        "-nostdin",
        "-n",
        "-i",
        str(artifact.media_path),
        "-map",
        f"0:{candidate.stream_index}",
        "-c:s",
        extraction_format.ffmpeg_codec,
        "-f",
        extraction_format.ffmpeg_muxer,
        "-fs",
        str(SUBTITLE_MAX_PAYLOAD_BYTES),
        str(raw_payload),
    )
    try:
        result = _run_extraction(command)
    except subprocess.TimeoutExpired:
        payload_evidence = _payload_evidence(raw_payload)
        return SubtitleCandidate(
            artifact.source_id,
            candidate.stream_index,
            CandidateState.INCOMPLETE,
            attempt_id=report_id,
            codec=codec,
            source_format=source_format,
            raw_payload_path=payload_evidence.path,
            raw_payload_sha256=payload_evidence.sha256,
            raw_payload_bytes=payload_evidence.byte_count,
            diagnostic=PlanningDiagnostic(
                "subtitle_extraction_timeout",
                f"Subtitle extraction exceeded {SUBTITLE_EXTRACTION_TIMEOUT_SECONDS} seconds.",
            ),
        )
    except KeyboardInterrupt:
        payload_evidence = _payload_evidence(raw_payload)
        return SubtitleCandidate(
            artifact.source_id,
            candidate.stream_index,
            CandidateState.INCOMPLETE,
            attempt_id=report_id,
            codec=codec,
            source_format=source_format,
            raw_payload_path=payload_evidence.path,
            raw_payload_sha256=payload_evidence.sha256,
            raw_payload_bytes=payload_evidence.byte_count,
            diagnostic=PlanningDiagnostic(
                "subtitle_extraction_interrupted",
                "Subtitle extraction was interrupted; retained bytes are not parseable.",
            ),
        )
    if result.returncode != 0:
        payload_evidence = _payload_evidence(raw_payload)
        return SubtitleCandidate(
            artifact.source_id,
            candidate.stream_index,
            CandidateState.INCOMPLETE,
            attempt_id=report_id,
            codec=codec,
            source_format=source_format,
            raw_payload_path=payload_evidence.path,
            raw_payload_sha256=payload_evidence.sha256,
            raw_payload_bytes=payload_evidence.byte_count,
            diagnostic=PlanningDiagnostic(
                "subtitle_extraction_failed",
                result.stderr.strip() or f"FFmpeg exited with code {result.returncode}.",
            ),
        )
    if not raw_payload.is_file():
        return SubtitleCandidate(
            artifact.source_id,
            candidate.stream_index,
            CandidateState.INCOMPLETE,
            attempt_id=report_id,
            codec=codec,
            source_format=source_format,
            diagnostic=PlanningDiagnostic("subtitle_extraction_failed", "No payload was retained."),
        )
    payload_evidence = _payload_evidence(raw_payload)
    payload_hash = payload_evidence.sha256
    payload_bytes = payload_evidence.byte_count
    assert isinstance(payload_hash, str)
    assert isinstance(payload_bytes, int)
    if payload_bytes > SUBTITLE_MAX_PAYLOAD_BYTES:
        return SubtitleCandidate(
            artifact.source_id,
            candidate.stream_index,
            CandidateState.INCOMPLETE,
            attempt_id=report_id,
            codec=codec,
            source_format=source_format,
            raw_payload_path=payload_evidence.path,
            raw_payload_sha256=payload_evidence.sha256,
            raw_payload_bytes=payload_evidence.byte_count,
            diagnostic=PlanningDiagnostic(
                "extraction_size_limit",
                f"Subtitle payload exceeds the {SUBTITLE_MAX_PAYLOAD_BYTES}-byte limit.",
            ),
        )
    try:
        source, decoder = _decode_payload(raw_payload.read_bytes(), requested_decoder)
    except _AmbiguousEncoding:
        return SubtitleCandidate(
            artifact.source_id,
            candidate.stream_index,
            CandidateState.ENCODING_AMBIGUOUS,
            attempt_id=report_id,
            codec=codec,
            source_format=source_format,
            raw_payload_path=payload_evidence.path,
            raw_payload_sha256=payload_evidence.sha256,
            raw_payload_bytes=payload_evidence.byte_count,
            diagnostic=PlanningDiagnostic(
                "encoding_ambiguous", "Payload is not strict UTF-8; no decoder was selected."
            ),
        )
    except (LookupError, UnicodeDecodeError) as error:
        return SubtitleCandidate(
            artifact.source_id,
            candidate.stream_index,
            CandidateState.ENCODING_AMBIGUOUS,
            attempt_id=report_id,
            codec=codec,
            raw_payload_path=payload_evidence.path,
            raw_payload_sha256=payload_evidence.sha256,
            raw_payload_bytes=payload_evidence.byte_count,
            decoder=requested_decoder,
            diagnostic=PlanningDiagnostic(
                "subtitle_decoder_invalid",
                f"Explicit decoder could not decode the retained payload: {error}.",
            ),
        )
    coverage_start, relative_coverage = _relative_coverage(coverage)
    track = accept_subtitle_track(
        source,
        source_format=source_format,
        part_id=artifact.source_id,
        track_id=f"stream-{candidate.stream_index}",
        coverage=relative_coverage,
    )

    if not track.valid:
        diagnostic = track.diagnostics[0]
        return SubtitleCandidate(
            artifact.source_id,
            candidate.stream_index,
            CandidateState.INVALID,
            attempt_id=report_id,
            codec=codec,
            decoder=decoder,
            source_format=source_format,
            raw_payload_path=payload_evidence.path,
            raw_payload_sha256=payload_evidence.sha256,
            raw_payload_bytes=payload_evidence.byte_count,
            diagnostic=PlanningDiagnostic(diagnostic.reason, diagnostic.message),
        )
    source_candidate_path, source_candidate_hash = _write_source_candidate(
        raw_payload.with_name(f"stream-{candidate.stream_index}.candidate.json"),
        track,
        coverage_start,
    )
    artifacts = _write_candidate_artifacts(raw_payload, track)
    return SubtitleCandidate(
        artifact.source_id,
        candidate.stream_index,
        CandidateState.VALID,
        attempt_id=report_id,
        codec=codec,
        decoder=decoder,
        source_format=source_format,
        raw_payload_path=payload_evidence.path,
        raw_payload_sha256=payload_evidence.sha256,
        raw_payload_bytes=payload_evidence.byte_count,
        source_candidate_path=source_candidate_path.as_posix(),
        source_candidate_sha256=source_candidate_hash,
        source_vtt_path=artifacts.source_vtt_path,
        source_srt_path=artifacts.source_srt_path,
        readable_vtt_path=artifacts.readable_vtt_path,
        readable_corrections_path=artifacts.readable_corrections_path,
        format_projection_losses=artifacts.projection_losses,
        cue_count=len(track.normalized_cues),
        coverage_start=_time_as_json(coverage_start),
    )


def _extract_candidate_with_resource_retention(
    artifact: SourceArtifact,
    candidate: SubtitleTrackCandidate,
    codec: str,
    extraction_format: ExtractionFormat,
    coverage_by_stream: tuple[tuple[int, StreamCoverage], ...],
    ffmpeg: PinnedExternalTool | None,
    report_id: str,
    project_root: Path,
    requested_decoder: str | None,
) -> SubtitleCandidate:
    try:
        return _extract_candidate(
            artifact,
            candidate,
            codec,
            extraction_format,
            coverage_by_stream,
            ffmpeg,
            report_id,
            project_root,
            requested_decoder,
        )
    except (OSError, subprocess.SubprocessError) as error:
        payload_path = (
            project_root
            / "work"
            / artifact.source_id
            / report_id
            / f"stream-{candidate.stream_index}.payload.{extraction_format.source_format}"
        )
        evidence = _payload_evidence_safely(payload_path)
        return SubtitleCandidate(
            artifact.source_id,
            candidate.stream_index,
            CandidateState.INCOMPLETE,
            source_format=extraction_format.source_format,
            raw_payload_path=evidence.path,
            raw_payload_sha256=evidence.sha256,
            raw_payload_bytes=evidence.byte_count,
            attempt_id=report_id,
            codec=codec,
            diagnostic=PlanningDiagnostic(
                "subtitle_extraction_resource_failure",
                f"Subtitle extraction could not retain a complete payload: {error}.",
            ),
        )


def _resolve_decoder_candidates(
    candidates: tuple[SubtitleCandidate, ...],
    decoders: dict[tuple[str, int], str],
    report: object,
) -> tuple[SubtitleCandidate, ...]:
    if not decoders:
        return candidates
    evidence_by_source = {
        evidence.source_id: evidence for evidence in getattr(report, "inspection_evidence", ())
    }
    resolved: list[SubtitleCandidate] = []
    for candidate in candidates:
        decoder = decoders.get((candidate.source_id, candidate.stream_index))
        if decoder is None or candidate.state is not CandidateState.ENCODING_AMBIGUOUS:
            resolved.append(candidate)
            continue
        if candidate.raw_payload_path is None or candidate.source_format is None:
            resolved.append(
                replace(
                    candidate,
                    decoder=decoder,
                    diagnostic=PlanningDiagnostic(
                        "subtitle_payload_unavailable",
                        "The ambiguous subtitle payload was not retained.",
                    ),
                )
            )
            continue
        try:
            payload_path = Path(candidate.raw_payload_path)
            payload = payload_path.read_bytes()
            payload_hash, payload_bytes = sha256_file(payload_path)
            if (
                payload_hash != candidate.raw_payload_sha256
                or payload_bytes != candidate.raw_payload_bytes
            ):
                raise SubtitleReportError(
                    "subtitle_payload_changed",
                    "The retained subtitle payload no longer matches its recorded evidence.",
                )
            if len(payload) > SUBTITLE_MAX_PAYLOAD_BYTES:
                raise SubtitleReportError(
                    "extraction_size_limit",
                    f"Subtitle payload exceeds the {SUBTITLE_MAX_PAYLOAD_BYTES}-byte limit.",
                )
            source, canonical_decoder = _decode_payload(payload, decoder)
            evidence = evidence_by_source[candidate.source_id]
            coverage = _playback_coverage(evidence.coverage_by_stream)
            if coverage is None:
                raise SubtitleReportError(
                    "coverage_indeterminate",
                    "Subtitle validation requires playable stream coverage.",
                )
            coverage_start, relative_coverage = _relative_coverage(coverage)
            track = accept_subtitle_track(
                source,
                candidate.source_format,
                part_id=candidate.source_id,
                track_id=f"stream-{candidate.stream_index}",
                coverage=relative_coverage,
            )
            if not track.valid:
                diagnostic = track.diagnostics[0]
                resolved.append(
                    replace(
                        candidate,
                        state=CandidateState.INVALID,
                        decoder=canonical_decoder,
                        diagnostic=PlanningDiagnostic(diagnostic.reason, diagnostic.message),
                    )
                )
                continue
            source_candidate_path, source_candidate_hash = _write_source_candidate(
                payload_path.with_name(f"stream-{candidate.stream_index}.candidate.json"),
                track,
                coverage_start,
            )
            artifacts = _write_candidate_artifacts(payload_path, track)
            resolved.append(
                replace(
                    candidate,
                    state=CandidateState.VALID,
                    decoder=canonical_decoder,
                    source_candidate_path=source_candidate_path.as_posix(),
                    source_candidate_sha256=source_candidate_hash,
                    source_vtt_path=artifacts.source_vtt_path,
                    source_srt_path=artifacts.source_srt_path,
                    readable_vtt_path=artifacts.readable_vtt_path,
                    readable_corrections_path=artifacts.readable_corrections_path,
                    format_projection_losses=artifacts.projection_losses,
                    cue_count=len(track.normalized_cues),
                    coverage_start=_time_as_json(coverage_start),
                    diagnostic=None,
                )
            )
        except (KeyError, OSError, SubtitleReportError, UnicodeDecodeError) as error:
            reason = getattr(error, "reason", "subtitle_decoder_invalid")
            resolved.append(
                replace(
                    candidate,
                    decoder=decoder,
                    diagnostic=PlanningDiagnostic(reason, str(error)),
                )
            )
    return tuple(resolved)


def _playback_coverage(
    coverage_by_stream: tuple[tuple[int, StreamCoverage], ...],
) -> tuple[HalfOpenInterval, ...] | None:
    intervals: list[HalfOpenInterval] = []
    for _, stream_coverage in coverage_by_stream:
        if stream_coverage.coverage is None or stream_coverage.diagnostics:
            continue
        boundaries = (stream_coverage.coverage.start, *(gap.end for gap in stream_coverage.gaps))
        endings = (*(gap.start for gap in stream_coverage.gaps), stream_coverage.coverage.end)
        intervals.extend(
            HalfOpenInterval(start, end) for start, end in zip(boundaries, endings, strict=True)
        )
    if not intervals:
        return None
    ordered = sorted(intervals, key=lambda interval: (interval.start, interval.end))
    merged = [ordered[0]]
    for interval in ordered[1:]:
        previous = merged[-1]
        if interval.start <= previous.end:
            merged[-1] = HalfOpenInterval(previous.start, max(previous.end, interval.end))
        else:
            merged.append(interval)
    return tuple(merged)


def _relative_coverage(
    coverage: tuple[HalfOpenInterval, ...],
) -> tuple[ExactTime, StreamCoverage]:
    start = coverage[0].start
    relative_intervals = tuple(
        HalfOpenInterval(interval.start - start, interval.end - start) for interval in coverage
    )
    gaps = tuple(
        HalfOpenInterval(left.end, right.start)
        for left, right in zip(relative_intervals, relative_intervals[1:], strict=False)
    )
    return start, StreamCoverage(
        HalfOpenInterval(relative_intervals[0].start, relative_intervals[-1].end), gaps, ()
    )


def _ffmpeg(plan: RunPlan) -> PinnedExternalTool | None:
    return next((tool for tool in plan.tools if tool.tool_id == "ffmpeg"), None)


def _time_as_json(value: ExactTime) -> dict[str, int]:
    return {"numerator": value.numerator, "denominator": value.denominator}


def _payload_evidence(raw_payload: Path) -> RawPayloadEvidence:
    if not raw_payload.is_file():
        return RawPayloadEvidence(None, None, None)
    digest, byte_count = sha256_file(raw_payload)
    return RawPayloadEvidence(raw_payload.as_posix(), digest, byte_count)


def _payload_evidence_safely(raw_payload: Path) -> RawPayloadEvidence:
    try:
        return _payload_evidence(raw_payload)
    except OSError:
        return RawPayloadEvidence(None, None, None)


def _write_source_candidate(
    path: Path, track: SubtitleTrack, coverage_start: ExactTime
) -> tuple[Path, str]:
    payload = {
        "schema_version": 1,
        "cues": [
            {
                "source_ordinal": cue.source_ordinal,
                "text": cue.text,
                "raw_pts_interval": {
                    "start": _time_as_json(cue.interval.start + coverage_start),
                    "end": _time_as_json(cue.interval.end + coverage_start),
                },
            }
            for cue in track.normalized_cues
        ],
    }
    _write_json_once(path, payload)
    digest, _ = sha256_file(path)
    return path, digest


def _write_candidate_artifacts(raw_payload: Path, track: SubtitleTrack) -> CandidateArtifacts:
    """Persist source-preserving and readable exports next to immutable payload evidence."""

    prefix = raw_payload.with_suffix("").with_suffix("")
    source_vtt_path = prefix.with_name(f"{prefix.name}.source.vtt")
    source_srt_path = prefix.with_name(f"{prefix.name}.source.srt")
    readable_vtt_path = prefix.with_name(f"{prefix.name}.readable.vtt")
    readable_corrections_path = prefix.with_name(f"{prefix.name}.readable.corrections.json")
    readable = readable_output(track)
    projection_losses = source_srt_projection_losses(track)
    _write_text_once(source_vtt_path, serialize_source_vtt(track))
    _write_text_once(source_srt_path, serialize_source_srt(track))
    _write_text_once(readable_vtt_path, serialize_vtt(readable.cues))
    _write_json_once(
        readable_corrections_path,
        {
            "schema_version": 1,
            "corrections": [_correction_as_json(correction) for correction in readable.corrections],
            "diagnostics": [_diagnostic_as_json(diagnostic) for diagnostic in readable.diagnostics],
        },
    )
    return CandidateArtifacts(
        source_vtt_path.as_posix(),
        source_srt_path.as_posix(),
        readable_vtt_path.as_posix(),
        readable_corrections_path.as_posix(),
        projection_losses,
    )


def _correction_as_json(correction: object) -> dict[str, object]:
    return {
        "reason": getattr(correction, "reason"),
        "source_ordinal": getattr(correction, "source_ordinal"),
        "source_token_range": list(getattr(correction, "source_token_range")),
        "compared_to_source_ordinal": getattr(correction, "compared_to_source_ordinal"),
        "source_character_range": (
            list(getattr(correction, "source_character_range"))
            if getattr(correction, "source_character_range") is not None
            else None
        ),
    }


def _diagnostic_as_json(diagnostic: object) -> dict[str, object]:
    return {
        "reason": getattr(diagnostic, "reason"),
        "source_ordinal": getattr(diagnostic, "source_ordinal"),
        "compared_to_source_ordinal": getattr(diagnostic, "compared_to_source_ordinal"),
        "markup": getattr(diagnostic, "markup"),
    }


def _required_string(value: Mapping[str, object], field: str) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result:
        raise ValueError(f"{field} must be a non-empty string")
    return result


def _optional_string(value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise ValueError("Optional string field must be a string or null")


def _required_positive_int(value: Mapping[str, object], field: str) -> int:
    result = value.get(field)
    if not isinstance(result, int) or isinstance(result, bool) or result <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return result


def _required_nonnegative_int(value: Mapping[str, object], field: str) -> int:
    result = value.get(field)
    if not isinstance(result, int) or isinstance(result, bool) or result < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return result


def _optional_nonnegative_int(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("Optional integer field must be a non-negative integer or null")
    return value


def _optional_format(value: object) -> Literal["srt", "vtt"] | None:
    if value is None:
        return None
    if value == "srt":
        return "srt"
    if value == "vtt":
        return "vtt"
    raise ValueError("Source format must be srt, vtt, or null")


def _optional_time(value: object) -> dict[str, int] | None:
    if value is None:
        return None
    if (
        not isinstance(value, Mapping)
        or not isinstance(value.get("numerator"), int)
        or not isinstance(value.get("denominator"), int)
        or isinstance(value.get("numerator"), bool)
        or isinstance(value.get("denominator"), bool)
        or value["denominator"] == 0
    ):
        raise ValueError("Coverage start must be an exact time object or null")
    return {"numerator": value["numerator"], "denominator": value["denominator"]}


def _projection_loss(value: object) -> FormatProjectionLoss:
    if not isinstance(value, Mapping):
        raise ValueError("Projection loss must be an object")
    source_ordinal = value.get("source_ordinal")
    if source_ordinal is not None and (
        not isinstance(source_ordinal, int)
        or isinstance(source_ordinal, bool)
        or source_ordinal < 0
    ):
        raise ValueError("Projection loss source ordinal is invalid")
    if value.get("reason") != "format_projection_loss" or not isinstance(value.get("setting"), str):
        raise ValueError("Projection loss is invalid")
    return FormatProjectionLoss("format_projection_loss", source_ordinal, value["setting"])


def _planning_diagnostic(value: object) -> PlanningDiagnostic:
    if not isinstance(value, Mapping):
        raise ValueError("Diagnostic must be an object")
    return PlanningDiagnostic(_required_string(value, "reason"), _required_string(value, "message"))


def _selection_from_json(value: object) -> SubtitleTrackSelection:
    if not isinstance(value, Mapping):
        raise ValueError("Selection must be an object")
    return SubtitleTrackSelection(
        _required_string(value, "source_id"), _required_nonnegative_int(value, "stream_index")
    )


def _persist_blocked_report(
    project_root: Path,
    plan_id: str,
    report_id: str,
    diagnostics: tuple[PlanningDiagnostic, ...],
) -> dict[str, object]:
    report_path = project_root / "work" / "subtitle-reports" / report_id / "report.json"
    report = SubtitleCandidateReport(
        report_id, plan_id, CandidateReportState.BLOCKED, None, (), diagnostics, report_path
    )
    _write_json_once(report_path, report.as_json())
    return {"status": "blocked", "report": report.as_json()}


def _report_path(project_root: Path, artifacts: tuple[SourceArtifact, ...], report_id: str) -> Path:
    if len(artifacts) == 1:
        return project_root / "work" / artifacts[0].source_id / report_id / "candidate-report.json"
    return project_root / "work" / "subtitle-reports" / report_id / "report.json"


def _write_json_once(path: Path, payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != encoded:
            raise ValueError(f"subtitle_report_conflict: immutable record differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded, encoding="utf-8")


def _write_text_once(path: Path, text: str) -> None:
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise ValueError(f"subtitle_artifact_conflict: immutable record differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
