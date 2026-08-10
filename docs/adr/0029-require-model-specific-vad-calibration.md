# Require model-specific VAD calibration

An uncalibrated VAD model may retain its raw segments and scores but cannot
classify formal Voice activity intervals as `speech_likely` or `non_speech`, or
produce uncovered-speech risk. Until a model-specific calibration profile
validates its thresholds, formal VAD output is `indeterminate`.

## Considered Options

- Model-specific calibration before VAD classification: accepted because VAD
  scores must not silently drive silence claims or ASR-planning evidence.
- Directly consume raw VAD scores: rejected because their thresholds have not
  yet been demonstrated for the execution configuration.
