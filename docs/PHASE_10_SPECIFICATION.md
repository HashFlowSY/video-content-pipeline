# Phase 10 Specification: Synthetic Engineering Verification

## Domain routing

Read [CONTEXT-MAP.md](../CONTEXT-MAP.md) first. This phase verifies existing
contexts rather than introducing a new one. The vocabulary it adds is owned
by [media-foundation](contexts/media-foundation/CONTEXT.md) (Real media,
Synthetic media fixture, the Phase 10 boundary) and
[orchestration](contexts/orchestration/CONTEXT.md) (Fault point, Fault class,
Golden run, Fault matrix). Every other context is exercised as a consumer.

## Status

`approved_for_implementation_planning` (grilling consensus 2026-08-16, 20
questions, all recommendations accepted).

Scope disclaimer: this phase proves engineering correctness only. It cannot
and does not claim domain quality (see Known Limitations). Closure flips the
project to `real_world_testing`.

## Objective

Prove, without Real media, that the whole pipeline is engineered correctly:
every layer the plan names (unit, property, integration, fault injection,
CLI acceptance) is present, deterministic, and green, and `vcp run` executes
end to end for the first time — real per-phase functions, real ffmpeg/ffprobe,
real filesystem — with deterministic substitute model adapters as the only
non-real component.

Unlike prior phases, Phase 10 starts from an audit: 1034 passing tests exist
at baseline `4e63fd5`. The work is gap-filling against that audit plus two
genuinely new structures (the property layer and the fault matrix), not a
rebuild.

## Coverage audit baseline

Audited 2026-08-16 at `4e63fd5` (997 test functions, 1034 after
parametrization, 3.85s wall):

- Unit layer: strong overall; thin spots are time/intervals
  (`test_timecode.py` has 7 tests against a large surface) and
  `subtitle_pipeline.py` (no dedicated unit file).
- Property layer: absent (no hypothesis, no `@given`, no randomness anywhere).
- Real-tool integration: absent (every ffmpeg/ffprobe touchpoint is
  fixture- or stub-backed; the binaries have never executed in a test).
- Fault injection: partial (Phase 9 recovery tests: kill-executor, torn
  state temp, truncated journal tail). Missing: ENOSPC, systematic power-loss
  enumeration, arbitrary corruption, control-file corruption.
- CLI acceptance: exists in pieces (`test_phase_9_orchestration_cli_contract.py`)
  but not as a named branch-complete layer.

## Verification boundaries

- No model is downloaded or executed. All model capabilities run through
  deterministic substitute adapters (ADR 0037 lineage). `vcp models *`
  remains outside this phase.
- No network access, ever.
- `media_processed` in project state refers to Real media only (glossary:
  Real media vs Synthetic media fixture). Generating and processing Synthetic
  media fixtures does not flip it; it stays `false` until Phase 11.
- External tools: the host `ffmpeg`/`ffprobe` recorded in `config/tools.json`
  are the approved Fixture toolchain (Phase 2 precedent). Tests that need
  them verify identity against `tools.json` and **error — never skip — on
  absence or mismatch**, so the gate cannot be silently hollowed out.
  `tools.json` is re-probed and refreshed once at the start of this phase.

## Workstream A — Property layer (greenfield)

`hypothesis` is added to the dev dependency group (explicitly authorized
addition, exact-version pinned, installed through `tools/uv/uv`; this is the
one governed dependency change of the phase). Gate profile is deterministic:
`derandomize=True`, fixed example budget (~50/property); exploratory random
runs are a manual developer option only.

Targets:

1. Time-base invariants and random intervals (plan-mandated): exactness of
   RawPtsTime / PartRelativeTime / CollectionVirtualTime conversions (no
   float drift), HalfOpenInterval algebra, coverage merging idempotence and
   order-independence, monotonic cue order stability.
2. Serialization round-trips: for every `as_json`/`from_json` pair across
   contexts, generated-object → JSON → object equality, plus rejection
   properties (malformed/mutated JSON fails with typed reasons, never
   crashes). This replaces building a generic JSON-schema framework.

## Workstream B — Synthetic fixtures and real-tool integration

A fixture generator (test-support code, not production) turns versioned
Fixture recipes into tiny media files (seconds long, low resolution) via the
pinned host ffmpeg at test-session time, cached per session; the repository
stays free of media binaries.

Five fixture branches, mirroring Phase 11's mandatory real-video branches
one for one:

1. Subtitle track present → subtitle-first flow.
2. No subtitle track → full-ASR flow.
3. Anomalous subtitles: rolling repeats and time drift, synthesized exactly.
4. Multi-Part collection.
5. Text-bearing frames → explicit visual-text OCR flow.

Integration tests execute the real binaries against these fixtures (probe
structure verified), giving Phase 11 a green engineering twin for every real
branch before any Real media is touched.

## Workstream C — Synthetic end-to-end

The injection point stays at the existing `_composition_factory` seam, but
what is injected shrinks to the minimum fake: a production `RunComposition`
wired to the real per-phase functions, real ffmpeg/ffprobe, and the real
filesystem, with deterministic substitute model adapters (content-derived,
hash-seeded outputs) as the only non-real parts. No production test modes
are added.

