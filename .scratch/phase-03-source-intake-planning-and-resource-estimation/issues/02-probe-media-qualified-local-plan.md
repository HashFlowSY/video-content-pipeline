# 02 -- Probe a media-qualified local plan

**What to build:** A local SourceArtifact preflight report gains retained
structural and packet-level ProbeDocuments, exact StreamCoverage, and
metadata-only SubtitleTrackCandidates so the user can see whether the source
is Media-qualified.

**Blocked by:** 01 -- Local source preflight report.

**Status:** claimed

- [x] Probe evidence is captured only from the SourceArtifact with a Pinned
  external tool identity and no metadata-duration fallback.
- [x] A usable audio or video stream with determinate StreamCoverage is required
  for a non-blocked report.
- [x] SubtitleTrackCandidates expose metadata only; subtitle text is not
  acquired, parsed, or serialized.
- [x] Invalid inspection evidence creates a retained blocked PlanReport.
