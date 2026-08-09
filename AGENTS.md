# Video Content Pipeline Local Rules

## Scope

- The project root is `/Users/shangyang/Desktop/workspace/projects/video-content-pipeline`.
- Keep all project code, downloads, caches, temporary files, models, input,
  work products, plans, and outputs inside this root.
- Do not mark the project `production_validated`.

## Runtime Boundary

- Use the project-local binary at `tools/uv/uv`; never install uv globally.
- The only managed Python runtime is under `runtime/python/`.
- The only project virtual environment is `.venv/`.
- Before every Python command, activate `.venv` and run
  `scripts/require-project-venv.sh` successfully.
- Do not use system Python, ordinary `pip install`, `uv run`, or implicit
  environment selection.
- Use the project-specific variables `VCP_PROJECT_ROOT`, `UV_INSTALL_DIR`,
  `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR`, `UV_PROJECT_ENVIRONMENT`,
  `PIP_CACHE_DIR`, and `TMPDIR` when invoking tooling.

## Downloads And Models

- Download only from explicitly authorized official sources.
- Keep downloads and caches in `cache/`, tools in `tools/`, and managed Python
  in `runtime/python/`.
- Do not download, install, or auto-fetch models. `models/registry.json` is
  metadata only until a later explicit authorization.
- Do not access browser cookies, credentials, media input, or paid APIs.

## Reporting And Safety

- Announce any script before executing it, including its purpose and impact.
- Record created, modified, downloaded, and read external files in the phase
  inventory. Do not delete archives, caches, temporary data, or failed outputs
  without explicit user authorization.
- Do not create Git remotes or pushes.

## Agent skills

### Issue tracker

Issues and specifications live as local Markdown files under `.scratch/`. See
`docs/agents/issue-tracker.md`.

### Triage labels

Use the canonical local tracker labels, including `ready-for-agent`, recorded
in `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository. Read the root `CONTEXT.md` and relevant
records under `docs/adr/` before changing a domain boundary. See
`docs/agents/domain.md`.
