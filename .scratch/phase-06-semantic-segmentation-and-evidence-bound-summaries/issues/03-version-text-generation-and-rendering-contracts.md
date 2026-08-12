# 03 -- Version text generation and rendering contracts

**What to build:** The controlled adapter, prompts, schema, evidence rules, and
Markdown rendering contract have explicit immutable identities suitable for a
future real-model boundary.

**Blocked by:** 02 -- Add text-analysis CLI and complete revalidation.

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] Add project-managed versioned prompt templates, Text-model output
  projection schemas, evidence-rule records, and controlled-adapter identity.
- [ ] Reject whole invalid or incomplete projections as
  `model_output_invalid`; retain raw output without defaults or partial
  formal output.
- [ ] Deterministically render Markdown from verified JSON, retaining renderer
  version and hash while keeping JSON authoritative.
