"""Machine-checkable acceptance proof for the Phase 11 exit-gate inventory.

Ticket 14 is the phase closure. It requires ``docs/PHASE_11_INVENTORY.json`` to
record the four 阶段 11 退出门禁 from ``docs/PHASED_EXECUTION_PLAN.md`` *plus* the
seven derived gates named in ``docs/PHASE_11_SPECIFICATION.md :: Exit gates``,
each mapped to named proving tests or retained-evidence pointers and cross-linked
to a summary boolean. A boolean is only trustworthy if a machine can check what
backs it, so this test verifies the blocks that carry real evidence:

* ``exit_gates`` -- the ``source == "plan"`` gates are re-derived straight from the
  phase plan (from ``### 阶段 11`` to ``### 阶段 12``) and must map to exactly the
  four 退出门禁, in order. The ``source == "derived"`` gates must be exactly the
  seven the specification names. Every gate carries a ``summary_key`` whose summary
  boolean agrees with its ``confirmed`` flag, and cites proving tests that exist.
* Phase 11 has no ``overall_stage`` gate: unlike Phase 10, its plan 退出门禁 does not
  flip the project stage. The closure ritual advances the phase fields
  (``current_phase`` 11, ``phase_status`` completed, ``next_phase`` 12) while
  ``overall_stage`` stays ``real_world_testing`` and ``production_validated`` stays
  ``false`` -- only Phase 12 user acceptance can reach ``production_validated``.
  This is machine-checked against ``project-state.json``.
* The two governance constraint flips (``models_downloaded`` by ticket 04,
  ``media_processed`` by ticket 13) are recorded with evidence in
  ``constraint_events`` and re-checked here, while ``paid_apis_used`` stays false.
* ``known_limitations`` -- the phase plan has no 本阶段不能验证 block (real-video
  quality is Phase 12's subject), so the recorded limitations are asserted present
  and Phase-12-facing rather than plan-derived.
* ``guarantees_asserted`` -- the pytest gate stays offline: no model is downloaded
  or executed in the fast suite, the real engines are exercised only under the
  ``slow`` marker and by maintainer-invoked prototype commands, and each guarantee
  is backed by a named test.
* ``full_suite`` -- the recorded wall time is within the recorded ≤ 5-minute budget.

If a proving test is renamed or removed, a plan gate or derived gate changes, or the
closure ritual is misrecorded, this test fails, so the recorded booleans cannot
drift away from the code (and the project state) that back them.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = PROJECT_ROOT / "docs" / "PHASE_11_INVENTORY.json"
PLAN_PATH = PROJECT_ROOT / "docs" / "PHASED_EXECUTION_PLAN.md"
PROJECT_STATE_PATH = PROJECT_ROOT / "project-state.json"
DEVICE_BASELINES_PATH = PROJECT_ROOT / "docs" / "phase-11-prototypes" / "device-baselines.json"

# The 12 GiB shared resource envelope (capabilities.MAX_MODEL_RESOURCE_BYTES); the
# third 阶段 11 退出门禁 requires every measured prototype peak to stay under it.
_TWELVE_GIB = 12 * 1024**3

# The seven derived gates the Phase 11 specification adds to the plan's 退出门禁 list
# (docs/PHASE_11_SPECIFICATION.md :: Exit gates :: Derived gates).
_DERIVED_GATE_REQUIREMENTS = {
    "lockfile is torch-free",
    "every registry entry hash-verifies on disk",
    "production adapters fail typed (never download) when an asset is missing",
    "the subprocess boundary returns memory (post-stage RSS assertion)",
    "envelope constant is 12 GiB everywhere it is asserted",
    "`media_processed`/`models_downloaded` flips are recorded with evidence",
    "full-suite wall time stays within the ≤5-minute budget",
}


def _inventory() -> dict[str, Any]:
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def _plan_section_11() -> str:
    """The 阶段 11 section, from its heading up to the 阶段 12 heading."""

    text = PLAN_PATH.read_text(encoding="utf-8")
    section = re.search(r"### 阶段 11[：:].*?(?=\n### 阶段 12)", text, re.DOTALL)
    assert section is not None, "Could not locate the 阶段 11 section in the phase plan."
    return section.group(0)


def _plan_bullet_block(label: str) -> list[str]:
    """Extract a labelled bullet list from the 阶段 11 section.

    Bullets run from ``label`` to the next blank line, sub-heading, or end of the
    section. A bullet that wraps onto an indented continuation line is joined back
    into one requirement string.
    """

    block = re.search(rf"{label}[：:]\s*\n(.*?)(?=\n\n|\n###|\Z)", _plan_section_11(), re.DOTALL)
    assert block is not None, f"Could not locate the 阶段 11 {label} block."
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
    references.update(inventory["guarantees_asserted"]["asserted_by"])
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
    assert inventory["phase"] == 11
    assert inventory["ticket_14_verification"] == "passed_offline"
    assert inventory["phase_exit_gates"] == "all_confirmed"


def test_plan_exit_gates_map_exactly_to_the_plan() -> None:
    """The ``source == 'plan'`` gates are exactly the plan's 退出门禁 list, in order."""

    plan_gates = _plan_bullet_block("退出门禁")
    assert len(plan_gates) == 4, plan_gates
    inventory_plan_gates = [
        gate["requirement"] for gate in _inventory()["exit_gates"] if gate["source"] == "plan"
    ]
    assert inventory_plan_gates == plan_gates


