# Require cue-level evidence for Phase 6 facts

Phase 6 treats text-model output as a candidate, never authority. Every formal
factual item and segment title must explicitly cite one or more NormalizedCue
identities; a SemanticSegment or timestamp alone is insufficient. This preserves
source-level auditability while allowing a cue to support multiple separately
cited claims.

## Considered Options

- Cue-level factual citations: accepted because they let an auditor check each
  statement against retained subtitle evidence without trusting generated prose.
- Segment-only or time-range citations: rejected because they leave the factual
  support of individual statements ambiguous.
- Model output as evidence: rejected because a generated claim can add,
  reinterpret, or omit content without a retained source citation.
