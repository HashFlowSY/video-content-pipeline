# 12 — Prove the Phase 9 CLI contract offline and publish the completion record

**What to build:** The phase exit proof: integration CLI-contract tests
driving `cli.main(argv)` inside synthetic test project roots for every
orchestration command and failure path, kill/truncation injection for crash
recovery, reformulation of prior phases' "`outputs/` does not exist"
assertions into "non-publication commands never write `outputs/`",
`docs/PHASE_09_INVENTORY.json` with the plan-derived and spec-derived exit
gates, `tests/acceptance/test_phase_09_inventory.py` machine-checking the
gate extraction from `### 阶段 9` to `### 阶段 10`, and — on explicit
maintainer instruction — `docs/PHASE_09_COMPLETION_REPORT.md` plus the
`project-state.json` flip.

**Blocked by:** 10, 11

**Status:** done
**Labels:** ready-for-agent

- [x] Every spec Offline Test Contract property has at least one named
  proving test; the coverage map in the inventory cites real test functions.
- [x] The five plan 退出门禁 and the derived gates are all `confirmed: true`
  with proving tests; the acceptance test re-derives plan gates by regex from
  the phase plan.
- [x] `guarantees_asserted_at_cli` records `model_execution`,
  `model_acquisition`, `network_access`, `frame_extraction` as
  `not_attempted` and `outputs_publication` as `synthetic_roots_only`, each
  backed by named tests including the repository-`outputs/`-untouched
  assertion.
- [x] `pytest -q`, `ruff check .`, `ruff format --check .`, and `mypy src`
  all pass at completion.
- [x] The completion report and `project-state.json` flip happen only on
  explicit maintainer instruction, following the Phase 8 closure pattern.

## Comments

Implemented in commit b99d306 feat: prove the Phase 9 CLI contract offline
(Phase 9 ticket 12). Acceptance criteria were checked at phase closure on the
maintainer's instruction, anchored to the current-head verification (pytest
1034 passed; ruff and mypy clean; 21 confirmed exit-gate booleans in
docs/PHASE_09_INVENTORY.json).
