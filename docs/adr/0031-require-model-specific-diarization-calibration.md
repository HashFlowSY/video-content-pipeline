# Require model-specific diarization calibration

An uncalibrated diarization model may retain raw clustering candidates and
scores, but cannot publish formal SpeakerTurns, stable Part-local labels, or
Role candidates. These become formal evidence only after a model-specific
calibration profile validates the execution configuration.

The profile binds the model asset hash, inference backend and version,
quantization or precision, device class, and rules fingerprint. Synthetic-only
profiles qualify only synthetic verification; a bound-identity change
invalidates the qualification.

## Considered Options

- Model-specific calibration before formal diarization: accepted because
  clustering stability and turn boundaries are configuration-dependent.
- Directly publish raw clusters: rejected because provisional groups do not yet
  justify a stable speaker structure or downstream role reasoning.
- Treat synthetic calibration as real-source qualification: rejected because
  fixture behavior does not demonstrate real conversational audio quality.
