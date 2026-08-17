# 03 — Per-run media host authorization (ADR 0057)

**What to build:** Downloading from a real video platform whose media lives
on CDN hosts distinct from the page host becomes possible without creating
any standing network authority. The metadata pass resolves the actual media
hosts; the download plan discloses that host set alongside size and disk
headroom; the maintainer's confirmation of the plan authorizes exactly
those hosts for that download only. The acquisition proxy admits
connections to the confirmed set and nothing else — a mid-download redirect
to an undisclosed host is still rejected as host escalation, fail-closed.
Nothing persists between downloads.

Governed by ADR 0057 (per-run disclosed host set; standing suffix
allowlists and strict page-host equality were both rejected).

**Blocked by:** None — can start immediately.

**Status:** done

- [x] Media hosts resolved from canned metadata appear in the download plan artifact
- [x] The proxy admits exactly the confirmed host set for the run
- [x] An undisclosed destination host (including a redirect target) is rejected as host escalation
- [x] No configuration or state carries host authority beyond the single confirmed download
- [x] Tests live at the URL-policy / proxy admission seam with fake hosts
