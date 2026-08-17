"""Model-free unit tests for the real RapidOCR engine (Phase 11 ticket 11).

Everything here runs without the RapidOCR wheel or its ONNX models: the versioned
config, the bundled-model manifest verification (against a faked models dir), the
pure region shaping, the per-resolution ``limit_side_len`` policy, and the typed
failure surface. The real pipeline over real models is proved by
``tests/integration/test_phase_11_ocr_engine.py``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from video_content_pipeline import ocr_engine
from video_content_pipeline.model_acquisition import file_sha256, manifest_asset_sha256
from video_content_pipeline.ocr_engine import (
    OcrEngineError,
    OcrFrame,
    RapidOcrEngineConfig,
    analyze_frames_ocr,
    load_rapidocr_engine_config,
    projected_items_from_regions,
    recognized_regions,
    verify_bundled_models,
)
from video_content_pipeline.timecode import ExactTime

_ASSET_SHA = "b074b483b736e064d9d09805e395e64da3abde87a27d7deb3fc127f1b5026ce3"


# --- config document helpers -------------------------------------------------


def _config_document(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": 1,
        "config_version": "phase-11-rapidocr-engine-config-v1",
        "model_identity": {
            "asset_sha256": _ASSET_SHA,
            "backend": "rapidocr",
            "backend_version": "3.9.2",
            "runtime_extra": "onnxruntime==1.28.0",
            "device_class": "apple-m1",
        },
        "detection": {
            "limit_side_len": 736,
            "high_resolution_limit_side_len": 1280,
            "high_resolution_min_side": 1080,
        },
        "classification": {"use_cls": False},
        "qualification_scope": "first_device_baseline",
    }
    document.update(overrides)
    return document


def _write_config(project_root: Path, document: dict[str, Any]) -> None:
    path = project_root / "config" / "visual-text" / "rapidocr-engine-config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def _frame(pts: int = 1) -> OcrFrame:
    return OcrFrame(
        part_id="part-01",
        visual_page_id="part-01:page-01",
        pts=ExactTime(pts),
        image_path=Path("unused.png"),
    )


# --- config: parsing, validation, fingerprint --------------------------------


def test_config_round_trips_and_binds_identity(tmp_path: Path) -> None:
    _write_config(tmp_path, _config_document())
    config = load_rapidocr_engine_config(tmp_path, expected_asset_sha256=_ASSET_SHA)
    assert config.asset_sha256 == _ASSET_SHA
    assert config.use_cls is False
    assert config.limit_side_len == 736
    assert config.as_json()["config_fingerprint"] == config.config_fingerprint


def test_config_fingerprint_changes_when_a_lever_changes() -> None:
    baseline = RapidOcrEngineConfig.from_json(_config_document())
    raised = RapidOcrEngineConfig.from_json(
        _config_document(
            detection={
                "limit_side_len": 960,
                "high_resolution_limit_side_len": 1280,
                "high_resolution_min_side": 1080,
            }
        )
    )
    cls_off = RapidOcrEngineConfig.from_json(_config_document(classification={"use_cls": True}))
    assert baseline.config_fingerprint != raised.config_fingerprint
    assert baseline.config_fingerprint != cls_off.config_fingerprint


def test_config_model_mismatch_is_typed(tmp_path: Path) -> None:
    _write_config(tmp_path, _config_document())
    with pytest.raises(OcrEngineError) as excinfo:
        load_rapidocr_engine_config(tmp_path, expected_asset_sha256="deadbeef")
    assert excinfo.value.reason == "ocr_config_model_mismatch"


def test_config_absent_is_typed(tmp_path: Path) -> None:
    with pytest.raises(OcrEngineError) as excinfo:
        load_rapidocr_engine_config(tmp_path)
    assert excinfo.value.reason == "ocr_config_invalid"


@pytest.mark.parametrize(
    "document",
    [
        _config_document(schema_version=2),
        _config_document(
            detection={
                "limit_side_len": 0,
                "high_resolution_limit_side_len": 1280,
                "high_resolution_min_side": 1080,
            }
        ),
        _config_document(
            detection={
                "limit_side_len": 1280,
                "high_resolution_limit_side_len": 736,
                "high_resolution_min_side": 1080,
            }
        ),
        _config_document(classification={"use_cls": "no"}),
    ],
)
def test_config_invalid_documents_are_rejected(document: dict[str, Any]) -> None:
    with pytest.raises(OcrEngineError) as excinfo:
        RapidOcrEngineConfig.from_json(document)
    assert excinfo.value.reason == "ocr_config_invalid"


# --- per-resolution limit_side_len policy ------------------------------------


def test_limit_side_len_raises_for_high_resolution_frames() -> None:
    config = RapidOcrEngineConfig.from_json(_config_document())
    # 720p (min side 720) uses the baseline; 1080p (min side 1080) uses the raise.
    assert config.limit_side_len_for_frame(1280, 720) == 736
    assert config.limit_side_len_for_frame(1920, 1080) == 1280
    # Portrait frames branch on the shorter side too.
    assert config.limit_side_len_for_frame(1080, 1920) == 1280


# --- pure region shaping -----------------------------------------------------


def test_regions_become_verbatim_items_on_the_part_clock() -> None:
    frame = _frame(pts=3)
    items = projected_items_from_regions(frame, (("你好 world", 0.98), ("Slide 1", 0.5)))
    assert len(items) == 2
    assert items[0].part_id == "part-01"
    assert items[0].visual_page_id == "part-01:page-01"
    assert items[0].pts == ExactTime(3)
    assert items[0].text == "你好 world"  # mixed-language text is never rewritten
    assert items[0].confidence == pytest.approx(0.98)
    assert items[0].language_spans == ()


def test_empty_frame_yields_no_items() -> None:
    assert projected_items_from_regions(_frame(), ()) == ()


def test_out_of_range_confidence_is_rejected_not_clamped() -> None:
    with pytest.raises(OcrEngineError) as excinfo:
        projected_items_from_regions(_frame(), (("x", 1.2),))
    assert excinfo.value.reason == "ocr_output_invalid"


def test_recognized_regions_pairs_texts_and_scores() -> None:
    output = SimpleNamespace(txts=("你好", "world"), scores=(0.99, 0.88))
    assert recognized_regions(output) == (("你好", 0.99), ("world", 0.88))


def test_recognized_regions_of_empty_output_is_empty() -> None:
    assert recognized_regions(SimpleNamespace(txts=None, scores=None)) == ()


def test_recognized_regions_rejects_mismatched_counts() -> None:
    output = SimpleNamespace(txts=("a", "b"), scores=(0.9,))
    with pytest.raises(OcrEngineError) as excinfo:
        recognized_regions(output)
    assert excinfo.value.reason == "ocr_output_invalid"


# --- bundled-model manifest verification (faked models dir) ------------------


def _fake_registry(project_root: Path, manifest: list[dict[str, Any]], **overrides: Any) -> None:
    candidate: dict[str, Any] = {
        "candidate_id": "rapidocr",
        "capability": "ocr_primary",
        "file_manifest": manifest,
        "asset_sha256": manifest_asset_sha256(manifest),
        "default_models": {
            "det": "PP-OCRv6_det_small.onnx",
            "cls": "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
            "rec": "PP-OCRv6_rec_small.onnx",
        },
    }
    candidate.update(overrides)
    registry_path = project_root / "models" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps({"schema_version": 1, "candidates": [candidate]}), encoding="utf-8"
    )


def _install_fake_models(models_dir: Path) -> list[dict[str, Any]]:
    """Write three fake bundled model files and return their canonical manifest."""

    models_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "PP-OCRv6_det_small.onnx": b"det-model-bytes",
        "ch_ppocr_mobile_v2.0_cls_mobile.onnx": b"cls-model-bytes",
        "PP-OCRv6_rec_small.onnx": b"rec-model-bytes-longer",
        ".gitkeep": b"",
    }
    manifest: list[dict[str, Any]] = []
    for name, payload in files.items():
        (models_dir / name).write_bytes(payload)
        manifest.append(
            {
                "path": name,
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return manifest


def test_verify_bundled_models_matches_installed_reality(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models_dir = tmp_path / "wheel-models"
    manifest = _install_fake_models(models_dir)
    _fake_registry(tmp_path, manifest)
    monkeypatch.setattr(ocr_engine, "locate_bundled_models_dir", lambda: models_dir)

    resolved_dir, asset_sha, role_paths = verify_bundled_models(tmp_path)
    assert resolved_dir == models_dir
    assert asset_sha == manifest_asset_sha256(manifest)
    assert set(role_paths) == {"det", "cls", "rec"}
    assert file_sha256(role_paths["det"]) == hashlib.sha256(b"det-model-bytes").hexdigest()


def test_verify_bundled_models_flags_a_tampered_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models_dir = tmp_path / "wheel-models"
    manifest = _install_fake_models(models_dir)
    _fake_registry(tmp_path, manifest)
    monkeypatch.setattr(ocr_engine, "locate_bundled_models_dir", lambda: models_dir)
    # Corrupt one installed model after the manifest was pinned.
    (models_dir / "PP-OCRv6_rec_small.onnx").write_bytes(b"tampered")

    with pytest.raises(OcrEngineError) as excinfo:
        verify_bundled_models(tmp_path)
    assert excinfo.value.reason == "ocr_asset_mismatch"


def test_verify_bundled_models_flags_manifest_digest_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models_dir = tmp_path / "wheel-models"
    manifest = _install_fake_models(models_dir)
    # The recorded asset_sha256 no longer digests its own manifest.
    _fake_registry(tmp_path, manifest, asset_sha256="not-the-manifest-digest")
    monkeypatch.setattr(ocr_engine, "locate_bundled_models_dir", lambda: models_dir)

    with pytest.raises(OcrEngineError) as excinfo:
        verify_bundled_models(tmp_path)
    assert excinfo.value.reason == "ocr_asset_mismatch"


def test_verify_bundled_models_flags_a_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models_dir = tmp_path / "wheel-models"
    manifest = _install_fake_models(models_dir)
    _fake_registry(tmp_path, manifest)
    monkeypatch.setattr(ocr_engine, "locate_bundled_models_dir", lambda: models_dir)
    (models_dir / "ch_ppocr_mobile_v2.0_cls_mobile.onnx").unlink()

    with pytest.raises(OcrEngineError) as excinfo:
        verify_bundled_models(tmp_path)
    assert excinfo.value.reason == "ocr_asset_unavailable"


def test_verify_bundled_models_rejects_incomplete_registry_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models_dir = tmp_path / "wheel-models"
    manifest = _install_fake_models(models_dir)
    _fake_registry(tmp_path, manifest, file_manifest=[])
    monkeypatch.setattr(ocr_engine, "locate_bundled_models_dir", lambda: models_dir)

    with pytest.raises(OcrEngineError) as excinfo:
        verify_bundled_models(tmp_path)
    assert excinfo.value.reason == "ocr_asset_unavailable"


def test_verify_bundled_models_requires_the_registered_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry_path = tmp_path / "models" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(json.dumps({"schema_version": 1, "candidates": []}), encoding="utf-8")
    monkeypatch.setattr(ocr_engine, "locate_bundled_models_dir", lambda: tmp_path)

    with pytest.raises(OcrEngineError) as excinfo:
        verify_bundled_models(tmp_path)
    assert excinfo.value.reason == "ocr_candidate_absent"


def test_locate_bundled_models_dir_typed_when_runtime_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    with pytest.raises(OcrEngineError) as excinfo:
        ocr_engine.locate_bundled_models_dir()
    assert excinfo.value.reason == "ocr_runtime_unavailable"


# --- analyze orchestration (faked verification + engine) ---------------------


class _FakeOutput:
    def __init__(self, txts: tuple[str, ...], scores: tuple[float, ...]) -> None:
        self.txts = txts
        self.scores = scores


class _FakeEngine:
    def __init__(self, limit_side_len: int) -> None:
        self.limit_side_len = limit_side_len

    def __call__(self, _image: Any) -> _FakeOutput:
        return _FakeOutput(("page text",), (0.9,))


def _write_png(path: Path, width: int, height: int) -> None:
    import cv2
    import numpy as np

    cv2.imwrite(str(path), np.full((height, width, 3), 255, np.uint8))


def _patch_analyze(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, asset_sha: str) -> list[int]:
    built: list[int] = []

    def _build(
        _models_dir: Path, _config: RapidOcrEngineConfig, limit_side_len: int
    ) -> _FakeEngine:
        built.append(limit_side_len)
        return _FakeEngine(limit_side_len)

    monkeypatch.setattr(
        ocr_engine,
        "verify_bundled_models",
        lambda _root: (tmp_path / "wheel-models", asset_sha, {}),
    )
    monkeypatch.setattr(ocr_engine, "build_engine", _build)
    return built


def test_analyze_projects_items_and_reuses_engine_per_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(tmp_path, _config_document())
    built = _patch_analyze(tmp_path, monkeypatch, asset_sha=_ASSET_SHA)

    small = tmp_path / "small.png"
    large_a = tmp_path / "large_a.png"
    large_b = tmp_path / "large_b.png"
    _write_png(small, 1280, 720)
    _write_png(large_a, 1920, 1080)
    _write_png(large_b, 3840, 2160)

    frames = (
        OcrFrame("part-01", "part-01:page-01", ExactTime(1), small),
        OcrFrame("part-01", "part-01:page-02", ExactTime(2), large_a),
        OcrFrame("part-01", "part-01:page-03", ExactTime(3), large_b),
    )
    result = analyze_frames_ocr(tmp_path, frames)

    assert result.frames_processed == 3
    assert len(result.items) == 3
    assert result.asset_sha256 == _ASSET_SHA
    assert (
        result.config_fingerprint
        == RapidOcrEngineConfig.from_json(_config_document()).config_fingerprint
    )
    # One baseline engine (720p) and one raised engine (1080p+), built once each.
    assert sorted(built) == [736, 1280]


def test_analyze_rejects_unreadable_frame(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path, _config_document())
    _patch_analyze(tmp_path, monkeypatch, asset_sha=_ASSET_SHA)
    frames = (OcrFrame("part-01", "part-01:page-01", ExactTime(1), tmp_path / "absent.png"),)
    with pytest.raises(OcrEngineError) as excinfo:
        analyze_frames_ocr(tmp_path, frames)
    assert excinfo.value.reason == "ocr_frame_unreadable"


def test_analyze_rejects_config_bound_to_other_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(tmp_path, _config_document())
    _patch_analyze(tmp_path, monkeypatch, asset_sha="a-different-model-set")
    with pytest.raises(OcrEngineError) as excinfo:
        analyze_frames_ocr(tmp_path, ())
    assert excinfo.value.reason == "ocr_config_model_mismatch"
