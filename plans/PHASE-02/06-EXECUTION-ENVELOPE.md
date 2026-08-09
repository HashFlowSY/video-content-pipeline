# Phase 2 Execution Envelope: Ticket 06

Status: approved and executed
Date: 2026-08-09

## Authorized Scope

Implement only the dependency-free SRT/WebVTT subtitle evidence boundary:
immutable `RawCue` and `NormalizedCue` records, atomic validation, and
project-local unit tests. No FFmpeg, FFprobe, user media, network, models,
new packages, or media-facing CLI behavior is authorized.

## Approved Commands

Every Python command activates `.venv`, sets project-local runtime variables,
and runs `scripts/require-project-venv.sh` first.

```text
export VCP_PROJECT_ROOT=/Users/shangyang/Desktop/workspace/projects/video-content-pipeline; export UV_INSTALL_DIR=$VCP_PROJECT_ROOT/tools/uv; export UV_CACHE_DIR=$VCP_PROJECT_ROOT/cache/uv; export UV_PYTHON_INSTALL_DIR=$VCP_PROJECT_ROOT/runtime/python; export UV_PROJECT_ENVIRONMENT=$VCP_PROJECT_ROOT/.venv; export PIP_CACHE_DIR=$VCP_PROJECT_ROOT/cache/python; export TMPDIR=$VCP_PROJECT_ROOT/tmp; source .venv/bin/activate; scripts/require-project-venv.sh; pytest -q tests/unit/test_subtitles.py
export VCP_PROJECT_ROOT=/Users/shangyang/Desktop/workspace/projects/video-content-pipeline; export UV_INSTALL_DIR=$VCP_PROJECT_ROOT/tools/uv; export UV_CACHE_DIR=$VCP_PROJECT_ROOT/cache/uv; export UV_PYTHON_INSTALL_DIR=$VCP_PROJECT_ROOT/runtime/python; export UV_PROJECT_ENVIRONMENT=$VCP_PROJECT_ROOT/.venv; export PIP_CACHE_DIR=$VCP_PROJECT_ROOT/cache/python; export TMPDIR=$VCP_PROJECT_ROOT/tmp; source .venv/bin/activate; scripts/require-project-venv.sh; ruff check src/video_content_pipeline/subtitles.py tests/unit/test_subtitles.py
export VCP_PROJECT_ROOT=/Users/shangyang/Desktop/workspace/projects/video-content-pipeline; export UV_INSTALL_DIR=$VCP_PROJECT_ROOT/tools/uv; export UV_CACHE_DIR=$VCP_PROJECT_ROOT/cache/uv; export UV_PYTHON_INSTALL_DIR=$VCP_PROJECT_ROOT/runtime/python; export UV_PROJECT_ENVIRONMENT=$VCP_PROJECT_ROOT/.venv; export PIP_CACHE_DIR=$VCP_PROJECT_ROOT/cache/python; export TMPDIR=$VCP_PROJECT_ROOT/tmp; source .venv/bin/activate; scripts/require-project-venv.sh; ruff format --check src/video_content_pipeline/subtitles.py tests/unit/test_subtitles.py
export VCP_PROJECT_ROOT=/Users/shangyang/Desktop/workspace/projects/video-content-pipeline; export UV_INSTALL_DIR=$VCP_PROJECT_ROOT/tools/uv; export UV_CACHE_DIR=$VCP_PROJECT_ROOT/cache/uv; export UV_PYTHON_INSTALL_DIR=$VCP_PROJECT_ROOT/runtime/python; export UV_PROJECT_ENVIRONMENT=$VCP_PROJECT_ROOT/.venv; export PIP_CACHE_DIR=$VCP_PROJECT_ROOT/cache/python; export TMPDIR=$VCP_PROJECT_ROOT/tmp; source .venv/bin/activate; scripts/require-project-venv.sh; mypy src
export VCP_PROJECT_ROOT=/Users/shangyang/Desktop/workspace/projects/video-content-pipeline; export UV_INSTALL_DIR=$VCP_PROJECT_ROOT/tools/uv; export UV_CACHE_DIR=$VCP_PROJECT_ROOT/cache/uv; export UV_PYTHON_INSTALL_DIR=$VCP_PROJECT_ROOT/runtime/python; export UV_PROJECT_ENVIRONMENT=$VCP_PROJECT_ROOT/.venv; export PIP_CACHE_DIR=$VCP_PROJECT_ROOT/cache/python; export TMPDIR=$VCP_PROJECT_ROOT/tmp; source .venv/bin/activate; scripts/require-project-venv.sh; pytest -q
```

## Resource And Retention Boundary

Expected unit-test memory is below 256 MiB with no network or downloads. All
source and test changes remain below the project root and are retained for
audit; no generated media or cleanup action is part of this ticket.
