# Require model-specific text-semantics decoding calibration

Phase 11 defines `text_semantics` as the text-analysis Context's one model
capability (ADR 0036 keeps it provider-neutral). Its real engine is a large
instruction-tuned language model run through the Model runtime subprocess
(ADR 0055) with mlx-lm. Unlike the audio engines, an LLM cannot produce output at
all without a decoding configuration, and that configuration changes the output:
temperature, seed, generation length, and KV-cache bound are not interchangeable
across models, quantizations, or devices.

Until an offline decoding-calibration profile pins a model's decoding
configuration, the real engine does not run. A missing profile reports
`text_semantics_calibration_required`; the Controlled offline text adapter
(ADR 0037) remains the deterministic test path.

Each profile binds the exact model asset hash, inference backend and version,
quantization or precision, device class, and text-analysis rules fingerprint, plus
the versioned prompt-template identity it was calibrated for. A change to any bound
identity — including a prompt-template revision — invalidates the profile.

Decoding is deterministic: temperature 0 (greedy argmax) under a fixed seed, a
bounded generation length, and a bounded KV cache so peak memory stays within the
12 GiB envelope (ADR 0055 returns that memory to the OS on stage exit).

A profile qualifies the engine only for offline engineering verification. The
Chinese/English semantic quality of a selection is confirmed only by a later
maintainer sample review on real material, never by this record. Regardless of the
profile, model-proposed boundaries and content are always validated against the
revalidated cue evidence by the unchanged Text-model output projection and
adjudication (ADR 0040): an invalid proposal is retained as a diagnostic, never
formal output, so the profile never makes unproven model output authoritative.

This mirrors the audio-analysis calibration precedent (ADRs 0027, 0029, 0031) for
the text modality.

## Considered Options

- Model-specific offline decoding calibration: accepted because an LLM's decoding
  configuration is not portable across models, precisions, or devices and must be
  pinned and hash-bound to be reproducible and memory-bounded.
- A universal decoding configuration or immediate real use: rejected because it
  would make an unpinned, unreproducible decoding path appear authoritative.
- Calibration by model name alone: rejected because backend, precision, device,
  and prompt-template changes alter the output the configuration produces.
- Treating offline verification as field validation: rejected because engineering
  fixtures cannot establish real subtitle-semantics quality.
- Relaxing cue-evidence validation for a calibrated model: rejected because
  ADR 0040 binds every formal fact to cue evidence regardless of the generator.
