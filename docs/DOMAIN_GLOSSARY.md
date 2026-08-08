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
| Phase 2 | The authorized deterministic media and timeline prototype stage. It uses only project-local synthetic fixtures until a later phase is authorized. |
| Synthetic media fixture | A retained, hash-pinned project-owned audio or video artifact generated deterministically from a versioned fixture recipe; it is never user-supplied source media. |
| Fixture recipe | A versioned declarative description of how FFmpeg creates one synthetic media fixture and its expected probe evidence. |
| Dependency-free Phase 2 core | The Phase 2 implementation boundary that uses only Python's standard library and already locked test tooling, with no new runtime packages. |
| Phase 2 library boundary | The rule that Phase 2 exposes no user-media CLI or source intake; its core is invoked only by library APIs and deterministic fixture work. |
| Fixture toolchain | The approved FFmpeg and FFprobe binaries used only to generate and probe Phase 2 synthetic media fixtures. |
| RawPtsTime | The exact signed source coordinate formed by a stream's raw PTS and time base. |
| PartRelativeTime | The exact non-negative coordinate formed by subtracting a Part's coverage start from RawPtsTime; it is used for per-Part subtitle export. |
| CollectionVirtualTime | A contiguous collection-facing coordinate system that shifts PartRelativeTime by preceding Part coverage while preserving RawPtsTime as the authority. |
| Atomic subtitle track | A subtitle candidate accepted only as a whole after every cue parses and validates; any failure makes the track unavailable rather than partially recovered. |
| RawCue | An immutable parsed subtitle record that retains original text, timing, and source coordinates. |
| NormalizedCue | An immutable, losslessly normalized representation of a RawCue that retains every token. |
| PresentationCue | An immutable display representation derived from a NormalizedCue; it may omit proven rolling-display tokens only with source-token provenance. |
| Monotonic cue order | The stable ordering of cues by `(start, end, source_ordinal)`; it permits overlapping intervals and never implies non-overlap. |
| Proven rolling overlap | An exact normalized token overlap between stable-order adjacent cues in one Part and subtitle track, with a strict textual extension and overlapping or contiguous intervals. |
| Raw PTS | The signed integer presentation timestamp reported by a media stream; negative values remain valid source evidence. |
| Serialization envelope | The millisecond SRT/VTT interval produced by flooring an exact start and ceiling an exact end; its sub-millisecond outward extension does not replace the authoritative exact range. |
| DecodedInterval | An observed, decodable stream interval with exact start and end boundaries. |
| StreamCoverage | The outer envelope of DecodedIntervals plus separately recorded internal gaps; it is indeterminate when needed boundaries are unknown. |
| ProbeDocument | The immutable raw JSON emitted by FFprobe and retained as media-inspection evidence. |
| ProbeProjection | The typed projection of a ProbeDocument used for decisions; unknown fields remain only in the raw document. |
