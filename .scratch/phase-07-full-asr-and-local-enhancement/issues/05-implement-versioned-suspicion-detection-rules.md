# 05 -- Implement versioned suspicion detection rules

**What to build:** The six deterministic suspicious-interval detectors — VAD
coverage, confidence, repetition, language switching, numbers/entities, and
coverage checks — as Versioned suspicion detection rules with conservative
defaults and `calibration_required` marks.

**Blocked by:** 02, 04

**Status:** done
**Labels:** ready-for-agent

- [ ] Version the detector set and thresholds in
  `config/transcription/suspicion-rules.json`; record rule version in every
  report.
- [ ] VAD coverage and non-silent-but-textless checks consume the required
  retained Audio analysis report (ADR 0043).
- [ ] Each flagged interval records detector identity, evidence, and exact
  time range; detectors are pure functions over projections and retained
  evidence.
- [ ] Mark every threshold `calibration_required`; real calibration happens
  only in real-world testing.