Evidence and report gatherers (left deliberately conservative in Phase 9)
are extended exactly as far as needed for the end-to-end published bundle's
core artifacts to be VALID — no broader byte-level reconstruction than the
acceptance requires.

## Workstream D — Fault matrix

Vocabulary: a **Golden run** (fault-free reference execution) dynamically
enumerates **Fault points** — every call into the four `durable_io`
functions (`write_and_fsync`, `durable_write`, `atomic_replace`,
`fsync_directory`), the single persistence outlet of the orchestration
layer. The **Fault matrix** replays the run injecting one **Fault class**
at the k-th point, for every k and every class:

- Process death: the interceptor raises and simultaneously freezes all
  further durable writes (exception handlers that would run after a real
  power loss cannot touch disk), then the test recovers from the on-disk
  state alone.
- Exhausted disk: `OSError(ENOSPC)` at the k-th call.
- Torn write: a prefix of the bytes lands, then the death freeze.

Exhaustiveness is structural, not curated: the golden count is recomputed
each run, so a new persistence call site automatically joins the matrix.
No production-side fault-point registry is added.

Asserted post-fault invariants, for every cell:

- `vcp status` classifies the wreck without mutating anything.
- `vcp resume` recovers to a terminal state, or the run fails into a
  Minimal RunBundle; no third outcome.
- `outputs/` is never corrupt or partial (atomic publish holds).
- Completed units never re-execute.
- Torn journal/state tails are repaired, with the repair journaled.

Included per the Phase 9 deferral: corrupt or truncated Control request
files must halt the run safely (if production code does not yet, fixing it
is in scope, with stage/schema version discipline observed).

Per-stage internals get representative injection (at least one raised
exception per stage via its adapter/executor seam), proving failed-bundle
publication and downstream `blocked` isolation stage by stage.

Power loss is verified at two tiers: the deterministic matrix above is the
exhaustive body; a small set of real SIGKILL subprocess tests (mid-stage,
mid-publish) provides end-to-end spot checks through the CLI.

## Workstream E — CLI acceptance

A named acceptance layer drives the full orchestration surface —
`plan / run / status / pause / resume / cancel / verify / inventory` plus
`improve` (added post-plan in Phase 9 ticket 11 and part of the published
contract) — across all five fixture branches, end to end on synthetic
fixtures. The 16 expert commands are not re-accepted here; their per-phase
tests remain their contract. `improve` is exercised against a bundle the
end-to-end run actually published.

Standing guarantees re-asserted at this layer: non-publication commands
never write `outputs/`; published bundles hash-verify; failed runs never
advance the latest pointer.

## Test-suite mechanics

- Markers `integration`, `slow`, `faultmatrix` are registered with
  `--strict-markers`. They are a developer convenience only: **the exit
  gate is always the full suite**; no layer is skipped by default.
- Shared fault-injection and fixture tooling lives in a plainly importable
  `tests/support/` package (explicit imports, no conftest magic), extracting
  Phase 9's in-file `KillingExecutor`.
- Hard wall-clock budget for the full gate: **≤ 5 minutes** on the gate
  machine. When over budget, shrink scenarios (shorter fixtures, smaller
  micro-plans), never exhaustiveness.

## Exit gates

From the plan (verbatim, 阶段 10 退出门禁):

1. 工程自动化测试通过。
2. 已知限制全部写入报告。
3. 项目整体状态切换为 `real_world_testing / 当前阶段：真实测试，尚未完成生产验收`。

Gate 3 is a maintainer closure action and, uniquely to this phase, flips
`overall_stage` in `project-state.json` in addition to the usual phase
fields.

Derived gates (proven by named tests, recorded in
`docs/PHASE_10_INVENTORY.json` with the established inventory schema):
property layer exists and is deterministic; five fixture branches generate
and probe-verify via the pinned toolchain; `vcp run` completes end to end
and publishes a hash-verified bundle with VALID core artifacts; the fault
matrix is exhaustive over enumerated fault points × classes with all
post-fault invariants; control-file corruption halts safely; CLI acceptance
covers all five branches plus `improve`; full-suite wall time within
budget; `media_processed` still `false`; `models_downloaded` still `false`.

## Known limitations (cannot verify — goes into the report verbatim)

- 真实中英 ASR 准确率。
- 真实直播重叠说话质量。
- 真实字幕强制对齐成功率。
- 真实 OCR 召回和数字准确率。
- 真实长视频摘要忠实度。

## Out of scope

- Any Real media processing (Phase 11).
- Model acquisition, `vcp models *`, real model execution.
- Real small-filesystem disk-full rigs (ENOSPC is injected at the seam).
- A generic JSON-schema validation framework.
- Evidence-gatherer reconstruction beyond what end-to-end VALID acceptance
  requires.
- Production readiness or domain-quality claims of any kind.

## Related decisions

- [ADR 0054](adr/0054-verify-engineering-with-synthetic-media-and-a-deterministic-fault-matrix.md)
  — synthetic media + deterministic fault matrix as the verification
  strategy, including the two-tier power-loss approach.
- ADR 0037 — controlled offline adapters (the substitute-adapter lineage).
- ADR 0051/0052/0053 — the publication, adoption, and run-state contracts
  the fault matrix stresses.
