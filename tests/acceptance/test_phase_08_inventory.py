"""Machine-checkable acceptance proof for the Phase 8 exit-gate inventory.

Ticket 09 requires the phase inventory to record ``*_confirmed`` exit-gate
booleans mapped to the phase plan's 退出门禁 list *plus* the derived gates named
in the Phase 8 specification. A boolean is only trustworthy if a machine can
check what backs it, so this test verifies the blocks that carry real evidence:

* ``exit_gates`` -- the ``source == "plan"`` gates are re-derived straight from
  ``docs/PHASED_EXECUTION_PLAN.md`` and must map to exactly the 阶段 8 退出门禁
  list (no gate invented, none dropped), and the ``source == "derived"`` gates
  must be exactly the four the specification names. Each gate must be
  ``confirmed`` with a ``summary_key`` whose summary boolean is ``true`` and cite
  proving tests that really exist in the tree.
* ``coverage_map`` -- every ticket-09 minimum scenario cites tests that exist,
  and the closed sets ("every classification class", "the item gate reasons") are
  literally the named tests, not a vague file reference.

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
INVENTORY_PATH = PROJECT_ROOT / "docs" / "PHASE_08_INVENTORY.json"
PLAN_PATH = PROJECT_ROOT / "docs" / "PHASED_EXECUTION_PLAN.md"

# The four derived gates the Phase 8 specification adds to the plan's 退出门禁 list
# (docs/PHASE_08_SPECIFICATION.md :: Offline Test Contract).
_DERIVED_GATE_REQUIREMENTS = {
    "Scope is always explicit (unscoped invocation errors).",
    "All extracted frames are inventoried with their selection reason.",
    "Page identity is Part-local with appearance records.",
    "All detection, sampling, and classification rules are versioned.",
}


def _inventory() -> dict[str, Any]:
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def _plan_phase_8_exit_gates() -> list[str]:
    """Extract the 阶段 8 退出门禁 bullet list from the authoritative phase plan."""

    text = PLAN_PATH.read_text(encoding="utf-8")
    # The section runs from the 阶段 8 heading up to the next phase heading, which
    # is the terminator this extraction depends on.
    section = re.search(r"### 阶段 8[：:].*?(?=\n### 阶段 9)", text, re.DOTALL)
    assert section is not None, "Could not locate the 阶段 8 section in the phase plan."
    # Bullets run from the 退出门禁 label to the next blank line, sub-heading, or
    # end of the section (\Z), whichever comes first.
    gate_block = re.search(r"退出门禁[：:]\s*\n(.*?)(?=\n\n|\n###|\Z)", section.group(0), re.DOTALL)
    assert gate_block is not None, "Could not locate the 阶段 8 退出门禁 block."
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
    assert inventory["phase"] == 8
    assert inventory["ticket_09_verification"] == "passed_offline"
    assert inventory["phase_exit_gates"] == "all_confirmed"


def test_every_summary_confirmed_boolean_is_well_formed() -> None:
    """Well-formedness of the conventional declaration; the machine-checkable
    backing for the exit-gate members lives in ``exit_gates`` (see summary_key)."""

    summary = _inventory()["summary"]
    assert isinstance(summary, dict) and summary
    for key, value in summary.items():
        assert key.endswith("_confirmed"), f"summary key {key!r} is not an exit-gate boolean"
        assert value is True, f"summary boolean {key!r} is not confirmed"


def test_plan_exit_gates_map_exactly_to_the_plan() -> None:
    """The ``source == 'plan'`` gates are exactly the plan's 退出门禁 list, in order."""

    plan_gates = _plan_phase_8_exit_gates()
    assert len(plan_gates) == 5, plan_gates
    inventory_plan_gates = [
        gate["requirement"] for gate in _inventory()["exit_gates"] if gate["source"] == "plan"
    ]
    assert inventory_plan_gates == plan_gates


def test_derived_exit_gates_are_exactly_the_specified_four() -> None:
    """The ``source == 'derived'`` gates are exactly the four the spec names."""

    inventory = _inventory()
    derived = [gate for gate in inventory["exit_gates"] if gate["source"] == "derived"]
    assert {gate["requirement"] for gate in derived} == _DERIVED_GATE_REQUIREMENTS
    # The recorded derived_gate_ids list stays in sync with the derived gates.
    assert {gate["id"] for gate in derived} == set(inventory["derived_gate_ids"])


def test_every_exit_gate_has_a_known_source() -> None:
    for gate in _inventory()["exit_gates"]:
        assert gate["source"] in {"plan", "derived"}, gate


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


def test_every_classification_class_and_item_gate_reason_is_named() -> None:
    """ "Every classification class" and "the item gate reasons" are literally
    the named tests, not a vague file reference."""

    coverage = _inventory()["coverage_map"]
    assert set(coverage["classification_classes"]) == {
        "page_text",
        "speaker_supplement",
        "background_ui",
        "classification_uncertain",
        "excluded",
    }
    assert set(coverage["item_gate_rejections"]) == {
        "out_of_coverage",
        "unknown_page",
        "page_time_mismatch",
    }


def test_absence_semantics_are_covered() -> None:
    """The absence-semantics scenario (ocr=not_requested) is mapped to a real test."""

    coverage = _inventory()["coverage_map"]
    assert coverage["absence_semantics"], "absence_semantics cites no test"


def test_guarantees_block_asserts_all_five_offline_guarantees() -> None:
    guarantees = _inventory()["guarantees_asserted_at_cli"]
    for key in (
        "model_execution",
        "model_acquisition",
        "network_access",
        "frame_extraction",
        "outputs_publication",
    ):
        assert guarantees[key] == "not_attempted", key
    assert guarantees["asserted_by"], "guarantees cite no asserting test"


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
