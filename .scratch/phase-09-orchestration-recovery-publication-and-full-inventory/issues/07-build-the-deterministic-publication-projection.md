# 07 — Build the deterministic publication projection

**What to build:** The Publication projection: a versioned, deterministic
layer that projects verified workspace artifacts into the plan §4 publication
file names and formats (`subtitles.*` by mode, `transcript.<basis>.*`,
`content-report.md`, `segments.json`, `correction-log.json`), selecting and
recording a timing view per export (ADR 0026: PartRelativeTime for per-Part,
CollectionVirtualTime for collection-level) — no new analysis, no content
change, `unavailable` recorded instead of fabricated files.

**Blocked by:** 05

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] Same verified inputs and projection version always produce
  byte-identical outputs.
- [ ] Timing-view selection and its basis are recorded per exported artifact;
  adopted-alignment timing is used only where its gates passed, original
  timing otherwise.
- [ ] Mode mapping follows plan §7: subtitle-first produces `source`/
  `readable`, enhancement produces `enhanced` with per-cue provenance, full
  ASR produces `verbatim`/`readable`; no mode ever fabricates another mode's
  artifacts.
- [ ] Part artifacts land under `parts/<part-id>/` with PartRelativeTime;
  collection artifacts use CollectionVirtualTime; Part boundaries stay hard.
- [ ] The projection has its own Stage version participating in invalidation.
- [ ] Missing upstream evidence yields `unavailable` manifest entries, never
  placeholder files.
