# Phase 5: Auditable Audio Analysis Prototype

Type: enhancement
Status: ready-for-agent
Labels: ready-for-agent
Phase: 5
Published: 2026-08-10

## Problem Statement

After Phase 4, a confirmed RunPlan can produce an auditable Primary subtitle
track but cannot test subtitle timing against audio, distinguish caption gaps
from silence, or expose anonymous speaker-turn structure. The pipeline needs a
safe Phase 5 path for forced alignment, voice activity detection (VAD), and
speaker diarization without silently acquiring models, reading user media,
rewriting Phase 4 evidence, or claiming real-world quality from synthetic proof.

## Solution

Add `vcp analyze-audio` as the single Phase 5 public contract. It starts only
from a revalidated confirmed RunPlan, its retained subtitle candidate report,
and an explicitly selected Analysis audio stream for every Part. It creates an
immutable Audio analysis workspace and machine-readable Audio analysis report.

The implementation is provider-neutral: forced alignment, VAD, and diarization
are modeled as capability contracts whose raw adapter output is retained and
projected through versioned schemas. Current engineering verification uses only
project-owned synthetic media, fixed candidate-output fixtures, and controlled
adapter substitutes. No model runtime, weight, real model, user media, or
network action is part of this phase.

The report makes capability availability, calibration status, candidate results,
accepted evidence, conflicts, partial completion, resource pauses, and required
user decisions explicit. It may create an Adopted alignment timing view only
when all required alignment, VAD, time, and calibration gates pass. It never
rewrites Phase 4 source/readable artifacts, performs ASR, produces a transcript,
or writes `outputs/`.

## User Stories

