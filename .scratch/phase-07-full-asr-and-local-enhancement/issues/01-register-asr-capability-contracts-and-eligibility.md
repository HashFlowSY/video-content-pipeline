# 01 -- Register ASR capability contracts and candidate eligibility

**What to build:** Provider-neutral `asr_primary` and `asr_review` capability
contracts evaluated offline from `models/registry.json`, including the
Independent-model review requirement and the `model_acquisition_required`
result when no eligible capability exists.

**Blocked by:** None -- can start immediately.

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] Add `capability: "asr_primary"` and `capability: "asr_review"` handling
  to the registry candidate matrix with the same eligibility fields as
  Phase 5 (https official source, approved license, pinned revision,
  `asset_sha256`, offline runtime, no credentials, no telemetry, dependency
  plan, resource envelope ≤ 24 GiB).
- [ ] Register Qwen3-ASR-1.7B (`asr_primary`) and WhisperKit / Whisper
  large-v3 (`asr_review`) as research candidates; no download, no execution.
- [ ] Enforce the Independent-model review requirement: the review capability
  must resolve to a different model identity than the primary.
- [ ] Produce an immutable `model_acquisition_required` result with no
  transcription evidence when no eligible capability is available.
