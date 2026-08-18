# Phase 12 Coverage Ledger — Real Video Testing

**Purpose.** One place where the maintainer can see exactly which of the five
Formal branches have been confirmed by real runs, by which runs, and what
remains before `production_validated`. This ledger is the **sole** evidence
base for declaring real-video acceptance complete
([orchestration CONTEXT — Coverage ledger](contexts/orchestration/CONTEXT.md)).

Governing documents: [PHASE_12_SPECIFICATION.md](PHASE_12_SPECIFICATION.md)
(decisions D1–D10) and
[PHASED_EXECUTION_PLAN.md §阶段 12](PHASED_EXECUTION_PLAN.md).

This is a **long-running acceptance phase**. It stays `in_progress` — possibly
for a long period, which is expected and not a stall — until every Formal
branch row below reads `confirmed`. Branches retire opportunistically as real
materials arrive; no new ticket is opened per run (see the standing procedure).

## Branch coverage — 0 / 5 confirmed

A Formal branch counts as covered only by a recorded Real-run confirmation.
Scenarios may share one video, but each branch needs its own recorded
confirmation.

| # | Formal branch | Status | Confirming run(s) | Date confirmed |
|---|---|---|---|---|
| 1 | subtitle-priority (embedded subtitle stream preferred over ASR) | unconfirmed | — | — |
| 2 | full ASR (no usable subtitle track; speech transcribed end-to-end) | unconfirmed | — | — |
| 3 | anomalous subtitles (rolling repeats or time drift) | unconfirmed | — | — |
| 4 | multi-part sources (multi-P / ordered manual collection) | unconfirmed | — | — |
| 5 | visual-text OCR (explicitly enabled `visual-text`) | unconfirmed | — | — |

## Run log

Every real run gets one row here and one confirmation file under
[`docs/phase-12-runs/`](phase-12-runs/) (shape:
[`docs/phase-12-runs/_TEMPLATE.md`](phase-12-runs/_TEMPLATE.md)). Empty until
run #1 lands.

| Run id | Source | Date | Branch(es) claimed | Confirmation record |
|---|---|---|---|---|
| `20260818T111753Z-45c7c50cac559ecf` | maintainer local file `f10e8895…a48889` (34m58s) | 2026-08-18 | branch 2 (full ASR) — completed, verified, published | [run record](phase-12-runs/20260818T111753Z-45c7c50cac559ecf.md) — real cue-cited semantic content; supersedes the pre-text-fix attempt `20260818T074454Z-…`. **D10 ratings PENDING maintainer review**; branch not yet flipped |

## Standing per-run procedure

The same sequence runs for **every** real material, forever, with no new
ticket per run — the ledger is the record. Steps, in order:

1. **Download plan (if the material is a URL).** Write a plan in the
   [`docs/phase-11-download-plans/prototype-media.md`](phase-11-download-plans/prototype-media.md)
   style: URL, probed duration and formats, total size, **disclosed media
   hosts** (per [ADR 0057](adr/0057-authorize-media-hosts-per-run-from-the-download-plan.md)),
   disk headroom, estimated time, **peak-memory estimate**, and **model
   status**. No bytes move before the maintainer confirms this plan.
   Media-download authorization is never reused as model-download
   authorization, and vice versa (standing registry rule). Local-file
   materials skip straight to intake but still get hashed.
2. **Plan / confirm.** `vcp plan` → `vcp plan decode` → `vcp plan confirm`.
   The plan report must show 预计时间、峰值内存、磁盘、模型状态 — all four —
   before the maintainer confirms. Credential-free quality only; no CER/WER
   unless a proofed human reference text exists and its source/scope is
   recorded.
3. **Run.** `vcp run` on the real engines (the controlled offline adapters
   remain the automated-test path, ADR 0037). At least one real run must
   exercise a deliberate `vcp pause` / `vcp resume` drill at a stage boundary;
   any genuine failure is published to the Minimal RunBundle floor (failure
   publication is recorded if and when it actually occurs — never staged).
4. **Publish.** RunBundle published atomically (ADR 0051); `vcp verify` and
   `vcp inventory` pass.
5. **Inspect + rate.** The maintainer inspects the published subtitles,
   speakers, detailed content, and summary, and records a **per-capability
   verbal rating (D10)** — `acceptable` / `marginal` / `unacceptable` — for
   each of **subtitle readability**, **speaker separation**, and **summary
   faithfulness**. Silence or an unrecorded glance is not a confirmation.
6. **Ledger entry.** Add one Run-log row above and one confirmation file under
   `docs/phase-12-runs/`. Flip each Formal-branch row the run actually covered
   (recorded honestly after probing — the *expected* branch and the *actual*
   branch may differ) from `unconfirmed` to `confirmed`, naming this run and
   the date, and update the header count `N / 5`.

## Record format

Each per-run confirmation file follows the **Phase 11 maintainer-review
shape** ([`docs/phase-11-prototypes/maintainer-review.md`](phase-11-prototypes/maintainer-review.md)),
captured in the template
[`docs/phase-12-runs/_TEMPLATE.md`](phase-12-runs/_TEMPLATE.md):

- **Header** — source identity + content hash (sha256 of the intake bytes).
- **Dated decision line** — "Reviewed and decided YYYY-MM-DD."
- **Confirmation table** — the D10 per-capability verbal ratings.
- **Notes / follow-ups** — including the observed real-world severity of any
  known quality leftover (e.g. the D7 diarization over-clustering and
  text_semantics single-segment collapse).
- **Provenance** — the model-stack snapshot that produced the outputs (from
  the RunBundle processing report: models used with name / revision / sha256 /
  path / size / purpose, tools, environment, resources), so any future
  model-swap discussion (D9) cites recorded verdicts against a known stack.

## Closure protocol

Closure happens **only** when the branch table reads **5 / 5 confirmed**, and
even then it is gated on the user. In order:

1. Author `PHASE_12_COMPLETION_REPORT.md` — the acceptance narrative, the five
   confirming runs, and the recorded verdicts.
2. Author `PHASE_12_INVENTORY.json` and an **acceptance test that
   machine-checks that inventory** (the Phase 11 pattern), and confirm the
   engineering suite is green.
3. **Obtain the user's explicit overall confirmation** of the whole phase.
   This gate is mandatory: the standing "do not mark" instruction in
   `AGENTS.md` holds until the user says so in words. An agent never infers
   this confirmation and never flips the state on its own judgment.
4. Only after that explicit confirmation, flip `project-state.json` to
   `production_validated`. `project-state.json` gains no branch-level fields
   before this moment (D4).
