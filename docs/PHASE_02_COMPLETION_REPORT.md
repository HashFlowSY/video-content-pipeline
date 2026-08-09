# Phase 2 Completion Report

## Status

Phase 2, deterministic media core and timeline prototype, is completed and
verified. The project remains in engineering development for the next phase;
real-world testing and `production_validated` are both `false`.

Phase 3 is not started or authorized by this task. No user media, URL, browser
data, model, paid API, ASR, OCR, source-intake command, or network resource was
used.

## Delivered Scope

- Dependency-free exact rational time, signed raw PTS, half-open intervals,
  Part-relative coordinates, and compact collection virtual time.
- Strict typed FFprobe projection retaining raw JSON evidence, decoded stream
  coverage, internal-gap diagnostics, and indeterminate-boundary handling.
- Atomic SRT/VTT parsing and validation with immutable RawCue, NormalizedCue,
  and PresentationCue layers, stable overlap ordering, and outward
  millisecond serialization.
- Conservative rolling-caption de-duplication with exact local proof,
  `possible_duplicate` diagnostics, and correction provenance.
- Hash-pinned project-owned synthetic media, subtitle fixtures, recipes, raw
  FFprobe documents, and read-only fixture-backed integration tests.

## Final Verification

All commands ran from the project root after activating `.venv` and passing
`scripts/require-project-venv.sh`. No command installed packages or changed
fixtures.

| Gate | Result |
| --- | --- |
| `pytest -q` | 49 passed in 0.18s |
| `ruff check src tests` | passed |
| `ruff format --check src tests` | 18 files already formatted |
| `mypy src` | passed; no issues in 9 source files |
| Environment gate | passed before every Python-invoking command |

Runtime versions were Python 3.12.13, pytest 8.3.5, Ruff 0.11.5, and mypy
1.15.0. The final full test run measured 37,142,528 bytes maximum resident
set size (about 35.4 MiB), 0.29 seconds wall time, and no swap or block I/O.

## Fixture And Audit Evidence

The canonical fixture manifest contains 12 retained entries: three synthetic
media files, three literal subtitle files, three raw ProbeDocuments, one
versioned recipe, and two tool-provenance records. Every entry has a byte count
and lowercase SHA-256 digest. Fixture generation used the recorded FFmpeg and
FFprobe 8.1.2 pair; tests consumed the retained artifacts read-only.

The integration proof verifies negative audio PTS, AAC priming metadata,
gap-video coverage `[10, 13.9)` with internal gap `[11, 13)`, compact timeline
mapping, contradictory metadata-duration resistance, and parseable SRT/VTT
round trips. Failed generation and manifest
repair attempts remain retained under `tmp/` for audit; no cleanup was done.

The complete command ledger, paths, hashes, provenance, retention classes,
and external-read record are maintained in `docs/PHASE_02_INVENTORY.json`.
The Phase 2 tool registry in `config/tools.json` records the actual FFmpeg and
FFprobe fixture use and their retained 8.1.2 provenance.

## Boundaries And Handoff

Phase 2 exposes a library-only core. Production validation is intentionally
deferred until a later explicitly authorized phase with real-world test
coverage. No dependency, lockfile, model registry payload, or remote was
added or changed by finalization.
