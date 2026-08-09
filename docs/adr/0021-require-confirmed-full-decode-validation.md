# Require confirmed full decode validation

After lightweight source probing, Phase 3 presents a three-point full-decode
estimate and waits for Decode preflight confirmation before linearly decoding
every audio and video stream without writing derived media. Any decode failure
blocks the RunPlan; the final PlanReport still requires separate Plan
confirmation before it produces a plan ID.

## Considered Options

- Confirmed full decode: accepted because complete media-integrity evidence is
  required, while users retain control over a potentially long preflight.
- Automatic or sample-only decode: rejected because the former starts
  substantial work without approval and the latter can miss corrupt intervals.
