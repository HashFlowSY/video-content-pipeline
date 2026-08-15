"""Phase 6's immutable text-analysis workspace, CLI contract, and revalidation.

Ticket 01 established the immutable workspace, the domain records, the report
identity, and the ``controlled_adapter_unavailable`` result. Ticket 02 adds the
``vcp analyze-text`` and explicit ``vcp resume-text-analysis`` public commands
and completes input revalidation before an attempt may proceed: a confirmed
RunPlan and its ready PlanReport (including SourceArtifact hashes and hash-pinned
inspection evidence), the retained Subtitle candidate report with every selected
Primary track, the versioned subtitle and text-analysis rules, and an optional
Audio analysis report binding. Any drift blocks the attempt as ``failed``.

Ticket 03 binds the versioned generation and rendering contracts: a fully
revalidated attempt now also revalidates the versioned prompt template, output
projection schema, evidence-rule record, and Controlled offline text adapter
identity (see ``text_contracts``), records their hash evidence, and writes a
deterministic Markdown rendition of the authoritative JSON report into the
immutable workspace.

No Controlled offline text adapter *generates* yet, so a fully revalidated
attempt still retains ``controlled_adapter_unavailable`` with no semantic
content. The generating adapter and semantic segmentation belong to later
Phase 6 tickets. See ``docs/PHASE_06_SPECIFICATION.md`` and the Text Analysis
Context.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
from pathlib import Path

from video_content_pipeline.evidence import (
    InputEvidence,
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
from video_content_pipeline.source import SourceArtifact, sha256_file
from video_content_pipeline.subtitle_pipeline import (
    CandidateReportState,
    CandidateState,
    SubtitleCandidateReport,
    SubtitleReportError,
    subtitle_rules_fingerprint,
)
from video_content_pipeline.text_contracts import (
    TextContractError,
    TextGenerationContracts,
    render_text_analysis_markdown,
    revalidate_text_generation_contracts,
)


class TextAnalysisReportStatus(StrEnum):
    """The recorded outcome of one text-analysis attempt.

    ``complete``/``partial``/``failed`` are the formal Text analysis report
    statuses. ``controlled_adapter_unavailable`` is the availability outcome
    recorded when no eligible offline text adapter exists; it retains no
    SemanticSegments. (A future real-model path would add its own
    ``model_acquisition_required`` outcome when that capability is built.)
    """

    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    CONTROLLED_ADAPTER_UNAVAILABLE = "controlled_adapter_unavailable"


class TextAnalysisError(ValueError):
    """A rejected Phase 6 input with a machine-readable diagnostic reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class RestrictedRawOutput:
    """Raw adapter or model output retained as restricted local audit evidence.

    It is excluded from formal content and from default publication; any export
    requires separate explicit authorization.
    """

    path: Path
    sha256: str
    byte_count: int

    def as_json(self) -> dict[str, object]:
        return {
            "path": self.path.as_posix(),
            "sha256": self.sha256,
            "byte_count": self.byte_count,
            "restriction": "local_audit_only",
        }


@dataclass(frozen=True)
class ControlledTextAdapterState:
    """The availability outcome for the Controlled offline text adapter."""

    state: str
    diagnostic: PlanningDiagnostic | None

    def as_json(self) -> dict[str, object]:
        return {
            "state": self.state,
            "model": None,
            "diagnostic": self.diagnostic.as_json() if self.diagnostic is not None else None,
        }


