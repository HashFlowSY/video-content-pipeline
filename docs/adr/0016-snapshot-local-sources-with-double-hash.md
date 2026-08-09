# Snapshot local sources with a double hash

Phase 3 copies an explicitly authorized local source into a project-owned,
content-addressed SourceArtifact only when hashes before and after the copy
match. A source that changes during copying is rejected and must be replanned;
duplicate content is retained once without using symbolic or hard links.

## Considered Options

- Copy with source and destination hash verification: accepted because planned
  evidence and later processing must refer to stable bytes owned by the
  project.
- Process the original path or link it into the project: rejected because the
  source may change or disappear after planning and links preserve that mutable
  dependency.
