# Phase 2 Specification

## Status

Phase 2 is completed and verified. The implementation and retained synthetic
fixture evidence passed the approved quality gates. No user-media access,
model download, paid API, or production validation occurred.

## Objective

Build a deterministic, testable media-time and subtitle-core prototype. The
prototype must resolve stream-duration disagreement, signed PTS, exact
cross-Part time mapping, subtitle validity, and conservative rolling-caption
de-duplication without ASR, models, user media, or source ingestion.

## In Scope

- Exact rational time values with no floating-point accumulation.
- Signed `RawPtsTime`, non-negative `PartRelativeTime`, and contiguous
  `CollectionVirtualTime` mappings.
- Half-open intervals and deterministic cue ordering by
  `(start, end, source_ordinal)`.
- FFprobe JSON retained as `ProbeDocument` and parsed into a typed
  `ProbeProjection` without text or metadata fallback guessing.
- `DecodedInterval` and `StreamCoverage` calculation from exact observed
  boundaries, including separately reported internal gaps and indeterminate
  coverage states.
- SRT and VTT parsing, whole-track validation, lossless normalization, and
  outward millisecond serialization.
- Immutable `RawCue`, `NormalizedCue`, and `PresentationCue` representations.
- Exact local proof for rolling-caption token ownership and correction records.
- Hash-pinned synthetic fixtures generated only by a separately authorized,
  explicit FFmpeg task and consumed by integration tests.
- Unit, integration, and regression tests for all Phase 2 acceptance cases.

## Explicitly Out Of Scope

- User-provided media, local-file intake, URLs, browser data, network access,
  or paid APIs.
- New user-media CLI commands, including `vcp plan <source>`.
- ASR, alignment execution, VAD, diarization, OCR, LLMs, model downloads, and
  real-world video testing.
- New third-party runtime dependencies, lockfile changes, or package downloads.
- Automatic cleanup or deletion of generated fixtures, caches, or failed
  outputs.

## Domain Contracts

### Exact Time And Coordinates

- `RawPtsTime` is the exact signed rational time derived from a stream PTS and
  time base. Raw PTS is never clamped or rewritten.
- `PartRelativeTime` is `RawPtsTime - Part.coverage_start`. It is exact,
  non-negative, and is the per-Part SRT/VTT export coordinate.
- `CollectionVirtualTime` shifts `PartRelativeTime` by the exact coverage spans
  of preceding ordered Parts. It creates no artificial container-duration or
  absolute-PTS gaps.
- All internal intervals are half-open `[start, end)`. A valid cue has
  `start < end`.
- Cue ordering is stable by `(start, end, source_ordinal)` and permits overlap.
  Overlap is not a validation failure and is never automatically repaired.
- SRT/VTT serialization uses `floor(exact_start)` and `ceil(exact_end)` in
  milliseconds. The resulting sub-millisecond outward serialization envelope
  does not replace the exact range or source PTS.

### Probe And Coverage

- FFprobe raw JSON is stored unchanged as `ProbeDocument`.
- `ProbeProjection` reads known fields only. Unknown fields remain in the raw
  document and do not fail parsing.
- Missing or invalid required fields produce `probe_invalid`; coverage-critical
  missing data also produces `coverage_indeterminate`. No regular-expression,
  human-readable-text, container-duration, or stream-duration fallback is
  allowed.
- `StreamCoverage` is the outer envelope `[min(start), max(end))` of observed
  `DecodedInterval` values with exact endpoints. Internal gaps are diagnostics.
- Required unknown boundaries make coverage indeterminate. A subtitle track
  requiring that coverage cannot pass validation.

### Subtitle Evidence

- A subtitle track is atomic. Its raw source remains unchanged, and every cue
  must parse and validate before the track is accepted.
- Any invalid cue makes the track `invalid`, produces structured diagnostics,
  and prevents partial recovery or output.
- `RawCue` preserves parsed source text, time, and coordinates.
- `NormalizedCue` makes only lossless canonical-format changes and preserves
  every token.
- `PresentationCue` may omit token ownership only under the exact local rolling
  proof. All omissions reference source-token ranges and a correction reason.
- Rolling de-duplication is limited to stable-order adjacent cues in one Part
  and one subtitle track. It requires an exact contiguous normalized overlap,
  strict textual extension, and overlapping or contiguous intervals.
