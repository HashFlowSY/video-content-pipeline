# 07 -- Retain text-attempt state and diagnostics

**What to build:** Text-generation attempts, pauses, resources, diagnostics,
and synthetic human-review records are immutable and auditable.

**Blocked by:** 02 -- Add text-analysis CLI and complete revalidation; 03 -- Version text generation and rendering contracts.

**Status:** resolved
**Labels:** ready-for-agent

- [x] Record prompt/rendered-prompt, input-cue manifest, adapter identity,
  sampling, raw output, projection, hashes, and resource measurement per
  immutable attempt.
- [x] Enforce no automatic retry and decision pauses only for model/configuration
  identity changes or the future 24 GiB resource envelope.
- [x] Keep raw outputs restricted to workspace diagnostics and prove
  append-only synthetic human-review record shape without creating
  `human_verified` results.

**Implementation:** `analyze_text` now composes an immutable `AttemptProvenance`
record (prompt + deterministically rendered prompt, input-cue manifest, adapter
identity, sampling-configuration hash, output-schema and evidence-rule hashes,
restricted raw-output and projection state, and an execution-resource
measurement) written into the immutable workspace. It evaluates the
future-real-model 24 GiB envelope from an optional `resource_plan` in the
versioned rules, retaining a resumable `resource_envelope_exceeded` report with a
`required_decision`; `resume_text_analysis` continues only that pause from an
explicit `resource_configuration_changed` decision as a fresh, non-overwriting
attempt (no automatic retry), while a model/configuration identity change stays a
revalidation-drift `failed`. `record_restricted_raw_output` keeps raw outputs as
hash-only `local_audit_only` pointers, and the new `text_review` module appends
sequence-numbered, immutable synthetic human-review records that label a scope
and never emit `human_verified`.
