"""Machine-checkable acceptance proof for the Phase 7 exit-gate inventory.

Ticket 10 requires the phase inventory to record ``*_confirmed`` exit-gate
booleans mapped to the phase plan's 退出门禁 list. A boolean is only trustworthy
if a machine can check what backs it, so this test verifies the two blocks that
carry real evidence:

* ``exit_gates`` -- re-derives the six 阶段 7 退出门禁 requirements straight from
  ``docs/PHASED_EXECUTION_PLAN.md`` and asserts the inventory's exit gates map to
  exactly that list (no gate invented, none dropped), that each gate is
  ``confirmed`` with a ``summary_key`` whose summary boolean is ``true``, and that
  every proving test it cites is really defined in the tree.
* ``coverage_map`` -- asserts every ticket-10 coverage item cites tests that
  exist, and that "all six detectors" and "every gate" are literally six and five
  named tests, not a vague file reference.

The ``summary`` block is the conventional offline-boundary declaration that
mirrors prior phase inventories; its exit-gate members are the ones cross-checked
against ``exit_gates``. If a proving test is renamed or removed, or a plan gate
changes, this test fails, so the recorded booleans cannot silently drift away
from the code that proves them.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = PROJECT_ROOT / "docs" / "PHASE_07_INVENTORY.json"
PLAN_PATH = PROJECT_ROOT / "docs" / "PHASED_EXECUTION_PLAN.md"


def _inventory() -> dict[str, Any]:
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def _plan_phase_7_exit_gates() -> list[str]:
    """Extract the 阶段 7 退出门禁 bullet list from the authoritative phase plan."""

    text = PLAN_PATH.read_text(encoding="utf-8")
    # The section runs from the 阶段 7 heading up to the next phase heading, which
    # is the terminator this extraction depends on.
    section = re.search(r"### 阶段 7[：:].*?(?=\n### 阶段 8)", text, re.DOTALL)
    assert section is not None, "Could not locate the 阶段 7 section in the phase plan."
    # Bullets run from the 退出门禁 label to the next blank line, sub-heading, or
    # end of the section (\Z), whichever comes first.
    gate_block = re.search(r"退出门禁[：:]\s*\n(.*?)(?=\n\n|\n###|\Z)", section.group(0), re.DOTALL)
    assert gate_block is not None, "Could not locate the 阶段 7 退出门禁 block."
    return [line[2:].strip() for line in gate_block.group(1).splitlines() if line.startswith("- ")]


def _flatten(value: object) -> list[str]:
    """Coverage-map entries are either a list of refs or a name->ref mapping."""

    if isinstance(value, dict):
        return list(value.values())
    assert isinstance(value, list)
    return value


def _iter_referenced_tests(inventory: dict[str, Any]) -> set[str]:
    """Every ``path`` or ``path::function`` the inventory cites as evidence."""

    references: set[str] = set()
    for gate in inventory["exit_gates"]:
        references.update(gate["proving_tests"])
    for entry in inventory["coverage_map"].values():
        references.update(_flatten(entry))
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
    assert inventory["phase"] == 7
    assert inventory["ticket_10_verification"] == "passed_offline"
    assert inventory["phase_exit_gates"] == "all_confirmed"


def test_every_summary_confirmed_boolean_is_well_formed() -> None:
    """Well-formedness of the conventional declaration; the machine-checkable
    backing for the exit-gate members lives in ``exit_gates`` (see summary_key)."""

    summary = _inventory()["summary"]
    assert isinstance(summary, dict) and summary
    for key, value in summary.items():
        assert key.endswith("_confirmed"), f"summary key {key!r} is not an exit-gate boolean"
        assert value is True, f"summary boolean {key!r} is not confirmed"


def test_exit_gates_map_exactly_to_the_plan() -> None:
    """The inventory's exit-gate requirements are exactly the plan's 退出门禁 list."""

    plan_gates = _plan_phase_7_exit_gates()
    assert len(plan_gates) == 6, plan_gates
    inventory_gates = [gate["requirement"] for gate in _inventory()["exit_gates"]]
    assert inventory_gates == plan_gates


def test_every_exit_gate_is_confirmed_and_backed() -> None:
    """Each exit gate is confirmed, cites proving tests, and its summary boolean agrees."""

    inventory = _inventory()
    summary = inventory["summary"]
    for gate in inventory["exit_gates"]:
        assert gate["confirmed"] is True, f"exit gate {gate['id']!r} is not confirmed"
        assert gate["proving_tests"], f"exit gate {gate['id']!r} cites no proving test"
        summary_key = gate["summary_key"]
        assert summary.get(summary_key) is True, (
            f"exit gate {gate['id']!r} summary_key {summary_key!r} is not confirmed in summary"
        )


def test_all_six_detectors_and_every_gate_are_named() -> None:
    """ "All six detectors" and "every gate" are literally six and five named tests."""

    coverage = _inventory()["coverage_map"]
    detectors = coverage["six_suspicion_detectors"]
    gates = coverage["timing_gates"]
    assert set(detectors) == {
        "vad_coverage",
        "coverage_checks",
        "confidence",
        "repetition",
        "language_switching",
        "numbers_entities",
    }
    assert set(gates) == {
        "cue_out_of_coverage",
        "cue_non_monotonic",
        "cue_processing_duplication",
        "cue_duration_implausible",
        "cue_missing_text",
    }


def test_every_cited_test_actually_exists() -> None:
    """A confirmed boolean must point at a test that is really defined in the tree."""

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