@dataclass(frozen=True)
class SelectedPrimaryTrack:
    """One revalidated Primary subtitle track bound to a Part's SourceArtifact."""

    source_id: str
    stream_index: int
    sha256: str

    def as_json(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "stream_index": self.stream_index,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class RevalidationEvidence:
    """The auditable outcome of complete text-analysis input revalidation."""

    run_plan_confirmed: bool
    subtitle_rules_fingerprint: str | None
    text_analysis_rules_fingerprint: str | None
    selected_primary_tracks: tuple[SelectedPrimaryTrack, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "run_plan_confirmed": self.run_plan_confirmed,
            "subtitle_rules_fingerprint": self.subtitle_rules_fingerprint,
            "text_analysis_rules_fingerprint": self.text_analysis_rules_fingerprint,
            "selected_primary_tracks": [track.as_json() for track in self.selected_primary_tracks],
        }


@dataclass(frozen=True)
class AudioAnalysisBinding:
    """The optional Audio analysis context binding for one text-analysis attempt.

    Its absence is recorded as ``not_available``; the report always keeps
    ``audio_completeness=not_verified`` regardless of any bound audio evidence.
    """

    state: str
    report_id: str | None = None
    plan_id: str | None = None
    subtitle_report_id: str | None = None

    def as_json(self) -> dict[str, object]:
        if self.state != "bound":
            return {"state": self.state}
        return {
            "state": self.state,
            "report_id": self.report_id,
            "plan_id": self.plan_id,
            "subtitle_report_id": self.subtitle_report_id,
        }


@dataclass(frozen=True)
class TextAnalysisReport:
    """Immutable machine-readable result of one text-analysis attempt."""

    report_id: str
    plan_id: str
    subtitle_report_id: str
    status: TextAnalysisReportStatus
    workspace_path: Path
    report_path: Path
    run_plan_evidence: InputEvidence | None
    subtitle_report_evidence: InputEvidence | None
    text_analysis_rules_evidence: InputEvidence | None
    audio_analysis_report_evidence: InputEvidence | None
    resumed_from_report: InputEvidence | None
    resumption_decision: str | None
    controlled_text_adapter: ControlledTextAdapterState
    audio_analysis: AudioAnalysisBinding
    revalidation: RevalidationEvidence
    text_generation_contracts: TextGenerationContracts | None
    rendered_report: dict[str, object] | None
    restricted_raw_output: tuple[RestrictedRawOutput, ...]
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
                "run_plan": (
                    self.run_plan_evidence.as_json() if self.run_plan_evidence is not None else None
                ),
                "subtitle_candidate_report": (
                    self.subtitle_report_evidence.as_json()
                    if self.subtitle_report_evidence is not None
                    else None
                ),
                "text_analysis_rules": (
                    self.text_analysis_rules_evidence.as_json()
                    if self.text_analysis_rules_evidence is not None
                    else None
                ),
                "audio_analysis_report": (
                    self.audio_analysis_report_evidence.as_json()
                    if self.audio_analysis_report_evidence is not None
                    else None
                ),
                "resumed_from_report": (
                    self.resumed_from_report.as_json()
                    if self.resumed_from_report is not None
                    else None
                ),
                "resumption_decision": self.resumption_decision,
            },
            "controlled_text_adapter": self.controlled_text_adapter.as_json(),
            "audio_analysis": self.audio_analysis.as_json(),
            "audio_completeness": "not_verified",
            "revalidation": self.revalidation.as_json(),
            "text_generation_contracts": (
                self.text_generation_contracts.as_json()
                if self.text_generation_contracts is not None
                else None
            ),
            "rendered_report": self.rendered_report,
            "segments": [],
            "chapters": [],
            "collection_summary": None,
            "restricted_raw_output": [output.as_json() for output in self.restricted_raw_output],
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


_ADAPTER_UNAVAILABLE_MESSAGE = (
    "No Controlled offline text adapter is available; no semantic content was generated."
)


def _adapter_unavailable_diagnostic() -> PlanningDiagnostic:
    return PlanningDiagnostic(
        TextAnalysisReportStatus.CONTROLLED_ADAPTER_UNAVAILABLE.value,
        _ADAPTER_UNAVAILABLE_MESSAGE,
    )


def text_analysis_rules_fingerprint(project_root: Path) -> str:
    """Validate and fingerprint the versioned, project-owned Phase 6 text rules.

    The rules bundle the cue, prompt-template, output-schema, evidence-rule, and
    Controlled offline text adapter identity versions. Ticket 02 revalidates the
    whole-file identity; later tickets interpret the individual versioned fields.
    """

    rules_path = project_root / "config" / "text-analysis-rules.json"
    try:
        raw_rules = rules_path.read_bytes()
        decoded = json.loads(raw_rules)
    except (OSError, json.JSONDecodeError) as error:
        raise TextAnalysisError(
            "text_analysis_rules_invalid", "Text analysis rules cannot be read."
        ) from error
    if not isinstance(decoded, Mapping) or decoded.get("schema_version") != 1:
        raise TextAnalysisError(
            "text_analysis_rules_invalid", "Text analysis rules have an invalid schema."
        )
    return sha256(raw_rules).hexdigest()


