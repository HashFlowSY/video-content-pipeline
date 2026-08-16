"""Deterministic Hypothesis gate profile for the Phase 10 property layer.

There is no conftest in this project (zero-conftest convention), so profile
registration cannot hook into collection. Instead, any test module that
exercises Hypothesis imports this module; importing it registers the profiles
and activates the deterministic gate profile as a module-level side effect. The
import is idempotent — modules load once per session, and re-loading a profile
is harmless.

The gate profile is fully reproducible: ``derandomize=True`` seeds every
property from the test's identity rather than the clock, a fixed example budget
keeps the full suite inside its wall-clock budget, and the example database is
disabled so no ``.hypothesis`` cache can smuggle state between runs. Two runs of
the same property therefore draw the identical example sequence.

Exploratory *random* search is a manual developer option only: set
``HYPOTHESIS_PROFILE=dev`` (or call ``load_profile("dev")``) to widen the budget
and let Hypothesis pick fresh seeds. The gate — and CI — always run the
deterministic profile because the environment variable is unset there.
"""

from __future__ import annotations

import os

from hypothesis import settings

GATE_PROFILE = "vcp-gate"
DEV_PROFILE = "dev"

#: Fixed per-property example budget for the deterministic gate (~50/property).
GATE_MAX_EXAMPLES = 50

settings.register_profile(
    GATE_PROFILE,
    derandomize=True,
    max_examples=GATE_MAX_EXAMPLES,
    # No timing-based failures and no cross-run example cache: the gate must be
    # a pure function of the source, not of the machine or a local database.
    deadline=None,
    database=None,
)

settings.register_profile(
    DEV_PROFILE,
    derandomize=False,
    max_examples=200,
    deadline=None,
)

settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", GATE_PROFILE))
