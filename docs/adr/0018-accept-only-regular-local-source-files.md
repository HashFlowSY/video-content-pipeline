# Accept only regular local source files

Phase 3 accepts a local source only when the user explicitly supplies one
regular file. Directories, symbolic links, device files, named pipes, and
standard input are rejected because they cannot provide the stable, bounded
bytes required for a double-hashed SourceArtifact.

## Considered Options

- Explicit regular files only: accepted because the intake boundary can inspect
  and snapshot a stable byte sequence without following an ambient reference.
- Links or streaming-style inputs: rejected because targets can change during
  planning and do not fit the immutable source-artifact contract.
