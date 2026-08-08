# Keep the Phase 2 core dependency-free

Phase 2 uses only Python's standard library for rational time, JSON, subtitle
parsing, subprocess control, and hashing, plus the already locked test tools.
No third-party runtime package, lockfile change, or package download is part of
this phase without a separately approved decision, keeping the deterministic
prototype's supply chain small and explicit.

## Considered Options

- Standard-library core: accepted because the Phase 2 domain is deliberately
  narrow and the existing runtime already provides the required primitives.
- Add parsing or media libraries: rejected for now because it expands the lock
  file, download surface, and audit scope without a demonstrated necessity.
