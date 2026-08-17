# Ticket 08 — Real-engine bridge design (staged per-stage)

Authorized 2026-08-18: build the real-engine output-mapping bridge for `vcp run`,
one stage at a time (TDD, commit per stage), THEN promote registry + run #1.

## Key facts (verified)
- Env ready: arm64, mlx GPU OK, all 53 model assets on disk, calibration configs
  exist (`config/audio-analysis/*.json`, `config/text-analysis/*.json`).
- Real inference bodies EXIST + proven by `prototype_runs.py`. Gap: no production
  entrypoint calls them. Both standalone CLI + orchestrated `vcp run` route the
  "real" branch to `real_engine_adapter.dispatch_real_stage` = verify assets then
  fail closed with `real_engine_execution_deferred`.
- Registry promotion = add `resource_estimate:{high_bytes:N}` per candidate (N =
  max measured peak from device-baselines.json). Flips unsupported→eligible.
  MUST land AFTER the bridge (promoting alone => runs fail closed). ADR-0037 gate,
  authorized.
- `3dspeaker-campplus-zh-en-advanced` (diarization embedding) has NO baseline —
  do NOT invent an estimate. Diarization capability qualifies via
  `sherpa-onnx-pyannote-segmentation-3-0` (has baseline); 3dspeaker is a
  secondary asset loaded by sha, not the eligibility-driving candidate.

## Promotion values (max peak per candidate, GiB / bytes)
- qwen3-asr-1-7b (asr_primary): 5462840040
- qwen3-4b-instruct-2507-8bit (text_semantics): 5051028740
- qwen3-forced-aligner-0-6b (forced_alignment): 4289933349
- whisper-large-v3 (asr_review): 3785541174
- rapidocr (ocr_primary): 535003136
- sherpa-onnx-pyannote-segmentation-3-0 (diarization): 348241920
- silero-vad (vad): 124551168
- 3dspeaker-campplus-zh-en-advanced: NO baseline — leave un-promoted OR record
  honestly (decide when wiring diarization; likely leave without resource_estimate
  since it is not the diarization eligibility driver — VERIFY selection still works).

## AUDIO stage plan (template) — from Plan agent
Offline spine (`audio_analysis.py:498-767`) is fully shared: revalidation, stream
selection, derivative prep, resource pauses, per-stage sequencing, report assembly.
Real path must plug into the evidence-derivation + stage-execution seams, NOT
short-circuit at :496-497.

Engine outputs serialize via the SAME functions offline uses:
- `analyze_derivative_vad` -> `SileroVadResult.part_evidence` is a `VadPartEvidence`
  -> `_vad_part_evidence_as_json`.
- `analyze_derivative_alignment` -> `Qwen3AlignmentResult.adopted_view` is an
  `AdoptedAlignmentTimingView` -> `_alignment_view_as_json`; also carries
  `peak_memory_bytes` (subprocess-measured).
- `analyze_derivative_diarization` -> `SherpaDiarizationResult.raw_turns` are
  `SpeakerTurnCandidate`s -> feed `ProjectedDiarizationPart(turns=raw_turns,
  role_candidates=())` -> `_speaker_turn_part_evidence_as_json`.

formal_evidence envelope (all 3): `{capability, candidate_id, calibration_profile,
parts:[...]}`. Real `calibration_profile` = the REAL config
(`qualification_scope: real_sample_confirmed`) retained as InputEvidence .as_json().

