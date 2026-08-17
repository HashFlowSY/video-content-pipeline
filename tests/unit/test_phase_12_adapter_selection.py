"""Phase 12 ticket 06: run composition's real-vs-offline adapter selection.

The one new observable point ticket 06 adds is composition's *adapter selection*
— which capabilities an orchestrated run drives through the real engine and which
keep the controlled offline adapter (ADR 0037's automated-test path). These tests
pin that selection as a pure function of the model registry's metadata: no model
asset is opened, so the automated suite — whose registry candidates all carry a
``controlled_adapter`` fixture, or grade ineligible — always selects offline,
while a real acquired registry (eligible candidates, no controlled adapter)
selects real. The real engines themselves are proven by run #1, never in CI.
"""

from __future__ import annotations

import json
from pathlib import Path

from video_content_pipeline.orchestration import RunLayout
from video_content_pipeline.planning import RunPlan, calculate_disk_headroom
from video_content_pipeline.real_engine_adapter import RealEngineSelection
from video_content_pipeline.run_choices import (
    COLLECTION_SCOPE,
    KEY_ASR_MODE,
    KEY_VISUAL_TEXT_ENABLED,
    STAGE_RUN,
    AsrMode,
    ChoiceProvenance,
    RunChoice,
    RunPlanChoices,
)
from video_content_pipeline.run_composition import (
    REAL_ENGINE_CAPABILITIES,
    AdapterKind,
    AdapterProfile,
    StageFunctions,
    build_run_composition,
    select_adapter_profile,
)
from video_content_pipeline.source import SourceArtifact
from video_content_pipeline.stage_dag import compute_invalidation_keys, plan_stage_units

_SHA = "a" * 64


