# 12 — Acquire the prototype material (agent-selected, maintainer-confirmed)

**What to build:** Select public, DRM-free real material for the
capability prototypes — zh/en speech (mixed where possible), multiple
speakers for diarization, text-bearing frames for OCR; minutes-scale, not
hours. Present a media download plan (URL/source, duration, size, URL
access mode per plan §14.2) for quick maintainer confirmation — the
material must never block on the maintainer choosing a video. Intake
through the existing acquisition path (yt-dlp pinned in ticket 01 for
URLs, or hash-copied local files) into `input/<source-id>/`. First
real-media processing flips `media_processed` to `true`; the completion
report records object and purpose. Media authorization is separate from
model authorization by standing rule.

**Blocked by:** 01
**Status:** open
**Labels:** ready-for-agent

- [ ] Confirmed media download plan retained as a record (source, mode,
      size, purpose)
- [ ] Intake produces a hash-verified `input/<source-id>/` snapshot via
      the existing acquisition contracts
- [ ] Material demonstrably covers speech (zh+en), multi-speaker, and
      text-bearing frames — or gaps are recorded with a follow-up plan
- [ ] `media_processed` flips at first processing with the fact recorded
- [ ] No network access outside the confirmed plan
