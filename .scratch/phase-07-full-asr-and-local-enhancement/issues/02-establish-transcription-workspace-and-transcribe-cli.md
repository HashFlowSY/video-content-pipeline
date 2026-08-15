# 02 -- Establish immutable transcription workspace and transcribe CLI

**What to build:** `vcp transcribe <plan-id> <subtitle-report-id>
<audio-report-id>` and `vcp resume-transcription <report-id> --decision`,
creating an immutable transcription workspace from exactly revalidated
retained inputs, with the Full-ASR resource confirmation pause.

**Blocked by:** 01

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] Add typed domain records for transcription reports, workspace
  identities, input bindings, statuses, pauses, and diagnostics.
- [ ] Revalidate confirmed RunPlan, SourceArtifact hashes, subtitle report,
  and the required Audio analysis report before any execution; any drift
  blocks the attempt.
- [ ] Enforce the start preconditions: retained
  `subtitle_unavailable_requires_asr_plan` handoff or explicit user demand;
  never auto-trigger from a subtitle-priority run.
- [ ] Pause at Full-ASR resource confirmation on subtitle-unavailable sources
  and record the explicit decision on resume; never auto-resume.
- [ ] Record the Transcription resource-envelope pause when the conservative
  estimate exceeds 24 GiB, without silently changing model or quantization.
