# 08 -- Prove offline audio-analysis CLI contract

**What to build:** The complete Phase 5 public CLI contract is proven with
project-owned synthetic media, fixed candidate-output fixtures, and controlled
adapters. The result is a repeatable offline proof of the user-visible states
and immutable evidence boundaries, not a real-world audio-quality claim.

**Blocked by:** 07 -- Deliver serial pauses and partial analysis reports.

**Status:** resolved
**Labels:** ready-for-agent

- [x] End-to-end CLI tests cover missing and blocked models, stream selection, revalidation drift, calibration results, VAD risks, alignment and diarization conflicts, pauses, resumes, and partial reports.
- [x] Tests prove no model/runtime/dependency download, network request, user-media access, ASR, RunBundle publication, or mutation of Phase 4 artifacts and RunPlans.
- [x] The full project test suite, Ruff checks, formatter check, and Mypy pass from the project virtual environment, while production validation remains false.

## Comments

2026-08-10: Extended the controlled offline CLI contract with a retained
audio-stream selection pause/resume and drift case plus a confirmed
model-release resume case. Resume now compares the full hash-bound stream
selection record before it reuses completed-stage evidence, and it retains a
release-unverified VAD stage only after the explicit
`model_release_verified` decision. The focused Phase 5 tests (14), Ruff,
formatter check, strict Mypy, and full 150-test suite passed from the project
virtual environment. No model or dependency was acquired, no network, FFmpeg,
FFprobe, or model action occurred, no user media was accessed, and no
`outputs/` content was written. `production_validated` remains false.
