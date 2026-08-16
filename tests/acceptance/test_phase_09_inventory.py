"""Machine-checkable acceptance proof for the Phase 9 exit-gate inventory.

Ticket 12 is the phase exit proof. It requires the phase inventory to record
``*_confirmed`` exit-gate booleans mapped to the phase plan's 退出门禁 list *plus*
the derived gates named in the Phase 9 specification, and to map every one of the
specification's eighteen Offline Test Contract properties to named proving tests.
A boolean is only trustworthy if a machine can check what backs it, so this test
verifies the blocks that carry real evidence:

* ``exit_gates`` -- the ``source == "plan"`` gates are re-derived straight from
  ``docs/PHASED_EXECUTION_PLAN.md`` (from ``### 阶段 9`` to ``### 阶段 10``) and
  must map to exactly the 阶段 9 退出门禁 list (no gate invented, none dropped),
  and the ``source == "derived"`` gates must be exactly the eight the
  specification names. Each gate must be ``confirmed`` with a ``summary_key``
  whose summary boolean is ``true`` and cite proving tests that really exist.
* ``offline_test_contract`` -- the eighteen contract properties, each mapped to
  named tests that exist in the tree.
* ``guarantees_asserted_at_cli`` -- the four heavy guarantees stay
  ``not_attempted`` and ``outputs_publication`` becomes ``synthetic_roots_only``,
  each backed by a named test including the repository-``outputs/``-untouched
  assertion.

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
INVENTORY_PATH = PROJECT_ROOT / "docs" / "PHASE_09_INVENTORY.json"
PLAN_PATH = PROJECT_ROOT / "docs" / "PHASED_EXECUTION_PLAN.md"

# The eight derived gates the Phase 9 specification adds to the plan's 退出门禁 list
# (docs/PHASE_09_SPECIFICATION.md :: Offline Test Contract).
_DERIVED_GATE_REQUIREMENTS = {
    "Single-writer run state and journaled control requests.",
    "Atomic same-filesystem publication.",
    "Latest-pointer eligibility (failed runs never advance it).",
    "Non-interactive run execution.",
    "Run-scoped adoption (manual workspaces are never scavenged).",
    "Decision-pause mapping distinct from user pauses.",
    "Crash-recovery semantics (discard past checkpoint, revalidate, journal, continue).",
    "Hash-layer verify.",
}

# The eighteen Offline Test Contract properties the specification enumerates
# (docs/PHASE_09_SPECIFICATION.md :: Offline Test Contract, first paragraph).
_OFFLINE_TEST_CONTRACT_PROPERTIES = {
    "non_interactive_run_over_front_loaded_choices",
    "exact_state_machine_with_queued_as_transient_lock_wait",
    "single_writer_state_and_journaled_control_requests",
    "pause_at_stage_unit_boundaries",
    "cancel_still_publishes",
    "decision_pauses_distinct_from_user_pauses_with_matching_resume",
    "kill_and_truncation_injection_crash_recovery",
    "stage_version_and_config_subset_invalidation_downstream_only",
    "run_scoped_adoption_refuses_manual_workspaces",
    "projection_determinism_and_timing_view_recording",
    "staging_st_dev_precheck_rename_atomicity_and_reverification",
    "minimal_run_bundle_on_every_ordinary_failure",
    "manifest_disk_bidirectional_coverage",
    "latest_pointer_eligibility_failed_never_advances",
    "no_overwrite_of_existing_run_directories",
    "verify_hash_layer_scope",
    "inventory_coverage_of_used_created_modified_deletable",
    "improvement_runs_carry_forward_only_from_published_bundles_with_provenance",
}


def _inventory() -> dict[str, Any]:
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def _plan_phase_9_exit_gates() -> list[str]:
    """Extract the 阶段 9 退出门禁 bullet list from the authoritative phase plan."""

    text = PLAN_PATH.read_text(encoding="utf-8")
    # The section runs from the 阶段 9 heading up to the next phase heading, which
    # is the terminator this extraction depends on.
    section = re.search(r"### 阶段 9[：:].*?(?=\n### 阶段 10)", text, re.DOTALL)
    assert section is not None, "Could not locate the 阶段 9 section in the phase plan."
    # Bullets run from the 退出门禁 label to the next blank line, sub-heading, or
    # end of the section (\Z), whichever comes first.
    gate_block = re.search(r"退出门禁[：:]\s*\n(.*?)(?=\n\n|\n###|\Z)", section.group(0), re.DOTALL)
    assert gate_block is not None, "Could not locate the 阶段 9 退出门禁 block."
    return [line[2:].strip() for line in gate_block.group(1).splitlines() if line.startswith("- ")]


def _flatten(value: object) -> list[str]:
    """Coverage entries are either a list of refs or a name->ref mapping."""

    if isinstance(value, dict):
        return list(value.values())
    assert isinstance(value, list)
    return value


def _iter_referenced_tests(inventory: dict[str, Any]) -> set[str]:
    """Every ``path`` or ``path::function`` the inventory cites as evidence."""

    references: set[str] = set()
    for gate in inventory["exit_gates"]:
        references.update(gate["proving_tests"])
    for entry in inventory["offline_test_contract"].values():
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
    assert inventory["phase"] == 9
    assert inventory["ticket_12_verification"] == "passed_offline"
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

    plan_gates = _plan_phase_9_exit_gates()
    assert len(plan_gates) == 5, plan_gates
    inventory_plan_gates = [
        gate["requirement"] for gate in _inventory()["exit_gates"] if gate["source"] == "plan"
    ]
    assert inventory_plan_gates == plan_gates


def test_derived_exit_gates_are_exactly_the_specified_eight() -> None:
    """The ``source == 'derived'`` gates are exactly the eight the spec names."""

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


def test_offline_test_contract_covers_all_eighteen_properties() -> None:
    """Every spec Offline Test Contract property is mapped to at least one test."""

    contract = _inventory()["offline_test_contract"]
    assert set(contract) == _OFFLINE_TEST_CONTRACT_PROPERTIES
    for name, refs in contract.items():
        assert _flatten(refs), f"offline-test-contract property {name!r} cites no test"


def test_guarantees_block_records_the_reformulated_publication_guarantee() -> None:
    guarantees = _inventory()["guarantees_asserted_at_cli"]
    for key in ("model_execution", "model_acquisition", "network_access", "frame_extraction"):
        assert guarantees[key] == "not_attempted", key
    # Publication changes for the first time this phase: exercised in synthetic roots only.
    assert guarantees["outputs_publication"] == "synthetic_roots_only"
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
