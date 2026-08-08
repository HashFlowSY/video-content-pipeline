# Keep Phase 2 media APIs internal

Phase 2 adds no user-media CLI command and exposes its deterministic media core
only through library APIs, explicit fixture generation, and integration tests.
The existing environment check remains unchanged; `vcp plan <source>`, local
file handling, and URL access stay in Phase 3, preventing source-intake scope
from entering the synthetic-fixture prototype.

## Considered Options

- Internal Phase 2 APIs only: accepted because the phase has no authorized user
  media or source-access boundary.
- Add a media-facing CLI now: rejected because it would imply user-input
  handling before the Phase 3 safety and planning rules exist.
