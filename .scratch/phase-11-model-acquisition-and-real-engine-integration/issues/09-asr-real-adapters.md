# 09 — Real ASR adapters: primary (mlx-audio) and review (mlx-whisper)

**What to build:** Both ASR engines through the Model runtime subprocess:
asr_primary = Qwen3-ASR-1.7B-8bit via mlx-audio over the ticket-06 chunk
stream (full transcripts assemble from per-chunk results on the
authoritative timeline); asr_review = whisper-large-v3-mlx fp16 via
mlx-whisper, invoked only on suspicious intervals with VAD-trimmed input
(the anti-hallucination measure the research recorded). The
Independent-model review requirement holds structurally (different model
families); review output remains independent evidence for deterministic
arbitration, never automatic truth. Existing suspicion detection,
arbitration, and gate-checked interval replacement are unchanged
consumers of the real output shapes.

**Blocked by:** 04, 05, 06
**Status:** open
**Labels:** ready-for-agent

- [ ] Primary subprocess adapter round-trips the transcription projection
      contract (stub unit tests + offline real-engine integration test)
- [ ] Chunked results assemble into a monotonic, coverage-consistent
      transcript on the authoritative timeline (property test)
- [ ] Review adapter runs only on given intervals, from VAD-trimmed
      audio, and its identity differs from primary (contract test)
- [ ] Same-model retry is still classified recovery, never independent
      review (existing rule re-proven with real identities)
- [ ] Peak-memory evidence per stage; typed failures on missing asset /
      child crash; hub-offline guards proven
- [ ] Full suite green within budget
