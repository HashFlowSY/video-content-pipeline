# Require deterministic calibration evaluation records

Every Phase 5 calibration profile derives from a deterministic evaluator over
hash-pinned reference fixtures. Its Calibration evaluation record retains model
candidate output, expected results, selected thresholds, false-accept and
false-reject summaries, and evaluator version; a manual declaration cannot
create calibration eligibility.

## Considered Options

- Deterministic recorded evaluation: accepted because calibration controls which
  model output can become timing, audio-state, or speaker evidence.
- Manual calibration declaration: rejected because its thresholds and quality
  claims cannot be reproduced or audited.
- Automatic threshold tuning after failure: rejected because changed thresholds,
  rules, or candidate combinations are new calibration experiments requiring
  explicit authorization.
