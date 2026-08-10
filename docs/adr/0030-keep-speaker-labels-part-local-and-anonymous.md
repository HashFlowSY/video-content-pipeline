# Keep speaker labels Part-local and anonymous

Phase 5 diarization identifies speakers only with anonymous labels stable
within a single Part, such as `part-03:speaker-01`. It makes no cross-Part,
cross-run, or real-person identity assertion, because those claims require
separate evidence and introduce privacy and quality risks.

Each SpeakerTurn retains its Part-local label, RawPtsTime half-open interval,
and confidence evidence. Overlapping turns remain independent, overlapping
records; the pipeline does not force one speaker, trim intervals, or merge
them into a serialized dialogue.
Candidates overlapping calibrated `non_speech` or `indeterminate` VAD audio are
retained as `diarization_vad_conflict` but cannot become formal SpeakerTurns;
the pipeline does not trim or shift them to manufacture agreement.

Role candidates such as host, guest, or questioner require cited subtitle text
or explicit user metadata and remain candidates. Voice characteristics and
diarization labels alone cannot establish a role.

## Considered Options

- Part-local anonymous labels: accepted because they support speaker-turn
  structure while matching the evidence actually produced by diarization.
- Cross-Part or cross-run linkage: rejected because it would imply voiceprint
  identity continuity not established in this phase.
- Real-name inference: rejected because a voice alone is not authority to name
  a person.
- Single-speaker overlap resolution: rejected because it discards or invents
  turn boundaries in a common live-discussion case.
- VAD-based turn repair: rejected because conflicting model evidence must remain
  diagnosable rather than be silently reconciled.
- Voice-inferred roles: rejected because social role and identity are not
  established by a diarization output.
