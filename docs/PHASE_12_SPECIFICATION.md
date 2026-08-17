# Phase 12 Specification: Real Video Testing (long-running acceptance)

## Domain routing

Read [CONTEXT-MAP.md](../CONTEXT-MAP.md) first. This phase introduces no new
context. New vocabulary — `Formal branch`, `Real-run confirmation`,
`Coverage ledger` — is owned by
[orchestration](contexts/orchestration/CONTEXT.md) (already recorded).
Affected existing contexts: [source-planning](contexts/source-planning/CONTEXT.md)
(plan report gains peak-memory and model-status fields; the URL download
plan gains per-run host disclosure per ADR 0057) and
[orchestration](contexts/orchestration/CONTEXT.md) (real engines invoked
from the orchestrated run; RunBundle provenance completed).

## Status

`specification_pending_approval` (grilling consensus 2026-08-17, four
rounds; decisions D1–D10 below). Governing plan section:
[PHASED_EXECUTION_PLAN.md §阶段 12](PHASED_EXECUTION_PLAN.md) (真实视频测试,
five 正式分支, plan-before-every-test rule, acceptance list,
`production_validated` gate).

## Objective

Phase 12 is a **long-running acceptance phase**, not a one-shot test
campaign. The maintainer has no capacity to assemble scenario-specific
materials up front; instead, one provided material proves the main flow
end-to-end on real engines, and the five Formal branches are retired
opportunistically as real materials arrive over time. The phase stays
`in_progress` — possibly for a long period, which is expected and not a
stall — until the Coverage ledger shows all five branches confirmed, and
only then does `production_validated` flip, with the user's explicit final
confirmation.

Before the first real run is possible, a bounded set of enablement
engineering must land, because Phase 11 deliberately wired real engines
*beside* the offline path without CLI invocation
(PHASE_11_COMPLETION_REPORT.md, "carried-forward limitations"), and the
first material is a bilibili URL that today's acquisition contract rejects
at three independent points.

## Locked decisions (D1–D10)

| # | Decision |
|---|---|
| D1 | Phase shape: standing per-run procedure + Coverage ledger; phase closes at 5/5 confirmed Formal branches, not on a calendar. |
| D2 | First material: `https://www.bilibili.com/video/BV1tcuz6EEkV` (zh livestream clip, published 2026-08-15; page shows no CC track, so it is *expected* to cover the **full-ASR** branch — the actual branch is recorded honestly after probing). |
| D3 | First-run bar: confirmed plan → full run **including one deliberate pause/resume drill at a stage boundary** → published RunBundle → maintainer inspection → ledger entry. Failure publication is never staged; it is recorded if and when a real failure occurs. |
| D4 | Ledger lives in `docs/PHASE_12_COVERAGE_LEDGER.md` (+ one `docs/phase-12-runs/<run-id>.md` per run); `project-state.json` gains no branch-level fields until closure. |
| D5 | URL intake is fixed **before** the first run (engineering-first chosen over an operator local-copy shortcut): real download → hash → intake is part of run #1. |
| D6 | The plan-report gap (missing peak memory & model status) and the RunBundle provenance stub (empty models/tools/environment/resources) are fixed **before** the first run — the inspected artifacts must be complete, not "带伤". |
| D7 | Known quality leftovers from Phase 11 — diarization over-clustering (en, threshold 0.5) and text_semantics single-segment collapse — are **not** tuned beforehand; the first real run measures their actual severity, then tuning/decisions follow evidence. |
| D8 | Media host authorization is **per-run disclosed host set** (ADR 0057); no standing platform allowlist. |
| D9 | Model swaps are out of scope: the maintainer does not currently intend to swap models. If quality evidence later motivates a swap, that is its own decision round (download plan, registry entry, and re-confirmation semantics for affected branches are all decided then, not pre-committed now). |
| D10 | Every Real-run confirmation records a per-capability verbal rating — acceptable / marginal / unacceptable — for subtitle readability, speaker separation, and summary faithfulness, so any future model-swap discussion cites recorded verdicts instead of impressions. |

## Governance boundaries

- **Per-download confirmation, plan first.** Every media download gets a
  written plan in the `docs/phase-11-download-plans/prototype-media.md`
  style: URL, probed duration and formats, total size, disclosed media
  hosts (ADR 0057), disk headroom, estimated time, peak-memory estimate,
  model status. No bytes move before the maintainer confirms that plan.
  Model-download authorization is never reused as media authorization
  (standing registry rule), and vice versa.
- **Credential-free quality only.** bilibili downloads take the formats
  available without login. Credentialed/HD acquisition is a separate
  future decision.
- **CER/WER discipline** (plan law): never fabricated. Computed only if a
  human reference text exists, and then the reference's source and
  proofing scope are recorded. No reference text is currently planned.
- **yt-dlp stays pinned** at 2026.07.04 (`config/tools.json`,
  ADR 0019). The known bilibili blockers are acquisition-*contract*
  limitations, not yt-dlp version defects; an upgrade is proposed only on
  a real observed failure attributable to the binary, with separate
  confirmation.
- **Runtime policy unchanged**: no runtime auto-downloads, no paid APIs,
  no dependency auto-upgrades. Real adapters keep hub-offline guards; a
  missing pinned asset stays a typed failure, never a download.
- **`production_validated`** flips only when the Coverage ledger shows all
  five Formal branches confirmed **and** the user explicitly confirms the
  whole; the standing "do not mark" instruction in AGENTS.md holds until
  that moment.

