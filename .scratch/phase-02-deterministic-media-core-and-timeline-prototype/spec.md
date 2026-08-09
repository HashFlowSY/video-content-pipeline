# Phase 2: Deterministic Media Core and Timeline Prototype

Type: enhancement
Status: resolved
Labels: enhancement
Phase: 2
Published: 2026-08-08
Completed: 2026-08-09

## Problem Statement

The project currently has a reproducible local runtime and an intentionally
small environment-check command, but it has no deterministic media-time or
subtitle-evidence core. Phase 3 cannot safely build source intake, ASR, OCR,
or a user-facing plan workflow on top of unproven assumptions about stream
time, container metadata, subtitle validity, and rolling captions.

Media inputs commonly expose disagreements between stream metadata, container
duration, actual decodable intervals, encoder PTS origins, and subtitle timing.
Those disagreements become correctness and auditability failures if the system
silently guesses a duration, clamps negative PTS, rewrites overlapping cues,
or removes repeated words based on similarity. A subtitle track that is only
partly valid is also not trustworthy evidence: publishing its surviving cues
would hide the missing portion behind plausible-looking output.

Phase 2 must therefore create a small, deterministic, library-only foundation
that proves the time and subtitle contracts with project-owned synthetic media
fixtures. It must make every source-time transformation, validation failure,
and presentation-only correction traceable while leaving user media, network
access, models, and source intake for later phases.

## Solution

Deliver a dependency-free Phase 2 core with exact time coordinates, typed
FFprobe evidence, observed stream coverage, atomic subtitle-track validation,
immutable cue layers, and conservative rolling-caption handling. The core
will be exercised first through unit tests and later through retained,
hash-pinned synthetic fixtures after a separate fixture-generation command
plan receives explicit approval.

The result is an internal library boundary rather than a media-facing command.
It will preserve signed source PTS and raw subtitle evidence, derive usable
Part-relative and collection-facing coordinates without inventing gaps, reject
invalid tracks as a whole, and emit only presentation changes that have an
exact local proof. Its behavior will be observable through structured values,
diagnostics, and deterministic serializations, providing a safe foundation for
subsequent phases without implying that Phase 3 capability already exists.

## User Stories

1. As a Phase 2 engineer, I want exact rational time values instead of
   accumulated floating-point seconds, so that repeated conversions and
   cross-Part translations remain mathematically stable.

   Acceptance criteria:

   - A time value can retain an exact numerator and denominator relationship.
   - Equality, ordering, addition, subtraction, and translation do not depend
     on floating-point rounding.
   - Test cases demonstrate that repeated transformations produce the same
     exact result as the direct transformation.

2. As a media-evidence consumer, I want signed `RawPtsTime` preserved exactly,
   so that edit-list offsets and decoder pre-roll are not hidden by a
   normalization step.

   Acceptance criteria:

   - Negative raw PTS values are accepted as source evidence.
   - No validation or mapping rule clamps a negative raw PTS to zero.
   - Derived coordinates retain a traceable relationship to the original raw
     PTS and time base.

3. As a timeline consumer, I want all internal ranges to use half-open
   intervals, so that adjacent boundaries can be compared without ambiguity.

   Acceptance criteria:

   - Every valid interval has an exact start strictly before its exact end.
   - Adjacent intervals may meet at one boundary without being treated as an
     overlap.
   - Zero-length or inverted subtitle intervals are rejected with structured
     diagnostics.

4. As a subtitle exporter, I want `PartRelativeTime` to begin at a Part's
   observed coverage start, so that per-Part subtitle timestamps are non-
   negative without changing source authority.

   Acceptance criteria:

   - `PartRelativeTime` is calculated by translating `RawPtsTime` by the
     Part's coverage start.
   - A valid Part-relative subtitle boundary is non-negative.
   - The original raw coordinate remains recoverable and authoritative.

5. As a collection consumer, I want `CollectionVirtualTime` to concatenate
   ordered Parts by observed coverage span, so that encoder-origin PTS gaps and
   unrelated container duration do not create artificial collection gaps.

   Acceptance criteria:

   - The first ordered Part starts at collection virtual time zero.
   - Each following Part starts exactly at the preceding Part's coverage
     endpoint.
   - The mapping preserves a hard Part boundary even when virtual time is
     contiguous.

6. As a maintainer reviewing multi-Part behavior, I want Part boundaries never
   to authorize cue merging, so that a compact collection view does not erase
   the source structure.

   Acceptance criteria:

   - Cues retain their owning Part throughout parsing, normalization,
     presentation, and serialization.
   - Identical or adjacent cue text on two Parts is not merged solely because
     their collection times meet.
   - Cross-Part mappings remain deterministic for nonzero and negative source
     PTS origins.

