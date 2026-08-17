# 08 — Run #1 execution and publication

**What to build:** The first end-to-end real run on real engines. From the
intaken run #1 media: `vcp plan` → `vcp plan decode` → `vcp plan confirm`
(maintainer confirms a plan showing all four legal fields) → `vcp run` with
the real adapters. Mid-run, perform one deliberate pause/resume drill at a
stage boundary (`vcp pause`, verify the paused state, `vcp resume`,
verify no completed stage re-runs). The run publishes an atomic RunBundle
whose processing report carries full provenance (models actually used with
revisions and hashes, tools, environment, parameters, measured peak memory
and durations); `vcp verify` and `vcp inventory` pass against it. If a real
failure occurs, it publishes per the Minimal RunBundle floor and is
recorded honestly — failure is a result, not a rollback.

**Blocked by:** 05 (RunBundle provenance), 06 (real engines in orchestrated
run), 07 (run #1 media acquisition).

**Status:** ready-for-agent

- [ ] Plan confirmed by the maintainer with time / peak memory / disk / model status all present
- [ ] Full run completes on real engines (or a real failure publishes the Minimal RunBundle floor and is recorded)
- [ ] One pause/resume drill performed at a stage boundary; resume re-runs no completed stage
- [ ] RunBundle published atomically; `vcp verify` and `vcp inventory` pass
- [ ] Processing report provenance is non-empty and names the real model stack with revisions
- [ ] Observed peak memory recorded and compared against the 12 GiB envelope