## Workstream A — Real-run enablement (before run #1)

1. **Wire real engines into the orchestrated run.** `vcp run` invokes the
   real adapters for every acquired capability; the controlled offline
   adapters remain the automated-test path (ADR 0037 stands). Pause/resume
   and cancel must behave correctly across Model runtime subprocess stages
   (ADR 0053, ADR 0055) — this is what the run #1 drill exercises.
2. **Multicomponent (DASH) acquisition support.** The metadata pass sums
   the component sizes of `requested_formats` instead of raising
   `url_multicomponent_unsupported` (acquisition.py); it still fails
   closed when no component size is determinable. This also retires the
   Phase 11 Wikimedia deviation family for split-stream sources.
3. **Per-run media host authorization** (ADR 0057). Metadata pass resolves
   media hosts; the download plan discloses them; the acquisition proxy
   admits exactly the confirmed set for that download and nothing else,
   including mid-download redirects.
4. **Plan report legal fields.** The plan output adds (a) a peak-memory
   estimate with its basis — the Phase 11
   `docs/phase-11-prototypes/device-baselines.json` per-capability
   measurements; (b) model status — per-capability state derived from the
   model registry. The plan's own rule requires 预计时间、峰值内存、磁盘、
   模型状态; today only two of four exist.
5. **RunBundle provenance.** Replace the conservative
   `_gather_report_inputs()` stub (run_composition.py, "ticket 10
   option 1"): the processing report must carry the models actually used
   (name, revision, sha256, path, size, purpose — from the registry),
   tools, environment, parameters, and resource usage (real peak memory
   from subprocess evidence, durations, disk delta). This is what binds a
   ledger entry to the model stack that produced it (D10/D9 depend on it).
6. **Tests.** Unit/acceptance coverage for A2–A5; the engineering suite
   stays green throughout.

## Workstream B — Run #1 (bilibili BV1tcuz6EEkV)

1. Media download plan (per Governance boundaries) → maintainer confirms →
   download at credential-free quality → hash → intake.
2. `vcp plan` → `vcp plan decode` → `vcp plan confirm` → `vcp run`, with
   one deliberate `vcp pause` / `vcp resume` drill at a stage boundary
   mid-run.
3. RunBundle published; `vcp verify` and `vcp inventory` pass.
4. Maintainer inspection of subtitles, speakers, detailed content, and
   summary; per-capability verbal ratings (D10); recorded as
   `docs/phase-12-runs/<run-id>.md` following the shape of
   `docs/phase-11-prototypes/maintainer-review.md` (header with source +
   hash, dated decision line, confirmation table, notes/follow-ups,
   provenance).
5. Ledger entry: which Formal branch(es) the run actually covered
   (expected: full ASR), model-stack snapshot reference, verdicts, plus
   the observed real-world severity of the two D7 leftovers.

## Workstream C — Ledger and the long tail

1. Create `docs/PHASE_12_COVERAGE_LEDGER.md`: a five-row Formal-branch
   table (branch, status unconfirmed/confirmed, confirming run(s), date)
   starting at 0/5, plus a run log (run id, source, date, branches
   claimed, confirmation file).
2. The **standing per-run procedure** for every future real material:
   download plan if URL → plan/confirm → run → publish → inspect + rate →
   ledger entry. No new ticket per run; the ledger is the record.
3. **Closure** (whenever 5/5 is reached): completion report +
   `PHASE_12_INVENTORY.json` + an acceptance test that machine-checks the
   inventory (Phase 11 pattern), the user's explicit overall
   confirmation, then the `project-state.json` flip to
   `production_validated`.

## Exit gates

- All five Formal branches confirmed in the Coverage ledger, each backed
  by a published RunBundle and a recorded Real-run confirmation.
- Every real run was preceded by a maintainer-confirmed plan showing
  estimated time, peak memory, disk, and model status.
- pause/resume exercised on at least one real run; if any real failure
  occurred, its failure publication produced the Minimal RunBundle floor.
- Processing reports of real runs carry non-empty model / tool /
  environment / resource provenance.
- No download without a confirmed plan; no connection beyond a confirmed
  host disclosure; no CER/WER without a recorded human reference.
- Engineering suite green; no unauthorized downloads of any kind.
- `production_validated` flipped only with the user's explicit final
  confirmation.

## Out of scope

- Model swaps (D9) — separate decision round if evidence motivates one.
- bilibili CC-subtitle API acquisition (bilibili subtitles are a separate
  JSON API the downloader does not fetch). The subtitle-priority branch is
  covered by a source with embedded subtitle streams, or this support is
  specced when such bilibili material actually arrives.
- Automatic playlist/multi-P expansion (`--no-playlist` stands). The
  multi-P branch goes through the existing manual ordered collection
  (`vcp plan --collect`, ADR 0017) unless a later decision changes that.
- Credentialed or HD downloads; materials outside the zh/en scope of the
  acquired model stack.
- Pre-emptive diarization threshold tuning or text_semantics
  recalibration (D7).

## Related decisions

- New: [ADR 0057](adr/0057-authorize-media-hosts-per-run-from-the-download-plan.md)
  (draft; approving this specification puts it into effect).
- Load-bearing existing: ADR 0015 (URL confirmation), 0017 (manual
  collection), 0019 (yt-dlp pinned prerequisite), 0037 (real adapters
  beside the offline path), 0051 (atomic RunBundle publish), 0053
  (single-writer run state, pause/resume), 0055 (model runtime
  subprocesses), 0056 (text-semantics calibration).
