# 02 -- Add text-analysis CLI and complete revalidation

**What to build:** A user can start `vcp analyze-text` and resume only through
`vcp resume-text-analysis`, with exact input revalidation before analysis.

**Blocked by:** 01 -- Establish immutable text-analysis workspace.

**Status:** resolved
**Labels:** ready-for-agent

- [x] Accept confirmed RunPlan and subtitle report identities, plus an optional
  Audio analysis report, through JSON-only public command contracts.
- [x] Revalidate source, subtitle selection, cue rules, prompts, schemas,
  evidence rules, adapter identity, and optional audio-report bindings; drift
  blocks a new attempt.
- [x] Require an explicit report ID and user decision for resumption; no
  automatic resume or identity-changing recovery is permitted.
