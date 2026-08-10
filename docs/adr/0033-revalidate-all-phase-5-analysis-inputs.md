# Revalidate all Phase 5 analysis inputs

Phase 5 runs only with a processing authorization that revalidates exact
SourceArtifact hashes, audio coverage evidence, the Primary subtitle track and
candidate report, selected model assets, and rules and calibration profiles.
Any drift blocks the run and requires a new plan, rather than extending a prior
plan with changed evidence.

## Considered Options

- Strict complete revalidation: accepted because Phase 5 consumes audio and
  produces new timing and speaker evidence whose provenance must be exact.
- Warning-only drift or mutable prior plan: rejected because either makes an
  immutable authorization describe inputs, models, or rules not actually used.
