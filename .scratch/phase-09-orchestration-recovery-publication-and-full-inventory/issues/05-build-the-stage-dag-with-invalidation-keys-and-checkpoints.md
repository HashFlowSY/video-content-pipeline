# 05 — Build the stage DAG with invalidation keys and checkpoints

**What to build:** The in-process stage DAG over `(stage, Part)` Stage units:
composition of the existing per-phase functions (source/plan revalidation →
subtitles → audio-analysis → {transcription | enhancement} by mode →
text-analysis / affected-Part re-analysis → visual-text when enabled), each
unit guarded by a Stage invalidation key (input hashes + stage-scoped config
subset hash + manual Stage version, ADR 0052), checkpointed only at unit
boundaries, with Run-scoped adoption and Run decision pauses.

**Blocked by:** 01, 02, 03

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] Stages are invoked in-process through the existing functions; no
  subprocess spawning; the sixteen expert commands remain unchanged.
- [ ] Every stage declares a Stage version constant and a config-subset
  extractor; the invalidation key is recorded per completed unit.
- [ ] A configuration change invalidates only affected stages and their
  downstream units; upstream completed units remain adoptable.
- [ ] Adoption reads only this run's recorded units and revalidates hashes
  before use; retained manual workspaces are never scavenged.
- [ ] A stage's required user decision surfaces as run `incomplete` with a
  machine-readable `required_decision` carrying the stage's pause reason
  vocabulary; per-Part failures do not block other Parts' units.
- [ ] Checkpoints exist only at completed unit boundaries; a mid-unit
  interruption leaves no adoptable record.
