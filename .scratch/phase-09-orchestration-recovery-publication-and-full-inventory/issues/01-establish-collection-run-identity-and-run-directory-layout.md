# 01 — Establish collection run identity and the run directory layout

**What to build:** The identity and filesystem foundation for runs: a
collection-level `source-id` derived from ordered Part content hashes and
collection structure (single medium: its content hash), a `run-id` formed
from run start time + immutable plan id + configuration hash, and the
run-owned layout `work/<source-id>/<run-id>/` (state, journal, stage
workspaces, `tmp/`, `staging/`) plus the published layout
`outputs/<source-id>/<run-id>/` and `outputs/<source-id>/latest.json` — so
that every later contract has a stable, non-colliding, plan-bound address for
a run and nothing can overwrite a published run by construction.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] The same collection content and structure always derives the same
  `source-id`; changing Part order or membership changes it.
- [ ] `run-id` composition includes the immutable plan id and configuration
  hash; a changed configuration can never reproduce an existing `run-id`.
- [ ] Run-owned paths live only under `work/<source-id>/<run-id>/`; the
  staging directory is created in final RunBundle layout.
- [ ] Creating a run whose `outputs/<source-id>/<run-id>/` already exists is
  an error before any work starts.
- [ ] All identity derivations are deterministic and covered by unit tests.
