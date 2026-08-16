# 04 — Per-model download plans and confirmed acquisition

**What to build:** For each of the seven assets: produce a download plan
(repo id, exact revision/commit SHA, file manifest, total size, license,
target `models/<provider>/<model>/<revision>/`), obtain maintainer
confirmation, execute via the pinned hf CLI (`hf download --revision`) or
pinned release URL (sherpa-onnx GitHub assets; silero tagged repo file),
stage through `cache/model-downloads/`, hash every file, and upgrade the
registry entry to acquired with the full plan §13.2 field set including
the first-download authorization record. Flip `models_downloaded` and
`models_registry_entries` in project state at the first completed
acquisition. If a pinned 8-bit variant does not exist, resolve the
fallback tier inside that model's confirmation — never silently.
RapidOCR's bundled models are recorded from the wheel
(`default_models.yaml` SHA-256s + post-install `RapidOCR().config` dump)
rather than downloaded separately.

**Blocked by:** 01, 03
**Status:** done
**Labels:** ready-for-agent

- [x] Seven confirmed download plans retained as records (one per asset),
      each with maintainer confirmation noted
- [x] Every acquired file SHA-256-verifies against its registry entry (an
      integration test re-hashes from disk)
- [x] Registry entries carry revision, manifest, sizes, quantization,
      compatible runtime versions, authorization record, verification
      status
- [x] `models_downloaded: true`, `models_registry_entries` correct;
      `paid_apis_used` still false
- [x] No download occurred outside a confirmed plan (cache and models
      trees contain only planned files)
- [x] No model asset is ever tracked by git: `models/*` is ignored with
      only `models/registry.json` kept (a test proves `git check-ignore`
      on a models-tree asset path and that the registry stays tracked)

## Completion notes (2026-08-16)

Seven assets acquired (4 HF snapshots via pinned `hf download --revision`;
silero-vad + 2 sherpa-onnx assets via pinned release/tag URLs), staged through
`cache/model-downloads/`, hashed, and placed under
`models/<provider>/<model>/<revision>/`. RapidOCR's three default det/cls/rec
models are recorded from the pinned `rapidocr==3.9.2` wheel (confirmed
wheel-resident against `dist-info/RECORD`), so eight registry entries are
`acquired`. Each entry pins `revision` + a `file_manifest` (per-file
SHA-256/size) + `asset_sha256` (the SHA-256 of its canonical manifest, defined
once in `src/video_content_pipeline/model_acquisition.py` and re-derived from
disk by `tests/integration/test_phase_11_acquired_assets.py`). Maintainer
confirmed all seven individually-presented plans before any bytes moved
(model-download authorization, never reused as media authorization). All four
HF weights matched their hub LFS SHA-256. `models_downloaded` → true,
`models_registry_entries` → 8, `paid_apis_used` stays false. Deliberately NOT
made runtime-eligible (no `resource_estimate`): capability states are unchanged
(`model_ineligible`), leaving runtime wiring to the adapter/prototype tickets.
Full suite 1378 passed; ruff/mypy clean.