1. As a media user, I want `vcp analyze-audio` to begin only from a confirmed RunPlan and retained subtitle candidate report, so that no unplanned media is analyzed.
2. As an auditor, I want SourceArtifact hashes, audio coverage evidence, subtitle selection evidence, model identities, rules, and calibration profiles revalidated before analysis, so that every result has exact provenance.
3. As a media user, I want each Part to use one Analysis audio stream, so that alignment, VAD, and diarization refer to the same audio evidence.
4. As a media user, I want an ambiguous multi-audio Part to pause for explicit stream selection, so that the pipeline never analyzes a default or mixed track.
5. As an auditor, I want an Analysis audio selection record bound to stream metadata and coverage hashes, so that a stale stream index cannot silently select different audio.
6. As an operator, I want audio model input derived by a revalidated pinned FFmpeg from the selected stream, so that model libraries cannot apply undocumented source decoding.
7. As an auditor, I want each Analysis audio derivative hash-recorded with its preprocessing profile, so that sample rate, channel transform, loudness treatment, and chunking can be reproduced.
8. As a media user, I want derivative boundaries mapped exactly back to RawPtsTime, so that a model's float seconds never replace the authoritative media clock by guesswork.
9. As a subtitle reader, I want the original Phase 4 source/readable artifacts to stay immutable, so that audio alignment never hides original cue timing.
10. As a media user, I want an Adopted alignment timing view to be a separate derivative, so that I can inspect original and adopted cue times side by side.
11. As an auditor, I want forced alignment to propose times only for existing Primary subtitle cue identities, so that it cannot become an unapproved transcript generator.
12. As a subtitle reader, I want any alignment output that changes cue text or cue cardinality rejected, so that added, removed, merged, or split text cannot masquerade as alignment.
13. As a media user, I want alignment candidates accepted or rejected per cue, so that a local problem does not erase independently valid evidence.
14. As an auditor, I want a proposed alignment view globally checked for stable order, coverage, non-negative duration, language-aware duration plausibility, and conflict evidence, so that individually plausible changes cannot form an invalid whole.
15. As a subtitle reader, I want lawful cue overlaps and original source order preserved in an Adopted alignment timing view, so that alignment does not serialize real overlapping subtitle evidence.
16. As a media user, I want low-confidence, unmappable, or out-of-coverage alignment candidates retained with their rejection reasons, so that original time remains authoritative without losing diagnostic evidence.
17. As an operator, I want an alignment candidate rejected when it overlaps calibrated non-speech, so that accepted subtitle timing does not contradict confirmed silence.
18. As an auditor, I want an alignment overlap with indeterminate audio reported but not automatically rejected, so that uncertainty is not treated as a false fact.
19. As a maintainer, I want candidate time adoption blocked until a model-specific Alignment calibration profile exists, so that a raw confidence score cannot become an authority by itself.
20. As a maintainer, I want calibration identity bound to model asset, backend, precision, device class, and rules, so that a threshold cannot survive an untested execution change.
21. As a product owner, I want synthetic calibration to qualify only synthetic verification, so that the project does not claim real-source alignment quality before authorized real-media calibration.
22. As a media user, I want VAD to produce audio-only Voice activity intervals, so that caption coverage and speech evidence remain distinct concepts.
23. As an auditor, I want known usable audio coverage completely partitioned into `speech_likely`, `non_speech`, or `indeterminate`, so that model omissions never become implicit silence.
24. As a media user, I want a subtitle gap over `speech_likely` audio retained as uncovered-speech risk evidence, so that missing captions are not mistaken for silence.
25. As a reader, I want only duration-qualified uncovered-speech intervals elevated in reports, so that short evidence is retained without overwhelming the result.
26. As an auditor, I want subtitle gaps over indeterminate audio reported separately, so that they do not become unsupported speech, silence, or ASR conclusions.
27. As a media user, I want long silence derived only from continuous calibrated non-speech inside usable audio coverage, so that audio gaps and absent captions cannot manufacture silence.
28. As a maintainer, I want formal VAD classification blocked until a model-specific VAD calibration profile passes deterministic evaluation, so that uncalibrated scores remain raw evidence.
29. As a media user, I want Phase 5 to run VAD for every planned Part, including a Part without a Primary subtitle track, so that audio-state evidence remains available without inventing a transcript.
30. As a media user, I want forced alignment limited to Parts with a Primary subtitle track, so that no-text Parts do not receive synthetic cue structures.
31. As a media user, I want diarization to produce only Part-local anonymous speaker labels, so that speaker-turn structure does not become a cross-Part, cross-run, or real-person identity claim.
32. As a media user, I want overlapping SpeakerTurns retained as separate overlapping intervals, so that live discussion and interruption are not flattened into a false single-speaker sequence.
33. As an auditor, I want a diarization candidate conflicting with non-speech or indeterminate VAD retained but not published as a SpeakerTurn, so that model disagreement is visible instead of silently repaired.
34. As a reader, I want role candidates to require cited subtitle text or explicit user metadata, so that host, guest, and questioner labels are never inferred from voice characteristics.
35. As a maintainer, I want formal SpeakerTurns and role candidates blocked until a model-specific Diarization calibration profile passes deterministic evaluation, so that raw clusters are not treated as stable dialogue evidence.
36. As an operator, I want VAD, full-audio alignment, and diarization to run serially with recorded resource measurement and unload evidence, so that the 24 GB envelope remains enforceable.
37. As an operator, I want missing model-unload evidence to pause later model loading and wait for my decision, so that automatic recovery does not compound an unmeasured memory state.
38. As an operator, I want an over-24 GB high estimate to pause before execution and wait for my choice of configuration or candidate, so that the system does not silently reduce precision or change models.
39. As a media user, I want completed independent Phase 5 results retained when a later stage pauses or blocks, so that a Partial audio analysis report does not discard valid earlier evidence.
40. As an auditor, I want every model adapter to retain raw native output plus a versioned Model-output projection, so that model-specific schemas cannot silently change gate behavior.
41. As a maintainer, I want incomplete model-output projection to yield `model_output_invalid`, so that no default value, field guess, or partial output becomes formal evidence.
42. As a product owner, I want models missing from the project to produce `model_acquisition_required`, so that the CLI never chooses or downloads a model implicitly.
43. As a product owner, I want Qwen3-ForcedAligner-0.6B and Silero VAD treated only as candidates, so that the implementation remains provider-neutral and no candidate becomes a mandatory dependency before evaluation.
44. As an operator, I want credential-gated model candidates blocked even if they can run offline after download, so that the pipeline never accesses tokens, browser state, or account-bound assets.
45. As a media user, I want the absence of an eligible diarization candidate reported explicitly, so that the pipeline never substitutes an unapproved implementation.
46. As an auditor, I want every calibration profile created by a deterministic, hash-pinned Calibration evaluation record, so that expected outputs, thresholds, false accepts, false rejects, and evaluator identity are reproducible.
47. As an operator, I want a failed calibration retained as `calibration_failed` rather than automatically retuned, so that changed thresholds, rules, or candidates become separately authorized experiments.
48. As a maintainer, I want repeated equivalent alignment failures to enter `alignment_diagnosis_required`, so that the system diagnoses a recurring root cause rather than retries blindly.
49. As an auditor, I want failure diagnosis to read only retained evidence and admit `root_cause_inconclusive`, so that diagnosis cannot become a hidden media probe, model run, or download.
50. As a test engineer, I want the public CLI contract exercised with synthetic fixtures and controlled adapters, so that Phase 5 engineering proof remains offline and repeatable.
51. As a product owner, I want Phase 5 to remain outside RunBundle publication and production validation, so that candidate analysis is not presented as a completed user-facing run.

## Implementation Decisions

