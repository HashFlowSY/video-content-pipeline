# Introduce a visual-text context with deterministic detection and OCR as the sole model capability

Phase 8 adds optional on-screen text evidence (page indices and OCR items) that
no existing Context owns. We introduce a separate `visual-text` Context that
directly depends on `source-planning` (transitively `media-foundation`), takes
`audio-analysis` as an optional informing context, and does not depend on
`subtitles`. Page-change detection, adaptive frame sampling, and OCR-item
classification are fully deterministic (pinned ffmpeg plus versioned rules);
OCR (`ocr_primary`) is the Context's only model capability, and general vision
models are entirely outside its contract — not even an optional capability
slot.

## Considered Options

- New `visual-text` Context with deterministic detection and OCR-only model
  capability: accepted because visual evidence is a new evidence kind, the
  Context stays single-modal (it produces evidence, never cross-modal facts),
  and determinism keeps the whole path replayable and offline-verifiable.
- Extend `text-analysis` with visual evidence production: rejected because it
  mixes an evidence producer into the Context that organizes and cites facts,
  and would force text-analysis to own frames, sampling, and OCR execution.
- Require `audio-analysis` as a hard dependency: rejected because only the
  low-confidence Suspected embedded-media interval uses audio evidence; page
  detection and OCR need none. The optional-dependency shape mirrors
  text-analysis's treatment of audio-analysis.
- Depend on `subtitles`: rejected because no visual-text contract consumes
  subtitle evidence; the one cross-modal rule (host-read comment upgrade) is
  deliberately placed in text-analysis (ADR 0049).
- Reserve an optional general-VLM capability slot: rejected as premature
  abstraction that blurs the phase exit gate "no general vision model as a
  required dependency"; segment-level visual semantics is a future, separately
  decided stage.
