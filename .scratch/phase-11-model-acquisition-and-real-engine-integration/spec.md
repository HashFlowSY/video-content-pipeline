# Phase 11 — Model Acquisition and Real Engine Integration

Type: spec
Status: specification_pending_approval
Labels: ready-for-agent
Phase: 11
Published: docs/PHASE_11_SPECIFICATION.md

## Domain routing

Read [CONTEXT-MAP.md](../../CONTEXT-MAP.md). No new context. New vocabulary
lands with each capability's owner: audio-analysis (vad, forced_alignment,
diarization — filling the recorded `Diarization capability vacancy`),
transcription (asr_primary, asr_review), visual-text (ocr_primary),
text-analysis (the new `text_semantics` capability). The cross-context
execution mechanism (`Model runtime subprocess`) is a new global ADR with
the term owned by orchestration.

## Problem Statement

Every model capability in the pipeline is real only up to the eligibility
gate: with no acquired model, every capability evaluation terminates in
`model_acquisition_required` and no real transcription, alignment,
diarization, OCR, or semantic text has ever been produced. The registry has
no diarization candidate at all and no text-generation capability exists in
code. The resource envelope constant still assumes the retired 32 GB
machine. Until all of this is closed, Phase 12 (real-video testing) has
nothing to test.

## Solution

Per [docs/PHASE_11_SPECIFICATION.md](../../docs/PHASE_11_SPECIFICATION.md)
and the selection evidence in
[research/phase-11-model-selection.md](../../research/phase-11-model-selection.md):
acquire seven pinned, credential-free, Apache-2.0/MIT model assets
(decisions D1–D7, 8-bit-first per plan §2.2); introduce a torch-free
inference dependency set through the locked uv flow; run every heavy MLX
engine in a `Model runtime subprocess` (JSON in/out, memory returned on
exit, peak-memory evidence); wire real adapters behind the existing
capability contracts (the Controlled offline adapters remain the
deterministic test path); define `text_semantics` as a first-class
capability; shrink the shared resource envelope to 12 GiB; and prove each
capability with a prototype on maintainer-confirmed public real material,
with zh+en sample outputs eyeballed by the maintainer.

## User Stories

1. As the maintainer, I want every model download to require my individual
   confirmation of a written download plan (repo, revision, files, size,
   license, target path), so that no asset ever enters the project
   unaudited.
2. As the maintainer, I want the seven selected models pinned by exact
   revision and SHA-256 in the model registry, so that any future run can
   prove exactly which bytes produced its evidence.
3. As the maintainer, I want the entire inference stack torch-free and
   machine-checked to stay that way, so that the dependency surface stays
   small enough to audit.
4. As the maintainer, I want one specification approval to authorize the
   full inference dependency list, so that I am not interrupted per
   package while anything beyond the list still needs separate consent.
5. As a pipeline operator, I want production runs to hard-fail with a
   typed reason when a model asset is missing, so that offline execution
   never silently downloads anything.
6. As a pipeline operator, I want each heavy model stage to run in its own
   subprocess that exits after the stage, so that unified memory is
   actually returned before the next model loads on a 16 GiB machine.
7. As a pipeline operator, I want each model stage's measured peak memory
   recorded as evidence, so that plan estimates converge on reality.
8. As a pipeline operator, I want plan estimation to pause any stage whose
   conservative estimate exceeds 12 GiB, so that runs degrade into
   explicit decisions instead of memory pressure.
9. As the maintainer, I want VAD-derived ≤5-minute chunking as the shared
   upstream for ASR and alignment, so that the aligner's hard window and
   the absence of any official long-audio path are handled by design
   rather than per-engine improvisation.
10. As the maintainer, I want the diarization capability filled by a
    credential-free pipeline whose speaker labels stay anonymous and
    Part-local, so that real speaker structure appears in Phase 12 review
    without violating the no-true-names rule.
11. As the maintainer, I want `text_semantics` defined as a registry-backed
    capability with the Controlled offline text adapter retained as the
    test path, so that real summaries become possible without weakening
    the deterministic verification lineage.
12. As the maintainer, I want the real text adapter to run with
    deterministic sampling and bounded KV memory, so that regenerated
    reports are reproducible and memory-safe.
13. As the maintainer, I want OCR to run from the pinned rapidocr wheel
    with its bundled zh+en models recorded by hash, so that visual-text
    evidence is reproducible from the registry alone.
14. As the maintainer, I want prototype material selected by the agent
    from public, DRM-free sources and confirmed by me as a quick download
    plan, so that prototyping never blocks on me picking a video.
15. As the maintainer, I want every capability prototyped on real zh+en
    material with short sample outputs I can eyeball, so that I validate
    my own model selections before Phase 12 — especially since Chinese
    quantization loss has no published data anywhere.
16. As the maintainer, I want prototype runs to record first device
    baselines (real-time factors, peak memory), so that Phase 12 plans
    show honest time and memory estimates instead of guesses.
17. As an auditor, I want `media_processed` and `models_downloaded` to
    flip exactly when the facts occur, with object and purpose recorded,
    so that the constraint flags stay factual records rather than badges.
18. As an auditor, I want acquisition, license, and authorization records
    on every registry entry, so that provenance questions have one
    authoritative answer.
19. As a developer, I want real adapters to implement the existing
    Model-output projection contracts, so that arbitration, gates, and
    calibration rules keep their tests and their meaning unchanged.
20. As a developer, I want the subprocess protocol testable with a stub
    executable, so that the engineering gate stays fast, offline, and
    model-free.
21. As a developer, I want the full pytest gate to stay within its
    5-minute budget with heavy prototypes excluded from it, so that
    engineering verification stays cheap while real-model evidence lives
    in retained artifacts.
