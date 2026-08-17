"""Model runtime child for the real primary ASR engine (Phase 11 ticket 09).

This is the model-touching end of the Model runtime subprocess (ADR 0055) for
asr_primary: the parent (:mod:`video_content_pipeline.asr_engine`) serializes one
VAD chunk's speech window, and this child loads Qwen3-ASR-1.7B-8bit through
mlx-audio *once*, slices that window out of the shared 16 kHz mono derivative wav,
transcribes it, and returns the chunk transcript text. Peak memory is reported from
the MLX allocator, and the process exits so unified memory returns to the OS.

Everything model-specific lives here and nowhere else: heavy imports are lazy so
the module is cheap to import. Exceptions are intentionally not caught: they
propagate to a nonzero exit whose stderr the parent isolates as
``engine_child_exit_nonzero`` evidence.
"""

from __future__ import annotations

import sys
import wave
from typing import Any

from video_content_pipeline.model_runtime import EngineRequest, execute_child

#: Qwen3-ASR matches its ``language`` argument case-insensitively against the
#: language *names* in its config (e.g. ``"Chinese"``, ``"English"``), so the
#: pipeline's short language codes are mapped to those names. The phase scope is
#: zh+en; an unmapped code is passed through unchanged and an empty code lets the
#: model auto-detect.
_ASR_LANGUAGE_NAMES = {"zh": "Chinese", "en": "English"}

_ASR_SAMPLE_RATE = 16000


def _read_wav_float32(wav_path: str) -> Any:
    """Read a 16 kHz mono PCM-16 wav into a normalized float32 sample array."""

    import numpy as np

    with wave.open(wav_path, "rb") as handle:
        if (
            handle.getnchannels() != 1
            or handle.getsampwidth() != 2
            or handle.getframerate() != _ASR_SAMPLE_RATE
        ):
            raise ValueError("The primary ASR requires a 16 kHz mono PCM-16 derivative wav.")
        frames = handle.readframes(handle.getnframes())
    pcm = np.frombuffer(frames, dtype="<i2").astype(np.float32)
    return pcm / 32768.0


def transcribe(request: EngineRequest) -> dict[str, Any]:
    """Load Qwen3-ASR, transcribe one chunk's speech window, and return its text."""

    from mlx_audio.stt.utils import load_model

    task = request.task
    samples = _read_wav_float32(str(task["wav_path"]))
    window = samples[int(task["start_sample"]) : int(task["end_sample"])]
    code = str(task["language"])
    language = _ASR_LANGUAGE_NAMES.get(code, code) if code else None

    model = load_model(str(request.model_path))
    result = model.generate(audio=window, language=language)
    return {"text": str(result.text)}


def _mlx_peak_memory_bytes() -> int:
    import mlx.core as mx

    return int(mx.get_peak_memory())


if __name__ == "__main__":  # pragma: no cover - exercised via the subprocess seam
    sys.exit(execute_child(transcribe, peak_probe=_mlx_peak_memory_bytes))
