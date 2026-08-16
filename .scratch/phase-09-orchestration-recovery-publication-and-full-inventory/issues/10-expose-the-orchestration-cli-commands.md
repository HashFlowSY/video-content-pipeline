# 10 — Expose the orchestration CLI commands

**What to build:** The Explicit orchestration command boundary in `cli.py`:
`vcp run --plan`, `vcp status [--run]`, `vcp pause --run`,
`vcp resume --run [--decision]`, `vcp cancel --run`, `vcp verify --run`, and
`vcp inventory --run` — each emitting a single machine-readable JSON object
under the existing CLI error contract, with `vcp run` non-interactive
end-to-end.

**Blocked by:** 06, 09

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] `vcp run` executes a confirmed plan end-to-end with no stdin
  interaction; every stop is a recorded state (`paused`, `incomplete` with
  required decision, `failed`, `cancelled`) plus a published bundle where
  the contract requires one.
- [ ] `vcp status` without `--run` lists runs; with `--run` it reports
  persisted state including the stale-running diagnosis, and never mutates
  state.
- [ ] `vcp pause`/`vcp cancel` only write control requests and report what
  was requested; they never write run state.
- [ ] `vcp verify` re-hashes published files against the manifest, checks
  bidirectional coverage, validates inventory structure, and does not re-run
  quality gates.
- [ ] `vcp inventory` renders the published run's inventory faithfully.
- [ ] All commands keep the sorted-JSON stdout / exit-code contract and pass
  the runtime and venv gates; the sixteen expert commands are untouched.
