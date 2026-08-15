# Recompute affected Parts with carried-forward analysis

After transcription or enhancement changes the cue evidence basis, semantic
re-analysis happens at Part granularity in a new immutable text-analysis
attempt: affected Parts are regenerated against the new basis, unaffected
Parts are carried forward from the retained prior report with an explicit
provenance link, and chapters plus collection summaries are recomputed from
the combined set. Parts are the natural grain because chapters are Part-local
and evidence ownership is Part-bounded.

## Considered Options

- Part-grain carry-forward re-analysis: accepted because it regenerates only
  what changed, avoids re-running the text model on untouched Parts of long
  collections, and preserves prior provenance.
- Whole-collection re-run: rejected because it recomputes unaffected Parts on
  an up-to-8B model against the project's serial, memory-bounded resource
  philosophy, and real-model regeneration of untouched Parts is not
  reproducible.
- Defer recomputation to Phase 9 orchestration: rejected because the phase
  plan places affected-segment recomputation in Phase 7, and markers alone
  would leave enhanced runs without consistent chapter and collection
  summaries.
