# Authorize media hosts per run from the download plan

(Draft — takes effect with the approval of the Phase 12 specification.)

Media acquisition keeps its fail-closed host guard, but the authorized set
becomes per-run instead of a strict page-host equality. The downloader's
metadata pass resolves the actual media hosts (video platforms serve media
from CDN hosts distinct from the page host — e.g. bilibili pages on
`www.bilibili.com`, media on `*.bilivideo.com`). The media download plan
discloses that resolved host set alongside size, duration, and disk
headroom; the maintainer's confirmation of that plan authorizes exactly
those hosts, for that download only. The acquisition proxy admits
connections to the confirmed set and nothing else; a mid-download
redirect to an undisclosed host is still `host_escalation`, fail-closed.
Nothing persists between downloads: the next download of the same
platform discloses and confirms its hosts again.

## Considered Options

- Per-run disclosed host set: accepted because every network authority
  remains traceable to one confirmed plan artifact, the posture stays
  fail-closed (undisclosed host = hard stop, even mid-download), and the
  cost is a few extra lines read at each download confirmation — a
  ceremony Phase 12 already requires for size and disk.
- Standing per-platform suffix allowlist (e.g. `*.bilivideo.com` in
  config): rejected because it creates permanent network authority that
  outlives any confirmation, silently widens as platforms move CDNs, and
  is in tension with the standing rule that no download happens without
  authorization.
- Keep strict page-host equality: rejected because it fails closed on
  effectively every real video platform, making URL intake permanently
  untestable — the guard would exist only to be bypassed by operator
  local copies.
- Prompting interactively during the download when an unknown host
  appears: rejected because acquisition must be able to run
  non-interactively, and a mid-transfer prompt invites rubber-stamping;
  disclosure belongs at plan time, where the maintainer is already
  deciding.
