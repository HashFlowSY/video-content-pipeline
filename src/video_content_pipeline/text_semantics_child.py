"""Model runtime child for the real text-semantics engine (Phase 11 ticket 10).

This is the model-touching end of the Model runtime subprocess (ADR 0055) for
text_semantics: the parent (:mod:`video_content_pipeline.text_semantics_engine`)
serializes the rendered versioned prompt and the deterministic decoding controls,
and this child loads Qwen3-4B-Instruct-2507-8bit through mlx-lm *once*, decodes the
prompt greedily under a fixed seed and a bounded KV cache, and returns the raw
generated text. Peak memory is reported from the MLX allocator, and the process exits
so unified memory returns to the OS.

Everything model-specific lives here and nowhere else: heavy imports are lazy so the
module is cheap to import, and the standing Phase 11 red line holds -- this imports
``mlx_lm``/``mlx``, never any torch path. Exceptions are intentionally not caught:
they propagate to a nonzero exit whose stderr the parent isolates as
``engine_child_exit_nonzero`` evidence.
"""

from __future__ import annotations

import sys
from typing import Any

from video_content_pipeline.model_runtime import EngineRequest, execute_child


def generate_text(request: EngineRequest) -> dict[str, Any]:
    """Load Qwen3-4B, decode the prompt deterministically, and return the raw text."""

    import mlx.core as mx
    from mlx_lm import generate, load
    from mlx_lm.sample_utils import make_sampler

    task = request.task
    sampling = task["sampling"]
    if not isinstance(sampling, dict):
        raise ValueError("The text-semantics request omits a sampling configuration.")

    # A fixed seed plus temperature 0 (greedy argmax) makes decoding deterministic.
    mx.random.seed(int(sampling["seed"]))
    model, tokenizer = load(str(request.model_path))
    messages = [{"role": "user", "content": str(task["prompt"])}]
    prompt_ids = tokenizer.apply_chat_template(messages, add_generation_prompt=True)
    text = generate(
        model,
        tokenizer,
        prompt=prompt_ids,
        max_tokens=int(sampling["max_tokens"]),
        sampler=make_sampler(temp=float(sampling["temperature"])),
        max_kv_size=int(task["max_kv_size"]),
    )
    return {"text": str(text)}


def _mlx_peak_memory_bytes() -> int:
    import mlx.core as mx

    return int(mx.get_peak_memory())


if __name__ == "__main__":  # pragma: no cover - exercised via the subprocess seam
    sys.exit(execute_child(generate_text, peak_probe=_mlx_peak_memory_bytes))
