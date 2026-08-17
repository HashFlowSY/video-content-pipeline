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

**Status:** ready-for-agent

- [ ] Confirmation record exists in the maintainer-review shape, bound to the run identity and model-stack snapshot
- [ ] Maintainer inspected subtitles, speakers, detailed content, and summary and their verdict is recorded verbatim
- [ ] Per-capability verbal ratings recorded (acceptable/marginal/unacceptable × three dimensions)
- [ ] Observed severity of the diarization and text_semantics leftovers noted with evidence pointers
- [ ] Ledger shows the branch(es) this run covered, honestly stated; branch count updated (expected 1/5)
- [ ] No CER/WER appears anywhere in the record
