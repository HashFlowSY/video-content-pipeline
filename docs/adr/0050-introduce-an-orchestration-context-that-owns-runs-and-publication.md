# Introduce an orchestration context that owns runs and publication

Phase 9 composes the per-phase prototypes into a single recoverable CLI and
performs the project's first `outputs/` writes. We introduce one
`orchestration` Context that owns both the run machinery (run identity and
state, stage units, invalidation keys, process control, crash recovery) and
publication (staging, atomic publish, RunBundle, manifest, reports,
inventory, latest pointer). `RunBundle` and `Publication boundary` migrate
from `media-foundation` to `orchestration`; the placeholder term
`Future publication stage` is retired because Phase 9 now defines that stage.

## Considered Options

- One `orchestration` Context owning runs and publication: accepted because
  publication is the terminal stage of the run DAG and shares the run
  identity, invalidation keys, and state machine; a single owner keeps the
  always-publish guarantee (every ordinary failure still publishes a Minimal
  RunBundle) expressible inside one vocabulary.
- Two Contexts, `orchestration` and `publication`: rejected because the
  boundary between them would be crossed by nearly every contract (run
  status decides bundle content; staging is keyed by run identity), creating
  a chatty, high-frequency boundary with no independent evolution today. It
  can be split later if real-world testing shows publication evolving
  separately.
- Extend `media-foundation` with run and publication machinery: rejected
  because the base evidence vocabulary consumed by every Context must not own
  process control; it would also invert the dependency direction, since
  orchestration depends on every evidence Context.
- Keep `RunBundle` ownership in `media-foundation`: rejected because the term
  stops being a future placeholder and becomes a governed artifact of the
  publication contract; its definition must change in step with the
  orchestration vocabulary that produces it.
