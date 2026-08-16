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
**Status:** open
**Labels:** ready-for-agent

- [ ] Seven confirmed download plans retained as records (one per asset),
      each with maintainer confirmation noted
- [ ] Every acquired file SHA-256-verifies against its registry entry (an
      integration test re-hashes from disk)
- [ ] Registry entries carry revision, manifest, sizes, quantization,
      compatible runtime versions, authorization record, verification
      status
- [ ] `models_downloaded: true`, `models_registry_entries` correct;
      `paid_apis_used` still false
- [ ] No download occurred outside a confirmed plan (cache and models
      trees contain only planned files)
- [ ] No model asset is ever tracked by git: `models/*` is ignored with
      only `models/registry.json` kept (a test proves `git check-ignore`
      on a models-tree asset path and that the registry stays tracked)
