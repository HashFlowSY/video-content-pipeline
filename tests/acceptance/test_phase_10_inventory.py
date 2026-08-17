"""Machine-checkable acceptance proof for the Phase 10 exit-gate inventory.

Ticket 11 is the phase closure. It requires ``docs/PHASE_10_INVENTORY.json`` to
record the three 阶段 10 退出门禁 from ``docs/PHASED_EXECUTION_PLAN.md`` *plus* the
nine derived gates named in the Phase 10 specification, each mapped to named
proving tests; the five 本阶段不能验证 known limitations verbatim; a
``guarantees_asserted_at_cli`` block reflecting synthetic-fixture semantics; and
the measured full-suite wall time against the ≤ 5-minute budget. A boolean is only
trustworthy if a machine can check what backs it, so this test verifies the blocks
that carry real evidence:

* ``exit_gates`` -- the ``source == "plan"`` gates are re-derived straight from the
  phase plan (from ``### 阶段 10`` to ``### 阶段 11``) and must map to exactly the
  three 退出门禁, in order. The ``source == "derived"`` gates must be exactly the
  nine the specification names. Every gate carries a ``summary_key`` whose summary
  boolean agrees with its ``confirmed`` flag, and cites proving tests that exist.
* The third plan gate — flipping ``project-state.json``'s ``overall_stage`` to
  ``real_world_testing`` — was deferred by ticket 11 per the Phase 8/9 precedent
  and performed at closure on explicit maintainer instruction. It is recorded
  ``confirmed: true`` with ``governance_status: performed_by_maintainer``, and the
  flip is itself machine-checked: ``project-state.json`` is ``real_world_testing``
  at phase 10 completed while the media/model constraints remain untouched.
* ``known_limitations`` -- exactly the five 本阶段不能验证 items, re-derived from the
  plan verbatim.
* ``guarantees_asserted_at_cli`` -- media/frame processing become
  ``synthetic_fixtures_only``, models/network stay ``not_attempted``, publication
  stays ``synthetic_roots_only``, each backed by a named test.
* ``full_suite`` -- the recorded wall time is within the recorded budget.

If a proving test is renamed or removed, a plan gate or known limitation changes,
or the deferred state flip is silently performed, this test fails, so the recorded
booleans cannot drift away from the code (and the project state) that back them.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = PROJECT_ROOT / "docs" / "PHASE_10_INVENTORY.json"
PLAN_PATH = PROJECT_ROOT / "docs" / "PHASED_EXECUTION_PLAN.md"
PROJECT_STATE_PATH = PROJECT_ROOT / "project-state.json"

# The nine derived gates the Phase 10 specification adds to the plan's 退出门禁 list
# (docs/PHASE_10_SPECIFICATION.md :: Exit gates :: Derived gates).
_DERIVED_GATE_REQUIREMENTS = {
    "property layer exists and is deterministic",
    "five fixture branches generate and probe-verify via the pinned toolchain",
    "`vcp run` completes end to end and publishes a hash-verified bundle with VALID core artifacts",
    "the fault matrix is exhaustive over enumerated fault points × classes with all post-fault invariants",  # noqa: E501
    "control-file corruption halts safely",
    "CLI acceptance covers all five branches plus `improve`",
    "full-suite wall time within budget",
    "`media_processed` still `false`",
    "`models_downloaded` still `false`",
}


def _inventory() -> dict[str, Any]:
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def _plan_section_10() -> str:
    """The 阶段 10 section, from its heading up to the 阶段 11 heading."""

    text = PLAN_PATH.read_text(encoding="utf-8")
    section = re.search(r"### 阶段 10[：:].*?(?=\n### 阶段 11)", text, re.DOTALL)
    assert section is not None, "Could not locate the 阶段 10 section in the phase plan."
    return section.group(0)


def _plan_bullet_block(label: str) -> list[str]:
    """Extract a labelled bullet list from the 阶段 10 section.

    Bullets run from ``label`` to the next blank line, sub-heading, or end of the
    section. A bullet that wraps onto an indented continuation line (as the state
    flip gate does) is joined back into one requirement string.
    """

    block = re.search(rf"{label}[：:]\s*\n(.*?)(?=\n\n|\n###|\Z)", _plan_section_10(), re.DOTALL)
    assert block is not None, f"Could not locate the 阶段 10 {label} block."
    bullets: list[str] = []
    for line in block.group(1).splitlines():
        if line.startswith("- "):
            bullets.append(line[2:].strip())
        elif line.strip() and bullets:
            bullets[-1] = f"{bullets[-1]} {line.strip()}"
    return bullets


def _iter_referenced_tests(inventory: dict[str, Any]) -> set[str]:
    references: set[str] = set()
    for gate in inventory["exit_gates"]:
        references.update(gate["proving_tests"])
    references.update(inventory["guarantees_asserted_at_cli"]["asserted_by"])
    return references


def _defined_functions(test_path: Path) -> set[str]:
    tree = ast.parse(test_path.read_text(encoding="utf-8"), filename=str(test_path))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def test_inventory_is_well_formed() -> None:
    inventory = _inventory()
    assert inventory["schema_version"] == 1
    assert inventory["phase"] == 10
    assert inventory["ticket_11_verification"] == "passed_offline"
    assert inventory["phase_exit_gates"] == "all_confirmed"


def test_plan_exit_gates_map_exactly_to_the_plan() -> None:
    """The ``source == 'plan'`` gates are exactly the plan's 退出门禁 list, in order."""

    plan_gates = _plan_bullet_block("退出门禁")
    assert len(plan_gates) == 3, plan_gates
    inventory_plan_gates = [
        gate["requirement"] for gate in _inventory()["exit_gates"] if gate["source"] == "plan"
    ]
    assert inventory_plan_gates == plan_gates


