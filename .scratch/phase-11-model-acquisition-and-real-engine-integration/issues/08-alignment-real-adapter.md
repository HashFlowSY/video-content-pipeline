# 08 — Real forced-alignment adapter (Qwen3-ForcedAligner via mlx-audio)

**What to build:** The forced_alignment engine:
Qwen3-ForcedAligner-0.6B-8bit executed through the Model runtime
subprocess (ticket 05) with mlx-audio, fed ≤5-minute chunks from ticket
06. Word/char-level `{text, start, end}` output projects into the
existing AlignmentCandidate / Model-output projection contracts; the
cue-level adoption gates, low-confidence non-override rule, and
Order-preserving alignment view are unchanged consumers. Calibration per
ADR 0027 (model-specific alignment calibration profile) — synthetic
calibration first, real-sample calibration lands in ticket 13.

**Blocked by:** 04, 05, 06
**Status:** open
**Labels:** ready-for-agent

- [ ] Subprocess request/response for an alignment chunk round-trips the
      projection contract (stub-executable unit tests; real-engine
      integration test behind the registry path, offline)
- [ ] Chunk-local timestamps map back to the authoritative timeline
      exactly (property test over the time mapping)
- [ ] Low-confidence alignment never overrides original cue times
      (existing gate proven against real-adapter output shape)
- [ ] Alignment calibration profile produced and gate-checked per ADR
      0027
- [ ] Peak-memory evidence recorded per run; typed failures on
      missing asset / child crash
- [ ] Full suite green within budget
