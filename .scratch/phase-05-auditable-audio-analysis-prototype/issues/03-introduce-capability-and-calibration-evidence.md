# 03 -- Introduce capability and calibration evidence

**What to build:** An operator can evaluate provider-neutral forced-alignment,
VAD, and diarization candidates through one auditable contract. Controlled
offline adapters retain raw output and versioned projections, and formal output
remains unavailable until deterministic calibration proves the exact execution
identity.

**Blocked by:** 02 -- Select and prepare auditable analysis audio.

**Status:** resolved
**Labels:** ready-for-agent

- [x] Each candidate is reported as `eligible`, `blocked`, or `unsupported` using the approved source, license, offline-runtime, credential, telemetry, dependency, and resource rules; Qwen and Silero remain candidates only.
- [x] Every controlled adapter retains raw native output plus a complete versioned Model-output projection; incomplete projection returns `model_output_invalid` and produces no formal evidence.
- [x] Deterministic Calibration evaluation records create identity-bound profiles or `calibration_failed`; uncalibrated, drifted, or failed profiles cannot publish formal alignment, VAD, or diarization evidence.

## Comments

2026-08-11: Candidate eligibility, controlled raw/projection retention, and
deterministic calibration gates are implemented and covered by the current
offline suite. This ticket cannot be closed until Ticket 02 produces and binds
the actual analysis-audio derivative that those capability projections must
consume.

2026-08-11: Ticket 02 is now resolved by `f1278dd`, which supplies the
provenance-bound Analysis audio derivative required by this ticket. Rechecking
at `f8e0e10` passed the full 155-test suite, Ruff, formatter, and strict Mypy;
Ticket 03 is resolved.
