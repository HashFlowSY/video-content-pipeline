"""Phase 4 retained processing for one verified embedded SRT subtitle track."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
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
from video_content_pipeline.source import SourceArtifact, sha256_file
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

    def as_json(self) -> dict[str, object]:
        result: dict[str, object] = {
            "source_id": self.source_id,
            "stream_index": self.stream_index,
            "state": self.state.value,
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

    def as_json(self) -> dict[str, object]:
        return {
            "report_id": self.report_id,
            "plan_id": self.plan_id,
            "state": self.state.value,
            "subtitle_rules_fingerprint": self.subtitle_rules_fingerprint,
            "candidates": [candidate.as_json() for candidate in self.candidates],
            "diagnostics": [diagnostic.as_json() for diagnostic in self.diagnostics],
            "report_path": self.report_path.as_posix(),
        }


def process_subtitles(plan_id: str, project_root: Path) -> dict[str, object]:
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
    except (OSError, ValueError) as error:
        reason = getattr(error, "reason", "run_plan_not_confirmed")
        return _persist_blocked_report(
            project_root, plan_id, report_id, (PlanningDiagnostic(reason, str(error)),)
        )

    candidates: list[SubtitleCandidate] = []
    for artifact, evidence in zip(plan.source_artifacts, report.inspection_evidence, strict=True):
        candidates.extend(
            _extract_supported_candidates(artifact, evidence, plan, report_id, project_root)
        )
    state = (
        CandidateReportState.COMPLETED
        if any(candidate.state is CandidateState.VALID for candidate in candidates)
        else CandidateReportState.BLOCKED
    )
    report_path = _report_path(project_root, plan.source_artifacts, report_id)
    candidate_report = SubtitleCandidateReport(
        report_id,
        plan.plan_id,
        state,
        rules_fingerprint,
        tuple(candidates),
        (),
        report_path,
    )
    _write_json_once(report_path, candidate_report.as_json())
    return {"status": state.value, "report": candidate_report.as_json()}


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
) -> tuple[SubtitleCandidate, ...]:
    candidates: list[SubtitleCandidate] = []
    for candidate in evidence.subtitle_tracks:
        extraction_format = _source_format(evidence, candidate)
        if not candidate.available or candidate.origin != "embedded" or extraction_format is None:
            candidates.append(
                SubtitleCandidate(
                    artifact.source_id,
                    candidate.stream_index,
                    CandidateState.UNAVAILABLE,
                    diagnostic=PlanningDiagnostic(
                        "subtitle_format_unsupported",
                        "Subtitle candidate is not an embedded SRT, WebVTT, or mov_text payload "
                        "supported by this slice.",
                    ),
                )
            )
            continue
        candidates.append(
            _extract_candidate(
                artifact,
                candidate,
                extraction_format,
                evidence.coverage_by_stream,
                _ffmpeg(plan),
                report_id,
                project_root,
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


def _extract_candidate(
    artifact: SourceArtifact,
    candidate: SubtitleTrackCandidate,
    extraction_format: ExtractionFormat,
    coverage_by_stream: tuple[tuple[int, StreamCoverage], ...],
    ffmpeg: PinnedExternalTool | None,
    report_id: str,
    project_root: Path,
) -> SubtitleCandidate:
    if ffmpeg is None:
        raise AssertionError("Plan revalidation must require FFmpeg before extraction.")
    coverage = _playback_coverage(coverage_by_stream)
    if coverage is None:
        return SubtitleCandidate(
            artifact.source_id,
            candidate.stream_index,
            CandidateState.INVALID,
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
        str(raw_payload),
    )
    result = run_tool(command)
    if result.returncode != 0:
        payload_evidence = _payload_evidence(raw_payload)
        return SubtitleCandidate(
            artifact.source_id,
            candidate.stream_index,
            CandidateState.INCOMPLETE,
            source_format=source_format,
            raw_payload_path=payload_evidence.path,
            raw_payload_sha256=payload_evidence.sha256,
            raw_payload_bytes=payload_evidence.byte_count,
            diagnostic=PlanningDiagnostic("subtitle_extraction_failed", result.stderr.strip()),
        )
    if not raw_payload.is_file():
        return SubtitleCandidate(
            artifact.source_id,
            candidate.stream_index,
            CandidateState.INCOMPLETE,
            source_format=source_format,
            diagnostic=PlanningDiagnostic("subtitle_extraction_failed", "No payload was retained."),
        )
    payload_evidence = _payload_evidence(raw_payload)
    payload_hash = payload_evidence.sha256
    payload_bytes = payload_evidence.byte_count
    assert isinstance(payload_hash, str)
    assert isinstance(payload_bytes, int)
    try:
        source = raw_payload.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return SubtitleCandidate(
            artifact.source_id,
            candidate.stream_index,
            CandidateState.ENCODING_AMBIGUOUS,
            source_format=source_format,
            raw_payload_path=payload_evidence.path,
            raw_payload_sha256=payload_evidence.sha256,
            raw_payload_bytes=payload_evidence.byte_count,
            diagnostic=PlanningDiagnostic(
                "encoding_ambiguous", "Payload is not strict UTF-8; no decoder was selected."
            ),
        )
    coverage_start, relative_coverage = _relative_coverage(coverage)
    track = accept_subtitle_track(
        source,
        source_format,
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
        source_format,
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