def _write_registry(project_root: Path, candidates: list[dict[str, object]]) -> None:
    registry_path = project_root / "models" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps({"schema_version": 2, "candidates": candidates}, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _eligible_candidate(
    project_root: Path,
    candidate_id: str,
    capability: str,
    *,
    controlled_adapter: bool,
) -> dict[str, object]:
    """A registry candidate the shared eligibility gate grades ``eligible``.

    ``controlled_adapter`` toggles the one field that distinguishes the automated
    test path's controlled offline adapter from a real acquired asset.
    """

    plan = project_root / "models" / "plans" / f"{candidate_id}.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("# dependency plan\n", encoding="utf-8")
    candidate: dict[str, object] = {
        "candidate_id": candidate_id,
        "capability": capability,
        "official_source": {"url": "https://models.invalid/x", "approved": True},
        "license_approved": True,
        "revision": "r1",
        "asset_sha256": _SHA,
        "offline_runtime": True,
        "credential_required": False,
        "telemetry": False,
        "dependency_plan": f"models/plans/{candidate_id}.md",
        "resource_estimate": {"high_bytes": 1024},
    }
    if controlled_adapter:
        candidate["controlled_adapter"] = {
            "adapter_version": "v1",
            "raw_output": {"native": []},
            "projection": {},
        }
    return candidate


# --- AdapterProfile accessors ------------------------------------------------


def test_profile_defaults_unlisted_capabilities_to_offline() -> None:
    profile = AdapterProfile({"vad": AdapterKind.REAL})
    assert profile.kind("vad") is AdapterKind.REAL
    assert profile.is_real("vad")
    assert profile.kind("diarization") is AdapterKind.OFFLINE
    assert not profile.is_real("diarization")


def test_profile_reports_its_real_capabilities_and_any_real() -> None:
    profile = AdapterProfile(
        {"vad": AdapterKind.REAL, "diarization": AdapterKind.OFFLINE}
    )
    assert profile.real_capabilities == frozenset({"vad"})
    assert profile.any_real
    assert not AdapterProfile({"vad": AdapterKind.OFFLINE}).any_real
    assert not AdapterProfile({}).any_real


# --- select_adapter_profile --------------------------------------------------


def test_absent_registry_selects_offline_everywhere(tmp_path: Path) -> None:
    profile = select_adapter_profile(tmp_path)
    assert not profile.any_real
    for capability in REAL_ENGINE_CAPABILITIES:
        assert profile.kind(capability) is AdapterKind.OFFLINE


def test_controlled_adapter_candidate_selects_offline(tmp_path: Path) -> None:
    # The ADR 0037 automated-test path: an eligible candidate that carries a
    # controlled offline adapter fixture is never the real engine.
    _write_registry(
        tmp_path,
        [_eligible_candidate(tmp_path, "controlled-vad", "vad", controlled_adapter=True)],
    )
    profile = select_adapter_profile(tmp_path)
    assert profile.kind("vad") is AdapterKind.OFFLINE
    assert not profile.any_real


def test_eligible_real_candidate_selects_real(tmp_path: Path) -> None:
    # A real acquired asset: eligible, no controlled adapter fixture.
    _write_registry(
        tmp_path,
        [_eligible_candidate(tmp_path, "silero-vad", "vad", controlled_adapter=False)],
    )
    profile = select_adapter_profile(tmp_path)
    assert profile.kind("vad") is AdapterKind.REAL
    assert profile.real_capabilities == frozenset({"vad"})


def test_ineligible_candidate_without_controlled_adapter_stays_offline(tmp_path: Path) -> None:
    candidate = _eligible_candidate(tmp_path, "broken-asr", "asr_primary", controlled_adapter=False)
    del candidate["asset_sha256"]  # a missing required field makes it unsupported
    _write_registry(tmp_path, [candidate])
    profile = select_adapter_profile(tmp_path)
    assert profile.kind("asr_primary") is AdapterKind.OFFLINE


def test_credential_gated_candidate_stays_offline(tmp_path: Path) -> None:
    candidate = _eligible_candidate(tmp_path, "gated-asr", "asr_primary", controlled_adapter=False)
    candidate["credential_required"] = True
    _write_registry(tmp_path, [candidate])
    profile = select_adapter_profile(tmp_path)
    assert profile.kind("asr_primary") is AdapterKind.OFFLINE


def test_real_wins_when_any_candidate_is_eligible_real(tmp_path: Path) -> None:
    # Diarization carries two candidates in the real registry; a real selection
    # holds if any eligible non-controlled candidate exists, whatever the order.
    controlled = _eligible_candidate(tmp_path, "c-diar", "diarization", controlled_adapter=True)
    real = _eligible_candidate(tmp_path, "r-diar", "diarization", controlled_adapter=False)
    _write_registry(tmp_path, [controlled, real])
    assert select_adapter_profile(tmp_path).kind("diarization") is AdapterKind.REAL
    # Reversed order selects real just the same.
    _write_registry(tmp_path, [real, controlled])
    assert select_adapter_profile(tmp_path).kind("diarization") is AdapterKind.REAL


def test_unknown_capability_is_ignored(tmp_path: Path) -> None:
    candidate = _eligible_candidate(tmp_path, "x", "not_a_capability", controlled_adapter=False)
    _write_registry(tmp_path, [candidate])
    profile = select_adapter_profile(tmp_path)
    assert not profile.any_real


def test_schema_one_registry_selects_offline(tmp_path: Path) -> None:
    # The legacy status-list registry names no acquired candidate matrix; the real
    # path is a schema-2 concept, so a schema-1 registry stays wholly offline.
    registry_path = tmp_path / "models" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps({"schema_version": 1, "models": [{"capability": "vad", "status": "acquired"}]}),
        encoding="utf-8",
    )
    assert not select_adapter_profile(tmp_path).any_real


def test_malformed_registry_selects_offline(tmp_path: Path) -> None:
    registry_path = tmp_path / "models" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text("{not json", encoding="utf-8")
    assert not select_adapter_profile(tmp_path).any_real