- Add `vcp analyze-audio` as the single public Phase 5 seam. Its normal action creates an immutable Audio analysis report; all pause recovery uses a separate explicit resume operation that references a retained report and user decision.
- Build Phase 5 around an immutable Audio analysis workspace under the project-owned work area. It retains raw adapter outputs, Model-output projections, Analysis audio derivatives, calibration records, candidate records, reports, diagnostics, and user selection records. It must not write `outputs/` or modify existing Phase 4 workspaces.
- Require a Phase 5 processing authorization before any capability stage. It revalidates SourceArtifact hashes, audio coverage evidence, Primary subtitle track and candidate report, selected Analysis audio stream, pinned FFmpeg identity, model asset identity, rules, preprocessing profiles, and calibration profiles. Drift blocks the run and requires a new plan or selection rather than mutating retained records.
- Add the explicit Analysis audio stream selection state. A Part proceeds only when planning evidence uniquely identifies one usable audio stream or the user supplies an immutable `part-id=stream-index` selection. Selection is bound to stream metadata and coverage hashes; no mixing, merging, index-default selection, or cross-stream analysis is allowed.
- Generate Analysis audio derivatives only with revalidated pinned FFmpeg and a versioned preprocessing profile. The profile defines all sample-rate, channel, loudness, chunking, and exact derivative-to-source time mappings. Capability adapters receive retained derivatives, never direct SourceArtifact reads.
- Define provider-neutral capability contracts for forced alignment, VAD, and speaker diarization. Every adapter writes a retained raw native output and a complete versioned Model-output projection; gates consume only the projection. Projection failure produces `model_output_invalid` and no formal evidence.
- Treat model candidates as matrix entries, not dependencies. Qwen3-ForcedAligner-0.6B is a non-mandatory forced-alignment candidate and Silero VAD is a non-mandatory VAD candidate. No diarization candidate is preselected. A candidate is `eligible`, `blocked`, or `unsupported` before it may appear in an acquisition plan.
- A candidate is eligible only with an official source, acceptable license, fixed revision/hash, offline runtime, no runtime credential or telemetry path, project-local dependency plan, and 24 GB resource evidence. A credential-gated acquisition is `blocked` even if later offline runtime is possible.
- Model acquisition is outside this specification's execution. A later per-model plan must disclose source, license, revision, hash, bytes, target path, offline interface, resource estimate, and credential isolation, and must receive explicit user approval before any download.
- Model stages run only in the fixed order: VAD, full-audio forced alignment, then diarization. Stages never overlap in model memory. Record output, resource measurement, and unload evidence before the following stage can load.
- A missing unload proof enters `model_release_unverified`, blocks later model loads, preserves completed artifacts, and waits for an explicit user decision. A high resource estimate over 24 GB enters `resource_envelope_exceeded`; changing batch, precision, or model waits for explicit user choice.
- VAD applies to every Part, while forced alignment applies only to a Part with a Primary subtitle track. Neither capability generates or substitutes an ASR transcript.
- Formal Voice activity intervals are RawPtsTime half-open intervals strictly inside usable audio DecodedIntervals. They form a complete `speech_likely` / `non_speech` / `indeterminate` partition of known audio coverage. Gaps, missing audio, rounding gaps, and undecidable output are `indeterminate`, never silence.
- Derive uncovered-speech risk only by comparing calibrated `speech_likely` intervals with absence of Primary subtitle coverage. Retain every non-empty intersection. A versioned calibrated duration threshold changes report prominence and ASR-planning recommendation only; it never deletes evidence. Missing subtitle coverage over indeterminate audio is `audio_state_indeterminate`, not speech, silence, or an ASR recommendation.
- Derive Long-silence evidence only from a continuous calibrated `non_speech` interval over the versioned calibrated duration threshold. Do not bridge coverage gaps, indeterminate audio, or subtitle gaps.
- Force alignment may only propose start/end times for existing Primary subtitle cue identities. Any added, removed, merged, split, or modified cue is `alignment_text_contract_violation`. Preserve original text and source/readable artifacts in all cases.
- Record candidate adoption per cue, retaining rejected candidates and rejection evidence. Candidate eligibility requires calibrated confidence, exact mapping to RawPtsTime, usable-audio coverage, a language-aware duration rule, and no calibrated-non-speech conflict. Original cue time is preserved for rejected candidates; no interpolation or guessed repair is allowed.
- Create an Adopted alignment timing view only when the complete mixed cue sequence passes its global gates. It preserves `source_ordinal` and legal overlaps. A failed global gate rejects all candidate adoption for that Part as `alignment_untrusted`; no selective automatic rollback is allowed.
- An alignment candidate over calibrated non-speech is `alignment_vad_conflict` and rejected. Overlap with indeterminate audio is retained as risk but does not itself reject a candidate.
- After the second recurrence of the same Alignment failure fingerprint, enter `alignment_diagnosis_required`. Diagnosis reads retained subtitle, candidate-time, model-output, VAD, and gate evidence only; it may produce `root_cause_inconclusive` and may not rerun a model, acquire an asset, or read new media.
- Formal SpeakerTurns use Part-local anonymous labels, RawPtsTime half-open intervals, and confidence evidence. They never make cross-Part, cross-run, real-person, or voiceprint claims. Valid overlapping turns remain separate; do not trim, merge, or serialize them.
- A raw diarization candidate overlapping calibrated non-speech or indeterminate VAD is `diarization_vad_conflict` and cannot become a formal SpeakerTurn. Role candidates require cited subtitle text or explicit user metadata and are never inferred from voice characteristics.
- Alignment, VAD, and diarization each require a distinct model-specific calibration profile. Each profile binds model asset hash, backend/version, precision, device class, and rules. Any identity drift invalidates qualification. Synthetic-only calibration qualifies only synthetic verification, not real-source adoption.
- Calibration profiles arise only from deterministic Calibration evaluation records over hash-pinned reference fixtures. Preserve candidate output, expected results, thresholds, false-accept and false-reject summaries, and evaluator version. A failed profile is `calibration_failed`; no automatic threshold tuning or retry is allowed.
- The machine-readable Audio analysis report states capability availability, processing authorization result, selected streams, profile identities, candidates, adopted and original timing relationships, VAD partitions, risk evidence, speaker evidence, conflicts, diagnostics, artifacts, report status, and any required explicit user decision.

