# 03 -- Version Controlled offline ASR adapters and output projections

**What to build:** Versioned Controlled offline ASR adapter descriptors
(mirroring the Phase 6 text adapter: hash-pinned input and output fixtures,
implementation and schema versions) and the versioned ASR output projection
that is the only entry point for ASR text.

**Blocked by:** 01

**Status:** done
**Labels:** ready-for-agent

- [x] Define adapter descriptor JSON under `config/transcription/` with
  version, fixture hashes, and projection-schema version; verify hashes on
  load, project-relative non-escaping paths only.
- [x] Define the ASR output projection: cues with exact rational times, text,
  optional per-token confidence, language spans; incomplete or schema-invalid
  output is `model_output_invalid` and fails the attempt.
- [x] Retain raw output as restricted local audit evidence, excluded from
  formal reports.
- [x] Symmetric input hashing (input manifest sha256) proving the fixture
  matches revalidated inputs.

## Comments

Implemented 2026-08-15 as a new module
`src/video_content_pipeline/transcription_contracts.py`, mirroring the Phase 6
text adapter (`text_contracts` + `text_generation`) for the Transcription
Context. This is the single auditable entry point for ASR text; tickets 04+
(gating projected cues, detection, arbitration) build on it.

**Shipped versioned config** under `config/transcription/`:
`transcription-rules.json` (the single version-of-truth naming
`projection_schema_version` + `controlled_adapter_identity`, extensible for the
ticket 05/06 suspicion-rule and arbitration-rule versions),
`asr-projection-schema.json`, and `controlled-adapter.json`. The shipped adapter
carries no bound `fixture` block -- fixtures bind per attempt/test exactly as the
Phase 6 controlled-adapter does -- so no synthetic input/output is pinned into
committed config.

**Contracts.** `revalidate_asr_contracts` binds the two versioned identities to
hash evidence and rejects a version mismatch, a missing artifact, or an adapter
that names a stale projection-schema version (`asr_projection_schema_invalid` /
`controlled_asr_adapter_invalid`). `load_controlled_asr_fixture` reads the
optional `fixture` block, hash-verifies the output-fixture bytes, confines the
path to project-relative non-escaping (`..`/absolute rejected), validates the
capability against `("asr_primary", "asr_review")`, and carries the bound
`input_fixture_sha256` (`controlled_asr_fixture_invalid` on any violation).

**Projection.** `project_asr_output` is a typed projector (not a generic
envelope check, because ASR cues carry rational times, tokens, and language
spans): each cue projects to a `ProjectedAsrCue` with a `HalfOpenInterval` of
`ExactTime`, `text`, optional `ProjectedAsrToken`s (optional per-token
confidence in `[0, 1]`), and `AsrLanguageSpan`s over half-open token-index
ranges (spans require the tokens they index, so mixed zh/en stays two adjacent
spans, never rewritten). Any incomplete or schema-invalid output rejects whole
as `model_output_invalid` with no partial projection and no defaults -- config
is our ground truth (raises), model output is untrusted (returns a state).

**Symmetric input hashing.** `asr_input_manifest_document` /
`asr_input_manifest_sha256` build a canonically-ordered manifest over the Audio
analysis report id plus the revalidated `(source_id, stream_index,
source_artifact_sha256)` inputs, so a fixture's `input_fixture_sha256` proves it
matches the revalidated inputs regardless of caller order.

**Restricted raw output.** `retain_restricted_raw_output` writes bytes once
(immutable; differing rewrite is `transcription_raw_output_conflict`) under
`<workspace>/restricted/asr/<capability>/<label>-raw-native-output.json`, apart
from the formal report tree, and the record is marked `restricted`/`audit_only`
so callers keep it out of formal artifacts.

Offline boundary held: no model download/execution, no `outputs/` write, no user
media read. Covered by `tests/unit/test_transcription_contracts.py`,
ruff + mypy(`src`) clean.

**Two-axis code review (Standards + Spec) applied.** Two findings converged and
were fixed: (1) *Standards, hard* -- `ASR_CAPABILITIES` had been re-declared,
duplicating the ticket-01 constant; it now lives once in `transcription_contracts`
(the lower layer, imported only by `evidence`/`planning`/`timecode`) and
`transcription.py` imports it, keeping a single source with no import cycle. (2)
*Spec headline + Standards Speculative-Generality* -- the `asr-projection-schema.json`
`cue`/`token`/`language_span` blocks were inert (the projector hardcoded every
rule), unlike the Phase 6 `output-schema` it mirrors, whose envelope content is
consumed. `revalidate_asr_contracts` now parses an `AsrProjectionRuleset` from the
schema document (malformed → `asr_projection_schema_invalid`), and
`project_asr_output` drives the declarative rules -- required cue/token/span
fields and the token confidence range -- from it, so the projection-schema version
is meaningful (new tests prove narrowing the confidence range or adding a required
field changes projection behavior with no code change). Structural invariants
(half-open positive intervals, token-index bounds, nesting) stay in the typed
projector. Findings not actioned, with rationale: exception-based projection
control flow (justified by cue/token/span nesting depth vs. Phase 6's flat
envelope); inline `ExactTime` `{numerator, denominator}` serialization (pre-existing
repo pattern, the real fix belongs on `ExactTime`); and the echoed identity fields
on `AsrOutputProjection` (consumed downstream for the Independent-model review
requirement and attempt provenance). Full suite green (363).
