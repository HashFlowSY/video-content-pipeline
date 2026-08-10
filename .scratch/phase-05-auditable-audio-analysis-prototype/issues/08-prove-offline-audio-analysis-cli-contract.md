# 08 -- Prove offline audio-analysis CLI contract

**What to build:** The complete Phase 5 public CLI contract is proven with
project-owned synthetic media, fixed candidate-output fixtures, and controlled
adapters. The result is a repeatable offline proof of the user-visible states
and immutable evidence boundaries, not a real-world audio-quality claim.

**Blocked by:** 07 -- Deliver serial pauses and partial analysis reports.

**Status:** ready-for-agent
**Labels:** ready-for-agent

- [ ] End-to-end CLI tests cover missing and blocked models, stream selection, revalidation drift, calibration results, VAD risks, alignment and diarization conflicts, pauses, resumes, and partial reports.
- [ ] Tests prove no model/runtime/dependency download, network request, user-media access, ASR, RunBundle publication, or mutation of Phase 4 artifacts and RunPlans.
- [ ] The full project test suite, Ruff checks, formatter check, and Mypy pass from the project virtual environment, while production validation remains false.
