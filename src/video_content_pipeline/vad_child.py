"""Model runtime child for the real silero VAD engine (Phase 12 ticket 08).

The model-touching end of the Model runtime subprocess for VAD. ADR 0055 crosses
this boundary for MLX-scale engines so unified memory returns to the OS; ADR 0058
extends the boundary to the ONNX-scale VAD engine as well so the orchestrated
``vcp run`` records an **honest, baseline-
comparable** per-capability peak: the silero session is loaded in this fresh child
and its ``ru_maxrss`` high-water mark is measured exactly as the Phase 11 device
baselines were (one capability per process), instead of the process-cumulative
figure a shared in-process run would report.

The parent (:func:`video_content_pipeline.vad_engine.run_isolated_vad`) serializes
the pinned 16 kHz mono derivative wav path, its source-clock mapping, and the
Primary caption intervals; this child runs the whole ``analyze_derivative_vad``
sequence and returns the report-shaped Complete VAD partition evidence plus the
speech-run sample spans the parent re-derives chunks from. Heavy imports are lazy;
exceptions are intentionally not caught so they propagate to a nonzero exit whose
stderr the parent isolates as ``engine_child_exit_nonzero`` evidence.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from video_content_pipeline.model_runtime import EngineRequest, execute_child


def run_vad(request: EngineRequest) -> dict[str, Any]:
    """Run the real silero VAD over one derivative and return report evidence."""

    from video_content_pipeline.audio_analysis import _vad_part_evidence_as_json
    from video_content_pipeline.audio_derivation import DerivativeTimeMapping
    from video_content_pipeline.timecode import ExactTime, HalfOpenInterval
    from video_content_pipeline.vad_engine import analyze_derivative_vad

    task = request.task
    project_root = Path(str(request.model_path))
    wav_path = Path(str(task["wav_path"]))
    mapping = DerivativeTimeMapping.from_json(task["mapping"])
    caption_intervals = tuple(
        HalfOpenInterval(
            ExactTime(int(interval["start"]["numerator"]), int(interval["start"]["denominator"])),
            ExactTime(int(interval["end"]["numerator"]), int(interval["end"]["denominator"])),
        )
        for interval in task.get("caption_intervals", [])
    )

    result = analyze_derivative_vad(
        project_root,
        wav_path,
        mapping,
        source_id=str(task["source_id"]),
        stream_index=int(task["stream_index"]),
        caption_intervals=caption_intervals,
    )

    return {
        "part_evidence": _vad_part_evidence_as_json(result.part_evidence),
        "speech_runs_samples": [
            [int(start), int(end)] for start, end in result.speech_runs_samples
        ],
        "model_asset_sha256": result.model_asset_sha256,
        "calibrated": result.calibrated,
    }


if __name__ == "__main__":  # pragma: no cover - exercised via the subprocess seam
    sys.exit(execute_child(run_vad))
