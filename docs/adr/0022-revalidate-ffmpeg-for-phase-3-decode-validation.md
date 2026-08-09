# Revalidate FFmpeg for Phase 3 decode validation

Phase 3 may use the existing FFmpeg binary as a Pinned external tool only
after recording and revalidating its path, version, and content hash. It reads
only a SourceArtifact, performs Full decode validation to null output, and
creates no derived media; a missing or changed binary blocks the preflight.

## Considered Options

- Revalidated null-output FFmpeg: accepted because full stream-decode evidence
  is required without crossing into a processing or media-transformation stage.
- Extending the Phase 2 fixture-generator permission silently: rejected
  because real-source validation is a distinct authority and audit boundary.
