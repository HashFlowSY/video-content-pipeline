# Phase 12 — Real Video Testing (long-running acceptance)

Type: spec
Status: specification_pending_approval
Labels: ready-for-agent
Phase: 12
Published: docs/PHASE_12_SPECIFICATION.md

## Domain routing

Read [CONTEXT-MAP.md](../../CONTEXT-MAP.md). No new context. New vocabulary
— `Formal branch`, `Real-run confirmation`, `Coverage ledger` — is owned by
orchestration (already recorded in its CONTEXT and the owner index).
Affected contexts: source-planning (PlanReport gains peak-memory and
model-status fields; the URL download plan gains per-run host disclosure,
ADR 0057) and orchestration (real engines invoked from the orchestrated
run; RunBundle provenance completed). Load-bearing ADRs: 0015, 0017, 0019,
0037, 0051, 0053, 0055, 0056, and the new 0057.

## Problem Statement

The pipeline has never processed a user-chosen real video end to end. Every
real engine was wired beside the offline path in Phase 11 but is not
invoked by the orchestrated run; the plan a user must confirm before any
real test is missing two of its four legally required fields (peak memory,
model status); a published RunBundle's processing report carries empty
model/tool/environment/resource sections, so nothing binds a run's outputs
to the model stack that produced them; and the acquisition contract rejects
the user's actual first material (a bilibili URL) at three independent
points (split audio+video formats, CDN host escalation, and — for later
branches — no subtitle pulling and no playlist expansion). Meanwhile the
maintainer has no capacity to assemble scenario-specific test materials up
front, so a one-shot acceptance campaign is impossible: acceptance must
happen opportunistically, over a long period, without losing track of which
of the five required Formal branches has actually been confirmed and on
what evidence `production_validated` will eventually rest.

## Solution

Phase 12 becomes a long-running acceptance phase with three parts. First, a
bounded enablement effort makes a real run possible and its artifacts
complete: the orchestrated run invokes the real engines, acquisition
accepts split-format sources, media hosts are authorized per run from the
download plan (ADR 0057), the PlanReport shows all four legal fields, and
the RunBundle processing report carries full provenance. Second, run #1
processes the maintainer's bilibili material through the genuine production
entrance — download plan → confirmation → download → plan/confirm → run
(with one pause/resume drill) → atomic publication → maintainer inspection
with per-capability verbal ratings — and lands the first entry in a new
Coverage ledger. Third, a standing per-run procedure lets every future real
material repeat that flow with zero ceremony beyond the ledger entry; the
phase closes whenever all five Formal branches are confirmed, and only
then, with the user's explicit overall confirmation, does the project flip
to `production_validated`.

## User Stories

1. As the maintainer, I want to hand the pipeline a real video URL and have it processed end to end by the real engines, so that the system's value is finally demonstrated on my own material rather than synthetic fixtures.
2. As the maintainer, I want a written download plan (duration, formats, total size, media hosts, disk headroom, estimated time, peak memory, model status) before any bytes move, so that I can make an informed authorization decision per download.
3. As the maintainer, I want my confirmation of a download plan to authorize exactly the disclosed media hosts for that run and nothing else, so that no standing network authority accumulates behind my back.
4. As the maintainer, I want a mid-download redirect to an undisclosed host to fail closed, so that per-run authorization is real enforcement and not decoration.
5. As the maintainer, I want split audio+video (DASH) sources to be sized and acquired correctly, so that real video platforms are actually reachable instead of rejected at metadata time.
6. As the maintainer, I want the pre-run plan to show estimated time, peak memory, disk, and model status, so that I can judge whether my machine survives the run before committing to it.
7. As the maintainer, I want the peak-memory estimate grounded in the recorded Phase 11 device baselines, so that the number is evidence-based rather than invented.
8. As the maintainer, I want the orchestrated run to invoke the acquired real engines while automated tests keep the controlled offline adapters, so that production behavior and test determinism stop being the same code path pretending to be both.
9. As the maintainer, I want to pause a real run at a stage boundary and resume it later, so that a long video does not hold my machine hostage.
10. As the maintainer, I want an ordinary real-run failure to still publish the Minimal RunBundle, so that a failed evening run leaves me evidence instead of nothing.
11. As the maintainer, I want the processing report to name every model actually used (revision, hash, size, purpose) plus tools, environment, parameters, and measured resource usage, so that each run's outputs are bound to the exact stack that produced them.
12. As the maintainer, I want to inspect a published run's subtitles, speakers, detailed content, and summary and record an explicit confirmation, so that acceptance is a recorded decision and not a vague impression.
13. As the maintainer, I want each confirmation to carry per-capability verbal ratings (acceptable / marginal / unacceptable), so that a future model-swap discussion cites recorded verdicts instead of memory.
14. As the maintainer, I want a Coverage ledger mapping each of the five Formal branches to the confirmed runs that cover it, so that after months of intermittent testing I still know exactly what remains before production validation.
15. As the maintainer, I want each run's ledger entry to state honestly which Formal branches it covered, so that "顺带覆盖" claims cannot creep in without evidence.
16. As the maintainer, I want a standing per-run procedure for future materials with no per-run ticketing ceremony, so that long-tail acceptance stays cheap enough to actually happen.
17. As the maintainer, I want CER/WER never computed without a human reference text, and the reference's source and proofing scope recorded when one exists, so that accuracy numbers are never fabricated.
18. As the maintainer, I want the first real run to expose the actual severity of the known diarization over-clustering and text_semantics single-segment collapse before any tuning, so that calibration follows real evidence instead of blind parameter turning.
19. As the maintainer, I want `production_validated` to flip only when all five Formal branches are confirmed and I have explicitly confirmed the whole, so that the flag means what it says.
20. As a future implementer or auditor, I want each real run's confirmation recorded in the established maintainer-review format with the run identity and model-stack snapshot, so that the acceptance trail is machine-checkable at phase closure.
21. As a future implementer, I want the multi-P branch to have a named path (manual ordered collection) even before playlist support exists, so that the branch is coverable without silent scope inflation.
22. As the maintainer, I want yt-dlp to stay pinned unless a real observed failure is attributable to the binary itself, so that the toolchain does not churn speculatively.

