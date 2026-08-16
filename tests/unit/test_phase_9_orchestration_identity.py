from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from video_content_pipeline.orchestration import (
    OrchestrationError,
    RunLayout,
    derive_run_id,
    derive_source_id,
    initialize_run_workspace,
    run_id_from_run_plan,
    source_id_from_run_plan,
)
from video_content_pipeline.planning import RunPlan, calculate_disk_headroom
from video_content_pipeline.source import SourceArtifact

_HASH_A = "a" * 64
_HASH_B = "b" * 64
_HASH_C = "c" * 64
_PLAN_ID = "plan0123456789abcdef0123"
_CONFIG = "cfg" + "0" * 61
_START = datetime(2026, 8, 16, 8, 30, 0, tzinfo=UTC)
_RUN_ID = "20260816T083000Z-0123456789abcdef"


def _source_artifact(content_hash: str) -> SourceArtifact:
    return SourceArtifact(
        source_id=content_hash,
        sha256=content_hash,
        byte_count=1,
        media_path=Path("input") / content_hash / "media",
    )


def _run_plan(*content_hashes: str) -> RunPlan:
    return RunPlan(
        plan_id=_PLAN_ID,
        report_id="0" * 32,
        source_artifacts=tuple(_source_artifact(value) for value in content_hashes),
        tools=(),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint=_CONFIG,
    )


def test_single_medium_source_id_is_its_content_hash() -> None:
    assert derive_source_id((_HASH_A,)) == _HASH_A


def test_collection_source_id_is_deterministic() -> None:
    first = derive_source_id((_HASH_A, _HASH_B, _HASH_C))
    second = derive_source_id((_HASH_A, _HASH_B, _HASH_C))
    assert first == second
    assert len(first) == 64


def test_collection_source_id_is_not_a_bare_part_hash() -> None:
    collection = derive_source_id((_HASH_A, _HASH_B))
    assert collection not in {_HASH_A, _HASH_B}


def test_reordering_parts_changes_source_id() -> None:
    assert derive_source_id((_HASH_A, _HASH_B)) != derive_source_id((_HASH_B, _HASH_A))


def test_changing_membership_changes_source_id() -> None:
    assert derive_source_id((_HASH_A, _HASH_B)) != derive_source_id((_HASH_A, _HASH_B, _HASH_C))


def test_empty_collection_is_rejected() -> None:
    with pytest.raises(OrchestrationError) as error:
        derive_source_id(())
    assert error.value.reason == "empty_collection"


def test_non_sha256_part_hash_is_rejected() -> None:
    with pytest.raises(OrchestrationError) as error:
        derive_source_id(("not-a-hash",))
    assert error.value.reason == "invalid_content_hash"


def test_uppercase_part_hash_is_rejected() -> None:
    with pytest.raises(OrchestrationError) as error:
        derive_source_id(("A" * 64,))
    assert error.value.reason == "invalid_content_hash"


def test_run_id_is_deterministic() -> None:
    assert derive_run_id(_START, _PLAN_ID, _CONFIG) == derive_run_id(_START, _PLAN_ID, _CONFIG)


def test_run_id_starts_with_a_compact_utc_timestamp() -> None:
    run_id = derive_run_id(_START, _PLAN_ID, _CONFIG)
    assert run_id.startswith("20260816T083000Z-")


def test_run_id_normalizes_non_utc_start_to_utc() -> None:
    non_utc = datetime(2026, 8, 16, 10, 30, 0, tzinfo=timezone(timedelta(hours=2)))
    assert derive_run_id(non_utc, _PLAN_ID, _CONFIG) == derive_run_id(_START, _PLAN_ID, _CONFIG)


def test_changed_configuration_cannot_reproduce_a_run_id() -> None:
    base = derive_run_id(_START, _PLAN_ID, _CONFIG)
    changed = derive_run_id(_START, _PLAN_ID, "cfg" + "9" * 61)
    assert base != changed


def test_changed_plan_changes_run_id() -> None:
    base = derive_run_id(_START, _PLAN_ID, _CONFIG)
    changed = derive_run_id(_START, "plan9999999999999999999", _CONFIG)
    assert base != changed


def test_changed_start_time_changes_run_id() -> None:
    later = _START.replace(microsecond=500000)
    assert derive_run_id(_START, _PLAN_ID, _CONFIG) != derive_run_id(later, _PLAN_ID, _CONFIG)


def test_naive_run_start_is_rejected() -> None:
    with pytest.raises(OrchestrationError) as error:
        derive_run_id(datetime(2026, 8, 16, 8, 30, 0), _PLAN_ID, _CONFIG)
    assert error.value.reason == "naive_run_start"


def test_missing_plan_id_is_rejected() -> None:
    with pytest.raises(OrchestrationError) as error:
        derive_run_id(_START, "", _CONFIG)
    assert error.value.reason == "missing_plan_id"


def test_missing_configuration_fingerprint_is_rejected() -> None:
    with pytest.raises(OrchestrationError) as error:
        derive_run_id(_START, _PLAN_ID, "")
    assert error.value.reason == "missing_configuration_fingerprint"


def test_run_layout_addresses_are_run_owned(tmp_path: Path) -> None:
    layout = RunLayout(tmp_path, _HASH_A, _RUN_ID)
    work = tmp_path / "work" / _HASH_A / _RUN_ID
    assert layout.work_dir == work
    assert layout.state_path == work / "run-state.json"
    assert layout.journal_path == work / "events.jsonl"
    assert layout.stages_dir == work / "stages"
    assert layout.tmp_dir == work / "tmp"
    assert layout.staging_dir == work / "staging"
    assert layout.output_dir == tmp_path / "outputs" / _HASH_A / _RUN_ID
    assert layout.latest_path == tmp_path / "outputs" / _HASH_A / "latest.json"


def test_initialize_run_workspace_creates_run_owned_directories(tmp_path: Path) -> None:
    layout = RunLayout(tmp_path, _HASH_A, _RUN_ID)
    initialize_run_workspace(layout)
    assert layout.work_dir.is_dir()
    assert layout.stages_dir.is_dir()
    assert layout.tmp_dir.is_dir()
    assert layout.staging_dir.is_dir()
    # Nothing is written under outputs/ before publication.
    assert not layout.output_dir.exists()


def test_initialize_run_workspace_refuses_an_existing_published_bundle(tmp_path: Path) -> None:
    layout = RunLayout(tmp_path, _HASH_A, _RUN_ID)
    layout.output_dir.mkdir(parents=True)
    with pytest.raises(OrchestrationError) as error:
        initialize_run_workspace(layout)
    assert error.value.reason == "run_already_published"
    # The guard fires before any work directory is created.
    assert not layout.work_dir.exists()


def test_source_id_from_run_plan_uses_ordered_part_content_hashes() -> None:
    plan = _run_plan(_HASH_A, _HASH_B)
    assert source_id_from_run_plan(plan) == derive_source_id((_HASH_A, _HASH_B))


def test_run_id_from_run_plan_binds_plan_id_and_configuration() -> None:
    plan = _run_plan(_HASH_A)
    assert run_id_from_run_plan(plan, _START) == derive_run_id(_START, _PLAN_ID, _CONFIG)
