# 08 — Run #1 execution and publication

**What to build:** The first end-to-end real run on real engines. From the
intaken run #1 media: `vcp plan` → `vcp plan decode` → `vcp plan confirm`
(maintainer confirms a plan showing all four legal fields) → `vcp run` with
the real adapters. Mid-run, perform one deliberate pause/resume drill at a
stage boundary (`vcp pause`, verify the paused state, `vcp resume`,
verify no completed stage re-runs). The run publishes an atomic RunBundle
whose processing report carries full provenance (models actually used with
revisions and hashes, tools, environment, parameters, measured peak memory
and durations); `vcp verify` and `vcp inventory` pass against it. If a real
failure occurs, it publishes per the Minimal RunBundle floor and is
recorded honestly — failure is a result, not a rollback.

**Blocked by:** 05 (RunBundle provenance), 06 (real engines in orchestrated
run), 07 (run #1 media acquisition).

**Status:** done (definitive run `20260818T114653Z-19562f62a649ee1b`,
2026-08-18; this status sync recorded after the fact — the boxes below were
true when the run record and ledger row were committed)

- [x] Plan confirmed by the maintainer with time / peak memory / disk / model status all present — plan `8cb2d4c4d953982cdd608028`, front-loaded choices `asr_mode=full_asr`, `visual_text_enabled=false` (ticket 04 fields in effect)
- [x] Full run completes on real engines — five real-engine stages (silero-VAD, Qwen3-ASR, Qwen3-4B text_semantics among them) over the whole 34m58s timeline; no Minimal-floor failure needed
- [x] One pause/resume drill performed at a stage boundary; resume re-ran no completed stage — exercised on the first complete attempt (`20260818T074454Z-…`: paused at the transcription→text boundary, resume adopted the completed ASR stage); the superseding re-runs did not repeat the drill
- [x] RunBundle published atomically; `vcp verify` (hash_verified + inventory_valid) and `vcp inventory` pass
- [x] Processing report provenance non-empty, naming the real model stack with revisions (`971e898`, `d4e1109`)
- [x] Observed peak memory recorded, all far under the 12 GiB envelope — VAD 396 MiB / ASR 3.37 GiB / text 5.87 GiB (silero-vad headroom honestly labeled *estimated*, `97e8c40`)

## Closure note (2026-08-18, recorded in this status sync)

- **Deviation — media source**: run #1 processed a **maintainer-supplied
  local file** (zh Q&A session, 34m58s, no embedded subtitle stream;
  hash `f10e8895…a48889`), not the ticket-07 bilibili download. Recorded
  honestly in the run record ("Download plan: n/a — local-file material").
  The real URL production entrance therefore remains ticket 07's scope.
- **Superseded attempts** (both complete + verified, kept for provenance):
  `20260818T074454Z-adff4a51f8b8e118` (pre-fix text engine, no validated
  segment content) and `20260818T111753Z-45c7c50cac559ecf` (title-only
  content report). The definitive run carries the corrected text engine
  (single whole-transcript call, short cue ids, `detailed_content` field,
  pre-flight token gate) **and** the corrected content-report renderer
  (`25b788d`): published content-report holds the segment title plus all
  74 cue-cited detail points.
- ~11 real-media defects were fixed en route (the largest:
  text_semantics windowing revert → single-call at max_kv_size 32768).
- **Deferred, documented**: readable subtitle/transcript *files* are not
  in the published bundle (manifest records them `unavailable`;
  reconstruction is a recorded follow-up) — inspection of subtitle
  readability uses the retained 74 verbatim ASR cues as audit evidence.
- Run record: `docs/phase-12-runs/20260818T114653Z-19562f62a649ee1b.md`;
  ledger row registered (`971e898`, `8035000`). Maintainer acceptance and
  the branch flip live in ticket 09, not here.
