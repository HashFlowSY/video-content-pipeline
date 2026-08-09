#!/opt/homebrew/bin/bash
# Archives and repairs the Ticket 10 manifest without changing fixture bytes.
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixtures_root="$project_root/tests/fixtures"
manifest="$fixtures_root/phase-02-manifest.json"
archive="$fixtures_root/evidence/phase-02-manifest-rerun-03-invalid.json"
repair_root="$project_root/tmp/phase-02-fixture-manifest-repair-20260809-rerun-01"

readonly project_root fixtures_root manifest archive repair_root

byte_count() {
  wc -c < "$1" | tr -d '[:space:]'
}

sha256() {
  shasum -a 256 "$1" | awk '{print $1}'
}

if [[ ! -f "$manifest" || -e "$repair_root" ]]; then
  printf 'Expected repair preconditions are not satisfied.\n' >&2
  exit 1
fi

if ! jq -e '(.entries | length == 12) and all(.entries[]; .sha256 | endswith("\n"))' \
  "$manifest" > /dev/null; then
  printf 'Refusing to repair a manifest without the recorded newline defect.\n' >&2
  exit 1
fi

mkdir -p "$repair_root"
exec > "$repair_root/repair.log" 2>&1
if [[ -e "$archive" ]]; then
  cmp -s "$manifest" "$archive" || {
    printf 'Existing invalid-manifest archive does not match canonical source.\n' >&2
    exit 1
  }
  printf 'Reusing retained invalid Ticket 10 manifest archive at %s\n' "$archive"
else
  printf 'Archiving invalid Ticket 10 manifest at %s\n' "$archive"
  cp -p "$manifest" "$archive"
fi

corrected_manifest="$repair_root/phase-02-manifest.json"
cp -p "$archive" "$corrected_manifest"

while IFS= read -r relative_path; do
  fixture_path="$fixtures_root/$relative_path"
  [[ -f "$fixture_path" ]] || {
    printf 'Missing retained fixture: %s\n' "$fixture_path" >&2
    exit 1
  }

  bytes="$(byte_count "$fixture_path")"
  digest="$(sha256 "$fixture_path")"
  candidate="$repair_root/phase-02-manifest.next.json"
  jq --arg path "$relative_path" --arg sha256 "$digest" --argjson byte_count "$bytes" \
    '(.entries[] | select(.path == $path)) |= (.sha256 = $sha256 | .byte_count = $byte_count)' \
    "$corrected_manifest" > "$candidate"
  jq -e '(.entries | length == 12)' "$candidate" > /dev/null
  mv "$candidate" "$corrected_manifest"
done < <(jq -r '.entries[].path' "$archive")

jq -e '(.entries | length == 12) and all(.entries[]; .sha256 | test("^[0-9a-f]{64}$"))' \
  "$corrected_manifest" > /dev/null
while IFS=$'\t' read -r relative_path expected_bytes expected_hash; do
  fixture_path="$fixtures_root/$relative_path"
  [[ "$(byte_count "$fixture_path")" == "$expected_bytes" ]] || exit 1
  [[ "$(sha256 "$fixture_path")" == "$expected_hash" ]] || exit 1
done < <(jq -r '.entries[] | [.path, .byte_count, .sha256] | @tsv' "$corrected_manifest")

candidate="$repair_root/phase-02-manifest.candidate.json"
cp -p "$corrected_manifest" "$candidate"
mv "$candidate" "$manifest"
cmp -s "$corrected_manifest" "$manifest"
printf 'Replaced the canonical manifest after archiving the invalid source.\n'