def test_derived_exit_gates_are_exactly_the_specified_nine() -> None:
    inventory = _inventory()
    derived = [gate for gate in inventory["exit_gates"] if gate["source"] == "derived"]
    assert {gate["requirement"] for gate in derived} == _DERIVED_GATE_REQUIREMENTS
    assert {gate["id"] for gate in derived} == set(inventory["derived_gate_ids"])


def test_every_exit_gate_has_a_known_source() -> None:
    for gate in _inventory()["exit_gates"]:
        assert gate["source"] in {"plan", "derived"}, gate


def test_confirmed_gates_agree_with_the_summary_and_cite_existing_tests() -> None:
    """Each confirmed gate cites proving tests and its summary boolean is true."""

    inventory = _inventory()
    summary = inventory["summary"]
    for gate in inventory["exit_gates"]:
        summary_key = gate["summary_key"]
        assert summary_key in summary, f"gate {gate['id']!r} summary_key not in summary"
        assert gate["proving_tests"], f"gate {gate['id']!r} cites no proving test"
        assert gate["confirmed"] is True, f"gate {gate['id']!r} is not confirmed"
        assert summary[summary_key] is True, (
            f"gate {gate['id']!r} summary boolean disagrees with confirmed"
        )


def test_the_state_flip_gate_was_performed_by_the_maintainer() -> None:
    """Gate 3 is the only governance gate; the closure flip is explicit and confirmed."""

    governed = [g for g in _inventory()["exit_gates"] if g.get("governance_status")]
    assert len(governed) == 1, governed
    assert governed[0]["id"] == "overall_stage_flips_to_real_world_testing"
    assert governed[0]["governance_status"] == "performed_by_maintainer"
    assert governed[0]["confirmed"] is True


def test_project_state_reflects_the_closure_flip() -> None:
    """The third plan gate happened: overall_stage flipped with the phase fields."""

    state = json.loads(PROJECT_STATE_PATH.read_text(encoding="utf-8"))
    assert state["overall_stage"] == "real_world_testing"
    assert state["real_world_testing"] is True
    assert state["current_phase"] == 10
    assert state["phase_status"] == "completed"
    assert state["next_phase"] == 11
    phase_10 = [entry for entry in state["phase_history"] if entry["phase"] == 10]
    assert len(phase_10) == 1
    assert phase_10[0]["status"] == "completed"
    assert phase_10[0]["verification"] == "passed_offline"


def test_project_state_constraints_remain_untouched() -> None:
    """Phase 10 synthetic verification touched no media/model constraint; the
    later, legitimate flips stay bounded.

    The name is frozen by the Phase 10 inventory's citation of this test. Phase
    10 closed with both ``media_processed`` and ``models_downloaded`` false;
    the two legitimate later flips both happened outside Phase 10 and are
    expected here: Phase 11 ticket 04 acquired the real model assets
    (``models_downloaded`` true), and Phase 11 ticket 13's first real-media
    prototype runs processed the ticket-12 clips (``media_processed`` true).
    ``paid_apis_used`` is the boundary that still holds.
    """

    state = json.loads(PROJECT_STATE_PATH.read_text(encoding="utf-8"))
    constraints = state["constraints"]
    # Flipped by Phase 11 ticket 13 (first real-media prototype processing).
    assert constraints["media_processed"] is True
    # Flipped by Phase 11 ticket 04 (model acquisition), never by Phase 10.
    assert constraints["models_downloaded"] is True
    assert constraints["paid_apis_used"] is False


def test_known_limitations_match_the_plan_verbatim() -> None:
    """The five recorded known limitations are exactly the plan's 本阶段不能验证 list."""

    plan_limitations = _plan_bullet_block("本阶段不能验证")
    assert len(plan_limitations) == 5, plan_limitations
    assert _inventory()["known_limitations"] == plan_limitations


def test_guarantees_block_reflects_synthetic_fixture_semantics() -> None:
    guarantees = _inventory()["guarantees_asserted_at_cli"]
    for key in ("model_execution", "model_acquisition", "network_access"):
        assert guarantees[key] == "not_attempted", key
    for key in ("media_processing", "frame_extraction"):
        assert guarantees[key] == "synthetic_fixtures_only", key
    assert guarantees["outputs_publication"] == "synthetic_roots_only"
    assert guarantees["asserted_by"], "guarantees cite no asserting test"


def test_recorded_suite_wall_time_is_within_budget() -> None:
    full_suite = _inventory()["full_suite"]
    wall = full_suite["wall_time_seconds"]
    budget = full_suite["wall_time_budget_seconds"]
    assert budget == 300, budget
    assert 0 < wall <= budget, (wall, budget)
    assert full_suite["within_budget"] is True


def test_every_cited_test_actually_exists() -> None:
    for reference in sorted(_iter_referenced_tests(_inventory())):
        path_part, _, func_name = reference.partition("::")
        test_path = PROJECT_ROOT / path_part
        assert test_path.is_file(), f"cited test file is missing: {path_part}"
        if not func_name:
            continue
        assert func_name in _defined_functions(test_path), (
            f"cited test {func_name!r} is not defined in {path_part}"
        )


def test_recorded_created_or_modified_files_exist() -> None:
    for relative in _inventory()["created_or_modified"]:
        assert (PROJECT_ROOT / relative).exists(), f"recorded artifact is missing: {relative}"
