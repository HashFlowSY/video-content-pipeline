# Revalidate before subtitle processing

Phase 4 subtitle processing starts only from a confirmed RunPlan after the
SourceArtifact hashes, pinned FFmpeg identity, and versioned subtitle rules
have been revalidated. Evidence drift blocks processing and requires a new
plan rather than mutating the prior plan or continuing with a warning, so the
retained subtitle artifacts remain reproducible and truthfully authorized.

## Considered Options

- Strict revalidation and replanning: accepted because subtitle extraction
  reads media and creates new evidence whose provenance must remain exact.
- Continue with a warning or update the RunPlan: rejected because either
  makes an immutable plan describe a different source, tool, or rule set than
  the one actually used.
