# Separate visual evidence classification from fact upgrade

Visual-text classifies OCR items deterministically (page text, speaker
supplement, background UI, excluded platform noise) and retains everything;
it never decides facts. The one permitted promotion — a background-UI comment
becoming formal evidence because the host explicitly selected or read it — is
a cross-modal fact decision and is owned by text-analysis, which already holds
both cue and OCR evidence during affected-Part re-analysis and records page
time and selection basis for each upgrade.

## Considered Options

- Classification in visual-text, upgrade in text-analysis: accepted because
  "host read this comment" requires comparing OCR text with subtitle or
  transcript text, which is the same cross-modal fact-citation responsibility
  as ADR 0040's cue-level evidence rule; visual-text stays single-modal and
  its dependency set (ADR 0047) stays intact.
- Upgrade inside visual-text with an optional subtitles dependency: rejected
  because it turns an evidence producer into a fact adjudicator and adds a
  dependency used by exactly one rule.
- No upgrade path at all: rejected because the phase plan explicitly allows
  host-selected or host-read comments as formal evidence; dropping it would
  silently narrow the plan.
