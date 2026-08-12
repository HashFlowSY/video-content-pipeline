# 07 -- Retain text-attempt state and diagnostics

**What to build:** Text-generation attempts, pauses, resources, diagnostics,
and synthetic human-review records are immutable and auditable.

**Blocked by:** 02 -- Add text-analysis CLI and complete revalidation; 03 -- Version text generation and rendering contracts.

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] Record prompt/rendered-prompt, input-cue manifest, adapter identity,
  sampling, raw output, projection, hashes, and resource measurement per
  immutable attempt.
- [ ] Enforce no automatic retry and decision pauses only for model/configuration
  identity changes or the future 24 GiB resource envelope.
- [ ] Keep raw outputs restricted to workspace diagnostics and prove
  append-only synthetic human-review record shape without creating
  `human_verified` results.
