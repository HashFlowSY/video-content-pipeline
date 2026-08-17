# Phase 12 run &lt;run-id&gt; — maintainer confirmation

> Copy this file to `docs/phase-12-runs/<run-id>.md` for each real run, fill
> every section, and add the matching Run-log row + branch flip in
> [PHASE_12_COVERAGE_LEDGER.md](../PHASE_12_COVERAGE_LEDGER.md). Delete these
> quote lines when filling it in. Shape mirrors the Phase 11 maintainer review
> ([`docs/phase-11-prototypes/maintainer-review.md`](../phase-11-prototypes/maintainer-review.md)).

**Source:** `<title / platform id>` — `<url or local origin>`
(`<license / rights basis>`), `<duration>`.
**Content hash:** `sha256:<…>` (intake bytes).
**Run identity:** `<run-id>`; RunBundle `<path>`; `vcp verify` / `vcp inventory`
pass.
**Download plan:** `<link to the confirmed plan, if URL material>` —
maintainer-confirmed `<date>` (media-download authorization only).
**Formal branch(es) claimed:** `<expected vs. actually covered, recorded after probing>`.

Reviewed and decided `<YYYY-MM-DD>`.

## Per-capability confirmation (D10)

Verbal rating per capability: **acceptable** / **marginal** / **unacceptable**.

| Capability | Rating | Notes |
|---|---|---|
| subtitle readability | `<acceptable\|marginal\|unacceptable>` | `<…>` |
| speaker separation | `<acceptable\|marginal\|unacceptable>` | `<…>` |
| summary faithfulness | `<acceptable\|marginal\|unacceptable>` | `<…>` |

Overall decision: **`<Confirmed / Confirmed-with-note / Not confirmed>`**.

## Notes and follow-ups

- Observed real-world severity of the D7 leftovers on this material
  (diarization over-clustering; text_semantics single-segment collapse) —
  measured, not assumed.
- pause/resume drill: `<exercised at which stage boundary, or n/a>`.
- Any failure publication (Minimal RunBundle floor) if a real failure
  occurred.
- CER/WER: only if a proofed human reference exists; record its source and
  proofing scope, else state "no reference text".

## Provenance (model stack that produced these outputs)

From the RunBundle processing report — this binds the confirmation to the
exact stack (D9/D10):

| Model | Revision | sha256 | Purpose |
|---|---|---|---|
| `<name>` | `<rev>` | `<…>` | `<capability>` |

- **Tools:** `<yt-dlp 2026.07.04, ffmpeg …>`.
- **Environment:** `<os / runtime>`.
- **Resources:** peak memory `<GiB>` (from subprocess evidence), stage
  durations, disk delta.
