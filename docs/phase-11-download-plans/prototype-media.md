# Media download plan — Phase 11 ticket 12 prototype material

**Status:** acquired · maintainer-confirmed 2026-08-17 · media-download
authorization (separate from model-download authorization by standing rule,
spec.md:157)

**Purpose:** real, public, DRM-free material for the ticket 13 capability
prototypes — VAD/chunking, diarization (multi-speaker structure), forced
alignment, ASR (zh + en), OCR (zh + en text-bearing frames), and
`text_semantics` — with maintainer sample review before Phase 12. Selected by
the agent from Wikimedia Commons, confirmed by the maintainer.

## Authorization

Media-download authorization only; never reused as model-download
authorization (spec.md:157). The agent selected two Voice of America clips
from Wikimedia Commons and presented this plan (source page, direct URL,
license, exact byte count, duration, purpose, coverage) to the maintainer,
who confirmed both before any bytes moved.

## Access mode and executed intake

**Authorized mode:** `direct` (plan §14.2) — both clips are direct media URLs
on `upload.wikimedia.org`, HTTPS (transport integrity verified), single host.

**Executed intake:** the local-file acquisition contract, not the URL
contract. The `direct`-URL path (`vcp plan <url> --url-mode direct` →
`authorize_public_url` → `acquire_public_source`) was **attempted first and
failed closed**: the pinned yt-dlp 2026.07.04 generic extractor reports no
exact `filesize` for a plain Commons media URL in `--dump-single-json
--skip-download` mode, so the metadata pass returned `url_size_unknown` and
wrote zero bytes. The ticket's sanctioned alternative was used instead: each
confirmed URL was fetched with the pinned yt-dlp restricted to that host
(`--no-config --no-plugin-dirs --no-cookies --no-cookies-from-browser
--no-playlist`), byte count + Commons SHA-1 verified, then hash-copied into
`input/<source-id>/` via `vcp plan <file>` (`snapshot_local_source`).

**Network confinement:** the fetch invoked yt-dlp directly, so confinement to
`upload.wikimedia.org` was operator-controlled (hardened flags + the single
confirmed URL each time), **not** enforced by the fail-closed host proxy that
`acquire_public_source` provides. No host other than `upload.wikimedia.org`
was contacted; the failed `direct`-URL attempt fetched no bytes.

## Clips

### 1 — VOA连线(陈杰人)：江歌案为何引发中国网络大争议？

| Field | Value |
|---|---|
| Source page | https://commons.wikimedia.org/wiki/File:VOA连线(陈杰人)：江歌案为何引发中国网络大争议？.webm |
| Direct URL | `https://upload.wikimedia.org/wikipedia/commons/6/61/VOA连线(陈杰人)：江歌案为何引发中国网络大争议？.webm` |
| Author / license | Voice of America — **Public Domain** (work of the US federal government) |
| Container | WebM (VP9/Opus), 854×480 |
| Duration | 242.215 s (4:02) |
| Exact size | 10,995,689 bytes |
| Commons SHA-1 | `6047bf0c1e9a6c0ebc216c33078745a5598977ea` |
| Language | Mandarin Chinese |
| Speakers | 2 — studio anchor + Beijing commentator 陈杰人 via live two-way link |
| Text-bearing | zh on-screen text — 连线 banners, name supers, topic title |

### 2 — China's 'Princelings' Create a Name for Nepotism (VOA On Assignment Dec. 14)

| Field | Value |
|---|---|
| Source page | https://commons.wikimedia.org/wiki/File:China%27s_%27Princelings%27_Create_a_Name_for_Nepotism_(VOA_On_Assignment_Dec._14).webm |
| Direct URL | `https://upload.wikimedia.org/wikipedia/commons/1/1a/China's_'Princelings'_Create_a_Name_for_Nepotism_(VOA_On_Assignment_Dec._14).webm` |
| Author / license | Voice of America — **Public Domain** (work of the US federal government) |
| Container | WebM (VP9/Opus), 854×480 |
| Duration | 292.556 s (4:53) |
| Exact size | 64,988,425 bytes |
| Commons SHA-1 | `2f8075fd8874e1f61ce41112952141e8ca892739` |
| Language | English |
| Speakers | 3+ — host, VOA reporter Kathy Guofu Yang, interviewees |
| Text-bearing | en on-screen text — titles, lower-thirds, data graphics |

**Totals:** 2 clips, ~8m55s, 75,984,114 bytes (~72 MiB).

## Coverage vs. ticket matrix

| Dimension | Covered by |
|---|---|
| Speech — zh (Mandarin) | Clip 1 |
| Speech — en | Clip 2 |
| Multiple speakers (diarization) | Both (2 and 3+) |
| Text-bearing frames (OCR) | Both — zh (clip 1), en (clip 2) |
| Minutes-scale, not hours | Both (4:02, 4:53) |
| Public, DRM-free, clean license | Both (PD VOA) |

**Recorded gap + follow-up:** no *single* file mixes zh and en audio in one
stream; the set covers zh and en in separate clips. If a later prototype needs
intra-file code-switching, the follow-up plan is to add one bilingual VOA
interview (same source/mode/authorization pattern). Within the ticket's "gaps
recorded with a follow-up plan" allowance.

## On-disk record

Acquired 2026-08-17. Snapshots are content-addressed: source-id = media
SHA-256. The acquisition contract redacts raw URLs from persistent records, so
the authoritative on-disk provenance is this SHA-256 plus the redacted
host/path; each snapshot also carries ffprobe `structural`/`coverage`
evidence and both streams (VP9 video + Opus audio).

| Clip | source-id (= input SHA-256) | bytes | probed duration |
|---|---|---|---|
| 1 | `f6fd0cd7157b3d13502de534ed1d4930cb27f48192b57ce4f3ebcbefa26fd9da` | 10,995,689 | 242.215 s |
| 2 | `104eeec2d832d272ad36f9659aeddd5ba8c34ac35bc89f06ec672f3b3d2902fc` | 64,988,425 | 292.556 s |

**`media_processed`:** stays `false` after acquisition — acquisition is not a
capability run. The flip belongs to the first ticket-13 prototype run, with
object and purpose recorded there (spec.md:100, User Story 17).
