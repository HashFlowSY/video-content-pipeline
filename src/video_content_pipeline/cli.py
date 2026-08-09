"""CLI boundary for environment validation and Phase 3 planning."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from video_content_pipeline import __version__
from video_content_pipeline.environment import assert_project_venv, assert_runtime_policy
from video_content_pipeline.external_tools import PinnedExternalTool, identify_external_tool
from video_content_pipeline.inspection import (
    InspectionError,
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
    load_decode_throughput_profile,
    load_plan_report,
    perform_full_decode_validation,
    persist_plan_report,
    revalidate_report,
)
from video_content_pipeline.source import (
    SourceIntakeError,
    calculate_disk_headroom,
    ensure_disk_headroom,
    snapshot_local_source,
    validate_local_source_candidate,
)
from video_content_pipeline.url_policy import URLAccessMode, URLPolicyError, authorize_public_url


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
    raise AssertionError(f"Unhandled command: {arguments.command}")


def _handle_plan(arguments: argparse.Namespace) -> dict[str, object]:
    project_root = Path(__file__).resolve().parents[2]
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
        return _blocked_url_report(
            "manual_collection_requires_interactive_transport", project_root, plans_root
        )
    if not arguments.target:
        raise PlanningError(
            "source_missing", "vcp plan needs a local file, public URL, or --collect."
        )
    if arguments.target.startswith(("http://", "https://")):
        if arguments.url_mode is None:
            raise URLPolicyError(
                "url_mode_missing", "A public URL requires an explicit --url-mode."
            )
        authorize_public_url(
            arguments.target,
            URLAccessMode(arguments.url_mode),
            allow_insecure_http=arguments.allow_insecure_http,
        )
        return _blocked_url_report("url_acquisition_pending", project_root, plans_root)
    return _plan_local_file(Path(arguments.target), project_root, plans_root)


def _plan_local_file(source_path: Path, project_root: Path, plans_root: Path) -> dict[str, object]:
    try:
        candidate = validate_local_source_candidate(source_path)
    except SourceIntakeError as error:
        return _blocked_local_report(error, 0, plans_root)
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
        return _blocked_local_report(error, planned_increment, plans_root)
    ffprobe = _configured_tool(project_root, "ffprobe")
    ffmpeg = _configured_tool(project_root, "ffmpeg")
    documents = capture_probe_documents(ffprobe, artifact, artifact.media_path.parent / "evidence")
    inspection = inspect_documents(*documents)
    media_coverages = [
        coverage.coverage
        for coverage in inspection.coverage_by_stream.values()
        if coverage.coverage is not None
    ]
    if not media_coverages or len(media_coverages) != len(inspection.coverage_by_stream):
        blocked = create_plan_report(
            state=PlanState.BLOCKED,
            source_artifacts=(artifact,),
            tools=(ffprobe, ffmpeg),
            planned_increment_bytes=planned_increment,
            configuration_fingerprint="phase-03-local-plan-v1",
            diagnostics=(
                PlanningDiagnostic("coverage_indeterminate", "Source coverage is not complete."),
            ),
        )
        persist_plan_report(blocked, plans_root)
        return {"status": "blocked", "report": blocked.as_json()}
    duration = max(coverage.end for coverage in media_coverages) - min(
        coverage.start for coverage in media_coverages
    )
    profile = load_decode_throughput_profile(
        project_root / "config" / "decode-throughput-profiles.json"
    )
    awaiting_decode = create_plan_report(
        state=PlanState.AWAITING_DECODE_CONFIRMATION,
        source_artifacts=(artifact,),
        tools=(ffprobe, ffmpeg),
        planned_increment_bytes=planned_increment,
        configuration_fingerprint="phase-03-local-plan-v1",
        decode_estimate=estimate_full_decode(duration.as_fraction(), profile),
    )
    persist_plan_report(awaiting_decode, plans_root)
    return {"status": "awaiting_decode_confirmation", "report": awaiting_decode.as_json()}


def _blocked_local_report(
    error: SourceIntakeError, planned_increment: int, plans_root: Path
) -> dict[str, object]:
    """Retain a local-source failure as a non-executable planning outcome."""

    report = create_plan_report(
        state=PlanState.BLOCKED,
        source_artifacts=(),
        tools=(),
        planned_increment_bytes=planned_increment,
        configuration_fingerprint="phase-03-local-plan-v1",
        diagnostics=(PlanningDiagnostic(error.reason, str(error)),),
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
            parent_report_id=report.report_id,
        )
        persist_plan_report(stale, plans_root)
        return {"status": "blocked", "report": stale.as_json()}
    ffmpeg = next((tool for tool in report.tools if tool.tool_id == "ffmpeg"), None)
    if ffmpeg is None:
        raise PlanningError("ffmpeg_missing", "Report has no FFmpeg tool identity.")
    try:
        for artifact in report.source_artifacts:
            perform_full_decode_validation(ffmpeg, artifact)
    except PlanningError as error:
        blocked = create_plan_report(
            state=PlanState.BLOCKED,
            source_artifacts=report.source_artifacts,
            tools=report.tools,
            planned_increment_bytes=report.disk_headroom.increment_bytes,
            configuration_fingerprint=report.configuration_fingerprint,
            decode_estimate=report.decode_estimate,
            diagnostics=(PlanningDiagnostic(error.reason, str(error)),),
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
        parent_report_id=report.report_id,
    )
    persist_plan_report(ready, plans_root)
    return {"status": "ready_for_confirmation", "report": ready.as_json()}


def _blocked_url_report(reason: str, project_root: Path, plans_root: Path) -> dict[str, object]:
    report = create_plan_report(
        state=PlanState.BLOCKED,
        source_artifacts=(),
        tools=(),
        planned_increment_bytes=0,
        configuration_fingerprint="phase-03-url-policy-v1",
        diagnostics=(
            PlanningDiagnostic(
                reason, "URL policy accepted; acquisition remains intentionally disabled."
            ),
        ),
    )
    persist_plan_report(report, plans_root)
    return {"status": "blocked", "report": report.as_json()}


def _configured_tool(project_root: Path, tool_id: str) -> PinnedExternalTool:
    decoded = json.loads((project_root / "config" / "tools.json").read_text(encoding="utf-8"))
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
