# Phase 11 Completion Report

## Status

Phase 11, 模型采集与真实引擎集成 (model acquisition and real-engine
integration), is completed and verified in the project-local offline
environment. All seven model capabilities — `vad`, `asr_primary`,
`asr_review`, `forced_alignment`, `diarization`, `ocr_primary`, and the new
`text_semantics` — now have real inference engines that load their pinned,
hash-verified assets and produce contract-valid output, and each ran a real
prototype on the two Public-Domain Voice of America clips that the maintainer
confirmed on 2026-08-17. Every measured prototype peak is ≤ 5.5 GiB, well
inside the 12 GiB envelope. This remains an engineering-and-prototype pass: no
domain-quality (CER/WER, recall, faithfulness) claim is made — that is
Phase 12's subject with human reference. Per the amended plan, Phase 11 has no
`overall_stage` exit gate; the project stays
`real_world_testing / 当前阶段：真实测试，尚未完成生产验收` and
`production_validated` remains `false`, reserved for Phase 12 user acceptance.

## Delivered Scope

- Plan amendment and governance: Phase 11 split out of the original
  real-video-testing phase (old Phase 11 renumbered to Phase 12); the shared
  resource envelope shrunk 24 → 12 GiB as the single source of truth
  (`capabilities.MAX_MODEL_RESOURCE_BYTES`), asserted everywhere it is used.
- Torch-free inference stack: eight authorized inference dependencies locked
  as a dependency group with the sanctioned torch-phantom override (torch is
  never installable), plus a lockfile gate and offline, side-effect-free
  import proofs.
- Model acquisition: seven maintainer-confirmed downloads at pinned
  revisions/hashes plus RapidOCR from the pinned wheel = eight acquired
  registry entries, each carrying revision, sha256, size, quantization, and
  license/authorization records, and each re-hashing from disk.
- The Model runtime subprocess seam (ADR 0055): MLX-scale engines (forced
  alignment, both ASR adapters, text_semantics) run out-of-process, one
  subprocess per stage, returning peak memory on exit; ONNX-scale engines
  (silero VAD, sherpa-onnx diarization, RapidOCR) run in-process.
- Seven real engines wired *beside* the controlled offline adapters
  (ADR 0037), never replacing the offline test path, plus the new
  `text_semantics` capability and its model-specific decoding calibration
  (ADR 0056).
- Prototype material (two Public-Domain VOA clips), a reusable pytest-gated
  prototype harness, and fourteen maintainer-invoked real runs with retained
  device baselines and per-capability Chinese/English samples.
- This closing exit-gate inventory (`docs/PHASE_11_INVENTORY.json`) with
  `tests/acceptance/test_phase_11_inventory.py`: the four 阶段 11 退出门禁
  are re-derived from the phase plan, the seven derived gates from the
  specification, every citation is AST-verified, the constraint flips and the
  closure ritual are machine-checked against `project-state.json`, every
  recorded device peak is asserted within the 12 GiB envelope, and the
  full-suite wall time is checked against the ≤ 5-minute budget.

## Known Limitations

The 阶段 11 plan section has no 本阶段不能验证 block; real-video quality is
Phase 12's subject. The limitations carried forward are:

- 真实中英 ASR 准确率、真实直播重叠说话质量、真实字幕强制对齐成功率、真实
  OCR 召回和数字准确率、真实长视频摘要忠实度 — these real-video quality
  questions are out of scope here and are the subject of 阶段 12（真实视频测试）,
  and only with human reference text.
- `diarization` over-clusters the English clip (~15 clusters for ~3 speakers)
  at the current `cluster_threshold` 0.5; accepted as-is for the prototype,
  with real threshold tuning deferred to Phase 12 against human reference.
- `text_semantics` groups a single-topic prototype clip into one
  `SemanticSegment`; multi-segment behaviour is not yet exercised and remains
  to be tuned against human reference in Phase 12.
- All real engines are wired *beside* the offline path (ADR 0037) and are not
  yet invoked from the CLI commands; end-to-end real runs, pause/resume on
  real runs, and user acceptance are Phase 12.

## Recorded Deviations

- `mlx-whisper` (the `asr_review` engine) hard-declares `torch` in every
  version, contradicting the research document's torch-free claim. Resolved
  by the maintainer-chosen override that prunes torch to an artifact-free
  phantom node (never installable); `mlx_whisper.transcribe` over
  pre-converted MLX weights is torch-free. Standing red line: import
  `mlx_whisper`, never `mlx_whisper.torch_whisper`.
- The `direct`-URL acquisition contract cannot acquire plain Wikimedia
  Commons media URLs (the pinned yt-dlp generic extractor reports no exact
  filesize, so the metadata pass fails closed). Prototype media was acquired
  via the sanctioned hash-copied local-file path instead (operator-controlled
  yt-dlp restricted to `upload.wikimedia.org`, byte-count + Commons SHA-1
  verified), recorded in `docs/phase-11-download-plans/prototype-media.md`.
- A latent VAD bug surfaced on the first real speech: silero's mandatory
  64-sample context was never fed (input must be 576 = 64 + 512, not 512), so
  real speech scored ~0. The ticket-06 integration test had only run
  synthetic (correctly non-firing) audio, so it was latent. Fixed with a
  regression test; the real Chinese clip then yielded 53 speech intervals.
- `text_semantics` returned `model_output_invalid` on both languages under the
  ticket-10 v1 prompt — an adapter gap, not model quality: the prompt rendered
  only cue *identities* with no transcript text and no output schema. Ticket 15
  rendered each cue's verbatim text plus the exact output envelope, bumped the
  `prompt_template_version` to v2, recalibrated the decoding profile, and added
  a finer 30-second semantic cue window. The re-run projected verified,
  cue-cited `SemanticSegment`s in Chinese prose on both clips.
- The two constraint flips happened outside this ticket: `models_downloaded`
  (ticket 04 acquisition) and `media_processed` (ticket 13 first real-media
  prototype processing), each recorded in `project-state.json`
  `constraint_events` with an object and purpose. `paid_apis_used` stays
  `false`.
- Domain quality is explicitly not claimed; the five known limitations above
  are the subject of Phase 12.

## Final Verification

At the closure commit, re-run in full: `pytest -q` — full suite green
(including the fifteen real-model slow integration tests) within the
≤ 5-minute budget; `ruff check .` clean, `ruff format --check .` clean,
`mypy src` clean. `docs/PHASE_11_INVENTORY.json` records all eleven exit gates
confirmed (4 plan + 7 derived). `project-state.json` closes per ritual
(`current_phase` 11, `phase_status` completed, `next_phase` 12) with
`overall_stage` left at `real_world_testing` and `production_validated`
`false`; `constraints` `models_downloaded` and `media_processed` are `true`
(recorded with evidence), `paid_apis_used` remains `false`.
