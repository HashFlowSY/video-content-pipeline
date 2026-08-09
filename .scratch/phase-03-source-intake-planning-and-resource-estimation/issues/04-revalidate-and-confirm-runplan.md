# 04 -- Revalidate and confirm RunPlan

**What to build:** A user can confirm a decode-validated PlanReport and receive
an immutable RunPlan only when SourceArtifact, tool, disk, and configuration
evidence still match.

**Blocked by:** 03 -- Estimate and confirm full decode.

**Status:** resolved

- [x] PlanReport and RunPlan use separate immutable identifiers and retained
  records.
- [x] Confirmation revalidates SourceArtifact hashes, Pinned external tools,
  Disk headroom, and planning configuration.
- [x] Any changed evidence makes the report stale and requires a new attempt;
  confirmation never rewrites an old report.
- [x] A valid confirmation produces a RunPlan without raw URL data.

## Comments

2026-08-09: Implemented retained configuration evidence for Phase 3 final
confirmation. PlanReports now capture a deterministic fingerprint of the
project-owned tool registry and decode-throughput profile; a changed, missing,
unreadable, or non-regular configuration input produces
`planning_configuration_changed` and blocks RunPlan creation. A final-confirmation
drift creates an immutable blocked child report, makes its parent permanently
ineligible for confirmation, and reports non-file source reads as controlled
staleness instead of an unhandled operating-system error. RunPlans retain the
revalidated pinned-tool and disk-headroom evidence alongside their separate plan
ID and source scope. Focused tests cover configuration, source, tool, and disk
drift, retention, parent expiry, and source-read failure. Offline verification
passed: 93 tests, Ruff check and format check, and strict Mypy. No network,
user media, models, package downloads, FFmpeg, or FFprobe were accessed during
this ticket.
