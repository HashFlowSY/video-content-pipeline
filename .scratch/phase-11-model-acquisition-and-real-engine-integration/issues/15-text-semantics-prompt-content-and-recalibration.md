# 15 — Fix the text_semantics real-model prompt (content + schema) and recalibrate

**What to build:** The ticket-13 prototype proved every capability on real zh+en
material except `text_semantics`, which returned `model_output_invalid` on both
languages. Root cause (diagnosed, retained in
`docs/phase-11-prototypes/maintainer-review.md`): the versioned prompt rendered by
`render_text_semantics_prompt` carries only cue *identities* — no transcript text
and no output schema — although the template states "PresentationCues … provided
verbatim". Qwen3-4B can only echo the cue id, which the unchanged Text-model
output projection correctly rejects. This is an adapter-completeness gap, not a
model-selection quality problem (the Qwen3-4B-Instruct-2507-8bit choice is not
rejected).

Make the real text adapter give the model what it needs to summarise:

- Render each available cue's **text** (verbatim, ADR-0037 offline adapter parity)
  and the **output schema** into the prompt, so the model has content to segment
  and the exact JSON shape to return.
- This changes the prompt content, so bump `prompt_template_version` and
  recalibrate the Qwen3 text-semantics decoding profile against the new version
  (ADR 0056: a prompt revision invalidates the bound identity). Keep the
  Controlled offline text adapter as the deterministic test path unchanged in
  meaning.
- Address cue granularity: the ticket-09 chunk-level asr_primary emits one cue per
  ≤5-minute VAD chunk, so a single-chunk clip yields one giant cue with nothing to
  segment. Provide finer (sub-chunk) cue granularity for semantic segmentation and
  for meaningful forced-alignment adoption — coordinate with the transcription
  context so the projection/gate contracts stay unchanged.
- Re-run the text_semantics prototype on the ticket-12 zh+en material and record a
  real segment-summary sample for maintainer confirmation, replacing the recorded
  gap.

**Blocked by:** 13
**Status:** open
**Labels:** ready-for-agent, follow-up

- [ ] Prompt includes verbatim cue text + the output schema; `prompt_template_version` bumped
- [ ] Qwen3 text-semantics calibration recalibrated to the new prompt version (ADR 0056)
- [ ] Finer cue granularity available so segmentation/alignment are meaningfully exercised
- [ ] text_semantics prototype re-run on real zh+en material; segment-summary sample maintainer-confirmed
- [ ] Full pytest gate stays green and within budget; offline adapters unchanged in meaning
