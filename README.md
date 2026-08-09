# Video Content Pipeline

This repository contains a local, auditable video-content processing pipeline.
Phase 1, project initialization and reproducible runtime, is complete. Phase
2, the deterministic media core and timeline prototype, is complete. Phase 3,
source intake, planning, and resource estimation, is complete and verified
offline.

Phase 3 is limited to explicitly authorized local-file and public-URL intake,
source snapshotting, inspection, immutable planning, and pre-confirmed decode
validation. It does not implement ASR, OCR, subtitle-text processing, model
downloads, paid services, processing runs, or production validation.

## Local Runtime

All project-owned runtime state stays below this repository:

- `tools/uv/`: project-local uv binary.
- `runtime/python/`: project-local managed CPython runtime.
- `.venv/`: required project virtual environment.
- `cache/`: uv and package caches.
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

See `project-state.json` for the machine-readable status,
`docs/PHASE_01_COMPLETION_REPORT.md` for the Phase 1 audit,
`docs/PHASE_02_COMPLETION_REPORT.md` for the Phase 2 audit,
`docs/PHASE_03_SPECIFICATION.md` for the adopted Phase 3 boundary,
`docs/PHASE_03_COMPLETION_REPORT.md` for its verification record, and
`docs/PHASED_EXECUTION_PLAN.md` for the overall scope. The project remains in
engineering development and is not production-validated.
