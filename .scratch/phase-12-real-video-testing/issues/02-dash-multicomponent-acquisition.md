# 02 — DASH / multicomponent source acquisition

**What to build:** A URL whose media is served as separate audio and video
components (DASH — what bilibili and most real video platforms return) can
be sized and planned instead of being rejected. The acquisition metadata
pass sums the component sizes of `requested_formats` to produce the total
byte count used for disk-headroom planning; it still fails closed with a
typed reason when any component's size is indeterminable. The
single-file-source path behaves exactly as before.

This retires the failure family recorded as the Phase 11 Wikimedia
deviation for split-stream sources. yt-dlp itself stays pinned (ADR 0019);
this is an acquisition-contract change, not a binary upgrade.

**Blocked by:** None — can start immediately.

**Status:** done

- [x] Canned multicomponent (bilibili-shaped) yt-dlp metadata yields a correct summed byte count and an unblocked plan
- [x] Canned metadata with a missing component size fails closed with a typed reason (no guessing)
- [x] Canned single-file metadata behaves unchanged
- [x] Tests live at the acquisition metadata seam (canned downloader JSON), no live network in the suite
