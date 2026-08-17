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
**Status:** done
**Labels:** ready-for-agent

- [x] Seven prototype records retained (command, asset identities,
      timings, peak memory, sample paths) — `docs/phase-11-prototypes/<cap>/<src>-<lang>.record.json`
      + `.md`, zh + en, 14 runs
- [x] Every measured peak ≤ 12 GiB (max 5.09 GiB); every run offline
      (HUB_OFFLINE guards proven in each record)
- [x] Device baselines recorded where plan estimation reads them
      (`docs/phase-11-prototypes/device-baselines.json`, `confidence: measured`);
      `estimate_confidence` upgraded from the profile's `low`. Measured peaks are
      NOT written to the registry `resource_estimate` (that would flip every
      candidate `unsupported→eligible`, ADR 0037) — see `prototype.py` docstring.
- [x] Maintainer confirmation recorded per capability sample (zh + en),
      `docs/phase-11-prototypes/maintainer-review.md`: 6 confirmed; text_semantics
      recorded as a diagnosed adapter gap → follow-up ticket 15; diarization
      over-clustering accepted-as-is with a recorded note.
- [x] Real-sample calibration landed for alignment/VAD/diarization
      (`qualification_scope` off `first_device_baseline`; `real_sample_qualification`
      provenance) per ADRs 0027/0029/0031.

**Outcome (2026-08-17):** All seven capabilities prototyped on real zh+en VOA
material. VAD (after a silero 64-sample-context bug fix), asr_primary, asr_review,
ocr_primary, forced_alignment, and diarization confirmed; text_semantics produced
`model_output_invalid` due to a diagnosed prompt-content gap (not model quality) →
follow-up ticket 15. `media_processed` flipped true (first real-media processing).
