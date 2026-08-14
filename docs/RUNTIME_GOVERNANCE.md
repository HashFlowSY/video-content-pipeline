# Runtime Governance

This document owns environment and setup vocabulary. It is separate from the
domain Context Map because runtime policy describes how the project is operated,
not what media, subtitle, audio, or text concepts mean.

## Language

**Project root**:
The sole directory that owns project code, data, downloads, caches, temporary
files, and reports.
_Avoid_: ambient workspace

**Managed Python**:
The project-local CPython runtime provisioned under the repository's managed
runtime directory.
_Avoid_: system Python

**Project virtual environment**:
The sole project Python environment created from Managed Python.
_Avoid_: global environment

**Shell gate**:
The shell check that requires the project virtual environment before Python
starts.
_Avoid_: optional activation check

**In-process gate**:
The Python check that verifies the active virtual environment after startup.
_Avoid_: shell-only validation

**Tool registry**:
Project metadata describing approved tools and their identities; it does not
install or update tools.
_Avoid_: package installer

**Model registry**:
Project metadata describing future model management; it is not a model payload
or an acquisition authorization.
_Avoid_: model cache

**Runtime download**:
An explicitly requested acquisition performed as part of separately authorized
environment setup.
_Avoid_: implicit fetch

**Runtime auto-download**:
An acquisition initiated implicitly during normal application execution; project
runtime policy excludes this behavior.
_Avoid_: background install

## Operating rules

- Keep project code, downloads, caches, temporary files, models, work products,
  and outputs below the Project root.
- Use the project-local `tools/uv/uv`, the Managed Python runtime, and the
  Project virtual environment. Run the Shell gate before every Python command;
  the In-process gate remains the startup defense.
- Treat registries as metadata. A registry entry does not grant permission to
  install a tool, acquire a model, access user media, or contact a network.
- A Runtime download requires explicit authorization and an approved source.
  Normal execution has no Runtime auto-download path.
- Runtime governance does not authorize source access, media processing, model
  execution, publication, or production validation.

The project-specific paths, environment variables, and download restrictions
remain defined by [AGENTS.md](../AGENTS.md) and `config/runtime-policy.toml`;
this document gives their vocabulary one discoverable home.
