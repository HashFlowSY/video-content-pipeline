# Video Content Pipeline

This repository contains a local, auditable video-content processing pipeline.
Phase 1, project initialization and reproducible runtime, is complete. Phase
2, the deterministic media core and timeline prototype, is complete.

Phase 2 is limited to deterministic media and timeline behavior: structured
FFprobe parsing, rational time calculations, subtitle parsing and
normalization, virtual-timeline logic, conservative de-duplication, and
FFmpeg-generated synthetic fixtures. It does not access user media, download
models, call paid services, or implement ASR, OCR, or source ingestion.

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
`docs/PHASE_02_COMPLETION_REPORT.md` for the Phase 2 audit, and
`docs/PHASED_EXECUTION_PLAN.md` for the adopted scope. Phase 2 used only
project-owned synthetic fixtures; it did not process user media or validate
production behavior.
