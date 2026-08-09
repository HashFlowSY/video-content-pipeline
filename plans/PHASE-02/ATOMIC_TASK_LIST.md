# Phase 2 Atomic Task List

## Planning Tasks

- [x] Adopt the Phase 2 scope and record the project status.
- [x] Resolve and record the time, probe, coverage, subtitle, fixture,
  dependency, and CLI boundaries.
- [x] Publish the Phase 2 specification and this atomic task list.

## Implementation Tasks

- [x] Submit the exact implementation, Python-test, and fixture-generation
  command plan for user approval; awaiting explicit approval, and do not run
  media tools or generate fixtures before that approval.
- [x] Write failing unit tests for normalized rational values, signed
  `RawPtsTime`, half-open intervals, and exact comparison; implement the
  smallest `timecode.py` behavior that passes.
- [x] Write failing unit tests for `PartRelativeTime` and
  `CollectionVirtualTime`, including nonzero and negative PTS; implement only
  exact coordinate translations and compact Part concatenation.
- [x] Write failing unit tests for `ProbeDocument`, `ProbeProjection`, unknown
  fields, and required-field diagnostics; implement typed JSON parsing without
  text or metadata fallback.
- [ ] Write failing unit tests for `DecodedInterval`, `StreamCoverage`, gaps,
  and indeterminate boundaries; implement coverage envelopes without duration
  guesses.
- [ ] Write failing unit tests for valid SRT and VTT input, invalid atomic
  rejection, source-bound checks, and lossless normalization; implement the
  smallest `RawCue` and `NormalizedCue` parser behavior that passes.
- [ ] Write failing unit tests for `PresentationCue`, stable overlap ordering,
  and outward millisecond serialization; implement derived cue and export
  behavior without rewriting source evidence.
- [x] Write failing unit tests for exact rolling accumulation, exact duplicate
  deletion, real spoken repetition, and `possible_duplicate`; implement only
  the approved local-proof de-duplication and correction provenance.
- [x] Submit a concrete FFmpeg fixture recipe, expected paths, expected size,
  and retention record for user approval; receive explicit fixture-generation
  approval before creating or executing it.
- [x] After explicit fixture approval, generate retained synthetic media and
  expected ProbeDocuments under `tests/fixtures/`; hash and inventory every
  generated artifact without deleting any prior output.
- [ ] Write and run fixture-backed integration tests for FFprobe projection,
  stream coverage, coordinate mapping, and SRT/VTT output.
- [ ] Run the approved unit, integration, lint, type-check, and environment-gate
  commands; record all commands, paths, versions, test results, and resources
  in the Phase 2 inventory.
- [ ] Publish a Phase 2 completion report and update project status only when
  every accepted task and gate has passed. Do not mark production validation.