def analyze_text(
    plan_id: str,
    subtitle_report_id: str,
    project_root: Path,
    audio_report_id: str | None = None,
) -> dict[str, object]:
    """Create one immutable text-analysis report from fully revalidated inputs.

    Every bound input identity is revalidated before an attempt proceeds; any
    drift retains a ``failed`` report. When all inputs revalidate, the attempt —
    which has no Controlled offline text adapter available yet — retains a
    ``controlled_adapter_unavailable`` report with no semantic content.
    """

    report_id = uuid.uuid4().hex
    workspace_path = project_root / "work" / "text-analysis-reports" / report_id
    report_path = workspace_path / "text-analysis-report.json"
    run_plan_evidence: InputEvidence | None = None
    subtitle_report_evidence: InputEvidence | None = None
    text_analysis_rules_evidence: InputEvidence | None = None
    audio_analysis_report_evidence: InputEvidence | None = None
    diagnostics: tuple[PlanningDiagnostic, ...] = ()
    status = TextAnalysisReportStatus.FAILED
    report_plan_id = plan_id
    report_subtitle_id = subtitle_report_id
    run_plan_confirmed = False
    subtitle_rules_value: str | None = None
    text_rules_value: str | None = None
    selected_primary_tracks: tuple[SelectedPrimaryTrack, ...] = ()
    audio_binding = AudioAnalysisBinding("not_available")
    contracts: TextGenerationContracts | None = None

    try:
        plan_path = project_root / "plans" / plan_id / "run-plan.json"
        plan = load_run_plan(plan_path)
        if plan.plan_id != plan_id:
            raise TextAnalysisError(
                "run_plan_not_confirmed", "RunPlan identity does not match the requested plan ID."
            )
        confirmed_report = load_plan_report(
            project_root / "plans" / "reports" / plan.report_id / "plan-report.json"
        )
        if not confirmed_plan_matches(confirmed_report, plan):
            raise TextAnalysisError(
                "run_plan_not_confirmed", "RunPlan evidence does not match a confirmed PlanReport."
            )
        revalidate_confirmed_inspection_evidence(
            confirmed_report,
            plan,
            drift_error=lambda: TextAnalysisError(
                "inspection_evidence_changed",
                "PlanReport inspection evidence no longer matches the confirmed RunPlan.",
            ),
        )
        run_plan_confirmed = True
        expected_subtitle_id = _validated_report_id(subtitle_report_id)
        subtitle_path = _subtitle_report_path(
            project_root, plan.source_artifacts, expected_subtitle_id
        )
        subtitle_report = _load_subtitle_report(subtitle_path)
        if (
            subtitle_report.report_id != expected_subtitle_id
            or subtitle_report.plan_id != plan.plan_id
        ):
            raise TextAnalysisError(
                "subtitle_report_mismatch",
                "Subtitle candidate report does not belong to this RunPlan.",
            )
        subtitle_rules_value = _revalidate_subtitle_rules(subtitle_report, project_root)
        selected_primary_tracks = _selected_primary_tracks(plan, subtitle_report)
        text_rules_value = text_analysis_rules_fingerprint(project_root)
        contracts = revalidate_text_generation_contracts(project_root)
        if audio_report_id is not None:
            audio_analysis_report_evidence, audio_binding = _bind_audio_report(
                project_root, audio_report_id, plan.plan_id, subtitle_report.report_id
            )
        run_plan_evidence = _input_evidence(plan_path)
        subtitle_report_evidence = _input_evidence(subtitle_path)
        text_analysis_rules_evidence = _input_evidence(
            project_root / "config" / "text-analysis-rules.json"
        )
        report_plan_id = plan.plan_id
        report_subtitle_id = subtitle_report.report_id
        status = TextAnalysisReportStatus.CONTROLLED_ADAPTER_UNAVAILABLE
        diagnostics = (_adapter_unavailable_diagnostic(),)
    except (
        TextAnalysisError,
        TextContractError,
        PlanningError,
        SubtitleReportError,
        OSError,
        ValueError,
    ) as error:
        status = TextAnalysisReportStatus.FAILED
        run_plan_confirmed = False
        selected_primary_tracks = ()
        audio_analysis_report_evidence = None
        audio_binding = AudioAnalysisBinding("not_available")
        contracts = None
        diagnostics = (
            PlanningDiagnostic(
                getattr(error, "reason", "text_analysis_input_invalid"),
                str(error),
            ),
        )

    report = TextAnalysisReport(
        report_id=report_id,
        plan_id=report_plan_id,
        subtitle_report_id=report_subtitle_id,
        status=status,
        workspace_path=workspace_path,
        report_path=report_path,
        run_plan_evidence=run_plan_evidence,
        subtitle_report_evidence=subtitle_report_evidence,
        text_analysis_rules_evidence=text_analysis_rules_evidence,
        audio_analysis_report_evidence=audio_analysis_report_evidence,
        resumed_from_report=None,
        resumption_decision=None,
        controlled_text_adapter=ControlledTextAdapterState(
            state=TextAnalysisReportStatus.CONTROLLED_ADAPTER_UNAVAILABLE.value,
            diagnostic=_adapter_unavailable_diagnostic(),
        ),
        audio_analysis=audio_binding,
        revalidation=RevalidationEvidence(
            run_plan_confirmed=run_plan_confirmed,
            subtitle_rules_fingerprint=subtitle_rules_value,
            text_analysis_rules_fingerprint=text_rules_value,
            selected_primary_tracks=selected_primary_tracks,
        ),
        text_generation_contracts=contracts,
        rendered_report=None,
        restricted_raw_output=(),
        diagnostics=diagnostics,
    )
    report = _render_and_bind_markdown(report)
    _write_json_once(report_path, report.as_json())
    return {"status": report.status.value, "report": report.as_json()}


