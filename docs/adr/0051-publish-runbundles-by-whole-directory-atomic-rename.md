# Publish RunBundles by whole-directory atomic rename

Candidate artifacts are assembled in a Staging area
(`work/<source-id>/<run-id>/staging/`) already in final RunBundle layout, with
candidate hashes recorded. Publication is a single `rename` of the whole
staging directory to `outputs/<source-id>/<run-id>/`, preceded by a
same-filesystem check (`st_dev` equality between the staging parent and the
outputs parent; mismatch is an error, never a silent fallback to copying) and
followed by re-hashing every published file against the RunBundle manifest.
A failed publish leaves nothing visible under `outputs/`, and an existing run
directory is never overwritten.

## Considered Options

- Whole-directory atomic rename: accepted because it gives one commit point,
  no partially visible bundle, and trivial failure semantics (either the
  bundle exists completely or not at all), satisfying the exit gates "outputs
  never overwrite an old run" and "manifest matches disk".
- Per-file rename with the manifest written last as the commit point:
  rejected because it exposes a partially published directory to readers and
  crash windows between file renames require a repair protocol.
- Copy from staging then verify: rejected because copying is not atomic,
  doubles peak disk during publication, and invites a cross-device fallback
  that silently abandons atomicity.
- Write formal artifacts directly into `outputs/`: rejected because any
  failure leaves a corrupt half-bundle where consumers look, violating the
  always-publish and hash-verification gates.
