# Verify engineering with synthetic media and a deterministic fault matrix

Phase 10 proves whole-pipeline engineering correctness without Real media.
Synthetic media fixtures are generated at test-session time from versioned
Fixture recipes by the pinned host ffmpeg recorded in `config/tools.json`
(identity-verified; absence or mismatch is a test error, never a skip), one
fixture branch for each of Phase 11's mandatory real-video branches. `vcp run`
executes end to end through the production composition with real per-phase
functions, real ffmpeg/ffprobe, and the real filesystem; deterministic
substitute model adapters are the only non-real component, injected at the
existing composition seam so production code gains no test modes. Failure
behavior is verified by a deterministic Fault matrix: a Golden run enumerates
every durable write call (`durable_io` is the orchestration layer's single
persistence outlet), and the run is replayed injecting process death,
exhausted disk (ENOSPC), or a torn write at each enumerated Fault point.
Power loss is covered at two tiers: the deterministic matrix is the
exhaustive body; a small set of real SIGKILL subprocess tests provides
end-to-end spot checks. Injected process death freezes all further durable
writes so exception handlers cannot perform disk work a real power loss
would never run.

## Considered Options

- Synthetic fixtures plus a deterministic fault matrix: accepted because
  fixtures are reproducible, license-free, offline, and seconds-long; the
  matrix is exhaustive by construction (new persistence call sites join the
  enumeration automatically) and every cell is replayable.
- Small real sample videos committed to the repository: rejected because
  they add media binaries, licensing questions, and irreproducible
  provenance while proving nothing more about engineering than synthetic
  fixtures do; real-media qualities are Phase 11's subject.
- A production-side fault-point registry (labeled write sites): rejected as
  a second source of truth that drifts from the code; dynamic enumeration
  from the Golden run is complete and maintenance-free.
- Real small-filesystem disk-full rigs (e.g. a tiny dmg): rejected because
  they are slow, fragile, and platform-entangled; seam-injected ENOSPC is
  deterministic and covers the code paths under test.
- SIGKILL-only power-loss testing: rejected because real kills cannot
  enumerate write boundaries; they remain as spot checks only.
- Vendoring ffmpeg into `runtime/` for tests: rejected because it requires
  an authorized download with no payoff before Phase 11; the identity-pinned
  host toolchain already has fixture-generation precedent from Phase 2.
- Skipping integration tests when tools are absent: rejected because a
  skipped layer silently hollows out the exit gate on the gate machine.
