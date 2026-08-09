# 03 -- Estimate and confirm full decode

**What to build:** A user with a Media-qualified local plan sees a
Phase-bounded three-point decode estimate and can separately authorize Full
decode validation, receiving a new report that is ready for final confirmation
or clearly blocked.

**Blocked by:** 02 -- Probe a media-qualified local plan.

**Status:** resolved

- [x] The first estimate uses a versioned Decode throughput profile and is
  labelled low confidence unless matching measured history exists.
- [x] Full decode validation cannot start from the initial planning command;
  it needs explicit Decode preflight confirmation.
- [x] Validation decodes all audio and video streams to null output and creates
  no derived media.
- [x] Decode failures are retained as diagnostics in a new blocked PlanReport.

## Comments

2026-08-09: Implemented and verified the decode-confirmation boundary. Decode
throughput profiles now reject zero and negative values, and a failed FFmpeg
process start is reported as `full_decode_failed` so the CLI persists a blocked
child PlanReport. Focused TDD began with the two new failure cases; both passed
after the minimal implementation. Review identified the missing
matching-history exception to low confidence, so completed null-output decode
observations are now retained by exact SourceArtifact identity and replace the
profile estimate only for that same source. CLI contract tests prove that `plan
decode` only reaches `ready_for_confirmation` after validation and that a decode
failure retains a blocked report with its parent report ID. The null-output argv
explicitly maps all video and audio streams. Offline verification passed: 87
tests, Ruff check and format check, and strict Mypy. No FFmpeg or FFprobe was
executed, no user media or network was accessed, and no model or package was
downloaded. Final post-implementation standards and specification reviews found
no actionable issues.
