# 02 -- Select and prepare auditable analysis audio

**What to build:** A user can complete Phase 5 input preparation with one
Analysis audio stream per Part. Ambiguous Parts pause for an explicit immutable
selection and then create a hash-recorded Analysis audio derivative with an
exact derivative-to-source time mapping.

**Blocked by:** 01 -- Add minimum audio-analysis CLI contract.

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] A uniquely evidenced usable audio stream proceeds automatically; ambiguity returns `awaiting_audio_stream_selection` and resumes only from a retained explicit `part-id=stream-index` choice.
- [ ] Selection evidence is bound to stream metadata and coverage hashes, and input drift invalidates it rather than silently retaining a bare stream index.
- [ ] Revalidated pinned FFmpeg and a versioned preprocessing profile create retained deterministic Analysis audio derivatives; unmappable boundaries cannot become formal evidence.
