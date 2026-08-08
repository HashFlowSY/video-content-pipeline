# Derive stream coverage from decoded intervals

Phase 2 derives `StreamCoverage` only from observed `DecodedInterval` values
with exact starts and ends. Its coverage is the outer envelope of those
intervals, internal gaps are diagnostics, and unavailable required boundaries
make coverage `indeterminate`; container and stream duration metadata never
fills a missing endpoint.

## Considered Options

- Use decoded-interval envelopes: accepted because it makes coverage
  reproducible from media evidence and preserves gaps as observable facts.
- Use metadata duration to complete coverage: rejected because container and
  stream metadata can disagree with actual decodable content.
