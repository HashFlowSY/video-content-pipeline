# 01 — Register the OCR capability contract and candidate eligibility

**What to build:** The provider-neutral Visual-text capability contract with
exactly one model capability, `ocr_primary`, evaluated offline from the model
registry — so that a user with no eligible OCR capability gets an immutable
`model_acquisition_required` visual-text result instead of an implicit
download, and a future real OCR engine is a configuration change, not a
semantics change.

**Blocked by:** None — can start immediately.

**Status:** done
**Labels:** ready-for-agent

- [x] `ocr_primary` is evaluated with the shared eligibility fields (https
  official source, approved license, pinned revision, asset hash, offline
  runtime, no credentials, no telemetry, dependency plan, resource envelope).
- [x] RapidOCR (or a compatible free local candidate) is registered as a
  research candidate — metadata only: no download, no execution, no network.
- [x] No general vision model appears anywhere in the capability contract —
  no required dependency and no optional capability slot (ADR 0047).
- [x] When no eligible `ocr_primary` capability exists, the outcome is an
  immutable Model-acquisition-required visual-text result carrying no OCR
  evidence.

## Comments

Implemented in commit d84d196 feat: register the OCR capability contract and eligibility. Acceptance criteria were checked at phase
closure on the maintainer's instruction, anchored to the current-head
verification (pytest 700 passed; ruff and mypy clean; 30 confirmed exit-gate
booleans in docs/PHASE_08_INVENTORY.json).