# --- build_run_composition hands each stage its real selection ---------------

_PLAN_ID = "plan0123456789abcdef0123"
_PART = "b" * 64


def _full_asr_plan() -> RunPlan:
    return RunPlan(
        plan_id=_PLAN_ID,
        report_id="0" * 32,
        source_artifacts=(
            SourceArtifact(source_id=_PART, sha256=_PART, byte_count=1, media_path=Path("m")),
        ),
        tools=(),
        disk_headroom=calculate_disk_headroom(0),
        configuration_fingerprint="cfg" + "0" * 61,
        run_choices=RunPlanChoices.build(
            (
                RunChoice(
                    STAGE_RUN, KEY_ASR_MODE, COLLECTION_SCOPE,
                    AsrMode.FULL_ASR.value, ChoiceProvenance.USER_CHOSEN,
                ),
                RunChoice(
                    STAGE_RUN, KEY_VISUAL_TEXT_ENABLED, COLLECTION_SCOPE,
                    "false", ChoiceProvenance.USER_CHOSEN,
                ),
            )
        ),
    )


class _Recorder:
    """A stand-in for the per-phase functions that records each stage's kwargs."""

    def __init__(self) -> None:
        self.calls: dict[str, dict[str, object]] = {}

    def _make(self, name: str):
        def function(*args: object, **kwargs: object) -> dict[str, object]:
            self.calls[name] = kwargs
            return {"status": "complete", "report": {"report_id": f"{name}-1"}}

        return function

    def functions(self) -> StageFunctions:
        return StageFunctions(
            process_subtitles=self._make("subtitles"),
            resume_subtitles=self._make("resume_subtitles"),
            analyze_audio=self._make("audio"),
            transcribe=self._make("transcribe"),
            enhance=self._make("enhance"),
            analyze_text=self._make("text"),
            run_visual_text=self._make("visual"),
        )


def _drive(project_root: Path, recorder: _Recorder) -> None:
    plan = _full_asr_plan()
    layout = RunLayout(project_root=project_root, source_id="s" * 64, run_id="20260817T0000Z-0")
    composition = build_run_composition(layout, plan, functions=recorder.functions())
    keys = compute_invalidation_keys(plan)
    for unit in plan_stage_units(plan):
        composition.executor(unit, keys[unit])


def test_real_registry_hands_stages_their_real_selection(tmp_path: Path) -> None:
    # A real acquired registry (eligible, no controlled adapter) for vad and
    # asr_primary: the audio stage receives the vad real selection and the
    # transcription stage the asr_primary one — the ticket's headline assertion.
    _write_registry(
        tmp_path,
        [
            _eligible_candidate(tmp_path, "silero-vad", "vad", controlled_adapter=False),
            _eligible_candidate(tmp_path, "qwen3-asr", "asr_primary", controlled_adapter=False),
        ],
    )
    recorder = _Recorder()
    _drive(tmp_path, recorder)

    audio = recorder.calls["audio"]["real_engines"]
    assert isinstance(audio, RealEngineSelection)
    assert audio.capabilities == frozenset({"vad"})  # alignment/diarization not acquired
    transcribe = recorder.calls["transcribe"]["real_engines"]
    assert isinstance(transcribe, RealEngineSelection)
    assert transcribe.capabilities == frozenset({"asr_primary"})


def test_test_path_registry_hands_stages_no_real_selection(tmp_path: Path) -> None:
    # The automated-test path: a controlled-adapter vad candidate. Every stage
    # receives ``real_engines=None`` — the offline path, no model loaded.
    _write_registry(
        tmp_path,
        [_eligible_candidate(tmp_path, "controlled-vad", "vad", controlled_adapter=True)],
    )
    recorder = _Recorder()
    _drive(tmp_path, recorder)

    assert recorder.calls["audio"]["real_engines"] is None
    assert recorder.calls["transcribe"]["real_engines"] is None
