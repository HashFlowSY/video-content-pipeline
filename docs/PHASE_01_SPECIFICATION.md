# Phase 1 Specification

## Objective

Establish a reproducible, project-local Python runtime and a hard environment
boundary for Video Content Pipeline without implementing any media-processing
behavior.

## In Scope

- A local Git repository with no remote and no commits.
- Project-owned directories and a `.gitignore` policy.
- A checksum-verified project-local uv binary.
- A checksum-recorded uv-managed CPython 3.12 runtime under `runtime/python/`.
- A project-local `.venv` created from that managed runtime.
- Minimal, locked engineering dependencies for tests, linting, type checks, and
  packaging.
- Shell and in-process environment gates.
- Configuration, tool metadata, model registry metadata, state, tests, and a
  completion inventory.

## Explicitly Out Of Scope

- Phase 2 timeline, media, subtitle, FFmpeg, or FFprobe behavior.
- Any ASR, alignment, diarization, OCR, language, or vision model download.
- Video or audio input, browser session data, remote media download, paid API,
  remote Git repository, commit, or push.

## Public Test Boundaries

1. `scripts/require-project-venv.sh` and `scripts/run-vcp.sh` reject a missing
   or incorrect project virtual environment before Python starts.
2. `video_content_pipeline.environment.assert_project_venv()` accepts the
   activated project environment and rejects an invalid process environment.
3. `python -m video_content_pipeline check-environment` is the minimal package
   CLI contract for the in-process gate.

## Acceptance Criteria

- Every Python operation is preceded by activation and the shell gate.
- The active interpreter, `VIRTUAL_ENV`, and `sys.prefix` all resolve to
  `.venv`.
- uv, managed Python, cache, temporary data, and installed packages remain
  below the project root.
- `uv.lock` pins all installed package artifacts.
- No model registry entry is installed or downloaded.
- Tests, linting, type checks, lock verification, and boundary checks pass.
- `project-state.json` ends as Phase 1 `completed`, with engineering
  development as the overall stage.