def test_derived_exit_gates_are_exactly_the_specified_seven() -> None:
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


def test_project_state_reflects_the_phase_11_closure_ritual() -> None:
    """Phase 11 closes: the phase fields advance but the overall stage does not move.

    The durable anchor is Phase 11's own completed/passed_offline history entry;
    the live ``current_phase`` / ``next_phase`` pointers advance monotonically as
    later phases close, so they are checked with ``>=`` rather than fixed equality
    (this keeps the contract frozen instead of forcing a rewrite each closure).
    ``overall_stage`` / ``production_validated`` are the real Phase 11 exit claim:
    Phase 11 must not move them -- only Phase 12 user acceptance can.
    """

    state = json.loads(PROJECT_STATE_PATH.read_text(encoding="utf-8"))
    assert state["current_phase"] >= 11
    assert state["next_phase"] >= 12
    assert state["overall_stage"] == "real_world_testing"
    assert state["production_validated"] is False
    phase_11 = [entry for entry in state["phase_history"] if entry["phase"] == 11]
    assert len(phase_11) == 1
    assert phase_11[0]["status"] == "completed"
    assert phase_11[0]["verification"] == "passed_offline"


def test_the_constraint_flips_are_recorded_with_evidence() -> None:
    """The two legitimate flips carry object+purpose evidence; paid APIs stay false."""

    state = json.loads(PROJECT_STATE_PATH.read_text(encoding="utf-8"))
    constraints = state["constraints"]
    assert constraints["models_downloaded"] is True
    assert constraints["media_processed"] is True
    assert constraints["paid_apis_used"] is False

    events = {event["constraint"]: event for event in state["constraint_events"]}
    for name in ("models_downloaded", "media_processed"):
        event = events[name]
        assert event["value"] is True
        assert event["object"], f"{name} flip records no object"
        assert event["purpose"], f"{name} flip records no purpose"


def test_known_limitations_are_recorded_and_defer_to_phase_12() -> None:
    """Phase 11's plan has no 本阶段不能验证 block; limitations are Phase-12-facing."""

    assert re.search("本阶段不能验证", _plan_section_11()) is None
    limitations = _inventory()["known_limitations"]
    assert limitations, "no known limitations recorded"
    assert any("阶段 12" in item or "Phase 12" in item for item in limitations)


def test_guarantees_block_reflects_the_offline_pytest_gate() -> None:
    guarantees = _inventory()["guarantees_asserted"]
    assert guarantees["network_access"] == "not_attempted"
    assert guarantees["fast_suite_model_execution"] == "not_attempted"
    assert guarantees["real_engine_execution"] == "slow_marked_and_maintainer_invoked_only"
    assert guarantees["model_acquisition"] == "one_off_authorized_offline"
    assert guarantees["asserted_by"], "guarantees cite no asserting test"


def test_every_recorded_prototype_peak_is_within_the_envelope() -> None:
    """The third plan gate, machine-checked against the real measured baselines.

    Every prototype run recorded a measured peak in ``device-baselines.json``; each
    must be a positive integer no greater than the 12 GiB envelope.
    """

    baselines = json.loads(DEVICE_BASELINES_PATH.read_text(encoding="utf-8"))["baselines"]
    assert baselines, "no device baselines recorded"
    for baseline in baselines:
        peak = baseline["peak_memory_bytes"]
        assert isinstance(peak, int) and not isinstance(peak, bool)
        assert 0 < peak <= _TWELVE_GIB, (baseline["basis"], baseline["capability"], peak)


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


def test_recorded_evidence_pointers_exist() -> None:
    for relative in _inventory()["retained_evidence"]:
        assert (PROJECT_ROOT / relative).exists(), f"recorded evidence is missing: {relative}"


def test_recorded_created_or_modified_files_exist() -> None:
    for relative in _inventory()["created_or_modified"]:
        assert (PROJECT_ROOT / relative).exists(), f"recorded artifact is missing: {relative}"
