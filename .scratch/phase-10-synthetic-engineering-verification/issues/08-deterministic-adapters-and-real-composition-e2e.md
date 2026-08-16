# 08 — Wire deterministic model adapters into the real composition and run end to end

**What to build:** The first true offline end-to-end `vcp run`. Build
deterministic substitute model adapters (ADR 0037 lineage, in
`tests/support/`) for every model capability seam the per-phase functions
require — primary ASR, second ASR, text model, OCR — producing
content-derived, hash-seeded outputs (same input bytes → same output,
different inputs → different output; no randomness). Through the existing
`_composition_factory` seam, inject a **production `RunComposition`**
wired to the real per-phase functions, real ffmpeg/ffprobe, and the real
filesystem, over ticket-03 fixtures; the adapters are the only fakes, and
production code gains no test modes. Extend Phase 9's deliberately
conservative evidence/report gatherers exactly as far as needed for the
published bundle's core content artifacts (subtitles, transcript,
content-report, segments) to be VALID — no broader reconstruction
(grilling Q7). Prove at minimum the subtitle-first branch to
`complete`/published with `vcp verify` green; other branches are ticket 10.

**Blocked by:** 03
**Status:** open
**Labels:** ready-for-agent

- [ ] Adapters are deterministic (double-run byte-identical bundle digest)
- [ ] Composition is the production `RunComposition`; only model adapters faked
- [ ] Real ffmpeg/ffprobe execute inside the run
- [ ] Published bundle core artifacts VALID; `vcp verify` green
- [ ] No production test modes introduced; gatherer extensions minimal and listed
- [ ] Suite green within budget

## Comments
