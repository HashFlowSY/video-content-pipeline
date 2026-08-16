# Phase 11 Specification: Model Acquisition and Real Engine Integration

## Domain routing

Read [CONTEXT-MAP.md](../CONTEXT-MAP.md) first. This phase gives existing
capability contracts their first real engines; it introduces no new context.
Vocabulary it adds is owned as follows: real-adapter and acquisition terms
land in the context that owns each capability contract —
[audio-analysis](contexts/audio-analysis/CONTEXT.md) (vad,
forced_alignment, diarization — filling the recorded `Diarization
capability vacancy`), [transcription](contexts/transcription/CONTEXT.md)
(asr_primary, asr_review), [visual-text](contexts/visual-text/CONTEXT.md)
(ocr_primary), and [text-analysis](contexts/text-analysis/CONTEXT.md)
(the new `text_semantics` capability). The cross-context execution
mechanism (Model runtime subprocess) is a new global ADR with the term
owned by [orchestration](contexts/orchestration/CONTEXT.md).

## Status

`specification_pending_approval` (grilling consensus 2026-08-16, 20
questions over 4 rounds; decision checklist D1–D7 all resolved to the
research recommendations, with D7's quantization tier adjusted from 4-bit
to 8-bit-first to comply with the plan's standing §2.2 boundary "prefer
8-bit; drop to 4-bit only when memory estimates or measurements exceed
limits").

This phase was split out of the former Phase 11 (real-video testing, now
Phase 12) by the 2026-08-16 plan amendment. Selection evidence:
[research/phase-11-model-selection.md](../research/phase-11-model-selection.md)
(four primary-source research passes, decision matrices per capability).

## Objective

Take every model capability the code actually consumes — `asr_primary`,
`asr_review`, `forced_alignment`, `vad`, `diarization`, `ocr_primary` —
plus the newly defined `text_semantics`, from
`model_acquisition_required` to genuinely executable with prototype
evidence on real media, under the revised 12 GiB resource envelope, on
the confirmed final test machine (Apple M1, 16 GiB unified memory).

## Locked selections (D1–D7)

| Capability | Model asset | Runtime |
|---|---|---|
| asr_primary | `mlx-community/Qwen3-ASR-1.7B-8bit` | mlx-audio |
| asr_review | `mlx-community/whisper-large-v3-mlx` (fp16) | mlx-whisper |
| forced_alignment | `mlx-community/Qwen3-ForcedAligner-0.6B-8bit` | mlx-audio |
| vad | silero-vad v6.2.1 `silero_vad.onnx` (vendored, tag+hash pinned) | onnxruntime |
| diarization | sherpa-onnx pyannote-segmentation-3.0 ONNX + 3D-Speaker CAM++ zh-en advanced | sherpa-onnx |
| ocr_primary | RapidOCR bundled PP-OCRv6 small det+rec (zh+en) | rapidocr + onnxruntime |
| text_semantics | `mlx-community/Qwen3-4B-Instruct-2507-8bit` (8-bit first; 4-bit is the recorded over-envelope fallback) | mlx-lm |

Every asset is Apache-2.0 or MIT, credential-free, and free of charge.
The whole stack is **torch-free** (the silero pip package is bypassed by
vendoring its `.onnx`; the official `qwen-asr` package is rejected —
CUDA-oriented, gradio/flask hard deps, pinned `transformers==4.57.6`).
If a pinned 8-bit variant turns out not to exist at download-plan time,
the fallback ladder is the same repo family's next tier, resolved in that
model's download-plan confirmation — never silently.

## Governance boundaries

- **Per-model download confirmation** (plan §13.2, §14.2): each model gets
  an individual download plan — repo id, revision (commit SHA), file
  manifest, total size, license, target path
  `models/<provider>/<model>/<revision>/` — confirmed by the maintainer
  before any bytes move. Downloads go through the pinned `hf` CLI
  (`hf download --revision`) or direct pinned URLs (GitHub release assets
  for sherpa-onnx models, tagged repo file for silero), staged via
  `cache/model-downloads/`, then hashed into the registry. Model download
  authorization is never reused as media download authorization, and vice
  versa.
- **One-shot dependency authorization**: approving this specification
  authorizes exactly the dependency list in Workstream B. Any dependency
  beyond it needs separate confirmation (Phase 10 `hypothesis` precedent).
- **Production runs stay offline**: real adapters set `HF_HUB_OFFLINE=1`
  (and equivalents) so no library can silently reach a model hub;
  absence of a pinned asset is a typed failure, never a download.
- **Prototype media**: Claude selects public, DRM-free real material
  (zh/en speech; text-bearing frames) and presents a media download plan
  (URL, duration, size) for quick confirmation — prototype material must
  never block on the maintainer supplying a video. First real-media
  processing flips `media_processed` to `true`, recorded honestly in the
  completion report with object and purpose.
- Constraint flips this phase: `models_downloaded` → `true` at the first
  confirmed model download; `models_registry_entries` reflects the real
  count; `paid_apis_used` stays `false` forever.

## Workstream A — Registry and acquisition

1. Registry schema/entries: complete every candidate to the plan §13.2
   required fields (id, purpose, source, license, exact revision, file
   manifest + sizes + SHA-256, local path, quantization, compatible
   runtime versions, first-download authorization record, verification
   status). Add the missing candidates: the two diarization assets
   (`sherpa-onnx-pyannote-segmentation-3-0`,
   `3dspeaker-campplus-zh-en-advanced`), the `text_semantics` candidate,
   and the vendored silero asset. Record RapidOCR's approval (Apache-2.0,
   official source approved 2026-08-16; its default models ship inside
   the pinned wheel — the registry entry records wheel version + model
   SHA-256s from `default_models.yaml` and the post-install
   `RapidOCR().config` dump).
