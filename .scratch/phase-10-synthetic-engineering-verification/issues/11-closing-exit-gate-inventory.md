# 11 — Close the phase: exit-gate inventory and acceptance proof

**What to build:** The closing inventory/acceptance layer per house
convention. `docs/PHASE_10_INVENTORY.json` with the established schema:
the 3 plan gates verbatim (regex-derivable from `### 阶段 10` to
`### 阶段 11`) + the derived gates from the spec's Exit gates section, each
with `proving_tests`; the five 已知限制 recorded as known limitations; a
`guarantees_asserted_at_cli` block updating media/frame guarantees to
synthetic-fixture semantics (`media_processing` / `frame_extraction` →
`synthetic_fixtures_only`; `models_downloaded` / network stay
not_attempted; `outputs_publication` stays `synthetic_roots_only`) while
`project-state.json` constraints remain untouched (`media_processed:
false` — Real media semantics per glossary); the measured full-suite wall
time recorded against the ≤ 5-minute budget. Plus
`tests/acceptance/test_phase_10_inventory.py` mirroring the phase-09
pattern: plan gates re-derived by regex, derived-gate set exact, every
cited proving test AST-verified to exist, guarantees block checked.
Maintainer governance (completion report, `project-state.json` flip
including `overall_stage` → `real_world_testing`) stays deferred to
explicit maintainer instruction, per precedent — note it in the closing
comment.

**Blocked by:** 01, 02, 03, 04, 05, 06, 07, 08, 09, 10
**Status:** done
**Labels:** ready-for-agent

- [x] Inventory JSON complete: 3 plan gates + derived gates + proving tests
- [x] Five known limitations recorded verbatim
- [x] Guarantees block reflects synthetic-fixture semantics as specified
- [x] Measured suite wall time recorded and within budget
- [x] Acceptance test green: regex gates, exact sets, AST-verified citations
- [x] Full suite green; ruff check/format and mypy clean

## Comments

Done. `docs/PHASE_10_INVENTORY.json` records the three 阶段 10 退出门禁 (plan
gates) plus the nine derived gates from the spec's Exit gates section, each
mapped to named proving tests and cross-linked to a summary boolean; the five
本阶段不能验证 known limitations verbatim; a `guarantees_asserted_at_cli` block
(`media_processing` / `frame_extraction` → `synthetic_fixtures_only`;
`model_execution` / `model_acquisition` / `network_access` → `not_attempted`;
`outputs_publication` → `synthetic_roots_only`); and the measured full-suite
wall time (1332 passed in 15.47s wall) against the ≤ 5-minute budget.

`tests/acceptance/test_phase_10_inventory.py` (12 tests) re-derives the plan
gates and known limitations by regex from `docs/PHASED_EXECUTION_PLAN.md`
(`### 阶段 10` → `### 阶段 11`, joining the wrapped state-flip bullet),
asserts the derived-gate set is exactly the specified nine, AST-verifies every
cited proving test exists, checks the guarantees block and the wall-time
budget, and confirms `project-state.json` is still `engineering_development`
with `media_processed`/`models_downloaded` untouched.

Governance deferred per the Phase 8/9 precedent: the third plan gate — flipping
`project-state.json`'s `overall_stage` to `real_world_testing` — and writing
`docs/PHASE_10_COMPLETION_REPORT.md` are maintainer actions performed only on
explicit maintainer instruction, so this ticket leaves `project-state.json`
untouched. That gate is recorded `confirmed: false` with
`governance_status: deferred_to_maintainer`, and the deferral is itself
machine-checked (the state is verified un-flipped). Full suite green;
ruff check/format and mypy(src) clean.

**Closure (2026-08-16, explicit maintainer instruction):** the deferred
governance was performed in the closure commit — `docs/PHASE_10_COMPLETION_REPORT.md`
written (five known limitations verbatim); `project-state.json` flipped
(current_phase 10 completed, next_phase 11, `overall_stage` →
`real_world_testing`, `real_world_testing: true`; media/model constraints
untouched); the inventory's third plan gate re-recorded
`confirmed: true / governance_status: performed_by_maintainer` and the
acceptance test rewritten in the same commit to machine-check the flipped
state (`test_project_state_reflects_the_closure_flip`), with
`phase_exit_gates: all_confirmed`. Two ticket-08 test files carrying
formatter-owned drift were reformatted at closure re-verification. All
twelve gates now confirmed.
