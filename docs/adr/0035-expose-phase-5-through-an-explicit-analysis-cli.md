# Expose Phase 5 through an explicit analysis CLI

`vcp analyze-audio` is the only public Phase 5 entry point. It takes a
confirmed RunPlan and retained subtitle candidate report and emits an immutable,
machine-readable Audio analysis report; any paused state resumes only through a
separate command that explicitly names the report and user decision.

## Considered Options

- Explicit analysis and resume commands: accepted because normal execution,
  user-decision pauses, and recovery have distinct authority and evidence.
- One interactive or auto-resuming command: rejected because it could hide a
  model change, download, or recovery choice inside an otherwise ordinary call.
