# 03 -- Introduce capability and calibration evidence

**What to build:** An operator can evaluate provider-neutral forced-alignment,
VAD, and diarization candidates through one auditable contract. Controlled
offline adapters retain raw output and versioned projections, and formal output
remains unavailable until deterministic calibration proves the exact execution
identity.

**Blocked by:** 02 -- Select and prepare auditable analysis audio.

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] Each candidate is reported as `eligible`, `blocked`, or `unsupported` using the approved source, license, offline-runtime, credential, telemetry, dependency, and resource rules; Qwen and Silero remain candidates only.
- [ ] Every controlled adapter retains raw native output plus a complete versioned Model-output projection; incomplete projection returns `model_output_invalid` and produces no formal evidence.
- [ ] Deterministic Calibration evaluation records create identity-bound profiles or `calibration_failed`; uncalibrated, drifted, or failed profiles cannot publish formal alignment, VAD, or diarization evidence.
