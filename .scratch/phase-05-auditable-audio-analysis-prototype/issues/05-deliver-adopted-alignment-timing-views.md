# 05 -- Deliver adopted alignment timing views

**What to build:** A user can receive an immutable Adopted alignment timing view
for a Primary subtitle track when controlled alignment evidence passes all
calibration, time, and VAD gates. Original subtitle artifacts and original cue
times remain auditable throughout.

**Blocked by:** 04 -- Deliver VAD evidence and caption-gap risks.

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] Alignment can propose only times for existing cue identities; text or cue-cardinality changes return `alignment_text_contract_violation` and never become a transcript or candidate view.
- [ ] Cue-level candidates retain their reasoned acceptance or rejection, preserve original time when rejected, preserve source order and legal overlaps, and use language-aware duration evidence.
- [ ] The whole mixed view passes global validity and VAD-conflict gates before publication; failed views are `alignment_untrusted`, recurring equivalent failures require retained-evidence diagnosis, and no selective automatic rollback occurs.
