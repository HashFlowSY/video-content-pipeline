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
04 (plan legal fields) — all done.

**Status:** done-with-deviation (`93dbf42`, 2026-08-17) — run #1's
acquisition purpose was fulfilled by a **maintainer-supplied local file**,
not this ticket's bilibili URL; the URL production entrance remains
unverified in real conditions (see deviation note for why and what remains).

- [x] Written acquisition record with plan legal fields — `docs/phase-12-download-plans/run-01.md` (local-file variant: decode likely 700s, peak-memory 5.088 GiB within envelope, per-capability model statuses; hosts/DASH n/a for a local file)
- [x] No download traffic occurred without confirmation — trivially held: no download traffic at all; the maintainer supplied the file directly
- [ ] Download completed at credential-free quality via only the disclosed hosts — **NOT DONE**: no real URL download has ever run (see deviation note)
- [x] Acquired media hashed and intaken; subtitle-stream presence recorded honestly — source-id `f10e8895…a48889`, 129,909,345 B, 34m58s, h264 720p + aac, **no embedded subtitle stream ⇒ full-ASR branch (branch 2)**; intaken via `vcp plan <file>`
- [x] No credential, no HD-gated format, no undisclosed host used — trivially held (no network involved)

## Deviation note (2026-08-18 status sync)

Run #1 did **not** exercise this ticket's URL path. Reasons and residue,
stated plainly:

- **Durable acquisition constraint (learned during run #1):** this agent's
  Bash network egress is an AWS datacenter IP; bilibili returns HTTP 412
  风控 on video pages IP-wide, and YouTube demands sign-in. No credentials
  and no IP-circumvention are permissible, so **credential-free real URL
  acquisition cannot run from the agent's network at all** — it must be
  initiated from the maintainer's own (non-datacenter) network, or the
  material must be local-file / Commons / archive.org.
- Consequently the URL production entrance — written download plan →
  ADR 0057 host disclosure → maintainer confirmation → real DASH download
  through the per-run-authorized proxy → hash intake — is verified at the
  CI seams (tickets 02/03/04) but has **never run in real conditions**.
  `vcp plan <url>` itself is correct and fails closed.
- That outstanding verification is **not** re-opened here: this ticket is
  closed against run #1's actual acquisition. It becomes the first ledger
  run whose material arrives as a URL fetched from the maintainer's
  network — the standing per-run procedure (Coverage ledger) already
  requires the download plan + confirmation stops, so no new ticket is
  needed unless the maintainer wants one.
