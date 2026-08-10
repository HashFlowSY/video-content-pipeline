# Keep Phase 5 analysis in immutable workspaces

Each Phase 5 attempt writes an immutable Audio analysis workspace containing
raw model outputs, calibration and gate results, VAD, alignment and diarization
candidates, and diagnostics. It does not write `outputs/`; Phase 9 may promote
only verified artifacts without regenerating or rewriting them.

If a later Phase 5 stage pauses or blocks after an earlier stage produced
independently valid evidence, the workspace report is `partial`. It retains the
valid artifacts and identifies missing stages and the user decision required to
continue, rather than treating the complete attempt as failed.

## Considered Options

- Immutable analysis workspaces: accepted because they retain evidence for
  model attempts, pauses, and failures before formal publication.
- Publish directly or regenerate later: rejected because either obscures
  provenance or lets publication change the result it claims to promote.
- All-or-nothing phase reports: rejected because a later resource or user
  decision boundary must not discard valid earlier-stage evidence.
