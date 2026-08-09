# Require explicit URL access mode

Phase 3 requires every public-URL RunPlan to name `filtered` or `direct` before
the URL is accessed. We reject omitted, failed, and incompatible modes instead
of inferring a default or falling back, because the two modes authorize
different network behavior and the chosen boundary must be auditable. A
redirect, new media host, or HTTPS downgrade is a Host escalation and requires
new user confirmation before acquisition continues. Raw supplied URLs are
process-local only; persistent records retain Redacted source provenance and
the acquired SourceArtifact hash instead. An initial `http://` URL needs
separate Insecure HTTP authorization and is reported without a
transport-integrity claim.

## Considered Options

- Explicit `filtered` or `direct` mode: accepted because source access is a
  per-RunPlan authorization rather than an ambient downloader capability.
- Default or automatic fallback: rejected because it silently widens the
  network action a user authorized.
