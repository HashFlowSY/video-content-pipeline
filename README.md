# Video Content Pipeline

This repository contains a local, auditable video-content processing pipeline.
Phase 1, project initialization and reproducible runtime, is complete.

Phase 1 establishes only the project boundary, local Python runtime, virtual
environment gate, configuration, registries, and engineering checks. It does
not process media, download models, call paid services, or implement Phase 2
timeline functionality.

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

See `project-state.json` for the machine-readable status and
`docs/PHASE_01_COMPLETION_REPORT.md` for the Phase 1 audit. The next authorized
implementation stage is Phase 2; it is not started automatically.