7. As a media-inspection consumer, I want the original FFprobe JSON retained
   unchanged as a `ProbeDocument`, so that decisions can be audited against
   the exact observed tool evidence.

   Acceptance criteria:

   - The raw JSON document is preserved independently of typed interpretation.
   - The system does not replace raw JSON with reformatted text or a lossy
     summary.
   - Fixture evidence can associate a probe document with its tool provenance
     and expected content hash.

8. As an implementation engineer, I want a typed `ProbeProjection` of known
   FFprobe fields, so that downstream time and coverage decisions use explicit
   values rather than ad hoc JSON traversal.

   Acceptance criteria:

   - Unknown JSON fields do not invalidate a probe document and remain outside
     the typed decision surface.
   - Missing or invalid required fields yield a structured `probe_invalid`
     diagnostic.
   - No human-readable probe output, regular expression, or metadata-duration
     fallback is used to invent a required value.

9. As a validation consumer, I want `StreamCoverage` derived only from
   observed `DecodedInterval` values, so that coverage reflects decodable
   evidence rather than container claims.

   Acceptance criteria:

   - Coverage is the exact outer envelope from the minimum observed start to
     the maximum observed end.
   - Internal gaps are preserved as diagnostics instead of silently being
     filled or converted into separate Parts.
   - A missing required boundary makes coverage indeterminate and records the
     applicable diagnostic.

10. As a subtitle-track evaluator, I want SRT and VTT parsed into structured
    cue evidence, so that syntax, time values, source order, and source text
    can be validated consistently.

    Acceptance criteria:

    - Valid SRT and VTT tracks produce `RawCue` records with original text,
      exact time, source ordinal, Part, and track identity.
    - The parser preserves source text rather than treating parsed cues as
      editable display text.
    - Invalid syntax or invalid timing produces diagnostics that identify the
      failing track and cue context.

11. As a downstream evidence consumer, I want subtitle tracks to be atomic,
    so that a single invalid cue cannot be hidden by partial output.

    Acceptance criteria:

    - Every cue must parse and pass duration, ordering, and required coverage
      validation before the track is accepted.
    - One failure marks the entire track `invalid` and makes it unavailable for
      normalized or presentation output.
    - The raw track remains retained for audit, and no automatic repair or
      partial recovery is performed.

12. As a subtitle validator, I want cue bounds checked against determinate
    stream coverage, so that subtitle evidence is not accepted against an
    unknowable media range.

    Acceptance criteria:

    - A cue whose exact interval lies outside required observed coverage is
      invalid.
    - A track that needs a coverage boundary which is indeterminate cannot
      pass validation.
    - Metadata duration cannot substitute for an unavailable coverage boundary.

13. As an auditor, I want distinct immutable `RawCue`, `NormalizedCue`, and
    `PresentationCue` layers, so that source evidence, lossless formatting,
    and display-only corrections cannot be conflated.

    Acceptance criteria:

    - `RawCue` retains original text, timing, source ordinal, Part, and track
      identity.
    - `NormalizedCue` preserves every token while making only lossless
      canonical-format changes.
    - `PresentationCue` references its source-token provenance for every
      omitted display token and never mutates either prior layer.

14. As a consumer of simultaneous speech, I want overlapping subtitle cues
    preserved, so that valid concurrent evidence is not destroyed merely to
    make a display look serial.

    Acceptance criteria:

    - Cue ordering is stable by `(start, end, source_ordinal)`.
    - Overlap itself is not a validation failure.
    - No rule trims, merges, shifts, or reorders a cue solely to remove an
      overlap.

15. As a subtitle formatter, I want lossless normalization to retain every
    token, so that formatting cleanup cannot become an untracked text edit.

    Acceptance criteria:

    - Normalization changes only canonical formatting that does not remove or
      rewrite tokens.
    - Token identity and ordering remain available for provenance and later
      exact comparisons.
    - Tests distinguish harmless formatting normalization from presentation
      token omission.

16. As an export consumer, I want exact cue time serialized outward to
    milliseconds, so that every positive source interval remains represented
    in SRT and VTT output.

    Acceptance criteria:

    - Export floors an exact start and ceils an exact end in milliseconds.
    - A positive exact interval shorter than one millisecond still serializes
      as a positive millisecond interval.
    - The serialization envelope is identifiable as a derived export value and
      never replaces exact time or raw PTS as source authority.

17. As a reader of rolling captions, I want the presentation layer to remove
    only tokens with an exact local rolling-display proof, so that readable
    output improves without deleting possible speech.

    Acceptance criteria:

    - The candidate cues are stable-order adjacent and belong to the same Part
      and subtitle track.
    - Their normalized token overlap is exact and contiguous, the later cue
      strictly extends the earlier one, and the time intervals overlap or are
      contiguous.
    - Each omitted presentation token records its source-token range and a
      correction reason.

