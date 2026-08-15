# 06 -- Review suspicious intervals and arbitrate deterministically

**What to build:** Interval-scoped second-ASR review through the `asr_review`
capability, followed by Deterministic transcription arbitration (ADR 0044)
with retained Unresolved transcription conflicts, producing the `verbatim`
artifact semantics.

**Blocked by:** 05

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] Run review only on suspicious intervals by default; a full-length review
  requires an explicit recorded user decision.
- [ ] Enforce the Independent-model review requirement: a same-model retry is
  recorded as recovery, never as independent review.
- [ ] Version arbitration preference rules; when no rule decides, keep the
  primary text, retain both candidates as evidence, mark `review-needed`.
- [ ] Only a complete full-ASR run that passed coverage checks may emit
  `subtitles.verbatim.*` / `transcript.verbatim.*` and perform the
  Audio-completeness upgrade.
