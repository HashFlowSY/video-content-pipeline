# Use a Context Map with domain-owned glossaries

Use one root Context Map to route contributors to five domain-owned glossaries:
media foundation, source planning, subtitles, audio analysis, and text analysis.
Each term has one owning Context; dependent Contexts link to that owner instead
of restating a competing definition. Keep the ADR tree global and link-stable,
and index relevant existing and future ADRs from the map, because moving or
duplicating decision records would break historical references and create a
second source of truth.

Runtime setup vocabulary belongs in Runtime Governance rather than a domain
Context. Text analysis may consume audio-analysis evidence as an optional
context, while subtitle-derived claims remain valid without it. The migration
inventory records every retired term and relocated constraint so simplification
of glossary prose does not erase an operational rule.

## Considered Options

- One monolithic glossary: rejected because every contributor must load unrelated
  vocabulary and ownership boundaries remain ambiguous.
- Context-local ADR trees: rejected because duplicate or moved decisions would
  fragment the historical record and make links unstable.
- A sixth runtime Context: rejected because environment setup is governance, not
  domain vocabulary; it has a dedicated runtime document instead.
