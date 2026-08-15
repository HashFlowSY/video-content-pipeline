# 02 -- Establish immutable transcription workspace and transcribe CLI

**What to build:** `vcp transcribe <plan-id> <subtitle-report-id>
<audio-report-id>` and `vcp resume-transcription <report-id> --decision`,
creating an immutable transcription workspace from exactly revalidated
retained inputs, with the Full-ASR resource confirmation pause.

**Blocked by:** 01

**Status:** done
**Labels:** ready-for-agent

- [x] Add typed domain records for transcription reports, workspace
  identities, input bindings, statuses, pauses, and diagnostics.
- [x] Revalidate confirmed RunPlan, SourceArtifact hashes, subtitle report,
  and the required Audio analysis report before any execution; any drift
  blocks the attempt.
- [x] Enforce the start preconditions: retained
  `subtitle_unavailable_requires_asr_plan` handoff or explicit user demand;
  never auto-trigger from a subtitle-priority run.
- [x] Pause at Full-ASR resource confirmation on subtitle-unavailable sources
  and record the explicit decision on resume; never auto-resume.
- [x] Record the Transcription resource-envelope pause when the conservative
  estimate exceeds 24 GiB, without silently changing model or quantization.

## Comments

Implemented 2026-08-15 by extending `src/video_content_pipeline/transcription.py`
(the ticket-01 module) with the immutable transcription workspace and the
`vcp transcribe` / `vcp resume-transcription` CLI, wired in `cli.py`. Reports are
written once to `work/transcription-reports/<uuid>/transcription-report.json`,
mirroring the Phase 5 audio / Phase 6 text workspace idiom; the entry functions
return `{"status", "report"}` and never raise (drift degrades to `failed`).

Domain records: `TranscriptionReportStatus`, `TranscriptionReport`,
`SourceArtifactBinding`, `AudioReportBinding`, `TranscriptionStartPrecondition`,
`TranscriptionRevalidation`; pauses are recorded as `required_decision` blocks;
diagnostics reuse `PlanningDiagnostic`. Revalidation reuses the shared
`load_run_plan` / `confirmed_plan_matches` / `revalidate_confirmed_inspection_evidence`
gate (so SourceArtifact hashes are revalidated against the confirmed PlanReport,
never by re-reading user media -- proven by `test_transcribe_reads_no_source_media`),
plus the subtitle report identity + rules fingerprint and the required Audio
analysis report identity binding. Capability evaluation reuses ticket 01's
`evaluate_asr_capabilities`.

**Design decisions.** (1) The pre-execution recorded states are surfaced as
top-level `status` values -- `awaiting_full_asr_resource_confirmation`,
`resource_envelope_exceeded`, `model_acquisition_required` -- following the
Phase 6 `TextAnalysisReportStatus` precedent; the formal `complete`/`partial`
statuses are defined but only reachable once ASR execution lands (tickets 03+).
(2) The Transcription resource-envelope pause is checked before the Full-ASR
resource confirmation pause: an over-24-GiB candidate cannot run, so the
operator must reconfigure (`resource_configuration_changed`) before confirming a
resource plan (`full_asr_resource_plan_confirmed`). (3) The explicit
whole-selection upgrade demand is expressed by a new `--upgrade-all` flag (the
public-contract line named only the positional arguments); a subtitle-priority
run with neither the handoff nor `--upgrade-all` is `transcription_precondition_unmet`.

Offline boundary held: no model download/execution, no `outputs/` write, no user
media read; every report carries the `not_attempted` guarantees block. Covered by
`tests/integration/test_phase_7_transcription_cli_contract.py` (18 CLI-contract
cases: both pauses and their resumes, no-auto-trigger, explicit upgrade,
revalidation drift, resume guards, immutability, no-media-read) and
`tests/unit/test_transcription_workspace.py` (the pure precondition and
resource-envelope detectors). Full suite green (325), ruff + mypy(`src`) clean.
Reviewed on both the Standards and Spec axes; the one concrete finding
(a dead `resumed_from_report_id` parameter) was fixed by recording it as resume
provenance, matching the sibling text-analysis report.
