# 03 — Introduce the inference dependencies and the torch-free lockfile gate

**What to build:** Add the one-shot authorized dependency list via
`tools/uv/uv` (exact-pinned in `uv.lock`): mlx-audio (0.4.8 baseline),
mlx-whisper (0.4.3), mlx-lm (0.31.3), sherpa-onnx (1.13.5), onnxruntime
(1.28.0), rapidocr (3.9.2), opencv-python (declared floor, pinned at
lock), huggingface_hub (latest stable). Deviations from baselines are
recorded in the ticket comment, not silently absorbed. Add a lockfile gate
test asserting torch, torchvision, torchaudio, nvidia-*, and modelscope
never appear in `uv.lock`. Import smoke tests for each new package (import
only — no model load, no network).

**Blocked by:** —
**Status:** open
**Labels:** ready-for-agent

- [ ] All eight dependencies resolve and lock through the project uv flow
      (`uv sync --locked` clean afterward)
- [ ] Lockfile gate test fails if any banned package enters `uv.lock`
- [ ] Import smoke tests pass offline; no package performs network or
      filesystem writes at import time (asserted)
- [ ] Ruff/mypy/pytest gates green; any baseline-version deviation
      recorded
