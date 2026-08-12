# Keep Phase 6 text analysis in immutable workspaces

Each Phase 6 attempt writes an immutable text-analysis workspace containing its
input bindings, controlled-adapter or future model identity, raw output,
versioned projection, validation results, and diagnostics. It does not publish
to `outputs/`; Phase 9 may promote verified artifacts without regenerating or
rewriting the evidence.

## Considered Options

- Immutable text-analysis workspaces with deferred publication: accepted because
  model attempts, rejected content, and partial results remain auditable while
  publication has one later, explicit responsibility.
- Direct content-report publication: rejected because it would mix candidate
  generation, validation, and RunBundle publication in one mutable boundary.
- Recreate reports at publication time: rejected because regeneration could
  change non-deterministic model output or hide rejected candidate content.
