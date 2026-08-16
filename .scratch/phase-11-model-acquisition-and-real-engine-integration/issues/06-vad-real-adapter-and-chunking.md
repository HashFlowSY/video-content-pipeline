# 06 — Real VAD adapter and the shared ≤5-minute chunk derivation

**What to build:** The first real engine: silero-vad from the vendored,
hash-pinned ONNX via onnxruntime (in-process — ONNX-scale per the ADR 05
boundary), producing the existing Complete VAD partition contract from
real analysis-audio derivatives. On top of the partition, the shared
chunk derivation used by ASR and alignment: speech-anchored windows of at
most 5 minutes, cut at silence boundaries, each chunk carrying its
derivative-to-source time mapping so downstream evidence lands on the
authoritative timeline. Calibration follows ADR 0029 (model-specific VAD
calibration record).

**Blocked by:** 03, 04
**Status:** open
**Labels:** ready-for-agent

- [ ] Real silero inference over a fixture-derived wav produces a valid
      Complete VAD partition (integration test, offline, model from the
      registry path)
- [ ] Missing/hash-mismatched asset yields the typed acquisition failure,
      never a network attempt
- [ ] Chunk derivation is deterministic, ≤5 min per chunk, cuts only in
      silence, covers all speech, and round-trips time mappings exactly
      (unit + property tests)
- [ ] VAD calibration record produced and gate-checked per ADR 0029
- [ ] Full suite green within budget
