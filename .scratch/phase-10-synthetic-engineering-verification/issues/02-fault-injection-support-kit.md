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
**Status:** open
**Labels:** ready-for-agent

- [ ] `tests/support/` exists, no conftest.py added anywhere
- [ ] Golden-run counter reports a stable call count across two identical runs
- [ ] Death injector: no durable write succeeds after the injection point
- [ ] Torn-write injector: a strict byte-prefix lands, nothing after
- [ ] ENOSPC injector raises `OSError` with `errno.ENOSPC` at exactly call N
- [ ] `test_phase_9_recovery.py` imports the shared `KillingExecutor`, suite green

## Comments
