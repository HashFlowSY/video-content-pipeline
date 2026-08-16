# 02 — Build the shared fault-injection support kit

**What to build:** A plainly importable `tests/support/` package (explicit
imports, no conftest magic — preserves the repo's self-contained-test-file
convention) housing the fault tooling every later injection ticket uses.
Contents: (1) interceptors wrapping the four `durable_io` functions
(`write_and_fsync`, `durable_write`, `atomic_replace`, `fsync_directory`):
a golden-run call counter, and a fail-at-Nth-call injector supporting the
three Fault classes — process death (raise a dedicated exception AND freeze
all further durable writes so production exception handlers cannot perform
disk work a real power loss would never run; the test then recovers from
on-disk state alone), exhausted disk (`OSError(errno.ENOSPC)`), and torn
write (write a strict prefix of the bytes, then the death freeze).
(2) The `KillingExecutor` from `tests/integration/test_phase_9_recovery.py`
extracted into the kit (that file imports it from support; behavior
unchanged). (3) A control-file corruption helper (garbage bytes /
truncation). The kit itself gets direct unit tests proving each injector
does exactly what it claims.

**Blocked by:** 01
**Status:** done (`32c218f`)
**Labels:** ready-for-agent

- [x] `tests/support/` exists, no conftest.py added anywhere — new
      `fault_injection.py` + `executors.py`, plain importable modules
- [x] Golden-run counter reports a stable call count across two identical runs
      (`test_golden_run_count_is_stable_across_two_identical_runs`)
- [x] Death injector: no durable write succeeds after the injection point —
      `_frozen` re-raises `SimulatedProcessDeath` on every later write
      (`test_no_durable_write_succeeds_after_the_death_point`)
- [x] Torn-write injector: a strict byte-prefix lands, nothing after
      (`test_torn_write_lands_a_strict_prefix_and_nothing_after`; a torn
      `atomic_replace` leaves a partial `.tmp` beside an intact target)
- [x] ENOSPC injector raises `OSError` with `errno.ENOSPC` at exactly call N
      (`test_enospc_injector_raises_at_exactly_call_n`; no freeze)
- [x] `test_phase_9_recovery.py` imports the shared `KillingExecutor`, suite
      green (full suite 1052 passed)

## Comments

- Delivered as `32c218f`. `DurableIoInterceptor` wraps all four `durable_io`
  primitives behind one shared call counter; an `InjectionPlan` fails the Nth
  write with process-death / exhausted-disk / torn-write. `install()` takes
  `monkeypatch.setattr` as a parameter so the kit never imports pytest
  (zero-conftest convention). Control-file corruption helpers
  (`corrupt_with_garbage`, `truncate_file`) included. Two-axis code review
  (Standards + Spec) ran clean; the only applied fix was typing `install`'s
  callback precisely instead of `object` + a runtime assert.
