# 10 — Define text_semantics and its real adapter (Qwen3-4B via mlx-lm)

**What to build:** The phase's one new capability. Define `text_semantics`
in the text-analysis context: registry-evaluated like every other
capability (eligibility, acquisition state, credential-gate rejection),
with the Controlled offline text adapter permanently barred from
real-model status (ADR 0037 lineage) and retained as the deterministic
test path. Real adapter: Qwen3-4B-Instruct-2507-8bit through the Model
runtime subprocess with mlx-lm — deterministic sampling (temp 0, fixed
seed), bounded KV memory, versioned prompt template. Its proposals flow
into the existing adjudication: model-proposed boundaries and content are
validated against revalidated cue evidence; invalid proposals are
retained diagnostics, never formal output. A real-model
`model_acquisition_required` outcome joins the Text analysis report
statuses as the docstrings always promised. Glossary and CONTEXT-MAP
updated (term ownership: text-analysis).

**Blocked by:** 04, 05
**Status:** done
**Labels:** ready-for-agent

- [x] Capability evaluation over the registry yields
      eligible/acquisition-required/ineligible states with the same
      semantics as the audio capabilities (unit tests mirror prior art)
- [x] Offline adapter can never satisfy the real-model path (explicit
      test)
- [x] Real adapter subprocess request carries model path, prompt version,
      sampling, KV bound; response projects into the existing text-model
      output projection (stub unit tests + offline real-engine
      integration test)
- [x] Malformed model JSON becomes retained diagnostics + typed status,
      never a crash or fabricated content
- [x] Glossary/CONTEXT-MAP entries added; full suite green within budget
