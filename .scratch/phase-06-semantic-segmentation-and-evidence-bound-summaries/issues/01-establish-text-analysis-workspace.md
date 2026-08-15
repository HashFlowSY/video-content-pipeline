# 01 -- Establish immutable text-analysis workspace

**What to build:** A user can create an immutable Phase 6 Text analysis report
from explicitly named retained planning and subtitle inputs. When no Controlled
offline text adapter is available, the report states
`controlled_adapter_unavailable` without producing semantic content.

**Blocked by:** None -- can start immediately.

**Status:** resolved
**Labels:** ready-for-agent

- [ ] Add typed domain records for text-analysis reports, workspace identities,
  input bindings, statuses, diagnostics, and restricted raw output.
- [ ] Retain immutable workspace artifacts and an authoritative JSON report
  without modifying RunPlans, subtitle evidence, Phase 5 reports, or
  `outputs/`.
- [ ] Prove unavailable-adapter behavior and no-side-effect guarantees with
  synthetic retained inputs.
