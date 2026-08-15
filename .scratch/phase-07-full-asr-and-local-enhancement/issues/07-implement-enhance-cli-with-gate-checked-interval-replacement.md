# 07 -- Implement enhance CLI with gate-checked interval replacement

**What to build:** `vcp enhance <plan-id> <subtitle-report-id>
[--audio-report] --part/--range/--cue` and `vcp resume-enhancement`, merging
ASR cues into user-specified intervals by Gate-checked interval replacement
(ADR 0045) and producing `enhanced` artifacts with Cue-level transcription
provenance.

**Blocked by:** 04 (gates), 06 (arbitration machinery, reused when review is
requested inside an enhancement interval)

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] Enhancement scope comes only from user-named Parts, ranges, or cues,
  each revalidated against retained cue identities and stream coverage.
- [ ] Inside an interval, ASR cues replace the display layer only after
  passing the adoption-style gates; on failure the original cues stay with a
  recorded reason. No cue-level interleaved mixing.
- [ ] Every cue in `subtitles.enhanced.*` / `transcript.enhanced.*` carries
  `subtitle_track` or `asr` provenance; original cues remain immutable
  evidence.
- [ ] Enhanced artifacts never claim full verbatim completeness and never
  change `audio_completeness=not_verified`.
- [ ] Write all replacements, rejections, and conflicts to the correction log
  and readable correction report.
