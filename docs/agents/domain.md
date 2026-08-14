# Domain Docs

How engineering skills should consume this repository's domain documentation.

## Before exploring or changing a domain boundary

1. Read [`CONTEXT-MAP.md`](../../CONTEXT-MAP.md) first.
2. Select the smallest affected Context, then read every required dependency
   named by the map. Read `docs/RUNTIME_GOVERNANCE.md` only for environment or
   setup vocabulary.
3. Define or revise a term only in its owning Context. Link to an owner from a
   dependent Context instead of copying its definition.
4. For a cross-Context change, name every affected owner and relevant global
   ADR. Add governing decisions to the global `docs/adr/` tree and index them
   from the map; do not create context-scoped ADR trees.

The map is the sole domain-documentation entry point. If a proposed output
would contradict an ADR, surface that conflict instead of silently overriding
the decision. Historical inventories and archived records are evidence of their
original layout and are not rewritten for navigation.

## Layout

```text
/
|- CONTEXT-MAP.md
|- docs/contexts/
|- docs/
|  `- adr/
`- src/
```
