# Require explicit analysis audio stream selection

Each Part supplies one Analysis audio stream to all Phase 5 models. When
retained planning evidence cannot establish a unique stream, the Part enters
`awaiting_audio_stream_selection` for a user choice; the pipeline never mixes
streams, merges tracks, or defaults by stream index.

The choice is an immutable `part-id=stream-index` record bound to codec,
language and disposition metadata and coverage-evidence hashes. Any bound
evidence drift invalidates the choice and requires reselection.

## Considered Options

- One explicit or uniquely evidenced stream: accepted because every Phase 5
  conclusion must name the same source audio evidence.
- Mix, merge, or default a multi-stream source: rejected because that changes
  speech, silence, alignment, and speaker structure without authorization.
- Preserve a bare stream index across drift: rejected because it can silently
  select a different audio evidence stream after a changed source or probe.
