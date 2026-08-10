# Phase 4 Atomic Task List

Status: `completed`; all current tasks are complete and verified offline.

1. [x] Process one verified subtitle track end to end.
2. [x] Produce common-format readable subtitles.
3. [x] Resolve ambiguous subtitle-track selection explicitly.
4. [x] Preserve bounded subtitle-processing failures.
5. [x] Report partial collections and ASR handoff.
6. [x] Prove the subtitle CLI contract offline.

These tickets are vertical slices, each TDD-first and independently verifiable.
Ticket 1 is the initial frontier; tickets 2 and 3 depend only on it; ticket 4
depends on 1 and 3; ticket 5 depends on 2, 3, and 4; ticket 6 depends on all
preceding current tickets.
