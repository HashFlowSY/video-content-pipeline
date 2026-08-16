# Phase 10 — Synthetic Engineering Verification

Type: spec
Status: approved_for_implementation_planning
Labels: ready-for-agent
Phase: 10
Published: docs/PHASE_10_SPECIFICATION.md

## Domain routing

Read [CONTEXT-MAP.md](../../CONTEXT-MAP.md). Vocabulary added this phase is
owned by media-foundation (Real media, Phase 10 boundary) and orchestration
(Golden run, Fault point, Fault class, Fault matrix). All contexts are
exercised as consumers.

## Problem Statement

The pipeline has 1034 passing tests (baseline `4e63fd5`) but three structural
holes stand between it and real-video testing: no property layer exists at
all, the real ffmpeg/ffprobe binaries have never executed inside a test, and
`vcp run` has never run end to end (Phase 9 proved the loop only through
fake-executor seams). Failure behavior is proven only for a handful of
hand-picked crash sites; ENOSPC, systematic power loss, and control-file
corruption are unverified. The plan requires all five verification layers
green before the project may flip to `real_world_testing`.

## Solution

Audit-driven gap fill plus two new structures, per
[docs/PHASE_10_SPECIFICATION.md](../../docs/PHASE_10_SPECIFICATION.md):

- Property layer via an explicitly authorized, pinned `hypothesis` dev
  dependency (deterministic gate profile): time-base/interval invariants and
  serialization round-trips.
- Synthetic fixture generator: five fixture branches mirroring Phase 11's
  real-video branches, generated at test-session time by the identity-pinned
  host ffmpeg (error, never skip), zero media binaries in the repo.
- Synthetic end-to-end: production `RunComposition` + real per-phase
  functions + real tools through the existing composition seam; the only
  fakes are deterministic substitute model adapters. Evidence gatherers
  extended exactly until the published bundle's core artifacts are VALID.
- Deterministic fault matrix over the orchestration layer: Golden-run
  enumeration of every `durable_io` call × {process death, ENOSPC, torn
  write}, with a freeze-on-death interceptor; per-stage representative
  injection; SIGKILL subprocess spot checks; control-file corruption halts
  safely (Phase 9 deferral, production fix in scope if needed).
- Named CLI acceptance layer: all five branches ×
  plan/run/status/pause/resume/cancel/verify/inventory + improve.
- Mechanics: `tests/support/` importable kit (no conftest), strict markers,
  full suite ≤ 5 minutes as a hard budget, closing inventory + acceptance
  test per house convention.

Decisions recorded in ADR 0054. Exit gates: plan 阶段 10 退出门禁 (3,
verbatim) + derived gates in `docs/PHASE_10_INVENTORY.json`; closure flips
`overall_stage` to `real_world_testing`.

## Tickets

01 governance-and-dependency-baseline → 02 fault-injection-support-kit,
03 synthetic-fixture-generator, 04 time-interval-property-tests (needs 01),
05 serialization-round-trip-property-tests (needs 01),
06 subtitle-pipeline-unit-tests, 07 orchestration-fault-matrix (needs 02),
08 deterministic-adapters-and-real-composition-e2e (needs 03),
09 stage-faults-and-sigkill-spot-checks (needs 02, 08),
10 five-branch-cli-acceptance (needs 03, 08),
11 closing-exit-gate-inventory (needs all).
