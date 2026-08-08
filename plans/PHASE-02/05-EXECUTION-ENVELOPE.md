# Phase 2 Execution Envelope: Ticket 05

Status: approved and executed
Date: 2026-08-09

## Authorized Scope

Implement the dependency-free internal mapping from ordered observed Part
coverage to compact `CollectionVirtualTime`. The work uses exact in-memory
coordinates only. It preserves `PartRelativeTime` and its authoritative
`RawPtsTime`, creates no source-intake or CLI API, and never invokes media
tools.

## Intended Changes

| Path | Change | Boundary |
| --- | --- | --- |
| `src/video_content_pipeline/timeline.py` | Add immutable ordered-Part assembly and collection-time mapping. | Standard library only; coverage intervals and retained source coordinates are the only inputs. |
| `tests/unit/test_collection_timeline.py` | Add failing-first unit cases for compact Part concatenation, signed PTS provenance, and hard boundaries. | In-memory exact values only; no fixture or subprocess use. |
| `.scratch/.../issues/05-assemble-compact-collection-virtual-time.md` | Record the completed ticket. | Local tracker only. |
| `docs/PHASE_02_INVENTORY.json` | Record command and file evidence. | Audit record only. |

## Approved Commands

Every Python command activates `.venv`, sets project-local runtime variables,
and runs `scripts/require-project-venv.sh` first.

```sh
pytest tests/unit/test_collection_timeline.py
ruff check src/video_content_pipeline/timeline.py tests/unit/test_collection_timeline.py
ruff format --check src/video_content_pipeline/timeline.py tests/unit/test_collection_timeline.py
mypy src
pytest
```

## Resource And Retention Boundary

- Expected memory is below 256 MiB and additional disk use is below 10 MiB in
  existing project-local test and tool caches.
- No package action, download, FFmpeg, FFprobe, fixture generation, model,
  paid API, user-media access, or public CLI action is authorized.
- Source, tests, tracker, plan, and inventory records are retained. No cache,
  temporary data, fixture, archive, or failed output will be deleted.
