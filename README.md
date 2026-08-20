# Video Content Pipeline

This repository contains a local, auditable video-content processing pipeline.

Engineering development (Phases 1–11) is complete and verified offline:

- **Phase 1** — project initialization and reproducible runtime.
- **Phase 2** — deterministic media core and timeline prototype.
- **Phase 3** — source intake, planning, and resource estimation.
- **Phase 4** — subtitle-track priority pipeline.
- **Phase 5** — auditable audio analysis prototype.
- **Phase 6** — evidence-bound semantic segmentation and summaries.
- **Phase 7** — full ASR transcription and local enhancement.
- **Phase 8** — optional visual text (OCR).
- **Phase 9** — orchestration, recovery, publication, and full inventory.
- **Phase 10** — synthetic engineering verification.
- **Phase 11** — model acquisition and real-engine integration (eight pinned
  model assets in the local registry; the seven capability prototypes ran on
  real Public-Domain media).

The project is now in **Phase 12 — real video testing**, a long-running
acceptance phase (`overall_stage: real_world_testing`). Real run #1 completed
2026-08-18: five real-engine stages (Silero VAD, Qwen3-ASR, Qwen3-4B text
semantics) plus a pause/resume drill on real media, producing real semantic
output with cue-cited evidence and peak memory under 12 GiB. The maintainer
recorded an interim verdict but has not yet confirmed any Formal branch, so
the coverage ledger stands at 0 / 5 confirmed and `production_validated`
remains `false`. The phase stays `in_progress` until all five Formal branches
are confirmed by recorded real runs — an expected long tail, not a stall.

Constraints honored to date: no paid APIs, no runtime auto-downloads, no
dependency auto-upgrades. Model downloads and real-media processing are
explicitly authorized and recorded in `project-state.json`.

## Local Runtime

All project-owned runtime state stays below this repository:

- `tools/uv/`: project-local uv binary.
- `runtime/python/`: project-local managed CPython runtime.
- `.venv/`: required project virtual environment.
- `cache/`: uv and package caches.
- `models/`: pinned local model assets and registry.
- `tmp/`: project-local temporary files.

Before every Python command, activate the project environment and run the
shell gate:

```sh
source .venv/bin/activate
./scripts/require-project-venv.sh
```

Use `tools/uv/uv` for dependency management. Do not use global installs,
ordinary `pip install`, `uv run`, or a system Python interpreter for this
project.

## Current Status

See `project-state.json` for the machine-readable status. Key documents:

- `docs/PHASED_EXECUTION_PLAN.md` — overall scope and phase plan.
- `docs/PHASE_01_COMPLETION_REPORT.md` … `docs/PHASE_11_COMPLETION_REPORT.md`
  — per-phase audits for the completed engineering phases.
- `docs/PHASE_12_SPECIFICATION.md` — the adopted Phase 12 boundary
  (long-running acceptance, decisions D1–D10).
- `docs/PHASE_12_COVERAGE_LEDGER.md` — the sole evidence base for real-video
  acceptance: which of the five Formal branches are confirmed by recorded
  real runs.

The project remains in real-world testing and is **not** production-validated.
`production_validated` flips only when the coverage ledger shows all five
Formal branches confirmed and the maintainer gives explicit final
confirmation.
