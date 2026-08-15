# 01 -- Register ASR capability contracts and candidate eligibility

**What to build:** Provider-neutral `asr_primary` and `asr_review` capability
contracts evaluated offline from `models/registry.json`, including the
Independent-model review requirement and the `model_acquisition_required`
result when no eligible capability exists.

**Blocked by:** None -- can start immediately.

**Status:** done
**Labels:** ready-for-agent

- [x] Add `capability: "asr_primary"` and `capability: "asr_review"` handling
  to the registry candidate matrix with the same eligibility fields as
  Phase 5 (https official source, approved license, pinned revision,
  `asset_sha256`, offline runtime, no credentials, no telemetry, dependency
  plan, resource envelope ≤ 24 GiB).
- [x] Register Qwen3-ASR-1.7B (`asr_primary`) and WhisperKit / Whisper
  large-v3 (`asr_review`) as research candidates; no download, no execution.
- [x] Enforce the Independent-model review requirement: the review capability
  must resolve to a different model identity than the primary.
- [x] Produce an immutable `model_acquisition_required` result with no
  transcription evidence when no eligible capability is available.

## Comments

Implemented 2026-08-15. The Phase 5 eligibility gate was extracted into a
shared, context-neutral `src/video_content_pipeline/capabilities.py`
(`candidate_eligibility`, `candidate_eligibility_evidence`,
`parse_candidate_matrix`, and the 24 GiB / SHA-256 / candidate-id constants);
`audio_analysis` now consumes it and ignores foreign (`asr_*`) capabilities in
the shared registry rather than rejecting the whole file. The new
`src/video_content_pipeline/transcription.py` owns
`evaluate_asr_capabilities`, the provider-neutral `asr_primary` / `asr_review`
availabilities, the Independent-model review resolution
(`available` / `review_same_model_as_primary` / `no_eligible_primary` /
`no_eligible_review`), and the immutable `model_acquisition_required` result
carrying no transcription evidence and a `not_attempted` guarantees block.
Qwen3-ASR-1.7B (`qwen3-asr-1-7b`) and Whisper large-v3 (`whisper-large-v3`)
are registered as bare research candidates in `models/registry.json`, matching
the existing VAD / forced-alignment precedent (no fabricated asset hashes or
dependency plans), so they resolve to `model_ineligible` until a separately
authorized acquisition step pins them.

**Recorded deviation.** The phase spec's Capability And Eligibility Contract
describes candidates "registered ... with license, source, revision, and
eligibility fields." Those fields are deliberately left unpopulated here:
truthfully pinning a `revision`, an `asset_sha256`, and a dependency plan
requires inspecting the real model asset, which the phase's offline boundary
forbids (no download, no execution). Populating them with invented values would
fabricate facts about external artifacts. Field population is therefore
deferred, alongside the spec's already-recorded local-run / language / memory
validation, to the separately authorized model-prototype session. The
eligibility *gate* checks every one of those fields (see
`capabilities.candidate_eligibility`); only their registry-side values wait.

Covered by
`tests/unit/test_transcription_capabilities.py`,
`tests/unit/test_capabilities.py`, and a `test_phase_5_cli_contract.py`
regression that audio ignores registered transcription capabilities. No model
was downloaded or executed.
