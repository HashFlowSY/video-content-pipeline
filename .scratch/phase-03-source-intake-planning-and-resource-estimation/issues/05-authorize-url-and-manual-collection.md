# 05 -- Authorize URL and manual collection

**What to build:** A user can validate one public URL or build an ordered
Manual collection session without network activity, receiving explicit
authorization diagnostics instead of silent fallback or stored secrets.

**Blocked by:** 01 -- Local source preflight report.

**Status:** resolved

- [x] `filtered` or `direct` URL access mode is mandatory; omitted, failed, or
  incompatible modes do not fall back.
- [x] HTTP needs separate authorization, raw URLs never persist, and a Host
  escalation is surfaced for user confirmation.
- [x] A Manual collection validates each submitted URL locally, preserves input
  order, requires `结束`, and rejects duplicate entries.
- [x] The resulting persistent evidence contains only Redacted source
  provenance and no network action occurs in this slice.

## Comments

2026-08-09: Implemented the offline URL authorization and Manual collection
boundary. URL reports now retain access mode plus Redacted source provenance
only; HTTP requires explicit opt-in, and Host escalation remains an explicit
policy failure. `vcp plan --collect` records user-entered URLs in presentation
order, closes only on `结束`, and produces no network activity. Offline unit,
full-regression, lint, format, and strict type checks pass.
