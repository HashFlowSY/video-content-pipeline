# 09 — Run #1 Real-run confirmation and ledger entry

**What to build:** Run #1 becomes the Coverage ledger's first entry, backed
by a recorded Real-run confirmation. Prepare the confirmation record under
`docs/phase-12-runs/` in the Phase 11 maintainer-review shape (source +
hash, dated decision line, per-item confirmation table, notes, provenance
naming the model-stack snapshot from the processing report). Present the
published outputs to the maintainer for inspection — subtitles, speakers,
detailed content, summary — and **stop for their verdict**: per-capability
verbal ratings (acceptable / marginal / unacceptable) for subtitle
readability, speaker separation, and summary faithfulness. Record the
observed real-world severity of the two Phase 11 leftovers (diarization
over-clustering, text_semantics single-segment collapse) in the notes.
Then register the run in the Coverage ledger: which Formal branch(es) it
actually covered (expected: full ASR), confirmation file, date. No CER/WER
is computed (no human reference text exists).

**Blocked by:** 01 (Coverage ledger), 08 (run #1 execution and
publication).

**Status:** awaiting-maintainer (agent-completable parts done; three boxes are
maintainer-gated and an agent never fills them — see notes)

- [x] Confirmation record exists in the maintainer-review shape, bound to the run identity and model-stack snapshot — `docs/phase-12-runs/20260818T114653Z-19562f62a649ee1b.md` (`37b93aa`)
- [x] Maintainer inspected subtitles, speakers, detailed content, and summary and their verdict is recorded verbatim — inspected 2026-08-18; interim verdict 「内容我都看过了，目前看可以用」recorded verbatim in the run record
- [ ] Per-capability verbal ratings recorded (acceptable/marginal/unacceptable × three dimensions) — **DEFERRED BY MAINTAINER** (gave a general "usable for now"; declined to lock per-capability D10 yet, so rows stay `_pending_`)
- [x] Observed severity of the diarization and text_semantics leftovers noted with evidence pointers — diarization = none-observable (de-promoted, did not run); text_semantics single-segment collapse noted with segments.json evidence (`37b93aa`)
- [ ] Ledger shows the branch(es) this run covered, honestly stated; branch count updated (expected 1/5) — **DEFERRED BY MAINTAINER** (chose "暂不翻分支"; branch 2 not flipped, count honestly stays 0/5 until a clearer confirmation)
- [x] No CER/WER appears anywhere in the record — only "no proofed reference exists" statements

**Handoff to maintainer.** Run identity `20260818T114653Z-19562f62a649ee1b`
(source `f10e8895…a48889`), `vcp verify`/`inventory` pass. Inspect: detailed
content + summary in the published bundle
(`outputs/f10e8895…/20260818T114653Z-19562f62a649ee1b/content-report.md`,
`segments.json`); subtitle readability from the 74 verbatim ASR cues retained
as audit evidence (`work/transcription-reports/02ae872f…/transcript/…/source-candidate.json`
— readable subtitle files are a documented deferred publication follow-up).
Then record, in the run record: verbatim verdict, D10 ratings (subtitle
readability / speaker separation[n/a this run] / summary faithfulness), and —
if confirmed — flip Formal branch 2 in the ledger to `confirmed` and bump the
count to 1/5.
