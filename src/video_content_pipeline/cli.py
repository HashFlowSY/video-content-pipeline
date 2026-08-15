"""CLI boundary for environment validation and Phase 3 planning."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from fractions import Fraction
from pathlib import Path
from urllib.parse import urlsplit

from video_content_pipeline import __version__
from video_content_pipeline.acquisition import URLAcquisitionError, acquire_public_source
from video_content_pipeline.audio_analysis import analyze_audio, resume_audio_analysis
from video_content_pipeline.enhancement import (
    EnhancementError,
    enhance,
    resume_enhancement,
)
from video_content_pipeline.environment import assert_project_venv, assert_runtime_policy
from video_content_pipeline.external_tools import PinnedExternalTool, identify_external_tool
from video_content_pipeline.inspection import (
    InspectionError,
    PlanInspectionEvidence,
    ProbeCaptureError,
    capture_probe_documents,
    inspect_documents,
)
from video_content_pipeline.planning import (
    PlanningDiagnostic,
    PlanningError,
    PlanState,
    confirm_run_plan,
    create_plan_report,
    estimate_full_decode,
    find_matching_decode_measurement,
    load_decode_measurements,
    load_decode_throughput_profile,
    load_plan_report,
    perform_full_decode_validation,
    persist_plan_report,
    planning_configuration_fingerprint,
    record_decode_measurement,
    revalidate_report,
)
from video_content_pipeline.source import (
    SourceArtifact,
    SourceIntakeError,
    calculate_disk_headroom,
    ensure_disk_headroom,
    snapshot_local_source,
    validate_local_source_candidate,
)
from video_content_pipeline.subtitle_pipeline import process_subtitles, resume_subtitles
from video_content_pipeline.text_analysis import (
    TextAnalysisError,
    analyze_text,
    resume_text_analysis,
)
from video_content_pipeline.text_reanalysis import reanalyze_text
from video_content_pipeline.transcription import (
    TranscriptionError,
    resume_transcription,
    transcribe,
)
from video_content_pipeline.url_policy import (
    COLLECTION_CLOSURE_SIGNAL,
    ManualCollectionSession,
    URLAccessMode,
    URLAuthorization,
    URLPolicyError,
    authorize_public_url,
)
from video_content_pipeline.visual_text import VisualTextError
from video_content_pipeline.visual_text_command import resume_visual_text, run_visual_text


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vcp")
    parser.add_argument("--version", action="version", version=__version__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("check-environment")
    plan = subcommands.add_parser("plan")
    plan.add_argument("target", nargs="?")
    plan.add_argument("report_id", nargs="?")
    plan.add_argument("--collect", action="store_true")
    plan.add_argument("--url-mode", choices=tuple(mode.value for mode in URLAccessMode))
    plan.add_argument("--allow-insecure-http", action="store_true")
    plan.add_argument("--json", action="store_true")
    subtitles = subcommands.add_parser("subtitles")
    subtitles.add_argument("plan_id")
    subtitles.add_argument("--resume", metavar="REPORT_ID")
    subtitles.add_argument("--select", action="append", default=[], metavar="PART_ID=STREAM_INDEX")
    subtitles.add_argument(
        "--decoder",
        "--decode",
        dest="decoders",
        action="append",
        default=[],
        metavar="PART_ID=STREAM_INDEX=ENCODING",
    )
    subtitles.add_argument("--json", action="store_true")
    analyze_audio_command = subcommands.add_parser("analyze-audio")
    analyze_audio_command.add_argument("plan_id")
    analyze_audio_command.add_argument("subtitle_report_id")
    analyze_audio_command.add_argument(
        "--audio-stream", action="append", default=[], metavar="PART_ID=STREAM_INDEX"
    )
    analyze_audio_command.add_argument("--diarization-candidate", metavar="CANDIDATE_ID")
    analyze_audio_command.add_argument(
        "--role-metadata", action="append", default=[], metavar="PART_ID=CLUSTER_ID=ROLE"
    )
    analyze_audio_command.add_argument("--json", action="store_true")
    resume_audio_command = subcommands.add_parser("resume-audio-analysis")
    resume_audio_command.add_argument("report_id")
    resume_audio_command.add_argument(
        "--audio-stream", action="append", default=[], metavar="PART_ID=STREAM_INDEX"
    )
    resume_audio_command.add_argument("--diarization-candidate", metavar="CANDIDATE_ID")
    resume_audio_command.add_argument(
        "--decision",
        choices=("model_release_verified", "resource_configuration_changed"),
    )
    resume_audio_command.add_argument(
        "--role-metadata", action="append", default=[], metavar="PART_ID=CLUSTER_ID=ROLE"
    )
    resume_audio_command.add_argument("--json", action="store_true")
    analyze_text_command = subcommands.add_parser("analyze-text")
    analyze_text_command.add_argument("plan_id")
    analyze_text_command.add_argument("subtitle_report_id")
    analyze_text_command.add_argument("--audio-report", metavar="REPORT_ID")
    analyze_text_command.add_argument("--json", action="store_true")
    resume_text_command = subcommands.add_parser("resume-text-analysis")
    resume_text_command.add_argument("report_id")
    resume_text_command.add_argument("--decision", metavar="DECISION")
    resume_text_command.add_argument("--json", action="store_true")
    reanalyze_text_command = subcommands.add_parser("reanalyze-text")
    reanalyze_text_command.add_argument("plan_id")
    reanalyze_text_command.add_argument("subtitle_report_id")
    reanalyze_text_command.add_argument("--prior-report", required=True, metavar="REPORT_ID")
    reanalyze_text_command.add_argument("--enhancement-report", required=True, metavar="REPORT_ID")
    reanalyze_text_command.add_argument("--json", action="store_true")
    transcribe_command = subcommands.add_parser("transcribe")
    transcribe_command.add_argument("plan_id")
    transcribe_command.add_argument("subtitle_report_id")
    transcribe_command.add_argument("audio_report_id")
    transcribe_command.add_argument("--upgrade-all", action="store_true")
    transcribe_command.add_argument("--json", action="store_true")
    resume_transcription_command = subcommands.add_parser("resume-transcription")
    resume_transcription_command.add_argument("report_id")
    resume_transcription_command.add_argument("--decision", metavar="DECISION")
    resume_transcription_command.add_argument("--json", action="store_true")
    enhance_command = subcommands.add_parser("enhance")
    enhance_command.add_argument("plan_id")
    enhance_command.add_argument("subtitle_report_id")
    enhance_command.add_argument("--audio-report", metavar="REPORT_ID")
    enhance_command.add_argument("--part", action="append", default=[], metavar="PART_ID")
    enhance_command.add_argument(
        "--range", action="append", default=[], metavar="PART_ID:START-END"
    )
    enhance_command.add_argument("--cue", action="append", default=[], metavar="PART_ID:ORDINAL")
    enhance_command.add_argument("--json", action="store_true")
    resume_enhancement_command = subcommands.add_parser("resume-enhancement")
    resume_enhancement_command.add_argument("report_id")
    resume_enhancement_command.add_argument("--decision", metavar="DECISION")
    resume_enhancement_command.add_argument("--json", action="store_true")
    visual_text_command = subcommands.add_parser("visual-text")
    visual_text_command.add_argument("plan_id")
    visual_text_command.add_argument("--all", action="store_true")
    visual_text_command.add_argument("--part", action="append", default=[], metavar="PART_ID")
    visual_text_command.add_argument(
        "--range", action="append", default=[], metavar="PART_ID:START-END"
    )
    visual_text_command.add_argument("--audio-report", metavar="REPORT_ID")
    visual_text_command.add_argument("--json", action="store_true")
    resume_visual_text_command = subcommands.add_parser("resume-visual-text")
    resume_visual_text_command.add_argument("report_id")
    resume_visual_text_command.add_argument("--decision", metavar="DECISION")
    resume_visual_text_command.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the intentionally small Phase 1 CLI."""

    arguments = _parser().parse_args(argv)
    assert_runtime_policy()
    identity = assert_project_venv()
    if arguments.command == "check-environment":
        print(
            json.dumps(
                {
                    "executable": str(identity.executable),
                    "prefix": str(identity.prefix),
                    "virtual_env": str(identity.virtual_env),
                },
                sort_keys=True,
            )
        )
        return 0
    if arguments.command == "plan":
        try:
            result = _handle_plan(arguments)
        except (
            InspectionError,
            PlanningError,
            SourceIntakeError,
            URLPolicyError,
            ValueError,
        ) as error:
            print(json.dumps({"status": "error", "reason": _reason(error), "message": str(error)}))
            return 2
        print(json.dumps(result, sort_keys=True))
        return 0
    if arguments.command == "subtitles":
        if arguments.resume is None:
            result = process_subtitles(
                arguments.plan_id, _project_root(), tuple(arguments.decoders)
            )
        else:
            result = resume_subtitles(
                arguments.plan_id,
                arguments.resume,
                tuple(arguments.select),
                _project_root(),
                tuple(arguments.decoders),
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    if arguments.command == "analyze-audio":
        result = analyze_audio(
            arguments.plan_id,
            arguments.subtitle_report_id,
            _project_root(),
            tuple(arguments.audio_stream),
            arguments.diarization_candidate,
            tuple(arguments.role_metadata),
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    if arguments.command == "resume-audio-analysis":
        result = resume_audio_analysis(
            arguments.report_id,
            arguments.diarization_candidate,
            _project_root(),
            tuple(arguments.role_metadata),
            arguments.decision,
            tuple(arguments.audio_stream),
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    if arguments.command == "analyze-text":
        result = analyze_text(
            arguments.plan_id,
            arguments.subtitle_report_id,
            _project_root(),
            arguments.audio_report,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    if arguments.command == "resume-text-analysis":
        try:
            result = resume_text_analysis(arguments.report_id, arguments.decision, _project_root())
        except TextAnalysisError as error:
            print(json.dumps({"status": "error", "reason": error.reason, "message": str(error)}))
            return 2
        print(json.dumps(result, sort_keys=True))
        return 0
    if arguments.command == "reanalyze-text":
        result = reanalyze_text(
            arguments.plan_id,
            arguments.subtitle_report_id,
            arguments.prior_report,
            arguments.enhancement_report,
            _project_root(),
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    if arguments.command == "transcribe":
        result = transcribe(
            arguments.plan_id,
            arguments.subtitle_report_id,
            arguments.audio_report_id,
            _project_root(),
            upgrade_all=arguments.upgrade_all,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    if arguments.command == "resume-transcription":
        try:
            result = resume_transcription(arguments.report_id, arguments.decision, _project_root())
        except TranscriptionError as error:
            print(json.dumps({"status": "error", "reason": error.reason, "message": str(error)}))
            return 2
        print(json.dumps(result, sort_keys=True))
        return 0
    if arguments.command == "enhance":
        result = enhance(
            arguments.plan_id,
            arguments.subtitle_report_id,
            _project_root(),
            part_selectors=tuple(arguments.part),
            range_selectors=tuple(getattr(arguments, "range")),
            cue_selectors=tuple(arguments.cue),
            audio_report_id=arguments.audio_report,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    if arguments.command == "resume-enhancement":
        try:
            result = resume_enhancement(arguments.report_id, arguments.decision, _project_root())
        except EnhancementError as error:
            print(json.dumps({"status": "error", "reason": error.reason, "message": str(error)}))
            return 2
        print(json.dumps(result, sort_keys=True))
        return 0
    if arguments.command == "visual-text":
        try:
            result = run_visual_text(
                arguments.plan_id,
                _project_root(),
                all_parts=arguments.all,
                part_selectors=tuple(arguments.part),
                range_selectors=tuple(getattr(arguments, "range")),
                audio_report_id=arguments.audio_report,
            )
        except VisualTextError as error:
            print(json.dumps({"status": "error", "reason": error.reason, "message": str(error)}))
            return 2
        print(json.dumps(result, sort_keys=True))
        return 0
    if arguments.command == "resume-visual-text":
        try:
            result = resume_visual_text(arguments.report_id, arguments.decision, _project_root())
        except VisualTextError as error:
            print(json.dumps({"status": "error", "reason": error.reason, "message": str(error)}))
            return 2
        print(json.dumps(result, sort_keys=True))
        return 0
    raise AssertionError(f"Unhandled command: {arguments.command}")


def _handle_plan(arguments: argparse.Namespace) -> dict[str, object]:
    project_root = _project_root()
    plans_root = project_root / "plans"
    if arguments.target == "decode":
        if not arguments.report_id:
            raise PlanningError("report_id_missing", "vcp plan decode requires a report ID.")
        return _decode_report(arguments.report_id, project_root, plans_root)
    if arguments.target == "confirm":
        if not arguments.report_id:
            raise PlanningError("report_id_missing", "vcp plan confirm requires a report ID.")
        report = load_plan_report(plans_root / "reports" / arguments.report_id / "plan-report.json")
        plan = confirm_run_plan(report, project_root, plans_root)
        return {"status": "confirmed", "plan": plan.as_json()}
    if arguments.collect:
        if arguments.target:
            return _blocked_url_report(
                "collection_target_incompatible",
                "Manual collection accepts URLs only through its session.",
                plans_root,
                (),
            )
        if arguments.url_mode is None:
            return _blocked_url_report(
                "url_mode_missing",
                "A manual collection requires an explicit --url-mode.",
                plans_root,
                (),
            )
        return _plan_manual_collection(
            URLAccessMode(arguments.url_mode),
            arguments.allow_insecure_http,
            project_root,
            plans_root,
            arguments.json,
        )
    if not arguments.target:
        raise PlanningError(
            "source_missing", "vcp plan needs a local file, public URL, or --collect."
        )
    if urlsplit(arguments.target).scheme:
        if urlsplit(arguments.target).scheme.lower() not in {"http", "https"}:
            return _blocked_url_report(
                "url_scheme_invalid",
                "A public source must use HTTP or HTTPS.",
                plans_root,
                (),
            )
        if arguments.url_mode is None:
            return _blocked_url_report(
                "url_mode_missing",
                "A public URL requires an explicit --url-mode.",
                plans_root,
                (),
            )
        try:
            authorization = authorize_public_url(
                arguments.target,
                URLAccessMode(arguments.url_mode),
                allow_insecure_http=arguments.allow_insecure_http,
            )
        except URLPolicyError as error:
            return _blocked_url_report(error.reason, str(error), plans_root, ())
        return _plan_public_sources((authorization,), project_root, plans_root)
    return _plan_local_file(Path(arguments.target), project_root, plans_root)


def _plan_local_file(source_path: Path, project_root: Path, plans_root: Path) -> dict[str, object]:
    configuration_fingerprint = planning_configuration_fingerprint(project_root)
    try:
        candidate = validate_local_source_candidate(source_path)
    except SourceIntakeError as error:
        return _blocked_local_report(error, 0, plans_root, configuration_fingerprint)
    planned_increment = 0

    def check_snapshot_headroom(byte_count: int) -> None:
        nonlocal planned_increment
        planned_increment = byte_count * 2 + 64 * 1024**2
        ensure_disk_headroom(project_root, calculate_disk_headroom(planned_increment))

    try:
        artifact = snapshot_local_source(
            candidate, project_root / "input", before_copy=check_snapshot_headroom
        )
    except SourceIntakeError as error:
        return _blocked_local_report(
            error, planned_increment, plans_root, configuration_fingerprint
        )
    return _plan_source_artifacts(
        (artifact,),
        project_root,
        plans_root,
        planned_increment,
    )


def _plan_source_artifacts(
    source_artifacts: tuple[SourceArtifact, ...],
    project_root: Path,
    plans_root: Path,
    planned_increment: int,
    *,
    initial_tools: tuple[PinnedExternalTool, ...] = (),
    url_authorizations: tuple[URLAuthorization, ...] = (),
) -> dict[str, object]:
    """Apply the common strict inspection and decode-estimate workflow to snapshots."""

    configuration_fingerprint = planning_configuration_fingerprint(project_root)
    try:
        ffprobe = _configured_tool(project_root, "ffprobe")
        ffmpeg = _configured_tool(project_root, "ffmpeg")
    except (PlanningError, ValueError) as error:
        return _blocked_report(
            _reason(error),
            str(error),
            planned_increment,
            plans_root,
            configuration_fingerprint,
            source_artifacts=source_artifacts,
            tools=initial_tools,
            url_authorizations=url_authorizations,
        )
    tools = (*initial_tools, ffprobe, ffmpeg)
    inspection_evidence: list[PlanInspectionEvidence] = []
    durations = []
    for artifact in source_artifacts:
        evidence: PlanInspectionEvidence | None = None
        try:
            documents = capture_probe_documents(
                ffprobe, artifact, artifact.media_path.parent / "evidence"
            )
            evidence = PlanInspectionEvidence.from_documents(artifact.source_id, *documents)
            inspection = inspect_documents(*documents)
        except InspectionError as error:
            if isinstance(error, ProbeCaptureError):
                evidence = PlanInspectionEvidence.from_capture_error(artifact.source_id, error)
            if evidence is not None:
                inspection_evidence.append(evidence)
            return _blocked_report(
                error.reason,
                str(error),
                planned_increment,
                plans_root,
                configuration_fingerprint,
                source_artifacts=source_artifacts,
                tools=tools,
                url_authorizations=url_authorizations,
                inspection_evidence=_complete_inspection_evidence(
                    source_artifacts, inspection_evidence
                ),
            )
        evidence = PlanInspectionEvidence.from_inspection(artifact.source_id, inspection)
        inspection_evidence.append(evidence)
        media_coverages = [
            coverage.coverage
            for coverage in inspection.coverage_by_stream.values()
            if coverage.coverage is not None
        ]
        if not media_coverages or len(media_coverages) != len(inspection.coverage_by_stream):
            return _blocked_report(
                "coverage_indeterminate",
                "Source coverage is not complete.",
                planned_increment,
                plans_root,
                configuration_fingerprint,
                source_artifacts=source_artifacts,
                tools=tools,
                url_authorizations=url_authorizations,
                inspection_evidence=_complete_inspection_evidence(
                    source_artifacts, inspection_evidence
                ),
            )
        durations.append(
            max(coverage.end for coverage in media_coverages)
            - min(coverage.start for coverage in media_coverages)
        )
    try:
        profile = load_decode_throughput_profile(
            project_root / "config" / "decode-throughput-profiles.json"
        )
        measurements = load_decode_measurements(plans_root / "decode-throughput-history.json")
    except PlanningError as error:
        return _blocked_report(
            error.reason,
            str(error),
            planned_increment,
            plans_root,
            configuration_fingerprint,
            source_artifacts=source_artifacts,
            tools=tools,
            url_authorizations=url_authorizations,
            inspection_evidence=_complete_inspection_evidence(
                source_artifacts, inspection_evidence
            ),
        )
    awaiting_decode = create_plan_report(
        state=PlanState.AWAITING_DECODE_CONFIRMATION,
        source_artifacts=source_artifacts,
        tools=tools,
        planned_increment_bytes=planned_increment,
        configuration_fingerprint=configuration_fingerprint,
        decode_estimate=estimate_full_decode(
            sum((duration.as_fraction() for duration in durations), start=Fraction()),
            profile,
            matching_measurement=(
                find_matching_decode_measurement(measurements, source_artifacts[0].source_id)
                if len(source_artifacts) == 1
                else None
            ),
        ),
        url_authorizations=tuple(authorization.evidence for authorization in url_authorizations),
        inspection_evidence=tuple(inspection_evidence),
    )
    persist_plan_report(awaiting_decode, plans_root)
    return {"status": "awaiting_decode_confirmation", "report": awaiting_decode.as_json()}


def _blocked_local_report(
    error: SourceIntakeError | InspectionError | PlanningError,
    planned_increment: int,
    plans_root: Path,
    configuration_fingerprint: str,
    *,
    source_artifacts: tuple[SourceArtifact, ...] = (),
    tools: tuple[PinnedExternalTool, ...] = (),
    inspection_evidence: tuple[PlanInspectionEvidence, ...] = (),
) -> dict[str, object]:
    """Retain an intake or inspection failure as a non-executable planning outcome."""

    return _blocked_report(
        error.reason,
        str(error),
        planned_increment,
        plans_root,
        configuration_fingerprint,
        source_artifacts=source_artifacts,
        tools=tools,
        inspection_evidence=inspection_evidence,
    )


def _blocked_report(
    reason: str,
    message: str,
    planned_increment: int,
    plans_root: Path,
    configuration_fingerprint: str,
    *,
    source_artifacts: tuple[SourceArtifact, ...] = (),
    tools: tuple[PinnedExternalTool, ...] = (),
    url_authorizations: tuple[URLAuthorization, ...] = (),
    inspection_evidence: tuple[PlanInspectionEvidence, ...] = (),
) -> dict[str, object]:
    """Persist a blocked report without placing raw URL input in its evidence."""

    report = create_plan_report(
        state=PlanState.BLOCKED,
        source_artifacts=source_artifacts,
        tools=tools,
        planned_increment_bytes=planned_increment,
        configuration_fingerprint=configuration_fingerprint,
        diagnostics=(PlanningDiagnostic(reason, message),),
        url_authorizations=tuple(authorization.evidence for authorization in url_authorizations),
        inspection_evidence=_complete_inspection_evidence(
            source_artifacts, list(inspection_evidence)
        ),
    )
    persist_plan_report(report, plans_root)
    return {"status": "blocked", "report": report.as_json()}


def _decode_report(report_id: str, project_root: Path, plans_root: Path) -> dict[str, object]:
    report = load_plan_report(plans_root / "reports" / report_id / "plan-report.json")
    if report.state != PlanState.AWAITING_DECODE_CONFIRMATION:
        raise PlanningError("decode_not_available", "Report is not awaiting decode confirmation.")
    diagnostics = revalidate_report(report, project_root)
    if diagnostics:
        stale = create_plan_report(
            state=PlanState.BLOCKED,
            source_artifacts=report.source_artifacts,
            tools=report.tools,
            planned_increment_bytes=report.disk_headroom.increment_bytes,
            configuration_fingerprint=report.configuration_fingerprint,
            decode_estimate=report.decode_estimate,
            diagnostics=diagnostics,
            url_authorizations=report.url_authorizations,
            inspection_evidence=report.inspection_evidence,
            parent_report_id=report.report_id,
        )
        persist_plan_report(stale, plans_root)
        return {"status": "blocked", "report": stale.as_json()}
    ffmpeg = next((tool for tool in report.tools if tool.tool_id == "ffmpeg"), None)
    if ffmpeg is None:
        raise PlanningError("ffmpeg_missing", "Report has no FFmpeg tool identity.")
    try:
        for artifact in report.source_artifacts:
            elapsed_seconds = perform_full_decode_validation(ffmpeg, artifact)
            record_decode_measurement(
                plans_root / "decode-throughput-history.json", artifact.source_id, elapsed_seconds
            )
    except PlanningError as error:
        blocked = create_plan_report(
            state=PlanState.BLOCKED,
            source_artifacts=report.source_artifacts,
            tools=report.tools,
            planned_increment_bytes=report.disk_headroom.increment_bytes,
            configuration_fingerprint=report.configuration_fingerprint,
            decode_estimate=report.decode_estimate,
            diagnostics=(PlanningDiagnostic(error.reason, str(error)),),
            url_authorizations=report.url_authorizations,
            inspection_evidence=report.inspection_evidence,
            parent_report_id=report.report_id,
        )
        persist_plan_report(blocked, plans_root)
        return {"status": "blocked", "report": blocked.as_json()}
    ready = create_plan_report(
        state=PlanState.READY_FOR_CONFIRMATION,
        source_artifacts=report.source_artifacts,
        tools=report.tools,
        planned_increment_bytes=report.disk_headroom.increment_bytes,
        configuration_fingerprint=report.configuration_fingerprint,
        decode_estimate=report.decode_estimate,
        url_authorizations=report.url_authorizations,
        inspection_evidence=report.inspection_evidence,
        parent_report_id=report.report_id,
    )
    persist_plan_report(ready, plans_root)
    return {"status": "ready_for_confirmation", "report": ready.as_json()}


def _plan_manual_collection(
    mode: URLAccessMode,
    allow_insecure_http: bool,
    project_root: Path,
    plans_root: Path,
    json_output: bool,
) -> dict[str, object]:
    session = ManualCollectionSession(mode=mode, allow_insecure_http=allow_insecure_http)
    try:
        entries = _collect_manual_urls(session, json_output=json_output)
    except URLPolicyError as error:
        return _blocked_url_report(error.reason, str(error), plans_root, session.entries)
    return _plan_public_sources(entries, project_root, plans_root)


def _plan_public_sources(
    authorizations: tuple[URLAuthorization, ...], project_root: Path, plans_root: Path
) -> dict[str, object]:
    """Acquire authorized public sources before passing their snapshots to preflight."""

    configuration_fingerprint = planning_configuration_fingerprint(project_root)
    try:
        downloader = _configured_tool(project_root, "yt-dlp")
    except (PlanningError, ValueError) as error:
        return _blocked_report(
            _reason(error),
            str(error),
            0,
            plans_root,
            configuration_fingerprint,
            url_authorizations=authorizations,
        )
    artifacts: list[SourceArtifact] = []
    source_ids: set[str] = set()
    for ordinal, authorization in enumerate(authorizations, start=1):
        try:
            artifact = acquire_public_source(authorization, downloader, project_root)
        except (SourceIntakeError, URLAcquisitionError, URLPolicyError) as error:
            return _blocked_report(
                error.reason,
                str(error),
                _public_planned_increment(artifacts),
                plans_root,
                configuration_fingerprint,
                source_artifacts=tuple(artifacts),
                tools=(downloader,),
                url_authorizations=authorizations,
            )
        if artifact.source_id in source_ids:
            return _blocked_report(
                "duplicate_part",
                (
                    f"Collection Part {ordinal} has the same SourceArtifact content as an "
                    "earlier Part."
                ),
                _public_planned_increment(artifacts),
                plans_root,
                configuration_fingerprint,
                source_artifacts=tuple(artifacts),
                tools=(downloader,),
                url_authorizations=authorizations,
            )
        source_ids.add(artifact.source_id)
        artifacts.append(artifact)
    return _plan_source_artifacts(
        tuple(artifacts),
        project_root,
        plans_root,
        _public_planned_increment(artifacts),
        initial_tools=(downloader,),
        url_authorizations=authorizations,
    )


def _public_planned_increment(artifacts: Sequence[SourceArtifact]) -> int:
    return sum(artifact.byte_count * 2 for artifact in artifacts) + 64 * 1024**2


def _collect_manual_urls(
    session: ManualCollectionSession, *, json_output: bool
) -> tuple[URLAuthorization, ...]:
    prompt = (
        "Submit public URLs in presentation order; enter 结束 when the collection is complete: "
    )
    while True:
        try:
            if json_output:
                print(prompt, end="", file=sys.stderr)
            submitted = _read_collection_line("" if json_output else prompt)
        except EOFError as error:
            raise URLPolicyError(
                "collection_input_ended", "Manual collection ended before its 结束 closure signal."
            ) from error
        if submitted == COLLECTION_CLOSURE_SIGNAL:
            return session.close(submitted)
        session.append(submitted)


def _read_collection_line(prompt: str) -> str:
    return input(prompt)


def _blocked_url_report(
    reason: str,
    message: str,
    plans_root: Path,
    authorizations: tuple[URLAuthorization, ...],
) -> dict[str, object]:
    report = create_plan_report(
        state=PlanState.BLOCKED,
        source_artifacts=(),
        tools=(),
        planned_increment_bytes=0,
        configuration_fingerprint="phase-03-url-policy-v1",
        diagnostics=(PlanningDiagnostic(reason, message),),
        url_authorizations=tuple(authorization.evidence for authorization in authorizations),
    )
    persist_plan_report(report, plans_root)
    return {"status": "blocked", "report": report.as_json()}


def _complete_inspection_evidence(
    source_artifacts: tuple[SourceArtifact, ...],
    inspected: Sequence[PlanInspectionEvidence],
) -> tuple[PlanInspectionEvidence, ...]:
    """Retain explicit no-probe evidence for sources after a blocked earlier Part."""

    by_source = {evidence.source_id: evidence for evidence in inspected}
    return tuple(
        by_source.get(
            artifact.source_id,
            PlanInspectionEvidence(artifact.source_id, None, None, (), ()),
        )
        for artifact in source_artifacts
    )


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _configured_tool(project_root: Path, tool_id: str) -> PinnedExternalTool:
    try:
        decoded = json.loads((project_root / "config" / "tools.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PlanningError("tool_registry_invalid", "Tool registry cannot be read.") from error
    tools = decoded.get("tools")
    if not isinstance(tools, list):
        raise PlanningError("tool_registry_invalid", "Tool registry has no tools list.")
    for tool in tools:
        if (
            isinstance(tool, dict)
            and tool.get("id") == tool_id
            and isinstance(tool.get("path"), str)
        ):
            return identify_external_tool(tool_id, Path(tool["path"]))
    raise PlanningError("tool_registry_missing", f"Tool registry has no {tool_id} entry.")


def _reason(error: BaseException) -> str:
    return getattr(error, "reason", "unexpected_error")
