# 13 — Run the seven capability prototypes and the maintainer sample review

**What to build:** For each capability (vad, diarization,
forced_alignment, asr_primary, asr_review, ocr_primary, text_semantics):
a maintainer-invoked prototype command over the ticket-12 material that
(a) runs fully offline from registry assets, (b) passes its engineering
checks — structurally valid contract output, gates hold, measured peak
memory ≤ 12 GiB, (c) records device baselines (real-time factor, peak
memory) to seed plan estimation, and (d) emits a short zh+en sample
output for maintainer eyeball (transcript excerpt, aligned cues, speaker
turns, OCR items, segment summary). Maintainer review is the quality
gate: a sample rejection bounces to the recorded fallback (other quant
tier / other candidate) as a new confirmation, not an argument. These
prototype runs are retained evidence, not pytest; the pytest gate stays
within budget.

**Blocked by:** 06, 07, 08, 09, 10, 11, 12
**Status:** open
**Labels:** ready-for-agent

- [ ] Seven prototype records retained (command, asset identities,
      timings, peak memory, sample paths)
- [ ] Every measured peak ≤ 12 GiB; every run offline (no hub access —
      guards proven in the records)
- [ ] Device baselines recorded where plan estimation reads them;
      `estimate_confidence` upgraded from `low` where measured
- [ ] Maintainer confirmation recorded per capability sample (zh and en
      both represented); rejections and fallback decisions recorded
- [ ] Real-sample calibration records landed for alignment/VAD/
      diarization per ADRs 0027/0029/0031
