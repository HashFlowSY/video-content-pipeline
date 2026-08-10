# 02 -- Select and prepare auditable analysis audio

**What to build:** A user can complete Phase 5 input preparation with one
Analysis audio stream per Part. Ambiguous Parts pause for an explicit immutable
selection and then create a hash-recorded Analysis audio derivative with an
exact derivative-to-source time mapping.

**Blocked by:** 01 -- Add minimum audio-analysis CLI contract.

**Status:** resolved
**Labels:** ready-for-agent

- [x] A uniquely evidenced usable audio stream proceeds automatically; ambiguity returns `awaiting_audio_stream_selection` and resumes only from a retained explicit `part-id=stream-index` choice.
- [x] Selection evidence is bound to stream metadata and coverage hashes, and input drift invalidates it rather than silently retaining a bare stream index.
- [x] Revalidated pinned FFmpeg and a versioned preprocessing profile create retained deterministic Analysis audio derivatives; unmappable boundaries cannot become formal evidence.

## Comments

2026-08-11 (prior audit): The implementation selected a unique retained audio stream,
pauses for an explicit `PART_ID=STREAM_INDEX` choice when necessary, and binds
the selected stream to structural and coverage hashes. The reported pause reason
is `audio_stream_selection_required`, rather than the acceptance contract's
`awaiting_audio_stream_selection`. It also accepts a pre-existing hash-recorded
derivative as controlled evidence, but has no implementation that revalidates a
pinned FFmpeg binary, validates a preprocessing profile, or creates the
derivative. The first and third acceptance criteria are therefore open.

2026-08-11: Implemented the remaining Ticket 02 boundaries. Unique usable
audio streams now proceed automatically, ambiguous Parts pause as
`awaiting_audio_stream_selection`, and explicit resume remains bound to the
retained metadata and coverage hashes. Added immutable `AnalysisAudioDerivative`
records, strict versioned `PreprocessingProfile` validation, pinned FFmpeg
identity revalidation, deterministic argv-only WAV derivation, and exact sample
boundary to source-time mapping. Contiguous coverage and exactly representable
sample boundaries are required; gaps and unmappable boundaries fail closed.
Focused and full verification passed: 154 tests, Ruff, formatter, and strict
Mypy. No model, network, user media, or real FFmpeg execution occurred.
