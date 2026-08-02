# Domain Glossary

| Term | Meaning in this project |
| --- | --- |
| Project root | The sole directory that owns pipeline code, data, downloads, caches, and reports. |
| Managed Python | CPython downloaded by the project-local uv binary into `runtime/python/`. |
| Project virtual environment | The sole Python environment at `.venv/`, created from managed Python. |
| Shell gate | A shell-only check that refuses to start Python unless `.venv` is activated. |
| In-process gate | A Python check that verifies `VIRTUAL_ENV`, `sys.prefix`, and executable location after startup. |
| Tool registry | Metadata about tools that are available or managed; it does not install tools. |
| Model registry | Metadata for later model management. Phase 1 contains no model assets. |
| Runtime download | A Python or tool acquisition explicitly requested during environment setup. |
| Runtime auto-download | A dependency or model fetch initiated implicitly at normal application runtime; prohibited. |
| Phase 2 | The future deterministic media and timeline prototype stage; not part of this implementation. |
