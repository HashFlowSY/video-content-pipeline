# 07 — Run #1 media acquisition (bilibili BV1tcuz6EEkV)

**What to build:** The maintainer's first real material is acquired through
the genuine production entrance. Produce a written media download plan in
the established Phase 11 prototype-media style for
`https://www.bilibili.com/video/BV1tcuz6EEkV`: probed duration and formats,
total size (summed DASH components), disclosed media hosts (ADR 0057), disk
headroom, estimated processing time, peak-memory estimate, and model
status. Present it to the maintainer and **stop for their confirmation** —
no bytes move without it (media authorization is never inherited from model
authorizations). On confirmation: download at credential-free quality
through the per-run-authorized proxy, hash the acquired media, and complete
intake. Record honestly whether the acquired container carries any embedded
subtitle stream — this determines which Formal branch run #1 covers
(expected: full ASR).

**Blocked by:** 02 (DASH multicomponent), 03 (per-run host authorization),
04 (plan legal fields).

**Status:** ready-for-agent

- [ ] Written download plan exists with duration, formats, summed size, disclosed hosts, disk headroom, estimated time, peak memory, model status
- [ ] Maintainer confirmation recorded before any download traffic
- [ ] Download completed at credential-free quality via only the disclosed hosts
- [ ] Acquired media hashed and intaken; subtitle-stream presence recorded honestly
- [ ] No credential, no HD-gated format, no undisclosed host used
