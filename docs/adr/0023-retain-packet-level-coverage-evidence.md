# Retain packet-level coverage evidence

Phase 3 retains both structural FFprobe JSON and a packet-level Coverage
ProbeDocument for every SourceArtifact, and includes their storage in disk
preflight estimates. This preserves exact PTS-based StreamCoverage for real
sources rather than substituting container metadata duration.

## Considered Options

- Retain complete packet-level evidence: accepted because coverage boundaries
  and internal gaps need auditable raw PTS input.
- Retain only structural metadata or samples: rejected because either would
  reintroduce inferred coverage for real sources.
