"""Content-addressed verification for acquired model assets.

Phase 11 acquisition (ticket 04) pins each registry candidate by an
``asset_sha256`` that is the SHA-256 of the candidate's *canonical file
manifest* -- a sorted list of ``{path, size, sha256}`` over every acquired
file. A single digest therefore proves the exact bytes of a whole
multi-file model (HF snapshots, the extracted sherpa-onnx release tree) or a
single ``.onnx`` alike, so a later run can re-derive it from disk and prove it
is running the audited bytes.

The acquisition step and the disk-verification test both call these helpers, so
the digest written into the registry is, by construction, the digest the test
recomputes. No network, no model load -- only local file hashing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

_READ_CHUNK = 1024 * 1024

# ``local_path`` sentinel for a candidate whose files ship inside a pinned wheel
# rather than under the ``models/`` tree (RapidOCR); verification resolves it to
# the installed package's model directory.
BUNDLED_IN_WHEEL = "bundled-in-wheel"


def file_sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of a file, read in bounded chunks."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_READ_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_file_manifest(asset_root: Path) -> list[dict[str, object]]:
    """Hash every file under ``asset_root`` into a canonical, sorted manifest.

    Paths are POSIX-relative to ``asset_root`` and the list is sorted by path,
    so the manifest -- and therefore its digest -- is independent of filesystem
    walk order.
    """

    entries: list[dict[str, object]] = []
    for path in sorted(p for p in asset_root.rglob("*") if p.is_file()):
        entries.append(
            {
                "path": path.relative_to(asset_root).as_posix(),
                "size": path.stat().st_size,
                "sha256": file_sha256(path),
            }
        )
    return entries


def manifest_asset_sha256(manifest: Sequence[Mapping[str, object]]) -> str:
    """Digest a file manifest into the single ``asset_sha256`` recorded per entry.

    The manifest is re-sorted by path and reduced to ``(path, size, sha256)``
    triples before serialization, so equal file sets always hash equally
    regardless of key order or extra annotations.
    """

    canonical = [
        {"path": entry["path"], "size": entry["size"], "sha256": entry["sha256"]}
        for entry in sorted(manifest, key=lambda item: str(item["path"]))
    ]
    payload = json.dumps(canonical, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AssetVerificationError(ValueError):
    """An acquired asset on disk does not match its pinned registry manifest."""


def verify_acquired_asset(
    manifest: Sequence[Mapping[str, object]],
    asset_sha256: str,
    asset_root: Path,
) -> None:
    """Re-hash ``asset_root`` from disk and assert it matches the pinned manifest.

    Raises :class:`AssetVerificationError` on the first mismatch -- a missing
    file, a changed size, a changed hash, or an extra/absent file that shifts
    the manifest digest. Never reaches the network and never loads a model.
    """

    on_disk = build_file_manifest(asset_root)
    expected_by_path = {str(entry["path"]): entry for entry in manifest}
    disk_by_path = {str(entry["path"]): entry for entry in on_disk}

    missing = sorted(set(expected_by_path) - set(disk_by_path))
    if missing:
        raise AssetVerificationError(f"Acquired asset is missing pinned files: {missing}")
    extra = sorted(set(disk_by_path) - set(expected_by_path))
    if extra:
        raise AssetVerificationError(f"Acquired asset tree carries unpinned files: {extra}")
    for path, expected in expected_by_path.items():
        found = disk_by_path[path]
        if found["sha256"] != expected["sha256"] or found["size"] != expected["size"]:
            raise AssetVerificationError(f"Acquired file does not match its pinned hash: {path}")

    recomputed = manifest_asset_sha256(on_disk)
    if recomputed != asset_sha256:
        raise AssetVerificationError("Acquired manifest digest does not match asset_sha256.")
