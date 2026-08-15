# Keep visual page identity Part-local

A `visual_page_id` identifies a stable on-screen text state within exactly one
Part; first-appearance and reappearance records are Part-local. When the same
slide is shown in two Parts, each Part records its own page identity. This
mirrors ADR 0030 (Part-local anonymous speaker labels): cross-Part correlation
is a consumer-side judgment, not a visual-text evidence claim.

## Considered Options

- Part-local page identity: accepted because it keeps affected-Part
  re-analysis boundaries clean — one Part's visual evidence can never be
  invalidated by another Part's rerun — and requires no cross-Part fingerprint
  matching in the evidence layer.
- Collection-global page identity: rejected because global fingerprint
  matching entangles Parts (a rerun or a new Part could re-key pages
  elsewhere), inflates the deterministic-rule surface, and asserts an
  "is the same page" fact that consumers (text-analysis or a future stage)
  can derive later from retained fingerprints without loss.
