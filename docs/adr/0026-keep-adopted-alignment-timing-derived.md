# Keep adopted alignment timing derived

Phase 5 publishes accepted forced-alignment times only through an immutable
Adopted alignment timing view. The Phase 4 source and readable subtitle
candidate artifacts remain unchanged, so auditors can distinguish original
subtitle evidence from later timing adoption and Phase 9 can select a view
explicitly during publication. Acceptance is recorded per cue, but a timing
view publishes only after the complete mixed sequence passes global validity
gates. If a Part fails one of those gates, it retains the original times and an
`alignment_untrusted` diagnostic; the pipeline does not guess a selectively
valid candidate subset.
An aligner may propose times only for existing Primary subtitle cue identities.
Adding, removing, merging, splitting, or changing text is an
`alignment_text_contract_violation`, not an alignment candidate or transcript.
The resulting view preserves original cue `source_ordinal` order and permits
valid overlaps; it does not clip, force non-overlap, or reorder cues to create
a serialized timeline.
An otherwise eligible candidate that overlaps calibrated `non_speech` audio is
rejected as `alignment_vad_conflict` and leaves original cue time in place.
Overlap with `indeterminate` audio is reported as risk but does not alone
reject the candidate.

When the same SourceArtifact, Primary subtitle track, alignment model and
rules identity, and failed gate recur in a second independent attempt, the
pipeline enters `alignment_diagnosis_required` before another equivalent
attempt. This prevents repeated failures from being obscured as routine retry.
The diagnosis reads only retained subtitle, candidate-time, model-output, VAD,
and gate evidence. It can return `root_cause_inconclusive`; it cannot rerun an
aligner, download an asset, or access new media evidence.

## Considered Options

- A separate immutable timing view: accepted because it preserves Phase 4
  evidence, makes timing adoption reviewable, and permits explicit downstream
  selection.
- Rewrite existing source or readable artifacts: rejected because it hides the
  original timing and retroactively changes already retained evidence.
- Track-only acceptance: rejected because it would discard trustworthy cue
  candidates together with a local failure; independently publishing cue times
  is also rejected because it can violate global timeline constraints.
- Automatic selective rollback: rejected because an unrecorded heuristic would
  decide which timing evidence to discard after a failed global gate.
- Alignment-generated text: rejected because Phase 5 corrects timing evidence,
  not the Phase 4 subtitle source or the separately authorized ASR path.
- De-overlap or cue reordering: rejected because overlapping subtitle evidence
  is valid and cannot be rewritten merely to make timing look serialized.
- Treat VAD disagreement uniformly: rejected because confirmed non-speech is a
  direct timing conflict, while indeterminate VAD evidence cannot alone negate
  an otherwise valid candidate.
- Unlimited equivalent retries: rejected because repeated deterministic failure
  must be diagnosed rather than retried without new evidence.
- Active diagnostic reprobe: rejected because it would turn a diagnostic state
  into an unapproved new alignment or media-processing attempt.
