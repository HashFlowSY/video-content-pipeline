"""Real RapidOCR over a runtime-rendered text frame (Phase 11 ticket 11).

This is the one place the real offline RapidOCR pipeline runs against the bundled
PP-OCRv6 models that ship inside the pinned ``rapidocr==3.9.2`` wheel -- offline,
in-process, from disk, on the provisioned machine (error, never skip, mirroring
the other Phase 11 engine tests). It proves the real engine produces
*contract-valid* :class:`ProjectedOcrItem` that flow through the unchanged gate
(the classification / fact-upgrade consumers downstream, ADR 0049), and that the
installed bundled models match the pinned registry manifest. OCR quality on real
video frames (zh + en) is the maintainer's prototype review (ticket 13); this test
asserts the contract, structure, and provenance.

The frame is rendered at runtime with OpenCV's built-in vector font -- genuine
rendered text, no fixture asset -- so the test is self-contained and offline.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from video_content_pipeline.ocr_engine import (
    OcrFrame,
    analyze_frames_ocr,
    verify_bundled_models,
)
from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
from video_content_pipeline.visual_page_index import (
    PageAppearance,
    PartPageIndex,
    VisualPage,
)
from video_content_pipeline.visual_text_gates import gate_ocr_items

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]


def _render_text_frame(path: Path, width: int, height: int) -> None:
    """Render a white frame with large black text using OpenCV's built-in font."""

    import cv2
    import numpy as np

    image = np.full((height, width, 3), 255, np.uint8)
    cv2.putText(image, "SLIDE 12", (80, height // 2), cv2.FONT_HERSHEY_SIMPLEX, 6, (0, 0, 0), 12)
    cv2.imwrite(str(path), image)


def _registry_asset_sha256() -> str:
    registry = json.loads((REPO_ROOT / "models" / "registry.json").read_text(encoding="utf-8"))
    (candidate,) = [c for c in registry["candidates"] if c.get("candidate_id") == "rapidocr"]
    return str(candidate["asset_sha256"])


def test_real_ocr_yields_contract_valid_items_through_the_gate(tmp_path: Path) -> None:
    # A 1080p frame exercises the raised limit_side_len (small-text protection).
    frame_path = tmp_path / "page-01.png"
    _render_text_frame(frame_path, 1920, 1080)
    frame = OcrFrame(
        part_id="part-01",
        visual_page_id="part-01:page-01",
        pts=ExactTime(1),
        image_path=frame_path,
    )

    result = analyze_frames_ocr(REPO_ROOT, (frame,))

    # Provenance: the real bundled model set produced these, under the versioned config.
    assert result.frames_processed == 1
    assert result.asset_sha256 == _registry_asset_sha256()
    assert result.config_version == "phase-11-rapidocr-engine-config-v1"
    assert result.items, "the rendered text frame must yield at least one OCR item"

    # Every item is contract-valid: it carries the frame's identity and an in-range
    # confidence, and never rewrites its recognised text.
    for item in result.items:
        assert item.part_id == "part-01"
        assert item.visual_page_id == "part-01:page-01"
        assert item.pts == ExactTime(1)
        assert 0.0 <= item.confidence <= 1.0
        assert item.text != ""

    # The unchanged gate admits them against a page index that places the page on
    # the Part clock -- proving the real output feeds the existing consumers.
    page_index = PartPageIndex(
        part_id="part-01",
        detection_version="phase-08-page-change-detection-v1",
        sampling_version="phase-08-frame-sampling-v1",
        pages=(
            VisualPage(
                visual_page_id="part-01:page-01",
                content_fingerprint="fp-01",
                appearances=(PageAppearance(ExactTime(0), ExactTime(2), 1),),
                selected_frame_pts=ExactTime(1),
            ),
        ),
        retained_frames=(),
    )
    gated = gate_ocr_items(
        part_id="part-01",
        items=result.items,
        page_index=page_index,
        coverage=HalfOpenInterval(ExactTime(0), ExactTime(3)),
    )
    assert gated.rejected == ()
    assert len(gated.admitted) == len(result.items)


def test_real_ocr_bundled_models_match_the_registry_manifest() -> None:
    models_dir, asset_sha256, role_paths = verify_bundled_models(REPO_ROOT)
    assert models_dir.is_dir()
    assert asset_sha256 == _registry_asset_sha256()
    assert set(role_paths) == {"det", "cls", "rec"}
    for path in role_paths.values():
        assert path.is_file()
