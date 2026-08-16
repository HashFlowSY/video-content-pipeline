# Use single-writer run state with file-based control requests

The run process is the only writer of the Run state document (atomic
replace) and the Run events journal (append-only). `vcp pause` and
`vcp cancel` write Control request files that the run process observes at the
next stage-unit boundary; the request, its observation, and the resulting
transition are all journaled events. Heavy runs are serialized by a
Heavy-task lock recording run id, process id, and process start time.
`paused` means the run process has exited after a clean boundary; resume
starts a new process. A crash (power loss, forced kill) is never a persisted
state: it is detected as `running` state with a stale lock, and `vcp resume`
performs Crash recovery from the last checkpoint.

## Considered Options

- Single-writer state with file-based control requests: accepted because
  every control interaction leaves an audit artifact, the mechanism is
  portable, it aligns naturally with "pause takes effect at the current
  atomic work unit boundary", and its failure mode degrades exactly into the
  crash-recovery path that must exist anyway.
- OS signals (SIGTERM/SIGUSR1): rejected because signals leave no audit
  trace, are lossy and platform-divergent, and arrive at arbitrary points
  instead of stage-unit boundaries.
- A resident daemon with IPC: rejected as a second failure domain with its
  own lifecycle, exceeding what a single-CLI first version needs.
- Allowing control commands to write run state directly: rejected because
  two writers to one state file is a corruption class; the state machine
  would need cross-process locking for every transition.
- A persisted `crashed` state: rejected because a crashing process cannot
  reliably record its own crash; the absence of a clean transition, plus a
  stale lock, is itself the evidence.
