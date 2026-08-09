# Revalidate FFprobe for Phase 3 preflight

Phase 3 may use the existing FFprobe binary as a Pinned external tool only
after recording and revalidating its path, version, and content hash. It probes
only the acquired SourceArtifact and retains the resulting immutable
ProbeDocument; unavailable or invalid probe evidence blocks the RunPlan without
textual or metadata-duration fallback.

## Considered Options

- Revalidated FFprobe for SourceArtifact preflight: accepted because stream,
  subtitle-track, and exact-coverage evidence is required before planning.
- Extending the Phase 2 fixture-only permission silently: rejected because
  real-source inspection has a different authority and audit boundary.
