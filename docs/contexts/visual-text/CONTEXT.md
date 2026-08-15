# Visual-Text Context

This Context owns optional on-screen text evidence: deterministic page-change
detection, adaptive frame sampling, Part-local page indices, and OCR evidence
items. It directly depends on source-planning evidence; audio-analysis is an
optional informing context (used only by embedded-media suspicion). It
produces evidence and never cross-modal facts; fact upgrades belong to
text-analysis. Operational mechanics and exact thresholds remain in the linked
specifications and ADRs.

Relevant global decisions include
[ADR 0001](../../adr/0001-use-existing-ffmpeg-and-ffprobe.md),
[ADR 0036](../../adr/0036-keep-phase-5-model-capabilities-provider-neutral.md),
[ADR 0037](../../adr/0037-verify-phase-5-with-controlled-offline-adapters.md),
[ADR 0042](../../adr/0042-use-context-map-and-domain-owned-glossaries.md),
[ADR 0047](../../adr/0047-introduce-a-visual-text-context-with-deterministic-detection-and-ocr-only-model-capability.md),
[ADR 0048](../../adr/0048-keep-visual-page-identity-part-local.md), and
[ADR 0049](../../adr/0049-separate-visual-evidence-classification-from-fact-upgrade.md).

## Language

### Capabilities and eligibility

**Visual-text capability contract**:
The provider-neutral single OCR capability (`ocr_primary`); detection,
sampling, and classification are deterministic and never model capabilities.
_Avoid_: VLM capability slot

**Controlled offline OCR adapter**:
The fixed substitute capability used to verify visual-text contracts without
claiming real OCR quality.
_Avoid_: synthetic OCR accuracy qualification

**Model-acquisition-required visual-text result**:
The recorded outcome when no eligible OCR capability is locally available.
_Avoid_: implicit download

### Detection and sampling

**Deterministic page-change detection**:
Page-change detection from pinned-toolchain frame extraction plus versioned
rules; the same input and rule version always select the same changes.
_Avoid_: model-assisted detection

**Versioned frame-sampling rules**:
The versioned deterministic rules that adaptively select frames from
stability, text-value proxy metrics, and page changes.
_Avoid_: tuned-in-place sampling

**Text-value proxy metric**:
A deterministic signal (such as edge density or region-scoped frame
difference) approximating text-region change without recognizing text.
_Avoid_: detection-stage OCR

### Pages

**Visual page**:
A stable on-screen text state within one Part, identified by a deterministic
fingerprint.
_Avoid_: scene

**Part-local visual page identity**:
The rule that a `visual_page_id` is scoped to exactly one Part; cross-Part
correlation belongs to consumers.
_Avoid_: collection-global page ID

**Page appearance record**:
The record of a Visual page's first appearance and each reappearance with
exact times.
_Avoid_: single-timestamp page

### Evidence

**OCR evidence item**:
An OCR result carrying Part, PTS, `visual_page_id`, and confidence; it is
candidate evidence and never fact authority.
_Avoid_: uncited visual fact

**Versioned OCR-item classification rules**:
The versioned deterministic rules classifying OCR items as page text, speaker
supplement, or background UI.
_Avoid_: model-judged category

**Excluded visual item**:
An OCR item matched to platform noise (danmaku, high-speed chat, unrelated
watermarks, logos, follow/gift prompts, repeated platform shell); it is
retained in the workspace and marked non-evidence.
_Avoid_: silently dropped item

**Classification-uncertain visual item**:
An OCR item whose classification confidence is too low; it is marked
`classification_uncertain`, never forced into a category.
_Avoid_: forced categorization

**Suspected embedded-media interval**:
A low-confidence marker for a possible embedded video interval; provenance
states whether its basis is picture-plus-audio or picture-only, and it is
never reported as a confirmed fact.
_Avoid_: confirmed embedded media

### Frames

**Retained frame inventory**:
The rule that every extracted frame — including frames never selected for
OCR — is retained in the workspace inventory; deletion requires explicit user
cleanup authorization.
_Avoid_: pipeline-internal frame discard

**Unpublished internal frame**:
The rule that frames are internal evidence only and never appear in formal
outputs.
_Avoid_: published screenshot

### Execution boundaries

**Explicit visual-text command boundary**:
The sole start boundary for a visual-text attempt; scope must be explicit
(whole collection, Parts, or ranges), and an unscoped invocation is an error.
_Avoid_: implicit whole-collection sampling

**OCR resource confirmation pause**:
The pause after detection and before OCR that presents selected frame counts
and resource estimates and requires an explicit decision.
_Avoid_: automatic OCR execution

**Visual-text resource-envelope pause**:
A domain state in which a planned visual-text attempt exceeds its approved
resource envelope.
_Avoid_: silent candidate or batch change

**Immutable visual-text workspace**:
The immutable evidence set associated with one visual-text attempt.
_Avoid_: mutable frame folder

**Serialized OCR execution**:
A sequencing relationship in which OCR completes its evidence record and
release before another heavy model loads.
_Avoid_: concurrent model load

**OCR-not-requested record**:
The `ocr=not_requested` record for runs where visual-text was never enabled:
no frames, no detection, no visual facts.
_Avoid_: implied visual coverage
