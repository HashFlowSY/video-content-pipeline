# Use gate-checked interval replacement for enhanced subtitles

Local ASR enhancement merges at interval granularity, never at interleaved cue
granularity. Inside a user-specified enhancement interval, ASR cues replace the
display layer only after passing gates structurally equal to alignment
adoption (times inside actual stream coverage, monotonic order, no processing
duplication, plausible duration-to-text relation); on gate failure the original
subtitle cues stay with a recorded reason. Original cues always remain
immutable evidence, and every cue carries `subtitle_track` or `asr`
provenance. Enhancement never changes `audio_completeness=not_verified`.

## Considered Options

- Gate-checked interval replacement: accepted because interval-grained,
  gate-guarded, fallback-preserving replacement keeps one auditable story,
  consistent with the alignment adoption rules.
- Cue-level interleaved arbitration merge: rejected because a mixed
  subtitle/ASR cue sequence is exactly the silent mixing the track-selection
  rules forbid and is the hardest form to audit.
