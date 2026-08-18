"""Model runtime child for the real sherpa-onnx diarization engine (ticket 08).

The model-touching end of the Model runtime subprocess for diarization. The
diarization assets are ONNX-scale and ran in-process historically (ADR 0055);
ADR 0058 extends the boundary to them so the orchestrated ``vcp run``
records an honest, baseline-comparable per-capability peak -- both pinned assets
load in this fresh child and its ``ru_maxrss`` high-water mark is measured exactly
as the Phase 11 device baselines were (one capability per process).

The parent (:func:`video_content_pipeline.diarization_engine.run_isolated_diarization`)
serializes the pinned derivative wav path and mapping; this child runs the whole
``analyze_derivative_diarization`` sequence and returns the anonymous cluster
candidates and the two asset hashes. The ADR 0030/0031 gate is deliberately left
to the parent (it needs the real VAD partition and the run's role evidence), so
the child passes no VAD intervals. Heavy imports are lazy; exceptions propagate to
a nonzero exit the parent isolates as ``engine_child_exit_nonzero`` evidence.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from video_content_pipeline.model_runtime import EngineRequest, execute_child


def run_diarization(request: EngineRequest) -> dict[str, Any]:
    """Run the real sherpa-onnx diarization and return anonymous candidates."""

    from video_content_pipeline.audio_derivation import DerivativeTimeMapping
    from video_content_pipeline.diarization_engine import (
        _speaker_turn_candidate_as_json,
        analyze_derivative_diarization,
    )

    task = request.task
    project_root = Path(str(request.model_path))
    wav_path = Path(str(task["wav_path"]))
    mapping = DerivativeTimeMapping.from_json(task["mapping"])

    result = analyze_derivative_diarization(
        project_root,
        wav_path,
        mapping,
        source_id=str(task["source_id"]),
        stream_index=int(task["stream_index"]),
        part_label=str(task["part_label"]),
    )

    return {
        "raw_turns": [_speaker_turn_candidate_as_json(turn) for turn in result.raw_turns],
        "segmentation_asset_sha256": result.segmentation_asset_sha256,
        "embedding_asset_sha256": result.embedding_asset_sha256,
        "calibrated": result.calibrated,
    }


if __name__ == "__main__":  # pragma: no cover - exercised via the subprocess seam
    sys.exit(execute_child(run_diarization))
