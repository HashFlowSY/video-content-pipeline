"""The OCR subprocess seam: child handler shape + parent parsing (no real model)."""

from __future__ import annotations

from pathlib import Path

import pytest

from video_content_pipeline import ocr_child, ocr_engine
from video_content_pipeline.model_runtime import EngineRequest, EngineResult
from video_content_pipeline.ocr_engine import IsolatedOcrResult, OcrEngineError, OcrEngineResult
from video_content_pipeline.timecode import ExactTime
from video_content_pipeline.visual_text_contracts import ProjectedOcrItem


def _canned_result() -> OcrEngineResult:
    return OcrEngineResult(
        items=(
            ProjectedOcrItem(
                part_id="part-a",
                visual_page_id="page-1",
                pts=ExactTime(1),
                text="hello",
                confidence=0.9,
                language_spans=(),
            ),
        ),
        asset_sha256="a" * 64,
        config_version="cfg-v1",
        config_fingerprint="fp",
        frames_processed=1,
    )


def test_run_ocr_handler_returns_item_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_analyze(project_root, frames, **kwargs):
        captured["project_root"] = project_root
        captured["frames"] = frames
        return _canned_result()

    monkeypatch.setattr(ocr_engine, "analyze_frames_ocr", fake_analyze)

    request = EngineRequest(
        model_path="/proj",
        task={
            "frames": [
                {
                    "part_id": "part-a",
                    "visual_page_id": "page-1",
                    "pts": {"numerator": 1, "denominator": 1},
                    "image_path": "/proj/frames/1.png",
                }
            ]
        },
    )
    output = ocr_child.run_ocr(request)

    assert output == _canned_result().as_json()
    assert captured["project_root"] == Path("/proj")
    frame = captured["frames"][0]
    assert frame.part_id == "part-a"
    assert frame.image_path == Path("/proj/frames/1.png")
    assert frame.pts == ExactTime(1)


def test_run_isolated_ocr_moves_result_and_peak(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_subprocess(command, request, *, timeout_seconds):
        return EngineResult(
            result=_canned_result().as_json(), peak_memory_bytes=535_003_136, child_pid=7
        )

    monkeypatch.setattr(ocr_engine, "run_engine_subprocess", fake_subprocess)

    result = ocr_engine.run_isolated_ocr(
        Path("/proj"),
        (
            ocr_engine.OcrFrame("part-a", "page-1", ExactTime(1), Path("/proj/frames/1.png")),
        ),
        command=["stub"],
    )
    assert isinstance(result, IsolatedOcrResult)
    assert result.result == _canned_result().as_json()
    assert result.peak_memory_bytes == 535_003_136


def test_run_isolated_ocr_rejects_malformed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ocr_engine,
        "run_engine_subprocess",
        lambda *a, **k: EngineResult(result={"items": "nope"}, peak_memory_bytes=1, child_pid=1),
    )
    with pytest.raises(OcrEngineError) as error:
        ocr_engine.run_isolated_ocr(Path("/proj"), (), command=["stub"])
    assert error.value.reason == "ocr_output_invalid"
