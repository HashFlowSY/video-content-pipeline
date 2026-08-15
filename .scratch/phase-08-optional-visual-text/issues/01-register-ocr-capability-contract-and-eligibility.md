# 01 — Register the OCR capability contract and candidate eligibility

**What to build:** The provider-neutral Visual-text capability contract with
exactly one model capability, `ocr_primary`, evaluated offline from the model
registry — so that a user with no eligible OCR capability gets an immutable
`model_acquisition_required` visual-text result instead of an implicit
download, and a future real OCR engine is a configuration change, not a
semantics change.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] `ocr_primary` is evaluated with the shared eligibility fields (https
  official source, approved license, pinned revision, asset hash, offline
  runtime, no credentials, no telemetry, dependency plan, resource envelope).
- [ ] RapidOCR (or a compatible free local candidate) is registered as a
  research candidate — metadata only: no download, no execution, no network.
- [ ] No general vision model appears anywhere in the capability contract —
  no required dependency and no optional capability slot (ADR 0047).
- [ ] When no eligible `ocr_primary` capability exists, the outcome is an
  immutable Model-acquisition-required visual-text result carrying no OCR
  evidence.