def _render_and_bind_markdown(report: TextAnalysisReport) -> TextAnalysisReport:
    """Render the deterministic Markdown rendition and bind its version and hash.

    The renderer reads only verified report content, never the ``rendered_report``
    provenance it produces, so the Markdown hash is stable and the JSON report
    stays authoritative. The rendition is written into the immutable workspace.
    """

    rendition = render_text_analysis_markdown(report.as_json())
    markdown_path = report.workspace_path / "text-analysis-report.md"
    if markdown_path.exists():
        if markdown_path.read_text(encoding="utf-8") != rendition.text:
            raise TextAnalysisError(
                "text_analysis_report_conflict",
                f"Immutable Markdown rendition differs: {markdown_path}",
            )
    else:
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(rendition.text, encoding="utf-8")
    rendered_report = dict(rendition.as_json())
    rendered_report["path"] = markdown_path.as_posix()
    return replace(report, rendered_report=rendered_report)


def resume_text_analysis(
    report_id: str,
    decision: str | None,
    project_root: Path,
) -> dict[str, object]:
    """Resume one retained text-analysis attempt from an explicit user decision.

    Resumption never auto-resumes and never changes identity-bound inputs: it
    requires an explicit report ID and an explicit user decision, and it may
    continue only a retained decision pause. Ticket 02 produces no decision
    pause yet, so any resume request against a terminal report is rejected;
    ticket 07 adds the resource and model-release pauses this continues.
    """

    prior_path = _text_analysis_report_path(project_root, report_id)
    try:
        prior_document = json.loads(prior_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TextAnalysisError(
            "text_analysis_report_invalid", "Text analysis report cannot be read."
        ) from error
    if not isinstance(prior_document, Mapping) or prior_document.get("report_id") != report_id:
        raise TextAnalysisError("text_analysis_report_invalid", "Text analysis report is invalid.")
    if decision is None:
        raise TextAnalysisError(
            "text_analysis_resume_invalid", "Resume requires an explicit user decision."
        )
    # Ticket 02 produces no decision pause, so no retained report is resumable
    # yet. Ticket 07 adds the resource and model-release pauses this will
    # continue from the retained identities, without changing any identity-bound
    # input. Until then a resume request against a terminal report is rejected.
    raise TextAnalysisError(
        "text_analysis_resume_invalid",
        "Only a retained Phase 6 decision pause can be resumed.",
    )


def _revalidate_subtitle_rules(report: SubtitleCandidateReport, project_root: Path) -> str:
    """Reject subtitle-rule drift between report creation and this attempt."""

    current = subtitle_rules_fingerprint(project_root)
    if report.subtitle_rules_fingerprint != current:
        raise TextAnalysisError(
            "subtitle_rules_changed",
            "Subtitle rules no longer match the retained candidate report.",
        )
    return current


def _selected_primary_tracks(
    plan: RunPlan, report: SubtitleCandidateReport
) -> tuple[SelectedPrimaryTrack, ...]:
    """Revalidate the retained Primary subtitle track for every resolved Part."""

    if report.state not in {CandidateReportState.COMPLETED, CandidateReportState.PARTIAL}:
        raise TextAnalysisError(
            "subtitle_selection_unresolved",
            "Subtitle candidate report is not fully resolved for text analysis.",
        )
    selections = {selection.source_id: selection.stream_index for selection in report.selections}
    tracks: list[SelectedPrimaryTrack] = []
    for artifact in plan.source_artifacts:
        valid = [
            candidate
            for candidate in report.candidates
            if candidate.source_id == artifact.source_id and candidate.state is CandidateState.VALID
        ]
        if not valid:
            # A Part without a valid Primary subtitle track is text_content=unavailable;
            # later tickets record that omission during collection aggregation.
            continue
        if len(valid) == 1:
            selected = valid[0]
        else:
            chosen_index = selections.get(artifact.source_id)
            match = next(
                (candidate for candidate in valid if candidate.stream_index == chosen_index),
                None,
            )
            if match is None:
                raise TextAnalysisError(
                    "subtitle_selection_unresolved",
                    "A Part has multiple valid subtitle tracks without a retained selection.",
                )
            selected = match
        if selected.source_candidate_path is None or selected.source_candidate_sha256 is None:
            raise TextAnalysisError(
                "subtitle_track_changed",
                "A selected Primary subtitle track has incomplete retained evidence.",
            )
        try:
            actual_sha256, _ = sha256_file(Path(selected.source_candidate_path))
        except OSError as error:
            raise TextAnalysisError(
                "subtitle_track_changed",
                "A selected Primary subtitle track can no longer be read.",
            ) from error
        if actual_sha256 != selected.source_candidate_sha256:
            raise TextAnalysisError(
                "subtitle_track_changed",
                "A selected Primary subtitle track hash no longer matches.",
            )
        tracks.append(
            SelectedPrimaryTrack(
                source_id=selected.source_id,
                stream_index=selected.stream_index,
                sha256=selected.source_candidate_sha256,
            )
        )
    return tuple(tracks)


def _bind_audio_report(
    project_root: Path, audio_report_id: str, plan_id: str, subtitle_report_id: str
) -> tuple[InputEvidence, AudioAnalysisBinding]:
    """Bind an optional Audio analysis report and revalidate its input identities."""

    validated_id = validated_report_id(
        audio_report_id,
        invalid_error=lambda: TextAnalysisError(
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
        raise TextAnalysisError(
            "audio_report_invalid", "Audio analysis report cannot be read."
        ) from error
    if not isinstance(decoded, Mapping) or decoded.get("report_id") != validated_id:
        raise TextAnalysisError("audio_report_invalid", "Audio analysis report is invalid.")
    if decoded.get("plan_id") != plan_id or decoded.get("subtitle_report_id") != subtitle_report_id:
        raise TextAnalysisError(
            "audio_report_mismatch",
            "Audio analysis report is not bound to this RunPlan and subtitle report.",
        )
    return _input_evidence(audio_path), AudioAnalysisBinding(
        "bound",
        report_id=validated_id,
        plan_id=plan_id,
        subtitle_report_id=subtitle_report_id,
    )


def _text_analysis_report_path(project_root: Path, report_id: str) -> Path:
    validated_id = validated_report_id(
        report_id,
        invalid_error=lambda: TextAnalysisError(
            "text_analysis_report_invalid", "Text analysis report ID must be a UUID."
        ),
    )
    return (
        project_root / "work" / "text-analysis-reports" / validated_id / "text-analysis-report.json"
    )


def _validated_report_id(value: str) -> str:
    return validated_report_id(
        value,
        invalid_error=lambda: TextAnalysisError(
            "subtitle_report_invalid", "Subtitle candidate report ID must be a UUID."
        ),
    )


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
        raise TextAnalysisError(
            "subtitle_report_invalid", "Subtitle candidate report cannot be read."
        ) from error
    return SubtitleCandidateReport.from_json(decoded, path)


def _input_evidence(path: Path) -> InputEvidence:
    digest, byte_count = sha256_file(path)
    return InputEvidence(path, digest, byte_count)


def _write_json_once(path: Path, payload: object) -> None:
    write_json_once(
        path,
        payload,
        conflict_error=lambda message: TextAnalysisError("text_analysis_report_conflict", message),
    )
