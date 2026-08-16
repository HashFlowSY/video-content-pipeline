# 01 — Land the plan amendment, registry candidates, and tool re-probe

**What to build:** Commit the 2026-08-16 plan amendment (new 阶段 11
inserted, real-video testing renumbered to 阶段 12, envelope 24→12 GiB in
§2.2/§15.1). Complete `models/registry.json` candidates to the plan §13.2
field set (metadata only — no downloads): add the two diarization assets
(`sherpa-onnx-pyannote-segmentation-3-0`, MIT;
`3dspeaker-campplus-zh-en-advanced`, Apache-2.0), the `text_semantics`
candidate (`Qwen3-4B-Instruct-2507-8bit`), and the vendored silero-vad
asset; record RapidOCR's 2026-08-16 license/source approval
(`license_approved: true`, official source approved). Re-probe yt-dlp
2026.07.04 into `config/tools.json` (sha256, resolved path, version
identity — ffmpeg re-probe precedent). Update living test docstrings that
call the five real-video branches "Phase 11" to "Phase 12" (historical
inventories/reports untouched by policy).

**Blocked by:** —
**Status:** done
**Labels:** ready-for-agent

- [x] Plan amendment committed; only the four amended spots differ from
      the pre-amendment plan text (landed with the spec commit `6099f9d`:
      §2.2/§15.1 envelope 24→12 GiB, new 阶段 11 inserted, 阶段 12 renumber)
- [x] Every registry candidate carries capability, source, license,
      license approval state, and (for unacquired assets) the fields that
      keep it ineligible until acquisition
- [x] Registry schema/eligibility tests updated for the new candidates;
      diarization capability no longer reports an empty candidate set
      (still `model_acquisition_required`)
- [x] yt-dlp entry in `config/tools.json` carries binary_sha256,
      resolved_path, version_identity, probed_at for this machine
- [x] No living code/comment refers to real-video testing as Phase 11;
      historical docs unchanged
- [x] Full suite green (1340 passed); no download of any kind occurred
