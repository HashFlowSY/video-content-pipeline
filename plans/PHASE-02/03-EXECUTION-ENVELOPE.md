# Phase 2 Execution Envelope: Ticket 03

Status: approved and executed
Date: 2026-08-09

## Authorized Scope

Implement the internal, dependency-free path from a retained raw FFprobe JSON
`ProbeDocument` to a typed `ProbeProjection`. The approved seams are the
library `ProbeDocument` and `project_probe_document` APIs.

The implementation may read only in-memory JSON supplied by unit tests. It
must preserve that exact raw string, project stream `index`, `codec_type`, and
positive exact `time_base`, and reject missing or invalid required values with
`probe_invalid` and `coverage_indeterminate` diagnostics. It must not use
human-readable text, regular expressions, container duration, or stream
duration as a fallback source.

## Intended Changes

| Path | Change | Boundary |
| --- | --- | --- |
| `src/video_content_pipeline/probe.py` | Add immutable raw evidence, typed stream projection, and structured diagnostics. | Standard library JSON only; no FFprobe invocation. |
| `tests/unit/test_probe.py` | Add test-first in-memory evidence cases. | No fixtures, media, subprocesses, or network. |
| `.scratch/.../issues/03-project-ffprobe-evidence-without-fallback.md` | Record the completed ticket. | Local tracker only. |
| `docs/PHASE_02_INVENTORY.json` | Record command and file evidence. | Audit record only. |

## Executed Commands

Every Python command activated `.venv`, set the project-local runtime variables,
and ran `scripts/require-project-venv.sh` first.

```sh
pytest tests/unit/test_probe.py
ruff check src/video_content_pipeline/probe.py tests/unit/test_probe.py
ruff format --check src/video_content_pipeline/probe.py tests/unit/test_probe.py
mypy src
pytest
```

The focused test first failed at import time because the new module did not
exist. After the minimal implementation and formatting pass, the focused test
passed 4 tests; Ruff, strict Mypy, and the full suite passed.

## Resource And Retention Boundary

- No FFmpeg, FFprobe, fixture-generation command, package action, download,
  model, paid API, user-media access, or public CLI change occurred.
- Expected memory remained below 256 MiB; only existing project-local test and
  tool caches may have changed.
- Source, test, tracker, plan, and inventory records are retained. No cache,
  fixture, temporary data, or failed output was deleted.
