# 05 -- Deliver adopted alignment timing views

**What to build:** A user can receive an immutable Adopted alignment timing view
for a Primary subtitle track when controlled alignment evidence passes all
calibration, time, and VAD gates. Original subtitle artifacts and original cue
times remain auditable throughout.

**Blocked by:** 04 -- Deliver VAD evidence and caption-gap risks.

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [x] Alignment can propose only times for existing cue identities; text or cue-cardinality changes return `alignment_text_contract_violation` and never become a transcript or candidate view.
- [x] Cue-level candidates retain their reasoned acceptance or rejection, preserve original time when rejected, preserve source order and legal overlaps, and use language-aware duration evidence.
- [x] The whole mixed view passes global validity and VAD-conflict gates before publication; failed views are `alignment_untrusted`, recurring equivalent failures require retained-evidence diagnosis, and no selective automatic rollback occurs.

## Comments

2026-08-10: Implemented controlled-adapter Adopted alignment timing views through
`vcp analyze-audio`. Views preserve hash-pinned Phase 4 source-candidate evidence,
original/proposed/adopted RawPts intervals, per-cue gate reasons, legal overlaps,
and language-specific calibrated duration rules. Alignment requires the complete
Primary-track Part projection and completed VAD evidence. Text/cardinality changes
are rejected, non-speech conflicts retain original timing, global invalidity rejects
the whole mixed view without erasing per-cue reasons, and a second retained matching
failure enters `alignment_diagnosis_required` with both failed timing views retained. No model,
media, network, or `outputs/` action occurred. Unit/CLI tests, Ruff, strict Mypy,
and the full 150-test suite passed.