18. As a conservatism reviewer, I want exact full-text duplicates removed only
    when their start and end boundaries are also exactly equal, so that similar
    display artifacts do not erase valid timing variation.

    Acceptance criteria:

    - Text equality alone is insufficient for duplicate deletion.
    - The comparison uses exact normalized text and exact time endpoints.
    - Raw and normalized evidence remain available even when a presentation
      duplicate is omitted.

19. As a transcript consumer, I want ambiguous, fuzzy, semantic, or genuine
    spoken repetition retained as `possible_duplicate`, so that uncertainty
    remains visible instead of being converted into data loss.

    Acceptance criteria:

    - Edit distance, semantic similarity, and fuzzy matching do not authorize
      text removal.
    - Similar text without the full local proof remains in presentation output.
    - The system exposes a structured `possible_duplicate` indication when the
      evidence is ambiguous.

20. As an audit reviewer, I want structured diagnostics and correction
    provenance at every evidence boundary, so that invalidity, indeterminacy,
    and display changes can be explained without reconstructing hidden logic.

    Acceptance criteria:

    - Probe failures, coverage indeterminacy, invalid tracks, invalid cues,
      and possible duplicates have machine-readable reason categories.
    - Presentation corrections identify their originating cue and token range.
    - Diagnostics do not cause the implementation to fabricate a recovered
      value or partial valid output.

21. As a test maintainer, I want deterministic synthetic media fixtures with
    recipes, hashes, expected probe evidence, and tool provenance, so that
    integration behavior is reproducible without touching user media.

    Acceptance criteria:

    - Every retained fixture is project-owned and created from a versioned
      declarative recipe.
    - Each fixture records its producing tool identity, expected probe
      document, and content hash.
    - Fixture creation occurs only after an explicit command-and-resource plan
      is approved.

22. As a normal test runner, I want retained fixtures consumed read-only, so
    that ordinary tests do not depend on live FFmpeg behavior or delete audit
    evidence.

    Acceptance criteria:

    - Normal unit and integration tests never regenerate a media fixture.
    - Normal tests never delete fixtures, caches, or failed outputs.
    - A hash mismatch or missing retained fixture fails visibly rather than
      silently recreating the artifact.

23. As a project maintainer, I want the Phase 2 core to use only the standard
    library and already locked quality tooling, so that this prototype does not
    silently widen its dependency or download surface.

    Acceptance criteria:

    - The feature does not add runtime packages, package downloads, or lockfile
      changes.
    - Rational time, JSON handling, subtitle parsing, hashing, and controlled
      process invocation stay inside the approved dependency boundary.
    - Any future request for a new dependency is treated as a separate decision
      and authorization.

24. As a safety reviewer, I want Phase 2 to remain library-only, so that its
    deterministic fixture work cannot be mistaken for user-media support.

    Acceptance criteria:

    - The existing environment-check command remains the only public CLI
      behavior.
    - No local-file intake, URL handling, browser data, media planning command,
      or source-access API is introduced.
    - Internal media operations are limited to approved project-owned synthetic
      fixture work.

25. As an authorized implementation agent, I want a concrete pre-execution
    command plan before any runtime or media action, so that the execution
    boundary remains inspectable and user-controlled.

    Acceptance criteria:

    - The plan lists intended file changes, exact test commands, any Python
      execution, expected resources, and rollback or retention treatment.
    - A fixture-generation plan separately identifies FFmpeg and FFprobe
      paths, versions, expected output sizes, and expected retained artifacts.
    - No Python command, FFmpeg command, FFprobe command, fixture generation,
      package action, or real-media access runs until the applicable plan is
      explicitly approved.

## Implementation Decisions

- Model all authoritative temporal values as exact rational values; use no
  floating-point value as a canonical coordinate.
- Preserve signed `RawPtsTime` as source evidence. Derive
  `PartRelativeTime` and `CollectionVirtualTime` only through exact
  translations from observed Part coverage.
- Use half-open intervals throughout. A hard Part boundary remains visible
  even when collection virtual time is compact and contiguous.
- Retain raw FFprobe JSON as `ProbeDocument` and create a typed
  `ProbeProjection` only from known required fields. Unknown fields are
  tolerated; required missing or invalid fields are diagnostics, not guesses.
- Derive `StreamCoverage` from observed `DecodedInterval` boundaries only.
  Record internal gaps separately, and declare coverage indeterminate whenever
  a required boundary is unknown.
- Parse SRT and VTT into atomic subtitle-track candidates. Do not repair,
  salvage, or publish a partial track after any cue fails validation.
- Keep `RawCue`, `NormalizedCue`, and `PresentationCue` immutable and
  separately traceable. Lossless normalization retains all tokens; only the
  presentation layer may omit a proven rolling-display token.
