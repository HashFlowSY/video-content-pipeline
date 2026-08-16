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
**Status:** done
**Labels:** ready-for-agent

- [x] All eight dependencies resolve and lock through the project uv flow
      (`uv sync --locked --all-groups` is a clean no-op afterward; 79 locked
      packages). They live in a new `inference` dependency group; the base
      `dependencies = []` is unchanged (the offline deterministic adapters
      need none of them)
- [x] Lockfile gate test fails if any banned package enters `uv.lock`
      (`tests/unit/test_lockfile_gate.py`): asserts no torch / torchvision /
      torchaudio / nvidia-* / modelscope distribution is *installable* (a
      resolved node carrying a wheel or sdist), tolerating only the sanctioned
      artifact-free torch phantom (see deviation 1). RED proven: a torch node
      with real wheels, or the override removed, trips the gate
- [x] Import smoke tests pass offline (`tests/unit/test_inference_imports.py`):
      each of the eight import names is imported in a fresh subprocess with the
      network hard-blocked and HOME/cache/cwd redirected to empty temp trees;
      every import succeeds, writes zero files, and never pulls torch into
      `sys.modules`
- [x] Ruff/mypy/pytest gates green (ruff check clean, mypy clean on 57 src
      files, full suite 1356 passed in ~19s); deviations recorded below

## Completion notes (2026-08-16) — recorded deviations

1. **mlx-whisper pulls torch (maintainer-approved exclusion).** Every published
   `mlx-whisper` version (0.4.0–0.4.3) declares `torch` as an *unconditional*
   runtime dependency, contradicting the research doc's "torch-free" claim and
   the phase's torch-free invariant. Surfaced to the maintainer, who approved
   Option A (2026-08-16): keep `mlx-whisper==0.4.3` and exclude torch via
   `pyproject.toml` `[tool.uv] override-dependencies = ["torch; sys_platform ==
   'never'"]`. torch's entire subtree (CUDA `nvidia-*`, `triton`, `cuda-*`) is
   pruned; torch itself remains only as an artifact-free phantom node (no
   wheels, no sdist) reachable only through an always-false marker, so it can
   never install. Functional basis: torch is referenced solely by
   `mlx_whisper/torch_whisper.py` (the OpenAI→MLX checkpoint-conversion
   reference), which nothing in the package imports; `mlx_whisper.transcribe`
   on pre-converted `whisper-large-v3-mlx` weights never touches torch
   (verified against the 0.4.3 wheel). Standing red line for downstream
   tickets: import `mlx_whisper` (torch-free), never `mlx_whisper.torch_whisper`.
2. **sherpa-onnx-core==1.13.5 pinned explicitly (extra dependency).**
   `sherpa-onnx`'s wheel marks `requires-dist` as `Dynamic`, so uv's
   registry-metadata resolver drops the `sherpa-onnx-core==1.13.5` edge (pip
   would recover it post-install). Without the core companion the native
   `libonnxruntime.dylib` is absent and `import sherpa_onnx` fails with a dlopen
   error. Pinned the companion at the same version; nine expected dists now
   lock (the eight authorized names + this companion).
3. **opencv-python locked to 5.0.0.93.** Declared as the floor `>=4.5.1.48`
   (spec) and pinned by the lock to the current release, which is the opencv 5
   major line. Import smoke passes; real OCR behavior against opencv 5 is for
   ticket 11 to validate.

No baseline *version* deviated for the seven exact-pinned packages (mlx-audio
0.4.8, mlx-whisper 0.4.3, mlx-lm 0.31.3, sherpa-onnx 1.13.5, onnxruntime
1.28.0, rapidocr 3.9.2 all locked exactly); `huggingface-hub` latest-stable
resolved to 1.27.0. New files: `tests/unit/test_lockfile_gate.py`,
`tests/unit/test_inference_imports.py`. No model was downloaded, loaded,
hashed, or executed.
