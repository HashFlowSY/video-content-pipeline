# Phase 2 Execution Envelope: Ticket 08

Status: approved and executed
Date: 2026-08-09

## Authorized Scope

Implement only the dependency-free, presentation-only rolling-caption handling
for accepted subtitle evidence. The work may omit tokens only after the exact
local proof in ADR 0006, retain correction provenance and structured
`possible_duplicate` diagnostics, and add project-local unit tests. It creates
no media-facing CLI, source intake, fixture, package, download, model, network,
FFmpeg, or FFprobe behavior.

## Intended Changes

| Path | Change | Boundary |
| --- | --- | --- |
| `src/video_content_pipeline/subtitles.py` | Add immutable presentation correction and diagnostic evidence; apply exact stable-adjacent rolling proof. | Raw and normalized evidence remain immutable; no fuzzy or cross-Part removal. |
| `tests/unit/test_subtitles.py` | Add failing-first unit cases for rolling accumulation, exact duplicates, real repetition, and ambiguity. | In-memory subtitle evidence only. |
| `.scratch/phase-02-deterministic-media-core-and-timeline-prototype/issues/08-apply-proven-rolling-caption-deduplication.md` | Record completed ticket evidence. | Local tracker only. |
| `docs/PHASE_02_INVENTORY.json` | Record retained command and file evidence. | Audit record only. |

## Approved Commands

Every Python command activates `.venv`, sets the project-local runtime
variables, and runs `scripts/require-project-venv.sh` first. The commands run
only the focused subtitle tests, standard lint/format/type checks, and the
existing full test suite.

```text
pytest -q tests/unit/test_subtitles.py
ruff check src/video_content_pipeline/subtitles.py tests/unit/test_subtitles.py
ruff format --check src/video_content_pipeline/subtitles.py tests/unit/test_subtitles.py
mypy src
pytest -q
```

## Resource And Retention Boundary

Expected memory is below 256 MiB and any tool cache use remains below 10 MiB
inside the project. All source, test, plan, tracker, and inventory records are
retained. No cache, temporary file, fixture, failed output, archive, or other
artifact will be deleted.

## Execution Results

The initial TDD check failed as expected because the presentation correction
interfaces had not yet been implemented. The final focused subtitle suite
passed with 17 tests. Ruff check, Ruff format check, strict Mypy, and the full
suite passed with 44 tests. Worktree review against `HEAD` found no standards
or specification issues. No FFmpeg, FFprobe, network, download, package,
model, user-media, or CLI action occurred.
