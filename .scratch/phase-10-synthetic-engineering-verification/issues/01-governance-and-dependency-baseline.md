# 01 — Establish the Phase 10 governance and dependency baseline

**What to build:** The one governed change-set of the phase, done first so
every later ticket stands on it. (1) Add `hypothesis` to the
`[dependency-groups] dev` table, exact-version pinned, installed through
`tools/uv/uv` — this network install was explicitly authorized in the
2026-08-16 grilling (Q15); record that authorization in the commit message.
Configure a deterministic gate profile (registered via a small
`tests/support/hypothesis_profiles.py` or equivalent): `derandomize=True`,
fixed example budget (~50/property); document that exploratory random runs
are a manual developer option. (2) Register pytest markers `integration`,
`slow`, `faultmatrix` in `pyproject.toml` and add `--strict-markers` to
`addopts`; markers are developer convenience only — the gate is always the
full suite. (3) Re-probe the host ffmpeg/ffprobe and refresh their
`config/tools.json` entries (path, version, hash identity, and a status
noting Phase 10 synthetic-fixture/integration usage), clearing the pending
re-probe noted after the machine migration.

**Blocked by:** —
**Status:** open
**Labels:** ready-for-agent

- [ ] `uv.lock` contains exactly one new package, `hypothesis`, exact-pinned
- [ ] A trivial `@given` smoke test passes twice with identical example
      sequences (deterministic profile proven)
- [ ] `--strict-markers` active; an unregistered marker fails the suite
- [ ] `config/tools.json` refreshed with current probe evidence for
      ffmpeg/ffprobe
- [ ] Full suite green; ruff check/format and mypy clean

## Comments
