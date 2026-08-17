"""Model runtime child for the real review ASR engine (Phase 11 ticket 09).

This is the model-touching end of the Model runtime subprocess (ADR 0055) for
asr_review: the parent (:mod:`video_content_pipeline.asr_engine`) serializes one
suspicious interval's VAD-trimmed *speech* windows, and this child loads
whisper-large-v3-mlx through mlx-whisper *once*, slices and concatenates those
windows out of the shared 16 kHz mono derivative wav (dropping the interval's
silence, so whisper never hallucinates over it), transcribes the concatenation, and
returns the interval transcript text -- the independent-review candidate the
Deterministic transcription arbitration consumes. Peak memory is reported from the
MLX allocator, and the process exits so unified memory returns to the OS.

The standing Phase 11 red line holds: this imports ``mlx_whisper`` (whose
``transcribe`` runs on the pre-converted MLX weights torch-free), *never*
``mlx_whisper.torch_whisper``. Heavy imports are lazy; exceptions are intentionally
not caught so they propagate to a nonzero exit whose stderr the parent isolates as
``engine_child_exit_nonzero`` evidence.
"""

from __future__ import annotations

import sys
import wave
from typing import Any

from video_content_pipeline.model_runtime import EngineRequest, execute_child

_REVIEW_SAMPLE_RATE = 16000


def _read_wav_float32(wav_path: str) -> Any:
    """Read a 16 kHz mono PCM-16 wav into a normalized float32 sample array."""

    import numpy as np

    with wave.open(wav_path, "rb") as handle:
        if (
            handle.getnchannels() != 1
            or handle.getsampwidth() != 2
            or handle.getframerate() != _REVIEW_SAMPLE_RATE
        ):
            raise ValueError("The review ASR requires a 16 kHz mono PCM-16 derivative wav.")
        frames = handle.readframes(handle.getnframes())
    pcm = np.frombuffer(frames, dtype="<i2").astype(np.float32)
    return pcm / 32768.0


def review(request: EngineRequest) -> dict[str, Any]:
    """Load whisper, transcribe one interval's concatenated speech windows, return text."""

    import mlx_whisper
    import numpy as np

    task = request.task
    samples = _read_wav_float32(str(task["wav_path"]))
    windows = list(task["windows"])
    if not windows:
        return {"text": ""}
    speech = np.concatenate([samples[int(start) : int(end)] for start, end in windows])
    code = str(task["language"])

    output = mlx_whisper.transcribe(
        speech,
        path_or_hf_repo=str(request.model_path),
        language=code or None,
        word_timestamps=False,
        verbose=None,
    )
    return {"text": str(output["text"])}


def _mlx_peak_memory_bytes() -> int:
    import mlx.core as mx

    return int(mx.get_peak_memory())


if __name__ == "__main__":  # pragma: no cover - exercised via the subprocess seam
    sys.exit(execute_child(review, peak_probe=_mlx_peak_memory_bytes))