2. Execute the seven download plans (per-model confirmation as above).
3. `vcp models` surfaces acquisition state truthfully (list/verify
   against the registry; no download subcommand is added unless a ticket
   proves it necessary — downloads are maintainer-confirmed actions, not
   pipeline behavior).
4. Re-probe `yt-dlp` 2026.07.04 on this machine into `config/tools.json`
   (sha256, resolved path, version identity — ffmpeg re-probe precedent);
   upgrades only on real failure, separately confirmed. Remove nothing
   historical.

## Workstream B — Runtime dependencies (the one-shot authorized list)

Added via `tools/uv/uv` to `pyproject.toml` + `uv.lock`, exact-pinned:

| Dependency | Baseline | License | Serves |
|---|---|---|---|
| mlx-audio | 0.4.8 | MIT | asr_primary, forced_alignment |
| mlx-whisper | 0.4.3 | MIT | asr_review |
| mlx-lm | 0.31.3 | MIT | text_semantics |
| sherpa-onnx | 1.13.5 | Apache-2.0 | diarization |
| onnxruntime | 1.28.0 | MIT | vad, diarization, ocr_primary |
| rapidocr | 3.9.2 | Apache-2.0 | ocr_primary |
| opencv-python | >=4.5.1.48 pinned at lock | Apache-2.0 | ocr_primary (declared dep) |
| huggingface_hub | latest stable at lock | Apache-2.0 | pinned model downloads |

`mlx` core arrives transitively. A lockfile gate test asserts torch,
torchvision, torchaudio, nvidia-*, and modelscope never appear in
`uv.lock` — the torch-free claim stays machine-checked. Exact final
versions are whatever the locked resolution pins; deviations from the
baselines above are recorded, not silently absorbed.

## Workstream C — Real engines behind existing contracts

1. **Model runtime subprocess** (new global ADR): every heavy-model
   execution (mlx-audio, mlx-whisper, mlx-lm) runs in its own
   subprocess — JSON in/out, exit returns unified memory to the OS,
   crash isolates with retained evidence, `mx.get_peak_memory()` (or
   runtime equivalent) reported per stage and recorded as evidence.
   ONNX-scale models (vad, diarization, ocr) may run in-process; the ADR
   records the size boundary. This satisfies the plan's "模型卸载后再进
   入下一个大模型阶段" without trusting in-process framework unloading.
2. **Envelope revision**: `MAX_MODEL_RESOURCE_BYTES` (capabilities.py)
   24 GiB → 12 GiB, with every dependent docstring, message, and test
   updated; `plan` estimates and the `resource_envelope_exceeded` pause
   path keep working against the new number.
