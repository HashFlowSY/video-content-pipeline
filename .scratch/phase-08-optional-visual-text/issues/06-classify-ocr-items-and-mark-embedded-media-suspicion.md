# 06 — Classify OCR items and mark embedded-media suspicion

**What to build:** Versioned OCR-item classification rules and the Suspected
embedded-media interval marker — so that page text is distinguished from
speaker supplements and background UI, platform noise is auditable but never
formal evidence, and a possible embedded video is flagged at low confidence
with its evidential basis recorded rather than asserted as fact.

**Blocked by:** 05 — classification and suspicion operate on gated OCR
evidence items.

**Status:** done
**Labels:** ready-for-agent

- [x] Versioned deterministic rules classify each OCR evidence item as page
  text, speaker supplement, or background UI; rule versions are recorded in
  provenance and the same input and versions always classify identically.
- [x] Excluded visual items (danmaku, high-speed chat, unrelated watermarks,
  logos, follow/gift prompts, repeated platform shell) are retained in the
  workspace and marked non-evidence; they never appear as formal content.
- [x] Low-confidence classifications are marked `classification_uncertain`
  and never forced into a category.
- [x] Suspected embedded-media intervals are low-confidence markers only;
  provenance states whether the basis is picture-plus-audio or picture-only,
  and a supplied Audio analysis report is revalidated (hash and bound input
  identities) before its evidence is used.
- [x] No clothing, environment, object, or action description and no visual
  summaries are produced anywhere.

## Comments

Implemented in commit 69c6a40 feat: classify OCR items and mark embedded-media suspicion. Acceptance criteria were checked at phase
closure on the maintainer's instruction, anchored to the current-head
verification (pytest 700 passed; ruff and mypy clean; 30 confirmed exit-gate
booleans in docs/PHASE_08_INVENTORY.json).
