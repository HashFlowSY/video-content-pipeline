# 02 -- Probe a media-qualified local plan

**What to build:** A local SourceArtifact preflight report gains retained
structural and packet-level ProbeDocuments, exact StreamCoverage, and
metadata-only SubtitleTrackCandidates so the user can see whether the source
is Media-qualified.

**Blocked by:** 01 -- Local source preflight report.

**Status:** resolved

- [x] Probe evidence is captured only from the SourceArtifact with a Pinned
  external tool identity and no metadata-duration fallback.
- [x] A usable audio or video stream with determinate StreamCoverage is required
  for a non-blocked report.
- [x] SubtitleTrackCandidates expose metadata only; subtitle text is not
  acquired, parsed, or serialized.
- [x] Invalid inspection evidence creates a retained blocked PlanReport.

## Comments

2026-08-10: Implemented and verified media-qualified local planning. Structural
and packet-level ProbeDocuments are retained for each SourceArtifact, exact
packet-derived StreamCoverage is persisted, and subtitle candidates expose
metadata only, including container format. Invalid probe output, failed probe
invocations, and immutable-evidence conflicts retain blocked PlanReports with
available partial evidence. Source/evidence association is immutable and
validated during report creation and loading. Full offline verification passed:
82 tests, Ruff check and format check, and strict Mypy. Final standards and
specification reviews found no actionable issues.
