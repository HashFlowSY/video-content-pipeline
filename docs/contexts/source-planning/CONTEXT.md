# Source Planning Context

This Context owns explicit source authorization, Parts and collections,
inspection, resource evidence, and immutable planning. It depends on the exact
media evidence vocabulary in [Media Foundation](../media-foundation/CONTEXT.md).
Operational mechanics and exact thresholds remain in the linked specifications
and ADRs.

Relevant global decisions include [ADR 0015](../../adr/0015-require-explicit-url-access-mode.md),
[ADR 0016](../../adr/0016-snapshot-local-sources-with-double-hash.md), and
[ADR 0024](../../adr/0024-revalidate-evidence-before-plan-confirmation.md).

## Language

**Phase 3 source-intake and planning boundary**:
The authorized boundary for explicit local or public source intake,
inspection, estimation, and immutable planning.
_Avoid_: processing run

**Source access authorization**:
Per-plan permission to read one explicitly supplied local path or public URL.
_Avoid_: ambient network permission

**Part**:
One ordered source unit in a MediaCollection, with its own immutable source
identity, evidence, and time span.
_Avoid_: playlist item

**RunPlan**:
An immutable executable declaration of an approved source scope, planned work,
resource envelope, and unavailable prerequisites.
_Avoid_: mutable job configuration

**PlanReport**:
The immutable diagnostic outcome of one planning attempt, whether executable or
blocked.
_Avoid_: failed plan

**Plan confirmation**:
The user's explicit approval of one still-valid PlanReport that creates a
RunPlan.
_Avoid_: implicit approval

**Report revalidation**:
The determination that a planning report still represents current evidence.
_Avoid_: warning-only confirmation

**Decode preflight confirmation**:
The user's approval to perform the full decode validation after its estimate is
shown.
_Avoid_: automatic full decode

**Phase-bounded estimate**:
An evidence-backed three-point estimate limited to work measurable in the
current phase.
_Avoid_: speculative total

**Decode throughput profile**:
A versioned mapping from probe evidence to a low-confidence decode estimate
until matching measured history exists.
_Avoid_: hidden benchmark

**Disk headroom**:
The reserved free-space margin required before source acquisition.
_Avoid_: exact-fit check

**URL access mode**:
The explicit filtered or direct authorization recorded before public-URL access.
_Avoid_: automatic downloader mode

**Host escalation**:
An attempted redirect, media host, or transport change outside authorized host
scope that requires a new decision.
_Avoid_: trusted redirect

**Insecure HTTP authorization**:
The separate authorization required for an initial plaintext HTTP source.
_Avoid_: implicit HTTP fallback

**Redacted source provenance**:
Persistent scheme, host, and path provenance for a URL without query or
fragment secrets.
_Avoid_: replayable signed URL

**Pinned external tool**:
An external prerequisite whose identity is fixed for the evidence it produces.
_Avoid_: ambient tool

**Inspection toolchain**:
The prerequisite used to inspect a SourceArtifact and produce inspection
evidence.
_Avoid_: unbounded probe

**Full decode validation**:
Complete linear decoding of every usable audio and video stream without writing
derived media.
_Avoid_: sample decode

**Decode validation toolchain**:
The prerequisite used to validate complete decodability of a SourceArtifact.
_Avoid_: media converter

**SourceArtifact**:
An immutable, content-addressed project copy of one authorized source whose
original and copied bytes match.
_Avoid_: live source path

**Local source candidate**:
An explicitly supplied regular local file eligible for snapshotting.
_Avoid_: discovered media

**Media-qualified source**:
A SourceArtifact whose strict probe projection proves usable media and
establishable stream coverage.
_Avoid_: extension-approved file

**MediaCollection**:
The intentionally ordered set of Parts for one logical content item.
_Avoid_: auto-discovered playlist

**Manual collection session**:
The user-directed assembly of a MediaCollection in presentation order.
_Avoid_: playlist discovery

**Collection closure**:
The user's signal that freezes a manual collection for validation and
acquisition.
_Avoid_: incremental download

**Duplicate Part**:
A collection entry whose acquired SourceArtifact duplicates another Part by
content identity.
_Avoid_: duplicate URL only
