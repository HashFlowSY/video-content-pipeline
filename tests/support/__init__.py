"""Plainly importable test-support kit for the Phase 10 verification layers.

This package holds shared property-test and fault-injection tooling. It is a
normal importable package (``from tests.support import ...``) with **no
conftest magic**, preserving the project's zero-conftest convention: every
activation is an explicit import, never an implicit collection-time hook.
"""
