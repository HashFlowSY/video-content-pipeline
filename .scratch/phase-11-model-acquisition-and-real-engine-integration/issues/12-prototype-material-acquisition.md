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
**Status:** acquisition done — box 4 (`media_processed` flip) handed to
ticket 13's first prototype run; all acquisition criteria met
**Labels:** ready-for-agent

- [x] Confirmed media download plan retained as a record (source, mode,
      size, purpose) — `docs/phase-11-download-plans/prototype-media.md`
      (maintainer-confirmed 2026-08-17, two PD VOA clips; authorized mode
      `direct`, executed via the local-file path — see box 2 and the Note)
- [x] Intake produces a hash-verified `input/<source-id>/` snapshot via
      the existing acquisition contracts — two content-addressed snapshots
      (`f6fd0cd7…`, `104eeec2…`) via the local-file `vcp plan` contract;
      each byte-exact + Commons-SHA-1 verified, each with ffprobe evidence
- [x] Material demonstrably covers speech (zh+en), multi-speaker, and
      text-bearing frames — or gaps are recorded with a follow-up plan —
      zh (clip 1) + en (clip 2) speech, 2/3+ speakers, zh+en on-screen
      text; recorded gap: no intra-file code-switching, follow-up = add a
      bilingual VOA interview if ticket 13 needs it
- [ ] `media_processed` flips at first processing with the fact recorded —
      NOT this ticket: "first real-media *processing*" is ticket 13's first
      prototype run (acquisition is not a capability run; the flag is not
      yet wired in code — spec.md:100, User Story 17)
- [x] No network access outside the confirmed plan — only
      `upload.wikimedia.org` was contacted, for the two confirmed URLs;
      the failed `direct`-URL attempt fetched no bytes. Confinement was
      operator-controlled (hardened yt-dlp flags + the single confirmed
      URL each fetch), not enforced by the `acquire_public_source` host
      proxy — see the Note

**Note:** the `direct`-mode URL contract cannot acquire plain Wikimedia
Commons media URLs — the pinned yt-dlp 2026.07.04 generic extractor reports
no exact `filesize` in `--dump-single-json --skip-download` mode, so the
metadata pass fails closed with `url_size_unknown` (zero bytes written). The
ticket's sanctioned hash-copied local-file path was used instead: each
confirmed URL fetched with the pinned yt-dlp restricted to that host, then
`vcp plan <file>`. No acquisition-contract code was changed (Phase 3 scope).