Media prep: offline `_prepare_analysis_audio_derivatives` already yields wav path +
`DerivativeTimeMapping` (better than prototype's t=0 16k mono). Need
`DerivativeTimeMapping.from_json` (add to audio_derivation.py) to reconstruct from
the derivative dict. Engines assert 16k mono.

stage_execution: `_record_stage_execution` reads fixture `execution_controls`
offline. Real path: add `runtime_controls` param carrying measured `peak_bytes`
+ unload_evidence, validated vs `_resource_high_bytes(candidate)` (same fail-closed
=> over-envelope downgrades to release_unverified).

MAINTAINER DECISION 2026-08-18: **subprocess-isolate ALL real engines** (not just
the MLX ones). ASR/alignment/text already subprocess (real per-child peak). Build
NEW children `vad_child.py`, `diarization_child.py`, `ocr_child.py` so VAD/
diarization/OCR each run in their own `model_runtime` child => honest,
baseline-comparable per-capability `peak_memory_bytes` (ru_maxrss RUSAGE_SELF in a
fresh child, matching how device-baselines.json was measured — one capability per
process) AND hub-offline guards. This EXTENDS the model_runtime boundary beyond
MLX; model_runtime.py:27-29 + the Model-runtime-subprocess ADR say ONNX-scale runs
in-process => WRITE A NEW ADR superseding that (new rationale: honest per-capability
peak in the orchestrated run; the memory-return rationale is unaffected/additive).
Pattern to mirror: alignment_engine_child.py + how alignment_engine drives
run_engine_subprocess. Each child: request.task carries wav path + mapping json +
ids + caption/vad intervals; handler runs analyze_derivative_* and returns the
already-report-shaped part evidence JSON (pass-through, no dataclass rebuild in
parent) + chunks json (VAD, for alignment) + model_asset_sha256 + calibrated;
peak via default process_peak_rss_bytes. Parent runner in each engine module.
PACING: keep grinding, commit per stage.

Qualification gate: `_candidate_is_qualified`/`_qualified_*_candidate` require
fixture adapter.state==projected & calibration.state==qualified — a real candidate
has neither. Add real-aware qualification: eligible + valid model-matched real
calibration. Thread `real_engines` into `_qualified_*_candidate`.

Scope guard: `_derive_vad_evidence` synthetic-scope rejection +
`_require_synthetic_calibration_scope` must accept `real_sample_confirmed` for real.

Seam: parallel `_derive_{vad,alignment,diarization}_evidence_real` returning the
IDENTICAL dict; branch at call sites (:599,:644,:688) on
`real_engines and cap in real_engines.capabilities`. Alignment-real needs VAD
`chunks` (SpeechChunk) -> thread live `SileroVadResult` when VAD real. Retire
`dispatch_real_stage` for audio (keep for not-yet-migrated stages). Everything else
reused unchanged.

TDD: new `tests/test_audio_analysis_real_bridge.py`, monkeypatch engine fns to
return canned dataclasses, assert byte-for-byte mapping via `_canonical_json`;
plus over-envelope, untrusted-alignment, user role metadata, qualification gate,
scope guard, derivative reuse, composition wiring, partial failure, mixed
real/offline.

Edit order: (1) DerivativeTimeMapping.from_json (2) real-calibration loader
(3-5) _derive_*_real (6) _record_stage_execution runtime_controls (7) real
qualification+scope (8) derivative/chunks plumbing (9) remove :496-497 short
circuit + branch call sites (10) composition wiring + retire dispatch for audio.

## PROGRESS (audio stage)
Committed increments on main:
- `3dcfdae` DerivativeTimeMapping.from_json (+ interval/exact-time parsers).
- `40322cb` VAD subprocess seam: vad_child.py + vad_engine.run_isolated_vad /
  IsolatedVadResult / default_vad_command / DEFAULT_VAD_TIMEOUT_SECONDS(300) /
  _parse_isolated_vad_result (vad_output_invalid). Child returns part_evidence
  json (via _vad_part_evidence_as_json) + speech_runs_samples + sha + calibrated.
  Parent re-derives chunks with derive_speech_chunks(speech_runs_samples, mapping).
- `e9b9445` diarization subprocess seam: diarization_child.py +
  diarization_engine.run_isolated_diarization / IsolatedDiarizationResult /
  default_diarization_command / DEFAULT_DIARIZATION_TIMEOUT_SECONDS(600) /
  _speaker_turn_candidate_as_json|from_json / _parse_isolated_diarization_result
  (diarization_output_invalid). Child returns raw_turns json + seg/embed shas +
  calibrated; parent re-applies ADR0030 gate.

Architecture confirmed: ALIGNMENT/ASR/TEXT already self-isolate model in per-chunk
model_runtime children (real MLX peak returned) — call analyze_derivative_* directly
in parent (orchestration is model-free). Only VAD/diarization/OCR needed new
children (VAD+diar done; OCR pending for visual stage).

## NEXT (audio integration — do with fresh context, edits to 3800-line audio_analysis.py)
1. `_record_stage_execution(..., *, runtime_controls=None)`: use runtime_controls in
   place of candidate["execution_controls"] when given. Real path passes
   {"resource_measurement":{"peak_bytes":<child peak>},
    "unload_evidence":{"state":"released","resident_bytes":0}} (child exit => truthful).
   Envelope check (peak<=resource_high_bytes) unchanged => honest fail-closed.
2. Real-calibration-profile helper: `_real_calibration_profile_evidence(project_root,
   capability)` -> assert config qualification_scope=="real_sample_confirmed",
   return _input_evidence(config_path).as_json(). Paths: vad=silero-vad-calibration,
   forced_alignment=qwen3-aligner-calibration, diarization=sherpa-diarization-calibration
   under config/audio-analysis/. (Verify alignment/diar config scope values first.)
3. `_derive_vad_evidence_real(candidate, selections, subtitle_report, project_root,
   derivatives_by_key)` -> (evidence_dict, {key: IsolatedVadResult}). Uses
   run_isolated_vad per selection; parts=[r.part_evidence]; calibration_profile from (2).
   Keep IsolatedVadResult per key for peak + speech_runs (chunks via derive_speech_chunks).
4. `_derive_alignment_evidence_real`: per source call alignment_engine.
   analyze_derivative_alignment(project_root, wav, mapping, source_id, stream_index,
   language, source_cues=_primary_alignment_cues(...), chunks=derive_speech_chunks(
   vad_result.speech_runs_samples, mapping), usable_audio_intervals=_usable_audio_intervals(
   coverage), voice_activity_intervals=vad_by_source[source_id]); take result.adopted_view;
   FACTOR the offline untrusted-fingerprint/diagnosis + view-write block (2452-2492) into a
   shared helper `_alignment_part_from_view(view, source_evidence, source_id, selection,
   plan, candidate_id, calibration_ref, view_path, project_root)` and call from BOTH offline
   and real. Real peak = result.peak_memory_bytes (max chunk MLX peak).
5. `_derive_diarization_evidence_real`: per source call run_isolated_diarization ->
   raw_turns; build ProjectedDiarizationPart(turns=raw_turns, role_candidates=()) and feed
   the SAME _speaker_turn_part_evidence_as_json(...) as offline (min_confidence from real
   config, source_cues=_speaker_role_cues, user metadata unchanged). Real peak = child peak.
6. Real qualification: thread real_engines into _qualified_{vad,alignment,diarization}_candidate
   — when the cap is in real_engines.capabilities, qualify by state=="eligible" (skip the
   fixture adapter/calibration.state gate). Diarization real: requested candidate must be the
   eligible one (sherpa); 3dspeaker stays unpromoted (its footprint is inside sherpa's 348MB
   diarization baseline, measured by the prototype running the whole pipeline).
7. analyze_audio body: REMOVE top short-circuit (496-497). After
   _prepare_analysis_audio_derivatives, if real build
   derivatives_by_key={(sid,idx):(Path(d["path"]), DerivativeTimeMapping.from_json(d["mapping"]))}.
   Branch each derive call site (599/644/688) on real_engines&cap. Thread the live
   IsolatedVadResult (for alignment chunks). Each stage_execution call passes runtime_controls
   with the real peak when real.
8. Scope guard: real derive funcs read real config (real_sample_confirmed) — do NOT call
   _require_synthetic_calibration_scope for the real path.
9. Retire dispatch_real_stage for audio (keep import/type RealEngineSelection; keep dispatch
   for not-yet-migrated stages transcription/enhancement/text/visual until their commits).
10. ADR: write docs/adr/00XX extending the model_runtime boundary to ONNX engines
    (vad/diarization/ocr) for honest per-capability peak in the orchestrated run; supersede
    the "ONNX in-process" note in model_runtime.py:27-29 + diarization_engine docstring.
11. Integration tests tests/unit/test_audio_analysis_real_bridge.py: monkeypatch
    run_isolated_vad / analyze_derivative_alignment / run_isolated_diarization to canned
    outputs; assert formal_evidence + stage_execution mapping (incl over-envelope=>release_unverified,
    real qualification, mixed real/offline, partial engine failure). Full suite at end.

## Remaining stages (design when reached, same pattern)
- transcription (asr_primary + asr_review): `asr_engine.transcribe_derivative` +
  review windows. Records stage_execution (was empty). Subprocess peak available.
- enhancement (asr_primary): re-ASR suspicious intervals.
- text_analysis (text_semantics): `text_semantics_engine` subprocess.
- visual_text (ocr_primary): `ocr_engine.analyze_frames_ocr` in-process.

## Run (after all stages)
promote registry -> `vcp plan decode` -> `vcp plan confirm` (4 legal fields) ->
`vcp run` (background, multi-hour; ASR RTF ~4.65 => ~2.5-3h primary alone) ->
pause/resume drill at stage boundary -> `vcp verify` + `vcp inventory` -> record
provenance in docs/phase-12-download-plans/run-01.md + ledger -> commit -> review.
