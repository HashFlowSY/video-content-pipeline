# 01 — Coverage ledger and standing per-run procedure

**What to build:** The maintainer can open one document and know exactly
which of the five Formal branches (subtitle-priority, full ASR, anomalous
subtitles, multi-P, visual-text OCR) have been confirmed by real runs, by
which runs, and what remains before `production_validated`. Create the
Coverage ledger at `docs/PHASE_12_COVERAGE_LEDGER.md` (five-row branch
table starting 0/5 + a run log: run id, source, date, branches claimed,
confirmation record) and write down the standing per-run procedure —
download plan if URL → plan/confirm → run → publish → inspect + rate →
ledger entry — plus the closure protocol (at 5/5: completion report,
machine-checkable phase inventory + acceptance test, the user's explicit
overall confirmation, then the `production_validated` flip). Per-run
confirmation records follow the Phase 11 maintainer-review shape, one file
per run under `docs/phase-12-runs/`.

Documentation only; no code changes.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] Coverage ledger exists with all five Formal branches listed as unconfirmed (0/5) and an empty run log
- [ ] The standing per-run procedure is written, including the D10 verbal-rating requirement (acceptable/marginal/unacceptable per capability)
- [ ] The closure protocol is written, including the explicit-user-confirmation gate before any `production_validated` flip
- [ ] The record format references the Phase 11 maintainer-review shape (source + hash, dated decision line, confirmation table, notes, provenance)
