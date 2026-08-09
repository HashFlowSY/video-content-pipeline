# Revalidate evidence before plan confirmation

Before creating a RunPlan, Phase 3 revalidates the PlanReport's SourceArtifact
hashes, applicable Pinned external-tool identities, current Disk headroom, and
all planning configuration. Any difference makes the report stale and requires
a new planning attempt; confirmation never mutates a prior report.

## Considered Options

- Strict revalidation and replanning: accepted because an immutable RunPlan
  cannot truthfully inherit stale source, environment, or resource evidence.
- Confirm with warnings or rewrite the report: rejected because either hides
  the point at which the authorized plan diverged from its evidence.
