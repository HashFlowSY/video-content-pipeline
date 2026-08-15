# 10 -- Prove the Phase 7 CLI contract offline

**What to build:** Integration and acceptance coverage proving every Phase 7
contract with hash-pinned synthetic fixtures and Controlled offline ASR
adapters only, inside the Phase 7 offline transcription-verification boundary.

**Blocked by:** 06, 07, 09

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] Guarantees block asserts `model_execution`, `model_acquisition`,
  `network_access`, and `outputs_publication` all `not_attempted`.
- [ ] Cover: revalidation drift, both pauses and their resumes, precondition
  enforcement (no auto-trigger, resource confirmation), projection
  invalidity, every gate, all six detectors, interval-scoped review,
  arbitration and retained conflicts, same-model-retry exclusion,
  verbatim/enhanced semantics separation, per-cue provenance, gate-failure
  fallback, affected-Part selection, carry-forward provenance, immutability,
  statuses, and hashes.
- [ ] Mixed Chinese/English fixtures, multi-Part collections, and
  subtitle-unavailable sources are all represented.
- [ ] Update the phase inventory summary with machine-checkable
  `*_confirmed` exit-gate booleans mapped to the plan's 退出门禁 list.
