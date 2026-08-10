# Keep Phase 5 model capabilities provider-neutral

Phase 5 defines provider-neutral capability contracts for forced alignment, VAD,
and speaker diarization, and evaluates models through a common candidate matrix.
`Qwen3-ForcedAligner-0.6B` is a non-mandatory forced-alignment candidate, not a
required dependency or approved acquisition.

Each model adapter retains its raw native output and creates a versioned,
hash-recorded Model-output projection. Gates consume only that projection while
the raw output, projection, and adapter version remain independently auditable.
Failure to completely construct the projection produces `model_output_invalid`;
the raw output is retained, but no formal evidence uses defaults, guesses, or a
partial projection.

## Considered Options

- Provider-neutral contracts and common gates: accepted because models must
  remain replaceable while meeting identical evidence, resource, privacy, and
  calibration requirements.
- Bind the implementation to a named model: rejected because it would turn a
  provisional candidate into an architectural dependency before evaluation.
- Gate directly on native output: rejected because provider-specific format
  changes would change policy behavior without a versioned interface boundary.
- Best-effort partial projections: rejected because absent fields would become
  untraceable assumptions in timing or audio evidence.
