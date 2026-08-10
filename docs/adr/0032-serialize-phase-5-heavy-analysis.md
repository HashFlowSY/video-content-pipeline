# Serialize Phase 5 heavy analysis

Phase 5 runs VAD, full-audio forced alignment, and speaker diarization in that
order, with no concurrent heavy-model loading. A stage records its output,
resource measurement, and unload evidence before the next stage can load; this
preserves the 24 GB resource envelope and the provenance of each result.

Missing credible unload evidence transitions the run to
`model_release_unverified`: completed artifacts remain retained, later model
loads are blocked, and cleanup, retry, or recovery waits for an explicit user
decision.

Before a model starts, a high estimate above the 24 GB envelope transitions to
`resource_envelope_exceeded`. The pipeline retains estimates and alternatives,
but batch, precision, or model changes wait for an explicit user decision.

## Considered Options

- Fixed serial execution with unload evidence: accepted because it bounds memory
  use and makes model-stage provenance unambiguous.
- Parallel or overlapping model execution: rejected because it undermines the
  memory envelope and makes resource attribution unreliable.
- VAD-clipped forced alignment: rejected because Phase 5 requires alignment
  against full audio rather than a VAD-selected subset.
- Automatic recovery after an unverified unload: rejected because it could
  compound an unmeasured memory state without user authority.
- Automatic resource downgrade: rejected because changing a model's execution
  configuration affects quality and invalidates its calibration identity.
