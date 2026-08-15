# Introduce a transcription context with a required audio-analysis dependency

Phase 7 produces new citable text evidence (verbatim transcripts and enhanced
subtitles), while the audio-analysis Context deliberately excludes independent
transcription. We introduce a separate `transcription` Context that depends on
`subtitles` and — as a required, not optional, dependency — `audio-analysis`,
because the ASR quality gates (non-silent-but-textless coverage checks and
VAD-based suspicious-interval detection) are meaningless without voice-activity
evidence.

## Considered Options

- New `transcription` Context with required audio-analysis: accepted because
  ASR produces evidence of a new kind, text-analysis gains a new upstream, and
  audio-analysis keeps its "no independent transcript" boundary intact.
- Extend `audio-analysis` with ASR: rejected because it revokes a deliberate
  boundary declaration and mixes evidence producers with evidence evaluators.
- Optional VAD dependency: rejected because a `verbatim` artifact could then
  claim completeness while the coverage checks that justify the claim never
  ran.
