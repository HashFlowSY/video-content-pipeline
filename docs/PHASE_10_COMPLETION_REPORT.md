# Phase 10 Completion Report

## Status

Phase 10, synthetic engineering verification, is completed and verified in
the project-local offline environment. All five plan test layers — unit,
property, integration with the real pinned ffmpeg/ffprobe, fault injection,
and CLI acceptance — exist, are deterministic, and are green in one full
suite; `vcp run` executed end to end for the first time over Synthetic media
fixtures with deterministic substitute model adapters as the only non-real
component. This is an engineering pass only: no real model was downloaded or
invoked, no Real media was processed, no network was touched, and no
domain-quality claim is made. Per the third 阶段 10 退出门禁 the project's
overall stage now flips to
`real_world_testing / 当前阶段：真实测试，尚未完成生产验收`;
`production_validated` remains `false`.

## Delivered Scope

- Governance and dependency baseline: `hypothesis` pinned as the phase's one
  explicitly authorized dev dependency with a deterministic gate profile
  (derandomized, fixed example budget); `integration`/`slow`/`faultmatrix`
  markers under `--strict-markers` (the gate is always the full suite); the
  host ffmpeg/ffprobe re-probed and identity-refreshed in
  `config/tools.json`.
- Shared verification tooling in an importable `tests/support/` package (no
  conftest): the `DurableIoInterceptor` over the four `durable_io`
  primitives (golden-run write counting; process-death with freeze-on-death,
  ENOSPC, and torn-write injection), control-file corruption helpers, the
  extracted `KillingExecutor`, deterministic hash-seeded model adapters, and
  the synthetic fixture generator.
- Five synthetic fixture branches mirroring Phase 11's mandatory real-video
  branches one for one (subtitle-first, full-ASR, anomalous subtitles with
  rolling repeats and time drift, multi-Part, visual-text), generated per
  test session by the pinned host toolchain — identity mismatch is a test
  error, never a skip — with zero media binaries in the repository.
- The property layer: time-base and half-open-interval invariants (exact
  conversions, coverage-merge idempotence and order-independence, monotonic
  cue order) and serialization round-trips with typed-rejection properties
  across sixteen serialize/deserialize contracts; plus the dedicated
  `subtitle_pipeline.py` unit file.
- The exhaustive orchestration fault matrix: a Golden run enumerates every
  durable write (N = 23, asserted against a recorded constant so new write
  sites fail loudly) and replays every position × {process death, ENOSPC,
  torn write} — 74 cells including control-file corruption,
  ENOSPC-during-publish, and torn-latest-pointer — asserting in every cell
  read-only diagnosis, exactly-one-safe-outcome, atomic `outputs/` and
  `latest.json`, no re-execution of checkpointed units, and journaled
  repair.
- Representative per-stage faults (an injected exception at any of the seven
  stages aborts into a published verifiable `failed` bundle; per-Part
  failure isolates only its own downstream; collection failure fails the
  run) and real power-loss spot checks: a subprocess harness SIGKILLed
  mid-stage and mid-publish, diagnosed and recovered through the real CLI to
  a published verified bundle.
- The first true offline end-to-end `vcp run`: production `RunComposition`,
  real per-phase functions, real ffmpeg/ffprobe (probe, subtitle demux,
  analysis-audio extraction), publishing a hash-verified bundle whose core
  content artifacts are VALID, byte-identical across a double run.
- The five-branch CLI acceptance layer: `plan/run/status/verify/inventory`
  green per branch, `pause`/`resume`, `cancel`, and `improve` exercised
  against genuinely published bundles, standing guarantees re-asserted
  (non-publication commands never write `outputs/`; failed runs never
  advance the latest pointer).
- The closing exit-gate inventory `docs/PHASE_10_INVENTORY.json` (3 plan
  gates + 9 derived gates, each with named proving tests) and its acceptance
  test (regex-derived plan gates, AST-verified citations, machine-checked
  governance state).

## Known Limitations

本阶段不能验证（原文，转入阶段 11 真实视频测试）：

- 真实中英 ASR 准确率。
- 真实直播重叠说话质量。
- 真实字幕强制对齐成功率。
- 真实 OCR 召回和数字准确率。
- 真实长视频摘要忠实度。

## Recorded Deviations

- `hypothesis` is the phase's single governed dependency addition,
  explicitly authorized in the 2026-08-16 grilling; no other dependency,
  model, or runtime download occurred.
- Fixture recipes moved to FLAC @ 32 kHz with `-bitexact`
  (`RECIPES_VERSION` → 2): at 48 kHz AAC the decoded coverage had ~1 ms
  tiling gaps and a negative pre-roll invisible to structural probes and
  unprocessable end to end.
- Verification exposed and fixed six genuine production gaps, all
  contract-preserving: subtitle-first runs no longer auto-invoke
  transcription (the transcription context's own contract, previously
  violated by the composition); analysis-audio extraction emits decimal
  seconds (FFmpeg rejects rational timestamps — this code had never met
  real FFmpeg); an all-Parts full-ASR subtitle handoff now proceeds to ASR;
  garbage control requests surface `control_request_unreadable` instead of
  a bare decode error; publication wraps ENOSPC into machine-readable
  `PublicationError` reasons; evidence gatherers extended exactly as far as
  VALID core artifacts require (grilling Q7 boundary).
- Deterministic in-matrix power loss uses freeze-on-death (no durable write
  can follow the injected death, so exception handlers cannot do disk work
  a real power loss would never run); real SIGKILL coverage is the two-point
  CLI spot check by design.
- Two ticket-08 test files carried formatter-owned drift discovered at
  closure re-verification and reformatted in the closure commit (Phase 8/9
  precedent).
- Domain quality is explicitly not claimed; the five known limitations
  above are the subject of Phase 11.

## Final Verification

At the closure commit, re-run in full: `pytest -q` 1332 passed (≈ 15 s wall,
within the ≤ 5-minute budget), `ruff check .` clean, `ruff format --check .`
clean, `mypy src` clean over 57 source files. `docs/PHASE_10_INVENTORY.json`
records all 12 exit gates confirmed (3 plan + 9 derived) with the third plan
gate performed by the maintainer in this closure; constraints
`models_downloaded`, `media_processed`, and `paid_apis_used` remain `false`.