## Implementation Decisions

- **Phase shape (D1)**: standing per-run procedure + Coverage ledger; the
  phase closes at 5/5 confirmed Formal branches, not on a calendar. The
  ledger lives as a maintainer-readable document with a five-row branch
  table and a run log; one confirmation record per run in the established
  Phase 11 maintainer-review shape (D4). The project state file gains no
  branch-level fields until closure.
- **Run #1 material (D2)**: the maintainer's bilibili video
  `BV1tcuz6EEkV` (zh livestream clip; expected to cover the full-ASR
  branch — the actual branch is recorded after probing, not assumed).
- **Run #1 bar (D3)**: confirmed plan → full run including one deliberate
  pause/resume drill at a stage boundary → published RunBundle →
  maintainer inspection → ledger entry. Failure publication is never
  staged; it is recorded if a real failure occurs.
- **Engineering before run #1 (D5, D6)**: five enablement changes, all
  landed and green before the first real run —
  1. the orchestrated run invokes real adapters for acquired capabilities
     (offline adapters remain the automated-test path per ADR 0037);
     pause/resume/cancel correct across Model runtime subprocess stages;
  2. the acquisition metadata pass sums component sizes of split-format
     (DASH) sources instead of rejecting them, still failing closed when
     no component size is determinable;
  3. per-run media host authorization (ADR 0057): metadata resolves media
     hosts, the download plan discloses them, the acquisition proxy
     admits exactly the confirmed set for that download;
  4. the PlanReport adds a peak-memory estimate (basis: Phase 11 device
     baselines per capability) and per-capability model status from the
     registry;
  5. the run report inputs stub is replaced: processing reports carry
     models (name/revision/sha256/size/purpose), tools, environment,
     parameters, and measured resource usage.
- **Host authorization model (D8)**: per-run disclosed host set, no
  standing platform allowlist; ADR 0057 records the trade-off (rejected:
  suffix allowlist, strict page-host equality, mid-download prompting).
- **Model swaps deferred (D9)**: the maintainer does not currently intend
  to swap models; any future swap is its own decision round (download
  plan, registry entry, re-confirmation semantics decided then).
- **Ratings (D10)**: every Real-run confirmation records
  acceptable / marginal / unacceptable per capability (subtitle
  readability, speaker separation, summary faithfulness).
- **Quality leftovers (D7)**: diarization over-clustering and
  text_semantics single-segment collapse are observed on run #1 before
  any tuning decision.
- **Governance**: per-download confirmation with plan first; model
  authorization never reused as media authorization; credential-free
  quality only; runtime policy (no auto-downloads, no paid APIs, no
  dependency upgrades) unchanged; yt-dlp stays pinned per ADR 0019.
- **Closure contract**: completion report + machine-checkable phase
  inventory + acceptance test (Phase 11 pattern) + the user's explicit
  overall confirmation, then the `production_validated` flip.

## Testing Decisions

- A good test asserts external behavior at the highest existing seam —
  the artifacts a maintainer actually sees (PlanReport JSON, published
  RunBundle contents, typed failures) — never adapter internals.
- **Seams (confirmed with the maintainer, zero new seams)**:
  1. the CLI command boundary (`vcp plan` / `vcp run` with controlled
     offline adapters — the seam the Phase 9/10 golden-run and
     fault-matrix suites already use) carries the plan legal-field and
     RunBundle provenance assertions;
  2. the acquisition metadata seam (canned yt-dlp JSON) carries DASH
     multicomponent sizing and media-host disclosure;
  3. the URL-policy / proxy admission seam carries per-run host
     authorization: disclosed hosts admitted, undisclosed hosts rejected
     as host escalation, including redirect cases.
- The only new observable point is run composition's adapter selection:
  assert a real orchestrated run selects real adapters while the
  automated suite never loads a real model. The real engines themselves
  are verified by run #1's real evidence, not by CI.
- Prior art: Phase 10 golden-run/fault-matrix acceptance tests, existing
  acquisition metadata tests with canned downloader output, the Phase 11
  inventory acceptance test as the closure-gate pattern.
- The engineering suite stays green throughout; run #1's own acceptance
  is human (Real-run confirmation), recorded, and outside the automated
  suite by design.

## Out of Scope

- Model swaps of any capability (separate decision round if evidence
  motivates one).
- bilibili CC-subtitle API acquisition; the subtitle-priority branch is
  covered by a source with embedded subtitle streams, or that support is
  specced when such material actually arrives.
- Automatic playlist/multi-P expansion; the multi-P branch goes through
  the existing manual ordered collection.
- Credentialed or HD downloads; materials outside zh/en.
- Pre-emptive diarization threshold tuning or text_semantics
  recalibration.
- Computing CER/WER for run #1 (no human reference text is planned).

## Further Notes

- This phase is expected to stay `in_progress` for a long time; that is
  the designed shape, not a stall. Branch coverage advances only when
  real materials arrive.
- The canonical prose specification (with the full decision table D1–D10
  and exit gates) is published at docs/PHASE_12_SPECIFICATION.md; ADR
  0057 ships as a draft that takes effect with the specification's
  approval.
- Grilling consensus 2026-08-17, four rounds; the seam plan above was
  separately confirmed by the maintainer.
