# Phase 4 Completion Report

## Status

Phase 4, subtitle-track-priority processing, is completed and verified in the
project-local offline environment. The project remains in engineering
development; `real_world_testing` and `production_validated` are both `false`.

## Delivered Scope

- A `vcp subtitles` CLI contract that starts from a confirmed RunPlan and
  revalidates SourceArtifacts, FFmpeg identity, and subtitle rules before
  processing.
- Immutable Subtitle candidate workspaces, bounded extraction attempts, strict
  source retention, decoding policy, exact cue-time mapping, and atomic track
  validation.
- Source and readable subtitle candidates for supported text tracks, including
  explicit format-projection loss, conservative markup cleanup, and correction
  provenance.
- Explicit pause/resume selection when valid tracks are ambiguous; no implicit
  choice by disposition or stream order.
- Retained handling for unsupported tracks, ambiguous encoding, revalidation
  drift, disk limits, output limits, and interrupted attempts.
- Partial multi-Part collection reporting, caption-time coverage, and explicit
  ASR-planning handoff with no model, ASR, or RunBundle work.
- Offline unit, integration, and public CLI-contract proof using retained
  synthetic evidence and controlled external-tool substitutes.

## Final Verification

The final command ran from the project root after activating `.venv` and
passing `scripts/require-project-venv.sh`.

| Gate | Result |
| --- | --- |
| `pytest -q` | 136 passed in 0.87s |
| `ruff check src tests` | passed |
| `ruff format --check src tests` | 34 files already formatted |
| `mypy src` | passed; no issues in 16 source files |
| Environment gate | passed before the Python checks |

This verification did not access user media, make network requests, execute
FFmpeg, download a model or dependency, invoke a paid API, or change the
production-validation state.

## Retained Evidence And Handoff

All six current Phase 4 tracer-bullet tickets are resolved. The superseded
seventh ticket remains marked `wontfix` as an audit record. The Phase 4
inventory records design, implementation, external skill reads, all validation
commands, and the final result. No cleanup was performed.

Phase 5 may build on the completed subtitle candidates but must separately
authorize forced alignment, VAD, diarization, model selection, model download,
and any real-media testing. Phase 4 does not establish real-world subtitle
accuracy or production readiness.