- Fuzzy, edit-distance, and semantic similarity never remove text. Identical
  text is removed only when both endpoints are also exactly equal; all other
  ambiguous repetition remains with `possible_duplicate`.

## Implementation Boundary

- The core is a typed Python library, not a media-facing CLI.
- Proposed modules are `timecode.py`, `probe.py`, `coverage.py`,
  `subtitles.py`, and `timeline.py` under `src/video_content_pipeline/`.
- The core uses only the Python standard library. Existing `pytest`, `ruff`,
  and `mypy` remain the only test and quality tools.
- Library code may invoke FFprobe only through argument lists, never a shell;
  it may do so only for project-owned synthetic fixtures after an explicit
  execution authorization.

## Fixture Boundary

- Fixture recipes, generated media, expected probe documents, tool versions,
  and hashes live below `tests/fixtures/`.
- Fixture generation is separate from normal tests. It requires a user-approved
  command plan before FFmpeg runs.
- Normal tests read retained fixture assets and never regenerate or delete them.

## Test Matrix

| Area | Required Proof |
| --- | --- |
| Rational time | Exact comparison, translation, signed PTS, and no float accumulation |
| Coordinates | Part-relative origin, compact collection concatenation, cross-Part mapping |
| Probe parser | Unknown fields tolerated; required invalid or missing fields diagnosed |
| Coverage | Different A/V starts, priming, internal gaps, indeterminate endpoint, metadata disagreement |
| Subtitle parsing | Valid SRT/VTT, invalid atomic rejection, source-bound validation, lossless normalization |
| Cue semantics | Overlap preservation, stable ordering, three immutable layers, millisecond envelopes |
| De-duplication | Exact duplicate, rolling accumulation, real repetition, ambiguous repetition, correction provenance |
| Integration | Retained FFmpeg media, FFprobe projection, coverage, and SRT/VTT round-trip validation |

## Acceptance Criteria

- All time calculations are exact and do not accumulate floating-point error.
- Raw source time and text remain traceable through every derived representation.
- No real spoken repetition is removed by de-duplication.
- No invalid subtitle track yields partial output.
- Exported SRT/VTT is parseable, deterministically ordered, and maps to an
  exact in-range source representation.
- FFprobe data has an explicit provenance and failure state; unknown required
  values are never inferred.
- Every synthetic fixture is retained with a recipe, tool provenance, hash, and
  expected probe evidence.
- Tests, lint, type checks, and project-environment gates pass after execution
  is authorized.

## Planned File Changes

| Path | Intended Change |
| --- | --- |
| `src/video_content_pipeline/timecode.py` | Exact rational time, intervals, and coordinate transforms |
| `src/video_content_pipeline/probe.py` | Probe document, typed projection, and diagnostics |
| `src/video_content_pipeline/coverage.py` | Decoded intervals and stream coverage calculation |
| `src/video_content_pipeline/subtitles.py` | SRT/VTT parsing, validation, cue layers, export, and de-duplication |
| `src/video_content_pipeline/timeline.py` | Ordered-Part virtual-time assembly |
| `tests/unit/` | Test-first unit coverage for every deterministic contract |
| `tests/integration/` | Fixture-backed FFmpeg and FFprobe integration coverage |
| `tests/fixtures/` | Later, explicitly approved recipes and retained generated assets |
| `config/tools.json` | Later, record actual Phase 2 FFmpeg/FFprobe use and provenance |
| `docs/PHASE_02_INVENTORY.json` | Record every created, modified, read external, and generated artifact |

## Pre-Implementation Authorization

Before implementation begins, submit a separate command and file-change plan.
It must state the exact test commands, whether Python will run, the fixture
generation command and its expected size, FFmpeg and FFprobe paths and versions,
expected peak resource use, and rollback or retention treatment. Current
planning estimates are 4-8 engineering hours, less than 256 MiB for unit work,
less than 512 MiB for tiny fixture generation, and no more than 20 MiB of
retained synthetic fixtures. No download is expected.

## Rollback And Retention

Source and documentation changes are reversible through normal version control.
Generated fixtures, caches, and failed outputs are retained until the user
explicitly authorizes deletion; no cleanup is part of Phase 2 implementation.