## Testing Decisions

- Use the new `vcp analyze-audio` JSON contract as the highest primary seam. Tests should observe report state, report identity, workspace artifacts, user-decision transitions, and declared no-side-effect guarantees rather than helper sequencing or private data structures.
- Follow the existing `vcp subtitles` integration CLI-contract suite as the direct precedent. Its use of confirmed synthetic plans, retained workspace evidence, and JSON-only state transitions is the preferred model for the Phase 5 public seam.
- Use project-owned synthetic media, hash-pinned ProbeDocuments, existing exact RawPtsTime and coverage fixtures, fixed raw model-output fixtures, and controlled adapter substitutes. Tests must not install a runtime, download a model, invoke a real model, contact a network service, access user media, or use a paid API.
- Add a focused CLI-contract matrix covering missing models; credential-gated and ineligible candidates; complete/partial/blocked reports; stream-selection pause and explicit resume; model-release and resource-envelope pauses; full revalidation drift; and preservation of completed-stage artifacts.
- Add focused unit tests for audio-stream selection binding; preprocessing profile validation; exact derivative-to-source mapping; raw output projection completeness; calibration profile identity; deterministic calibration evaluation; and all model availability states.
- Add focused unit tests for VAD complete partitions, coverage gaps, long silence, uncovered-speech evidence, indeterminate audio, threshold prominence, and the distinction between subtitle coverage and audio state.
- Add focused unit tests for alignment text-contract violation, per-cue rejection, source-order preservation, legal overlaps, global rejection, recurring failure fingerprints, retained-only diagnosis, language-aware duration rules, and VAD conflicts.
- Add focused unit tests for Part-local SpeakerTurns, overlapping turns, VAD conflicts, no identity linkage, and text- or metadata-bound Role candidates.
- Require tests to assert that Phase 4 source/readable artifacts and RunPlans are never mutated and that no `outputs/` content is written by Phase 5.
- Run Python checks only from the activated project `.venv` after `scripts/require-project-venv.sh` succeeds. Finish implementation with the full test suite, Ruff check, formatter check, and Mypy.

## Out of Scope

- Downloading or installing model weights, model runtimes, or new dependencies.
- Real model invocation, user-media access, live URLs, browser state, cookies, credentials, or paid APIs.
- Real-source calibration, quality validation, CER/WER/DER claims, and production validation.
- ASR, transcript generation, cue text correction, subtitle-track merging, external subtitle discovery, OCR, visual understanding, RunBundle publication, `outputs/` writing, and automatic cleanup.
- Cross-Part or cross-run voice identity linking, real-name inference, role inference from voice characteristics, automatic diarization fallback, and automatic model selection.
- Automatic resource downgrades, automatic threshold tuning, automatic retries after repeated alignment failure, or automatic recovery from unverified model release.

## Further Notes

- This specification synthesizes the Phase 5 grilling record and uses the
  canonical vocabulary in `CONTEXT.md`.
- ADRs 0026 through 0039 define the immutable timing, calibration, VAD,
  diarization, resource, revalidation, workspace, CLI, provider-neutral,
  offline-verification, audio-selection, and calibration-evaluation boundaries.
- The current project state remains engineering development. `real_world_testing`
  and `production_validated` remain `false`.
- The primary implementation target is an offline auditable prototype. A later
  model-acquisition authorization and real-media calibration decision are
  required before it can analyze a real source with formal model-derived claims.
