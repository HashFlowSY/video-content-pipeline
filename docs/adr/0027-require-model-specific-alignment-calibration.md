# Require model-specific alignment calibration

Phase 5 treats alignment confidence as model-specific evidence. Until an
offline calibration profile validates a model's adoption threshold, the model
may create retained candidates but must report `alignment_calibration_required`
and cannot create an Adopted alignment timing view.

Each profile binds the exact model asset hash, inference backend and version,
quantization or precision, device class, and alignment rules fingerprint. A
change to any bound identity invalidates the profile for adoption.

A profile built only with project-owned synthetic media qualifies adopted timing
views only for synthetic verification. Real-source adoption requires a later,
separately authorized real-media calibration.

Duration plausibility belongs to a versioned, language-aware rule in the
profile. A global character-count or word-count threshold cannot substitute for
a missing rule, including for mixed-language cues.

## Considered Options

- Model-specific offline calibration: accepted because confidence scales are not
  interchangeable and time adoption needs evidence beyond a raw score.
- A universal threshold or immediate adoption: rejected because either would
  make unproven model output appear authoritative.
- Calibration by model name alone: rejected because backend, precision, device,
  and rules changes can alter the meaning of a confidence score.
- Treating synthetic calibration as field validation: rejected because
  engineering fixtures cannot establish real subtitle-alignment quality.
- Universal text-duration thresholds: rejected because languages and mixed
  language cues have different text-length and timing behavior.
