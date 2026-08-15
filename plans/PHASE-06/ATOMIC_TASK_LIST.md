# Phase 6 Atomic Task List

Status: `approved_for_implementation_planning`. All tasks are TDD-first,
offline-only, and must run Python only after activating `.venv` and passing
`scripts/require-project-venv.sh`. No task downloads a model, invokes a real
model, reads user media, accesses a network, or writes `outputs/`.

1. [x] Establish Phase 6 domain records, immutable workspace, report identity,
   and unavailable-adapter result.
2. [x] Add `vcp analyze-text` and explicit `vcp resume-text-analysis`
   contracts with complete input revalidation.
3. [ ] Define versioned prompt, schema, evidence-rule, adapter-projection, and
   deterministic Markdown-renderer contracts.
4. [ ] Implement cue-bound semantic-boundary adjudication, exactly-once cue
   ownership, technical-block deduplication, and conservative fallback.
5. [ ] Validate evidence-bound segment detail, titles, Q&A, people/roles,
   structured details, contradictions, unresolved questions, and diagnostics.
6. [ ] Aggregate Part-local chapters and collection summaries while preserving
   limitations, unavailable Parts, and source-language boundaries.
7. [ ] Implement immutable attempt provenance, state/decision/resource rules,
   restricted diagnostics, and synthetic append-only human-review records.
8. [ ] Prove the complete offline `analyze-text` CLI contract, no-side-effect
   guarantees, and project verification gates.

Dependency order:

```text
01 -> 02 -> 03
03 -> 04 -> 05 -> 06
02 + 03 -> 07
04 + 05 + 06 + 07 -> 08
```

Ticket 01 is the implementation frontier. Tickets must remain small vertical
slices and retain failed evidence rather than overwriting or deleting it.
