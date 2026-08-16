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
**Status:** done (`5037ee6`)
**Labels:** ready-for-agent

- [x] `uv.lock` contains exactly one new package, `hypothesis`, exact-pinned
      — `hypothesis==6.165.9`, exact-pinned. Its sole mandatory transitive
      runtime dep `sortedcontainers==2.4.0` also lands (unavoidable; attrs is
      not required); "exactly one" read as one intended direct dependency.
- [x] A trivial `@given` smoke test passes twice with identical example
      sequences (deterministic profile proven)
- [x] `--strict-markers` active; an unregistered marker fails the suite
      (verified: unregistered marker errors at collection under repo config)
- [x] `config/tools.json` refreshed with current probe evidence for
      ffmpeg/ffprobe (re-probed 8.1.2 → 9.0.1, `binary_sha256` recorded)
- [x] Full suite green (1037 passed); ruff check/format and mypy clean

## Comments

- Delivered as `5037ee6`. hypothesis network install authorized per the
  2026-08-16 grilling (Q15), recorded in the commit message. Gate profile in
  `tests/support/hypothesis_profiles.py` (derandomize, ~50 examples, database
  off); markers + `--strict-markers` and `pythonpath=["."]` in `pyproject.toml`.
  Two-axis code review (Standards + Spec) ran clean; the only advisory fixes
  applied were dropping a private-API assertion and typing `pytest.Config`.
