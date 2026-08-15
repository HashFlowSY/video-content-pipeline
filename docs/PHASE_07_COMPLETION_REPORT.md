# Phase 7 Completion Report

## Status

Phase 7, full ASR transcription and local enhancement, is completed and
verified in the project-local offline environment. Per the phase exit gate,
this is an engineering pass only: domain quality is not verified because no
real video was processed and no real ASR model was downloaded or invoked. The
project remains in engineering development; `real_world_testing` and
`production_validated` are both `false`.

## Delivered Scope

- `vcp transcribe` and `vcp resume-transcription` create and resume immutable
  transcription workspaces for the full-ASR path, with exact revalidation of
  the confirmed RunPlan, SourceArtifact hashes, retained subtitle report, and
  the required Audio analysis report; start preconditions (retained
  subtitle-unavailable handoff or an explicit whole-selection upgrade) forbid
  any automatic ASR trigger, and the Full-ASR resource confirmation pause and
  24 GiB resource-envelope pause are recorded immutable states.
- Provider-neutral `asr_primary` and `asr_review` capability contracts are
  evaluated offline from the model registry with the Phase 5 eligibility
  fields; the Independent-model review requirement excludes a same-model retry
  from counting as independent review, and no eligible capability yields an
  immutable `model_acquisition_required` result.
- ASR text enters only through versioned Controlled offline ASR adapter and
  output-projection contracts under `config/transcription/`; an incomplete or
  schema-invalid output is `model_output_invalid` and fails the attempt, and
  raw output is retained as restricted audit-only evidence.
- Projected cues pass deterministic timing gates on the canonical timeline
  (exact rational times inside actual stream coverage, monotonic order,
  half-open intervals, no processing duplication, plausible duration-to-text
  relation); rejects are never repaired and carry structured per-cue reasons.
- Suspicious intervals come only from the six versioned deterministic
  detectors (VAD coverage, confidence, repetition, language switching,
  numbers/entities, coverage checks) with conservative defaults and
  `calibration_required` marks; the second ASR reviews only those intervals by
  default, and deterministic arbitration retains unresolved conflicts as
  `review-needed` with both candidates preserved.
- Only a complete, coverage-checked full-ASR run may emit verbatim artifacts
  and perform the Audio-completeness upgrade.
- `vcp enhance` and `vcp resume-enhancement` merge ASR cues into user-named
  Parts, ranges, or cues by gate-checked interval replacement: originals stay
  on gate failure with recorded reasons, every enhanced cue carries
  `subtitle_track` or `asr` provenance, and enhanced artifacts never claim
  verbatim completeness (`audio_completeness=not_verified` is hard-wired).
- Retained text-analysis reports can be deserialized back into domain objects
  with hash verification, affected Parts are selected deterministically from
  the changed cue basis, and a new immutable text-analysis attempt regenerates
  affected Parts while carrying unaffected Parts forward with explicit
  provenance links, recomputing chapters and collection summaries.
- The Phase 7 CLI contract is proved offline end to end; the machine-checkable
  exit gates and file inventory are recorded in
  [PHASE_07_INVENTORY.json](PHASE_07_INVENTORY.json) (26 confirmed summary
  gates mapped to the phase plan's 退出门禁).

## Recorded Deviations

- The phase plan's original "validate ASR candidates locally" work items 1–2
  were rewritten, per the approved specification, into offline capability
  contracts and registry eligibility evaluation. Real local-run, language, and
  memory validation is deferred to an explicitly authorized model-prototype
  session before real-world testing.
- The ASR research candidates in the model registry deliberately leave
  eligibility fields unpopulated: truthful values (asset hashes, measured
  resource envelopes) require the real model assets the offline boundary
  forbids acquiring.

## Final Verification

The final commands ran from the project root through the project `.venv`.

| Gate | Result |
| --- | --- |
| `pytest -q` | 520 passed in 1.69s |
| `ruff check src tests` | All checks passed |
| `ruff format --check src tests` | 80 files already formatted |
| `mypy src` | Success: no issues found in 34 source files |
| Phase inventory summary | 26 exit-gate booleans, all `true` |

Closure note: per-ticket intermediate gate outputs were not retained by the
implementing sessions; verification is anchored to the current-head run above.
Ticket status bookkeeping (eight of ten files) and the `project-state.json`
`completed` transition were performed at closure on the maintainer's explicit
instruction, after the inventory had recorded all exit gates as confirmed.

Verification used only project-owned synthetic fixtures and Controlled offline
ASR adapters. It did not download, install, or invoke a model, access user
media or a network, invoke a paid API, write `outputs/`, or mark the project
`production_validated`.