22. As the maintainer, I want yt-dlp re-probed and identity-pinned on this
    machine with upgrades gated on real observed failure, so that the
    Phase 12 URL intake path is ready without speculative churn.
23. As a future reader, I want the phase renumbering (real-video testing →
    Phase 12) reflected in living code comments and glossaries while
    historical documents stay untouched, so that the record stays honest
    in both directions.

## Implementation Decisions

- Selections (D1–D7, locked 2026-08-16): Qwen3-ASR-1.7B-8bit via mlx-audio
  (asr_primary); whisper-large-v3-mlx fp16 via mlx-whisper (asr_review);
  Qwen3-ForcedAligner-0.6B-8bit via mlx-audio (forced_alignment); vendored
  silero-vad v6.2.1 ONNX via onnxruntime (vad); sherpa-onnx
  pyannote-segmentation-3.0 ONNX + 3D-Speaker CAM++ zh-en embedding
  (diarization); rapidocr 3.9.2 bundled PP-OCRv6 small (ocr_primary);
  Qwen3-4B-Instruct-2507-8bit via mlx-lm (text_semantics). 8-bit first per
  plan §2.2; 4-bit is the recorded over-envelope fallback; missing 8-bit
  variants resolve in that model's download-plan confirmation, never
  silently.
- The official `qwen-asr` package is rejected (CUDA-oriented, gradio/flask
  hard deps, pinned transformers); the whole stack is torch-free.
- `Model runtime subprocess` (new global ADR): every MLX engine executes
  in its own subprocess with a JSON request/response contract; exit
  returns memory; crashes isolate into typed failures with retained
  evidence; per-stage peak memory is reported. ONNX-scale models may run
  in-process; the ADR records the size boundary.
- `MAX_MODEL_RESOURCE_BYTES` shrinks from 24 GiB to 12 GiB; every
  dependent docstring, message, and the plan-estimation pause path move
  with it.
- `text_semantics` follows the audio-analysis capability precedent:
  registry-evaluated, calibration-gated (ADR 0027/0029/0031 lineage), with
  the Controlled offline adapter permanently barred from real-model
  status (ADR 0037).
- Real adapters slot in beside — never replace — the Controlled offline
  adapters, behind the existing projection and gate contracts.
- Production model execution sets hub-offline environment guards; missing
  assets are typed failures.
- Model downloads use the pinned hf CLI or pinned release URLs, staged in
  the project cache, hashed into `models/<provider>/<model>/<revision>/`.
- Media (prototype material) and model downloads use separate
  authorizations, per the plan's standing rule.

## Testing Decisions

- Tests assert external behavior at three existing seams — the capability
  registry evaluation, the versioned adapter contracts (offline adapters
  as fixtures), and the composition/end-to-end seam — plus exactly one new
  seam: the Model runtime subprocess protocol, tested with a stub
  executable (request serialization, typed crash isolation, peak-memory
  evidence, memory-return assertion).
- Real model quality never enters pytest: prototypes are
  maintainer-invoked commands with retained evidence and maintainer
  sample review, mirroring how heavy analysis is already serialized and
  paused for decisions.
- A lockfile gate test asserts torch/torchvision/torchaudio/nvidia-*/
  modelscope are never *installable* in `uv.lock` (no resolved node carries a
  wheel or sdist). mlx-whisper hard-depends on torch, so torch is excluded via
  a `[tool.uv]` always-false-marker override and survives only as a sanctioned
  artifact-free phantom node — recorded in ticket 03, not silently absorbed.
- Registry entries hash-verify on disk in an integration test; a missing
  asset produces the typed acquisition failure, never a network call.
- Prior art: Phase 10's identity-pinned toolchain tests (error, never
  skip), the Phase 5–8 capability-eligibility suites, the Phase 10
  inventory acceptance test (regex-derived gates, AST-verified citations).
- The full suite stays within the ≤5-minute budget.

## Out of Scope

- The five formal real-video branches, real pause/resume acceptance, and
  user review of full outputs (Phase 12).
- CER/WER of any kind (Phase 12, only with human reference text).
- Any VLM (no code consumer; research stays archived).
- Outlines/constrained decoding (recorded upgrade path only).
- yt-dlp upgrades without a real observed failure.
- Speaker true-name inference, paid APIs, telemetry.

## Further Notes

- Hardware truth: final test machine is Apple M1, 16 GiB. All memory
  reasoning in this phase derives from that plus the 12 GiB envelope.
- Chinese quantization degradation has no published numbers for any
  selected model; the maintainer sample review in the prototype ticket is
  the only quality gate before Phase 12.
- The 2026-08-16 plan amendment inserted this phase and renumbered
  real-video testing to Phase 12; exit gates live in the amended plan
  section and `docs/PHASE_11_INVENTORY.json` at closure.

## Tickets

01 plan-amendment-registry-candidates-and-tool-reprobe →
02 resource-envelope-12gib,
03 runtime-dependencies-and-torch-free-gate,
04 model-download-plans-and-acquisition (needs 01, 03),
05 model-runtime-subprocess-and-adr (needs 03),
06 vad-real-adapter-and-chunking (needs 03, 04),
07 diarization-real-adapter (needs 03, 04),
08 alignment-real-adapter (needs 04, 05, 06),
09 asr-real-adapters (needs 04, 05, 06),
10 text-semantics-capability-and-adapter (needs 04, 05),
11 ocr-real-adapter (needs 03, 04),
12 prototype-material-acquisition (needs 01),
13 capability-prototypes-and-sample-review (needs 06–12),
14 closing-exit-gate-inventory (needs all).