- Preserve valid overlap and use `(start, end, source_ordinal)` as the stable
  cue ordering key.
- Restrict rolling-caption de-duplication to an exact local proof. Fuzzy,
  semantic, and edit-distance similarity are diagnostics only and never a
  deletion authority.
- Serialize subtitle times by flooring exact starts and ceiling exact ends in
  milliseconds. Treat the result as a derived serialization envelope.
- Keep fixture generation separate from normal tests. Retain generated
  project-owned fixtures with recipe, hash, expected probe evidence, and tool
  provenance once explicitly authorized.
- Keep the Phase 2 runtime dependency-free and library-only. Existing public
  environment behavior remains unchanged; source intake and media-facing CLI
  work belong to Phase 3.
- Make diagnostics, correction provenance, and retention behavior first-class
  outputs. Absence of evidence must remain observable rather than being hidden
  by a fallback.

## Testing Decisions

- Establish one library-level deterministic seam that accepts structured probe
  evidence and subtitle text and returns typed time, coverage, cue, and
  diagnostic results. Unit tests must exercise that seam without a CLI,
  subprocess, fixture generation, or user media.
- Develop time, coordinate, probe, coverage, subtitle parsing, normalization,
  presentation, and de-duplication behavior test-first. Each rule in the user
  stories needs both a success case and a failure or ambiguity case where one
  exists.
- Cover signed and nonzero raw PTS, exact rational arithmetic, half-open
  boundaries, compact multi-Part translations, and retained hard Part
  boundaries.
- Cover probe documents with unknown tolerated fields, missing required
  fields, invalid required values, contradictory metadata, and
  coverage-indeterminate conditions.
- Cover observed stream intervals with different audio and video starts,
  priming-like offsets, internal gaps, incomplete endpoints, and no
  duration-metadata fallback.
- Cover valid SRT and VTT parsing, malformed syntax, zero or negative cue
  duration, out-of-coverage cue bounds, invalid atomic-track rejection,
  overlapping cues, stable ordering, and lossless normalization.
- Cover exact millisecond outward serialization, including a positive exact
  interval shorter than one millisecond.
- Cover full exact duplicates, valid rolling accumulation, real spoken
  repetition, different-time similar text, and ambiguous repetition. Assert
  both visible presentation text and correction or `possible_duplicate`
  provenance.
- Add fixture-backed integration coverage only after the separate fixture plan
  has been approved and retained synthetic fixtures exist. Verify fixture hash,
  expected raw probe evidence, typed projection, coverage, coordinate mapping,
  and SRT/VTT round-trip behavior.
- Continue to run the repository's existing unit, integration, acceptance,
  lint, type-check, and environment-gate layers after the exact command plan
  is approved. This specification alone authorizes no command execution.

## Out of Scope

- User-provided media, local-file intake, URLs, source copying, browser
  sessions, cookies, credentials, and network media access.
- Any new media-facing CLI, including `vcp plan <source>`.
- ASR, forced alignment execution, VAD, diarization, OCR, LLMs, model
  downloads, model loading, paid services, and real-world media testing.
- New third-party runtime dependencies, lockfile changes, package downloads,
  global installations, or implicit environment selection.
- Parsing human-readable FFprobe output, using regular expressions to infer
  probe fields, or substituting container or stream duration metadata for an
  unknown coverage boundary.
- Fuzzy, semantic, or edit-distance subtitle deletion; mutation of raw or
  normalized cue evidence; automatic overlap repair; cross-Part cue merging.
- Regenerating fixtures during ordinary tests or automatically deleting
  fixtures, caches, temporary data, or failed outputs.
- Declaring the project production validated or advancing the project to Phase
  3 without a separate authorization and adopted plan.

## Further Notes

- This issue synthesizes the adopted Phase 2 specification, atomic task list,
  domain glossary, and ADR-0001 through ADR-0014. It introduces no conflicting
  domain term or architectural decision.
- `ready-for-agent` means the behavioral requirements are complete enough for
  an agent to prepare the next authorized implementation action. It does not
  waive the separate pre-implementation command-plan approval required by the
  Phase 2 boundary.
- The immediate next action is to submit the exact incremental implementation,
  test, and fixture-generation command plan for approval. Until that approval,
  this issue remains documentation and planning only.
- The repository is configured to use local Markdown issues because it has no
  configured remote issue tracker. Future tracker changes can be made through
  the repository's agent-skill configuration without changing this Phase 2
  contract.

## Comments

2026-08-09: Phase 2 completed and verified. All 12 child tickets are resolved;
the final project-local gates recorded 49 passing tests plus passing Ruff,
format, Mypy, and environment checks. The retained fixtures are project-owned
synthetic evidence only. Phase 3 is not started or authorized by this status
sync and requires a separately adopted plan plus explicit user authorization.
