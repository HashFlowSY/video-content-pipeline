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
**Status:** open
**Labels:** ready-for-agent

- [ ] Inventory JSON complete: 3 plan gates + derived gates + proving tests
- [ ] Five known limitations recorded verbatim
- [ ] Guarantees block reflects synthetic-fixture semantics as specified
- [ ] Measured suite wall time recorded and within budget
- [ ] Acceptance test green: regex gates, exact sets, AST-verified citations
- [ ] Full suite green; ruff check/format and mypy clean

## Comments
