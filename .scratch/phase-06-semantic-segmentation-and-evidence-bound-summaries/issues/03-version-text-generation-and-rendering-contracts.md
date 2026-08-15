# 03 -- Version text generation and rendering contracts

**What to build:** The controlled adapter, prompts, schema, evidence rules, and
Markdown rendering contract have explicit immutable identities suitable for a
future real-model boundary.

**Blocked by:** 02 -- Add text-analysis CLI and complete revalidation.

**Status:** resolved
**Labels:** ready-for-agent

- [x] Add project-managed versioned prompt templates, Text-model output
  projection schemas, evidence-rule records, and controlled-adapter identity.
- [x] Reject whole invalid or incomplete projections as
  `model_output_invalid`; retain raw output without defaults or partial
  formal output.
- [x] Deterministically render Markdown from verified JSON, retaining renderer
  version and hash while keeping JSON authoritative.

**Implementation:** `src/video_content_pipeline/text_contracts.py` binds the four
versioned artifacts under `config/text-analysis/` against
`config/text-analysis-rules.json`, projects raw Text-model output through the
versioned output schema, and renders the deterministic Markdown rendition.
`analyze_text` revalidates the contracts, records their hash evidence under
`text_generation_contracts`, and writes `text-analysis-report.md` into the
immutable workspace with its renderer version and hash under `rendered_report`.
