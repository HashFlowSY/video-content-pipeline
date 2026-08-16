"""Phase 10 baseline: the deterministic Hypothesis gate and strict markers.

These tests protect the governance choices ticket 01 established. They are the
first users of ``tests.support`` and the first ``@given`` properties in the
project, so they double as proof that the property layer is wired up and
reproducible.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.support import hypothesis_profiles


def _drawn_sequence() -> list[int]:
    """Run one full property campaign and record every example it drew."""

    seen: list[int] = []

    @given(st.integers())
    def collect(value: int) -> None:
        seen.append(value)

    collect()
    return seen


def test_gate_profile_draws_identical_examples_across_runs() -> None:
    first = _drawn_sequence()
    second = _drawn_sequence()

    assert first, "the gate profile must draw at least one example"
    assert first == second, "derandomized gate must replay an identical sequence"


def test_gate_profile_is_the_active_deterministic_profile() -> None:
    # Identify the active profile by its observable settings rather than by
    # Hypothesis's private profile registry: derandomize + fixed budget + no
    # example database together pick out the gate profile among those we
    # register, and survive upstream renames of the internal machinery.
    active = settings()
    assert active.derandomize is True
    assert active.max_examples == hypothesis_profiles.GATE_MAX_EXAMPLES
    assert active.database is None


def test_strict_markers_and_the_phase_10_markers_are_registered(
    pytestconfig: pytest.Config,
) -> None:
    assert pytestconfig.getoption("strict_markers") is True
    registered = {line.split(":", 1)[0] for line in pytestconfig.getini("markers")}
    assert {"integration", "slow", "faultmatrix"} <= registered
