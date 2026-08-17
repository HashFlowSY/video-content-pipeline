"""Pure-core tests for the Phase 11 ticket 13 capability prototype harness.

The heavy real-model prototype runs are maintainer-invoked retained evidence and
never enter pytest; this module exercises the harness's deterministic core --
record shape, real-time-factor and envelope math, sample rendering, and the
device-baseline recording surfaces -- entirely without models.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

from video_content_pipeline.capabilities import MAX_MODEL_RESOURCE_BYTES
from video_content_pipeline.model_runtime import HUB_OFFLINE_GUARDS
from video_content_pipeline.prototype import (
    DEVICE_CLASS,
    PROTOTYPE_CAPABILITIES,
    AssetIdentity,
    DeviceBaseline,
    EngineeringCheck,
    PrototypeError,
    PrototypeRecord,
    PrototypeTiming,
    envelope_check,
    load_device_baselines,
    offline_guard_names,
    render_sample_markdown,
    within_envelope,
    write_device_baselines,
)


def _timing() -> PrototypeTiming:
    return PrototypeTiming(media_seconds=Fraction(242), wall_seconds=Fraction(121))


def _record(**overrides: object) -> PrototypeRecord:
    base: dict[str, object] = {
        "capability": "vad",
        "candidate_id": "silero-vad",
        "language": "zh",
        "source_id": "f6fd0cd7",
        "device_class": DEVICE_CLASS,
        "command": ("vcp-prototype", "vad", "f6fd0cd7"),
        "asset_identities": (AssetIdentity("silero_vad.onnx", "0" * 64),),
        "timing": _timing(),
        "peak_memory_bytes": 512 * 1024**2,
        "checks": (EngineeringCheck("partition_complete", True, "covers derivative"),),
        "offline_guards": offline_guard_names(),
        "sample_relpath": "docs/phase-11-prototypes/vad/f6fd0cd7-zh.md",
        "created_at": "2026-08-17T00:00:00Z",
    }
    base.update(overrides)
    return PrototypeRecord(**base)  # type: ignore[arg-type]


class TestPrototypeTiming:
    def test_real_time_factor_is_media_over_wall(self) -> None:
        timing = PrototypeTiming(media_seconds=Fraction(242), wall_seconds=Fraction(121))
        assert timing.real_time_factor == Fraction(2)

    def test_zero_wall_is_rejected(self) -> None:
        with pytest.raises(PrototypeError) as excinfo:
            PrototypeTiming(media_seconds=Fraction(242), wall_seconds=Fraction(0))
        assert excinfo.value.reason == "prototype_timing_invalid"

    def test_negative_media_is_rejected(self) -> None:
        with pytest.raises(PrototypeError) as excinfo:
            PrototypeTiming(media_seconds=Fraction(-1), wall_seconds=Fraction(1))
        assert excinfo.value.reason == "prototype_timing_invalid"

    def test_as_json_carries_exact_and_approx(self) -> None:
        document = _timing().as_json()
        assert document["real_time_factor"] == {"numerator": 2, "denominator": 1}
        assert document["real_time_factor_approx"] == pytest.approx(2.0)


class TestEnvelopeCheck:
    def test_peak_within_envelope_passes(self) -> None:
        check = envelope_check(MAX_MODEL_RESOURCE_BYTES)
        assert check.name == "peak_within_envelope"
        assert check.passed is True

    def test_peak_over_envelope_fails(self) -> None:
        check = envelope_check(MAX_MODEL_RESOURCE_BYTES + 1)
        assert check.passed is False

    def test_negative_peak_fails(self) -> None:
        assert envelope_check(-1).passed is False


class TestPrototypeRecord:
    def test_engineering_passes_when_all_checks_and_envelope_and_guards(self) -> None:
        record = _record()
        assert record.peak_within_envelope is True
        assert record.engineering_passed is True
        assert record.status == "engineering_pass"

    def test_over_envelope_peak_fails_engineering(self) -> None:
        record = _record(peak_memory_bytes=MAX_MODEL_RESOURCE_BYTES + 1)
        assert record.peak_within_envelope is False
        assert record.engineering_passed is False
        assert record.status == "engineering_fail"

    def test_failed_check_fails_engineering(self) -> None:
        record = _record(checks=(EngineeringCheck("gate_holds", False, "leaked"),))
        assert record.engineering_passed is False

    def test_missing_offline_guards_fails_engineering(self) -> None:
        record = _record(offline_guards=())
        assert record.engineering_passed is False

    def test_unknown_capability_rejected(self) -> None:
        with pytest.raises(PrototypeError) as excinfo:
            _record(capability="translation")
        assert excinfo.value.reason == "prototype_capability_unknown"

    def test_unknown_language_rejected(self) -> None:
        with pytest.raises(PrototypeError) as excinfo:
            _record(language="fr")
        assert excinfo.value.reason == "prototype_language_unknown"

    def test_as_json_roundtrips_the_envelope_and_guards(self) -> None:
        document = _record().as_json()
        assert document["capability"] == "vad"
        assert document["status"] == "engineering_pass"
        assert document["peak_memory_bytes"] == 512 * 1024**2
        assert document["envelope_bytes"] == MAX_MODEL_RESOURCE_BYTES
        assert document["offline_guards"] == list(offline_guard_names())
        assert document["asset_identities"] == [{"name": "silero_vad.onnx", "sha256": "0" * 64}]

    def test_every_prototype_capability_is_a_real_capability_name(self) -> None:
        assert PROTOTYPE_CAPABILITIES == (
            "vad",
            "diarization",
            "forced_alignment",
            "asr_primary",
            "asr_review",
            "ocr_primary",
            "text_semantics",
        )


class TestOfflineGuards:
    def test_offline_guard_names_are_the_hub_guards(self) -> None:
        assert offline_guard_names() == tuple(sorted(HUB_OFFLINE_GUARDS))


class TestSampleRendering:
    def test_sample_markdown_carries_header_and_entries(self) -> None:
        markdown = render_sample_markdown(
            capability="asr_primary",
            candidate_id="qwen3-asr-1-7b",
            language="zh",
            source_id="f6fd0cd7",
            timing=_timing(),
            peak_memory_bytes=2 * 1024**3,
            entries=("00:00 你好世界", "00:05 这是测试"),
            truncated=True,
        )
        assert "asr_primary" in markdown
        assert "你好世界" in markdown
        assert "truncated" in markdown.lower()
        # The real-time factor is eyeball-visible in the sample header.
        assert "2" in markdown

    def test_sample_markdown_without_entries_states_empty(self) -> None:
        markdown = render_sample_markdown(
            capability="ocr_primary",
            candidate_id="rapidocr",
            language="en",
            source_id="104eeec2",
            timing=_timing(),
            peak_memory_bytes=1024**3,
            entries=(),
            truncated=False,
        )
        assert "no sample entries" in markdown.lower()


class TestDeviceBaselines:
    def test_write_and_load_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "device-baselines.json"
        baselines = (
            DeviceBaseline(
                capability="asr_primary",
                candidate_id="qwen3-asr-1-7b",
                device_class=DEVICE_CLASS,
                real_time_factor=Fraction(3, 2),
                peak_memory_bytes=2 * 1024**3,
                basis="prototype:f6fd0cd7:zh",
            ),
        )
        write_device_baselines(path, baselines)
        loaded = load_device_baselines(path)
        assert loaded == baselines

    def test_missing_baselines_file_is_empty(self, tmp_path: Path) -> None:
        assert load_device_baselines(tmp_path / "absent.json") == ()

    def test_write_replaces_same_capability_language_basis(self, tmp_path: Path) -> None:
        path = tmp_path / "device-baselines.json"
        first = DeviceBaseline("vad", "silero-vad", DEVICE_CLASS, Fraction(10), 5, "prototype:s:zh")
        write_device_baselines(path, (first,))
        updated = DeviceBaseline(
            "vad", "silero-vad", DEVICE_CLASS, Fraction(20), 6, "prototype:s:zh"
        )
        write_device_baselines(path, (updated,))
        loaded = load_device_baselines(path)
        assert loaded == (updated,)


class TestWithinEnvelope:
    def test_boundary_and_negative(self) -> None:
        assert within_envelope(MAX_MODEL_RESOURCE_BYTES) is True
        assert within_envelope(MAX_MODEL_RESOURCE_BYTES + 1) is False
        assert within_envelope(-1) is False
