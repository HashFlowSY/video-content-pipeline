# Domain Docs

How engineering skills should consume this repository's domain documentation.

## Before exploring, read these

- `CONTEXT.md` at the repository root.
- Relevant ADRs under `docs/adr/`.

This is a single-context repository. There is no `CONTEXT-MAP.md` and no
context-scoped ADR tree. Use the glossary vocabulary in `CONTEXT.md` when
naming domain concepts in issues, tests, and implementation proposals. If a
new output would contradict an ADR, surface that conflict instead of silently
overriding the decision.

## Layout

```text
/
|- CONTEXT.md
|- docs/
|  `- adr/
`- src/
```
