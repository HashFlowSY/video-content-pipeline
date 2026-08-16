# Adopt stage outputs run-scoped with stage-versioned invalidation keys

On resume, a run re-uses a completed stage unit only if the unit is recorded
in the run's own Run state document and its Stage invalidation key still
matches: the unit's input hashes, the hash of the stage-scoped configuration
subset, and a manually incremented Stage version. Retained workspaces
produced by manual per-phase commands are never scavenged into a run. The
single sanctioned cross-run reuse is the Improvement run: `vcp improve`
carries forward artifacts from a named published RunBundle by recorded source
run id and hash (the ADR 0046 carry-forward pattern applied at run level).
Any behavior change in a stage must increment its Stage version; forgetting
to do so silently re-uses outputs produced by the old behavior, so the
increment rule is part of the development discipline recorded in the phase
specification.

## Considered Options

- Run-scoped adoption with stage-versioned keys: accepted because every
  adopted artifact has an unambiguous authorization chain (the run's own
  recorded decisions), satisfying "completed valid stages do not re-run on
  resume" without importing artifacts of unknown provenance.
- Cross-run adoption of any workspace whose key matches: rejected because
  pre-orchestration workspaces carry no Stage version, the audit narrative of
  who authorized the work becomes ambiguous, and a hash collision of
  configuration subsets across differently-intended runs is hard to reason
  about. It can be added later as an explicit opt-in flag without breaking
  this contract.
- No adoption (always re-run every stage): rejected because it violates the
  phase exit gate and makes crash recovery of a 4-hour run economically
  useless.
- Deriving the stage version automatically from source-code hashes: rejected
  because refactors would invalidate perfectly valid outputs, while
  behavior-relevant changes outside the hashed unit (helpers, configuration
  interpretation) would still slip through; a reviewed manual version states
  intent.
