# 11 — Real OCR adapter (RapidOCR)

**What to build:** The ocr_primary engine: rapidocr 3.9.2 in-process
(ONNX-scale) over the existing deterministic frame-sampling pipeline.
Configuration per the research: `limit_side_len` raised for ≥1080p frames
(small-text protection), `use_cls` disabled for screen content; both
recorded as versioned configuration. Output implements the existing OCR
evidence item contract; classification rules and the
classification-vs-fact-upgrade separation (ADR 0049) are unchanged
consumers. The registry entry's model manifest (bundled det/rec/cls
files + hashes from the wheel) is asserted against the installed reality.

**Blocked by:** 03, 04
**Status:** done
**Labels:** ready-for-agent

- [ ] Real OCR over the Phase 10 text-bearing fixture frames yields
      contract-valid OCR evidence items (integration test, offline)
- [ ] Installed bundled models match the registry manifest hashes
      (integration test)
- [ ] Config surface is versioned; changing it invalidates dependent
      stage keys like other versioned rules
- [ ] Typed failure when rapidocr/onnxruntime are missing or the manifest
      mismatches — never a download
- [ ] Full suite green within budget