3. **Per-capability real adapters**, each behind its existing contract
   (Model-output projection, calibration requirements, arbitration and
   gate rules unchanged): VAD partition from vendored silero ONNX;
   VAD-derived ≤5-minute chunking as the shared upstream for ASR and
   alignment (the aligner's hard window; no official long-audio path
   exists anywhere — pipeline-owned by design); forced alignment via
   mlx-audio; diarization via sherpa-onnx (anonymous Part-local labels,
   ADR 0030 unchanged); primary/review ASR via mlx-audio/mlx-whisper
   (Independent-model review contract unchanged — different families);
   OCR via rapidocr (`limit_side_len` raised for ≥1080p frames,
   `use_cls` off for screen content, per research §6); text_semantics
   via mlx-lm (deterministic sampling: temp 0, fixed seed, `max-kv-size`
   bound; strict JSON via prompt + the existing adjudication/validation
   layer; Outlines is the recorded upgrade path if prototype JSON
   failure rates demand it, not a first-version dependency).
4. **`text_semantics` capability definition** in the text-analysis
   context: registry-evaluated like every other capability; the
   Controlled offline text adapter remains the deterministic test path
   and can never earn real-model status (ADR 0037 lineage). Model
   calibration requirements follow the audio-analysis precedent (ADRs
   0027/0029/0031): a per-model calibration/prototype record gates real
   use.

## Workstream D — Prototypes on real material

Per capability: an engineering check (runs offline, structurally valid
output, contract gates hold, peak memory ≤ 12 GiB measured) plus a short
sample output for maintainer eyeball — zh and en both represented, since
quantization loss on Chinese has no published data anywhere and this is
the only verification point. Failures bounce to the recorded fallback
(other quant tier / other candidate) rather than being argued around.
Prototype runs also record first device baselines (real-time factors,
peak memory) to seed plan estimation (§15.2) with
`estimate_confidence=low` replaced by measured values.

## Workstream E — Consistency

- Living test docstrings that say "Phase 11" for the five real-video
  branches are updated to "Phase 12" (historical inventories and
  completion reports stay untouched by policy).
- CONTEXT.md glossaries gain the new terms at resolution time
  (text_semantics, Model runtime subprocess, acquisition vocabulary);
  CONTEXT-MAP owner index updated accordingly.

## Exit gates

From the amended plan (阶段 11 退出门禁):

1. 全部七个能力在真实素材上跑通原型且样例经维护者确认。
2. 模型注册表每个采集条目含 revision、哈希、大小、量化与授权记录。
3. 任一原型阶段实测峰值内存不超过 12 GiB。
4. 工程自动化测试全绿；未经授权不发生任何下载。

Derived gates (named tests, recorded in `docs/PHASE_11_INVENTORY.json`):
lockfile is torch-free; every registry entry hash-verifies on disk;
production adapters fail typed (never download) when an asset is
missing; the subprocess boundary returns memory (post-stage RSS
assertion); envelope constant is 12 GiB everywhere it is asserted;
`media_processed`/`models_downloaded` flips are recorded with evidence;
full-suite wall time stays within the ≤5-minute budget (heavy prototype
runs are maintainer-invoked commands with retained evidence, not
part of the pytest gate).

## Out of scope

- The five formal real-video branches, pause/resume on real runs, user
  acceptance review (Phase 12).
- CER/WER of any kind (Phase 12, and only with human reference text).
- Any VLM (no code consumer; the Qwen3-VL research document was removed
  by the maintainer 2026-08-16).
- Outlines/constrained decoding (recorded upgrade path only).
- yt-dlp upgrades without a real observed failure.
- Speaker true-name inference, paid APIs, telemetry of any kind.

## Related decisions

- New ADR: Model runtime subprocess (execution isolation and memory
  return; drafted in this phase).
- ADR 0019 (yt-dlp as pinned external prerequisite) — re-probe follows it.
- ADR 0027/0029/0031 (model-specific calibration) — extended to the new
  real engines.
- ADR 0030 (anonymous Part-local speaker labels) — diarization engine
  changes nothing about it.
- ADR 0036/0037 (provider-neutral capabilities, controlled offline
  adapters) — the real adapters slot in beside, never replace, the
  offline test path.
