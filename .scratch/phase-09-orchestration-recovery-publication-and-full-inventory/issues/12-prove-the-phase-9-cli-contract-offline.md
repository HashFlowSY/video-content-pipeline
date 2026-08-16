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

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] Every spec Offline Test Contract property has at least one named
  proving test; the coverage map in the inventory cites real test functions.
- [ ] The five plan 退出门禁 and the derived gates are all `confirmed: true`
  with proving tests; the acceptance test re-derives plan gates by regex from
  the phase plan.
- [ ] `guarantees_asserted_at_cli` records `model_execution`,
  `model_acquisition`, `network_access`, `frame_extraction` as
  `not_attempted` and `outputs_publication` as `synthetic_roots_only`, each
  backed by named tests including the repository-`outputs/`-untouched
  assertion.
- [ ] `pytest -q`, `ruff check .`, `ruff format --check .`, and `mypy src`
  all pass at completion.
- [ ] The completion report and `project-state.json` flip happen only on
  explicit maintainer instruction, following the Phase 8 closure pattern.
