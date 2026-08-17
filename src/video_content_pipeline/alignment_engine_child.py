"""Model runtime child for the real Qwen3-ForcedAligner engine (Phase 11 ticket 08).

This is the model-touching end of the Model runtime subprocess (ADR 0055): the
parent (:mod:`video_content_pipeline.alignment_engine`) serializes one VAD chunk's
per-cue audio windows, and this child loads Qwen3-ForcedAligner-0.6B-8bit through
mlx-audio *once*, slices each window out of the shared 16 kHz mono derivative wav,
batch-aligns the cue texts, and returns the aligner's word/char ``{text, start,
end}`` items (window-local seconds) per cue. Peak memory is reported from the MLX
allocator, and the process exits so unified memory returns to the OS.

Everything model-specific lives here and nowhere else: heavy imports are lazy so
the module is cheap to import, and the standing Phase 11 red line holds -- this
imports ``mlx_audio``/``mlx``, never ``mlx_whisper.torch_whisper``. Exceptions are
intentionally not caught: they propagate to a nonzero exit whose stderr the parent
isolates as ``engine_child_exit_nonzero`` evidence.
"""

from __future__ import annotations

import sys
import wave
from typing import Any

from video_content_pipeline.model_runtime import EngineRequest, execute_child

#: Qwen3-ForcedAligner tokenizes CJK languages differently from space-separated
#: ones; the pipeline's short language codes are mapped to the aligner's expected
#: language names so Chinese/Japanese/Korean take their character-aware paths.
#: Any other code passes through into the space-separated (English-style) path.
_ALIGNER_LANGUAGE_NAMES = {"zh": "Chinese", "ja": "Japanese", "ko": "Korean"}

_ALIGNER_SAMPLE_RATE = 16000


def _read_wav_float32(wav_path: str) -> Any:
    """Read a 16 kHz mono PCM-16 wav into a normalized float32 sample array."""

    import numpy as np

    with wave.open(wav_path, "rb") as handle:
        if (
            handle.getnchannels() != 1
            or handle.getsampwidth() != 2
            or handle.getframerate() != _ALIGNER_SAMPLE_RATE
        ):
            raise ValueError("The aligner requires a 16 kHz mono PCM-16 derivative wav.")
        frames = handle.readframes(handle.getnframes())
    pcm = np.frombuffer(frames, dtype="<i2").astype(np.float32)
    return pcm / 32768.0


def align(request: EngineRequest) -> dict[str, Any]:
    """Load the aligner, batch-align each cue's window, and return items per cue."""

    from mlx_audio.stt.utils import load_model

    task = request.task
    samples = _read_wav_float32(str(task["wav_path"]))
    language_name = _ALIGNER_LANGUAGE_NAMES.get(str(task["language"]), str(task["language"]))
    cues = list(task["cues"])

    audios = [samples[int(cue["start_sample"]) : int(cue["end_sample"])] for cue in cues]
    texts = [str(cue["text"]) for cue in cues]
    languages = [language_name] * len(cues)

    model = load_model(str(request.model_path))
    results = model.generate(audio=audios, text=texts, language=languages)
    if not isinstance(results, list):
        results = [results]

    return {
        "cues": [
            {
                "source_ordinal": int(cue["source_ordinal"]),
                "items": [
                    {
                        "text": str(segment["text"]),
                        "start": float(segment["start"]),
                        "end": float(segment["end"]),
                    }
                    for segment in result.segments
                ],
            }
            for cue, result in zip(cues, results, strict=True)
        ]
    }


def _mlx_peak_memory_bytes() -> int:
    import mlx.core as mx

    return int(mx.get_peak_memory())


if __name__ == "__main__":  # pragma: no cover - exercised via the subprocess seam
    sys.exit(execute_child(align, peak_probe=_mlx_peak_memory_bytes))
