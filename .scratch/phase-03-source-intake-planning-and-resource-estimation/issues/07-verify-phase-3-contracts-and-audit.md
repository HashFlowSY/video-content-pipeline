# 07 -- Verify Phase 3 contracts and audit

**What to build:** The user and maintainer receive repeatable evidence that the
complete Phase 3 planning contract works through the `vcp plan` seam and that
all retained artifacts, failures, and tool evidence are auditable.

**Blocked by:** 03 -- Estimate and confirm full decode; 04 -- Revalidate and
confirm RunPlan; 05 -- Authorize URL and manual collection; 06 -- Acquire
public source under host control.

**Status:** resolved

- [x] Offline tests use temporary roots, retained synthetic media, and
  controlled tool substitutes; they do not use a live URL or user media.
- [x] Fixture-backed checks exercise the explicit decode-confirmation path and
  preserve all source and ProbeDocument evidence.
- [x] Environment, test, lint, format, and type gates pass with no package,
  model, or network download.
- [x] The Phase 3 inventory records every created, modified, read external,
  generated, and retained artifact without cleanup.

## Answer

The public Phase 3 CLI contract is verified offline for both the successful
`plan` -> `decode` -> `confirm` transition and a revalidation-blocked decode.
The retained synthetic fixture is snapshotted and its structural and coverage
ProbeDocuments are preserved; the blocked child report retains the full pinned
FFprobe and FFmpeg identity evidence. All required project-local checks pass.

## Comments

2026-08-10: Added the offline integration proof in
`tests/integration/test_phase_3_contracts.py`. It uses the retained
`phase-02-offset-av-aac` synthetic fixture, derives a controlled packet-only
probe response from retained evidence, snapshots the source into a temporary
root, and drives the public `vcp plan`, `decode`, and `confirm` seam. The test
asserts preserved structural and coverage ProbeDocuments, the explicit null
output FFmpeg argv, separate report and plan IDs, decode history, and no output
media. A controlled pinned-tool revalidation failure also produces an auditable
blocked child report with retained tool identity evidence. The project-local
environment gate, full test suite (111 passed), Ruff check, format check, and
strict Mypy all passed. No package, model, network, yt-dlp, user-media, or
production-validation action occurred.
