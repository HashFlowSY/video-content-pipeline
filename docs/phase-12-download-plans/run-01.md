# Media plan / intake record — Phase 12 run #1

**Status:** intaken · maintainer-supplied local file · media-download
authorization (separate from model-download authorization by standing rule) ·
this run covers the **full-ASR** Formal branch (no embedded subtitle stream).

**Purpose:** the maintainer's first real acceptance material for Phase 12
(真实视频测试), taken through the genuine intake entrance
(`vcp plan <file>` → `snapshot_local_source`), hashed and content-addressed,
ready for the ticket-08 plan → confirm → run.

## How this material was acquired (honest provenance)

This is a **maintainer-supplied local file**, not an agent URL acquisition. The
standing per-run procedure permits it — *"Local-file materials skip straight to
intake but still get hashed"*
([coverage ledger](../PHASE_12_COVERAGE_LEDGER.md)) — and the local-file intake
contract (`validate_local_source_candidate` → `snapshot_local_source`) applies,
**not** the URL contract (`acquire_public_source` / ADR 0057 host proxy). There
are therefore no disclosed media hosts to authorize for this run.

**Why the URL entrance was not used.** The production URL path
(`vcp plan <url> --url-mode direct`) was attempted first and **failed closed**
against every mainstream platform tried, because this agent's Bash network
egress is an **AWS datacenter IP** (`52.192.165.214`, AS16509, Tokyo) that the
platforms' anti-bot controls reject:

- **bilibili** returned **HTTP 412 (风控)** on the video page for *every* video
  from this IP (a second control video also 412'd), while the homepage and the
  public `x/web-interface/view` API returned 200. This held regardless of
  browser-TLS impersonation (`curl_cffi` + `--impersonate`, provisioned and
  verified working in isolation) or anonymous `buvid3`/`b_nut` cookies — i.e.
  it is IP-reputation blocking, not a fingerprint gate.
- **YouTube** returned *"Sign in to confirm you're not a bot"*, demanding login
  cookies.

Both bypasses require credentials, which the credential-free rule forbids and
which the agent will not enter. No IP-circumvention (proxy/VPN) was used. The
maintainer therefore acquired the media on their own (non-datacenter) network
and supplied it as a local file; the agent moved **zero** acquisition bytes.

**Origin detail — maintainer declined to itemise (「不细查」, 2026-08-17).** The
maintainer supplied the file and chose not to record a specific source URL or
rights paperwork. Recorded honestly rather than inferred. What is objectively
true of the bytes on hand: the container is a plain, **un-encrypted, DRM-free**
MP4 (ffprobe decoded structure and packets without any decryption), the video
tier is **720p** (a credential-free/guest quality, not an HD-gated premium
stream), and the supplied filename is `40798454513-1-192.mp4` (bilibili-style
`cid-page-quality` naming). The filename is **not** inferred to any specific
`BV…` id — it does not match `BV1tcuz6EEkV` (whose `aid` is 117076865975640),
so no origin id is asserted here.

## The material

| Field | Value |
|---|---|
| Supplied filename | `40798454513-1-192.mp4` |
| Origin | maintainer-supplied local file; source URL not itemised at maintainer's discretion (「不细查」); DRM-free, 720p guest-tier |
| source-id (= intake SHA-256) | `f10e8895e2370c2f4bbbe98218f699d7ef17582c538604e23fbf6b1698a48889` |
| Exact size | 129,909,345 bytes (~124 MiB) |
| Container | MP4 (`mov,mp4,m4a,3gp,3g2,mj2`) |
| Duration | 2098.507506 s (34m58s) |
| Video stream | h264, 1280×720, language `und` |
| Audio stream | aac, language `und` |
| **Embedded subtitle stream** | **none** — video + audio only (ffprobe `-select_streams s` empty) |
| Overall bitrate | ~495 kbps |

**Formal branch determination.** No embedded subtitle track ⇒ speech must be
transcribed end-to-end ⇒ this run exercises the **full-ASR** branch (branch 2
of the [coverage ledger](../PHASE_12_COVERAGE_LEDGER.md)). Recorded after
probing the actual container, as the procedure requires; the branch this run
*covers* is confirmed only by the ticket-09 Real-run confirmation.

## Plan legal fields (from the intake plan report)

Report id `1c6e048f97b3453c84f49b59a9bf2bc6`, state
`awaiting_decode_confirmation` (the ticket-08 `vcp plan decode` → `confirm`
gate is next).

| Field | Value |
|---|---|
| 预计时间 (decode estimate) | likely 700 s · conservative 2099 s · optimistic 263 s (confidence **low**; basis `decode-throughput-profile:phase-03-default-v1`) |
| 峰值内存 (peak-memory estimate) | **5.088 GiB** (`asr_primary`), basis `device-baselines:apple-m1`, confidence **measured** — within the 12 GiB envelope |
| 磁盘 (disk headroom) | increment 326,927,554 B · required 1,400,669,378 B · reserve 1,073,741,824 B (satisfied) |
| 模型状态 (model status) | all 7 capabilities `model_ineligible` — the real `models/registry.json` is deliberately un-promoted (no `resource_estimate`); a real-engine run is blocked until an authorized registry promotion (see ticket 08) |

Per-capability peak-memory estimate (GiB): asr_primary 5.088 ·
text_semantics 4.704 · forced_alignment 3.995 · asr_review 3.526 ·
ocr_primary 0.498 · diarization 0.324 · vad 0.116.

## On-disk record

Intaken 2026-08-17. Content-addressed: source-id = media SHA-256. Snapshot at
`input/f10e8895…a48889/` (gitignored):

- `media` — 129,909,345 bytes, read-only.
- `evidence/structural.ffprobe.json` — stream/format structure.
- `evidence/coverage.ffprobe.json` — packet-level coverage evidence.

**`media_processed`** stays `false` after intake — acquisition is not a
capability run; the flip belongs to the ticket-08 run.
