"""Model runtime child for the real RapidOCR engine (Phase 12 ticket 08).

The model-touching end of the Model runtime subprocess for OCR. RapidOCR is
ONNX-scale and ran in-process historically (ADR 0055); ADR 0058 extends the
boundary to it so the orchestrated ``vcp run`` records an honest, baseline-
comparable per-capability peak -- the models load in this fresh child and its
``ru_maxrss`` high-water mark is measured exactly as the Phase 11 device baselines
were (one capability per process).

The parent (:func:`video_content_pipeline.ocr_engine.run_isolated_ocr`) serializes
the deterministically selected frames (identity + extracted image path); this child
runs the whole ``analyze_frames_ocr`` sequence and returns the projected-item
evidence (already ``as_json``) plus the bound provenance. Heavy imports are lazy;
exceptions propagate to a nonzero exit the parent isolates as
``engine_child_exit_nonzero`` evidence.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from video_content_pipeline.model_runtime import EngineRequest, execute_child


def run_ocr(request: EngineRequest) -> dict[str, Any]:
    """Run the real RapidOCR over the selected frames and return item evidence."""

    from video_content_pipeline.ocr_engine import OcrFrame, analyze_frames_ocr
    from video_content_pipeline.timecode import ExactTime

    task = request.task
    project_root = Path(str(request.model_path))
    frames = tuple(
        OcrFrame(
            part_id=str(frame["part_id"]),
            visual_page_id=str(frame["visual_page_id"]),
            pts=ExactTime(int(frame["pts"]["numerator"]), int(frame["pts"]["denominator"])),
            image_path=Path(str(frame["image_path"])),
        )
        for frame in task.get("frames", [])
    )
    result = analyze_frames_ocr(project_root, frames)
    return result.as_json()


if __name__ == "__main__":  # pragma: no cover - exercised via the subprocess seam
    sys.exit(execute_child(run_ocr))
