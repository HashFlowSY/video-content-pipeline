#!/opt/homebrew/bin/bash
# Generates only the approved, project-owned Phase 2 fixture corpus once.
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work_root="$project_root/tmp/phase-02-fixture-generation-20260809-rerun-03"
fixtures_root="$project_root/tests/fixtures"
ffmpeg="/opt/homebrew/bin/ffmpeg"
ffprobe="/opt/homebrew/bin/ffprobe"

readonly project_root work_root fixtures_root ffmpeg ffprobe

declare -a retained_paths=(
  "$fixtures_root/recipes/phase-02-fixtures-v1.json"
  "$fixtures_root/media/phase-02-offset-av-aac.mkv"
  "$fixtures_root/media/phase-02-gap-video.mkv"
  "$fixtures_root/media/phase-02-aac-priming.m4a"
  "$fixtures_root/subtitles/phase-02-rolling.srt"
  "$fixtures_root/subtitles/phase-02-out-of-range.srt"
  "$fixtures_root/subtitles/phase-02-roundtrip.vtt"
  "$fixtures_root/evidence/ffmpeg-version.txt"
  "$fixtures_root/evidence/ffprobe-version.txt"
  "$fixtures_root/evidence/phase-02-offset-av-aac.ffprobe.json"
  "$fixtures_root/evidence/phase-02-gap-video.ffprobe.json"
  "$fixtures_root/evidence/phase-02-aac-priming.ffprobe.json"
  "$fixtures_root/phase-02-manifest.json"
)

require_absent() {
  local path="$1"
  if [[ -e "$path" || -L "$path" ]]; then
    printf 'Refusing to overwrite existing path: %s\n' "$path" >&2
    exit 1
  fi
}

byte_count() {
  wc -c < "$1" | tr -d '[:space:]'
}

sha256() {
  shasum -a 256 "$1" | awk '{print $1}'
}

json_string() {
  jq -Rs .
}

write_entry() {
  local relative_path="$1"
  local retention_class="$2"
  local fixture_id="$3"
  local source_path="$4"
  local extra_fields="${5:-}"
  local digest
  digest="$(sha256 "$source_path")"
  printf '    {"path": %s, "byte_count": %s, "sha256": %s, ' \
    "$(printf '%s' "$relative_path" | json_string)" \
    "$(byte_count "$source_path")" \
    "$(printf '%s' "$digest" | json_string)"
  printf '"retention_class": %s, "fixture_id": %s' \
    "$(printf '%s' "$retention_class" | json_string)" \
    "$(printf '%s' "$fixture_id" | json_string)"
  if [[ -n "$extra_fields" ]]; then
    extra_fields="${extra_fields#\{}"
    extra_fields="${extra_fields%\}}"
    printf ', %s' "$extra_fields"
  fi
  printf '}'
}

media_provenance() {
  local fixture_id="$1"
  jq -c \
    --rawfile version "$work_root/evidence/ffmpeg-version.txt" \
    --arg executable "$ffmpeg" \
    --arg work_prefix "$work_root/" \
    --arg fixture_id "$fixture_id" \
    '(.fixtures[] | select(.id == $fixture_id)) as $fixture
      | {
          ffmpeg_version: $version,
          command_arguments:
            ([$executable] + $fixture.ffmpeg_arguments[0:-1]
             + [($work_prefix + $fixture.output)])
        }' \
    "$recipe"
}

probe_provenance() {
  local fixture_id="$1"
  jq -c \
    --rawfile version "$work_root/evidence/ffprobe-version.txt" \
    --arg executable "$ffprobe" \
    --arg work_prefix "$work_root/" \
    --arg fixture_id "$fixture_id" \
    '(.fixtures[] | select(.id == $fixture_id)) as $fixture
      | {
          ffprobe_version: $version,
          command_arguments:
            ([$executable] + .ffprobe_arguments
             + [($work_prefix + $fixture.output)])
        }' \
    "$recipe"
}

for retained_path in "${retained_paths[@]}"; do
  require_absent "$retained_path"
done
require_absent "$work_root"

if [[ ! -x "$ffmpeg" || ! -x "$ffprobe" ]] || ! command -v jq > /dev/null; then
  printf 'Approved FFmpeg/FFprobe binaries or required JSON tooling are unavailable.\n' >&2
  exit 1
fi

ffmpeg_version="$($ffmpeg -hide_banner -version)"
ffprobe_version="$($ffprobe -hide_banner -version)"
if [[ "${ffmpeg_version%%$'\n'*}" != ffmpeg\ version\ 8.1.2* || \
  "${ffprobe_version%%$'\n'*}" != ffprobe\ version\ 8.1.2* ]]; then
  printf 'Approved FFmpeg/FFprobe version 8.1.2 is required.\n' >&2
  exit 1
fi

mkdir -p "$work_root/recipes" "$work_root/media" "$work_root/subtitles" "$work_root/evidence"
exec >"$work_root/generation.log" 2>&1
printf 'Generating approved Phase 2 synthetic fixture corpus in %s\n' "$work_root"

printf '%s\n' "$ffmpeg_version" > "$work_root/evidence/ffmpeg-version.txt"
printf '%s\n' "$ffprobe_version" > "$work_root/evidence/ffprobe-version.txt"

recipe="$work_root/recipes/phase-02-fixtures-v1.json"
cat > "$recipe" <<'EOF'
{
  "schema_version": 1,
  "toolchain": {
    "ffmpeg": "/opt/homebrew/bin/ffmpeg",
    "ffprobe": "/opt/homebrew/bin/ffprobe",
    "required_version": "8.1.2"
  },
  "fixtures": [
    {
      "id": "offset-av-aac",
      "kind": "media",
      "output": "media/phase-02-offset-av-aac.mkv",
      "maximum_bytes": 6291456,
      "ffmpeg_arguments": ["-hide_banner", "-nostdin", "-n", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10:duration=4", "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=4", "-filter_complex", "[0:v]setpts=PTS-STARTPTS[v];[1:a]asetpts=PTS-STARTPTS-0.021/TB[a]", "-map", "[v]", "-map", "[a]", "-c:v", "ffv1", "-level:v", "3", "-c:a", "aac", "-b:a", "64k", "-map_metadata", "-1", "-metadata", "title=phase-02-offset-av-aac", "-metadata", "creation_time=1970-01-01T00:00:00Z", "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact", "-avoid_negative_ts", "disabled", "media/phase-02-offset-av-aac.mkv"]
    },
    {
      "id": "gap-video",
      "kind": "media",
      "output": "media/phase-02-gap-video.mkv",
      "maximum_bytes": 4194304,
      "ffmpeg_arguments": ["-hide_banner", "-nostdin", "-n", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10:duration=4", "-vf", "select='between(t,0,0.9)+between(t,3,3.9)',setpts=PTS+10/TB", "-fps_mode", "passthrough", "-c:v", "ffv1", "-level:v", "3", "-map_metadata", "-1", "-metadata", "title=phase-02-gap-video", "-metadata", "creation_time=1970-01-01T00:00:00Z", "-fflags", "+bitexact", "-flags:v", "+bitexact", "-avoid_negative_ts", "disabled", "media/phase-02-gap-video.mkv"]
    },
    {
      "id": "aac-priming",
      "kind": "media",
      "output": "media/phase-02-aac-priming.m4a",
      "maximum_bytes": 2097152,
      "ffmpeg_arguments": ["-hide_banner", "-nostdin", "-n", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2", "-c:a", "aac", "-b:a", "64k", "-map_metadata", "-1", "-metadata", "title=phase-02-aac-priming", "-metadata", "creation_time=1970-01-01T00:00:00Z", "-fflags", "+bitexact", "-flags:a", "+bitexact", "-avoid_negative_ts", "disabled", "media/phase-02-aac-priming.m4a"]
    },
    {
      "id": "rolling-srt",
      "kind": "subtitle",
      "output": "subtitles/phase-02-rolling.srt",
      "maximum_bytes": 65536
    },
    {
      "id": "out-of-range-srt",
      "kind": "subtitle",
      "output": "subtitles/phase-02-out-of-range.srt",
      "maximum_bytes": 65536
    },
    {
      "id": "roundtrip-vtt",
      "kind": "subtitle",
      "output": "subtitles/phase-02-roundtrip.vtt",
      "maximum_bytes": 65536
    }
  ],
  "ffprobe_arguments": ["-v", "error", "-of", "json", "-show_format", "-show_streams", "-show_packets", "-show_frames"]
}
EOF

if ! jq -e . "$recipe" > /dev/null; then
  printf 'Generated fixture recipe is not valid JSON.\n' >&2
  exit 1
fi

cat > "$work_root/subtitles/phase-02-rolling.srt" <<'EOF'
1
00:00:00,000 --> 00:00:01,000
we need

2
00:00:00,900 --> 00:00:02,000
we need proof

3
00:00:02,000 --> 00:00:03,000
repeat

4
00:00:02,000 --> 00:00:03,000
repeat

5
00:00:03,000 --> 00:00:04,000
repeat
EOF

cat > "$work_root/subtitles/phase-02-out-of-range.srt" <<'EOF'
1
00:00:03,900 --> 00:00:04,100
outside observed video coverage
EOF

cat > "$work_root/subtitles/phase-02-roundtrip.vtt" <<'EOF'
WEBVTT

cue-1
00:00:00.000 --> 00:00:00.001 align:start
Line one
Line two

cue-2
00:00:01.125 --> 00:00:02.500
Second cue
EOF

fixture_exists() {
  local fixture_id="$1"
  jq -e --arg fixture_id "$fixture_id" \
    'any(.fixtures[]; .id == $fixture_id and .kind == "media")' "$recipe" > /dev/null
}

run_media_fixture() {
  local fixture_id="$1"
  local output
  local last_index
  local -a command_arguments

  fixture_exists "$fixture_id"
  jq -e --arg fixture_id "$fixture_id" \
    '(.fixtures[] | select(.id == $fixture_id) | .ffmpeg_arguments) as $arguments
      | ($arguments | type == "array" and length > 0)' "$recipe" > /dev/null
  command_arguments=()
  while IFS= read -r command_argument; do
    command_arguments+=("$command_argument")
  done < <(
    jq -er --arg fixture_id "$fixture_id" \
      '.fixtures[] | select(.id == $fixture_id) | .ffmpeg_arguments[]' "$recipe"
  )
  output="$(jq -er --arg fixture_id "$fixture_id" \
    '.fixtures[] | select(.id == $fixture_id) | .output' "$recipe")"
  last_index=$(( ${#command_arguments[@]} - 1 ))
  [[ "${command_arguments[$last_index]}" == "$output" ]] || {
    printf 'Fixture output does not match its final FFmpeg argument: %s\n' "$fixture_id" >&2
    exit 1
  }
  command_arguments[$last_index]="$work_root/$output"
  printf 'Running approved %s FFmpeg command.\n' "$fixture_id"
  "$ffmpeg" "${command_arguments[@]}"
}

run_probe_document() {
  local fixture_id="$1"
  local destination="$2"
  local output
  local -a command_arguments

  fixture_exists "$fixture_id"
  jq -e '(.ffprobe_arguments | type == "array" and length > 0)' "$recipe" > /dev/null
  command_arguments=()
  while IFS= read -r command_argument; do
    command_arguments+=("$command_argument")
  done < <(jq -er '.ffprobe_arguments[]' "$recipe")
  output="$(jq -er --arg fixture_id "$fixture_id" \
    '.fixtures[] | select(.id == $fixture_id) | .output' "$recipe")"
  printf 'Capturing approved %s raw ProbeDocument.\n' "$fixture_id"
  "$ffprobe" "${command_arguments[@]}" "$work_root/$output" > "$destination"
}

run_media_fixture offset-av-aac
run_media_fixture gap-video
run_media_fixture aac-priming

run_probe_document offset-av-aac "$work_root/evidence/phase-02-offset-av-aac.ffprobe.json"
run_probe_document gap-video "$work_root/evidence/phase-02-gap-video.ffprobe.json"
run_probe_document aac-priming "$work_root/evidence/phase-02-aac-priming.ffprobe.json"

for json_path in "$work_root"/evidence/*.ffprobe.json; do
  [[ -s "$json_path" ]] || { printf 'Empty ProbeDocument: %s\n' "$json_path" >&2; exit 1; }
done

check_limit() {
  local path="$1"
  local maximum="$2"
  local bytes
  bytes="$(byte_count "$path")"
  if (( bytes > maximum )); then
    printf 'Fixture exceeds size limit (%s > %s): %s\n' "$bytes" "$maximum" "$path" >&2
    exit 1
  fi
}

check_limit "$work_root/media/phase-02-offset-av-aac.mkv" 6291456
check_limit "$work_root/media/phase-02-gap-video.mkv" 4194304
check_limit "$work_root/media/phase-02-aac-priming.m4a" 2097152
for small_path in "$work_root"/recipes/* "$work_root"/subtitles/* \
  "$work_root/evidence/ffmpeg-version.txt" "$work_root/evidence/ffprobe-version.txt"; do
  check_limit "$small_path" 65536
done
for probe_document in "$work_root"/evidence/*.ffprobe.json; do
  check_limit "$probe_document" 1048576
done

manifest="$work_root/phase-02-manifest.json"
{
  printf '{\n  "schema_version": 1,\n'
  printf '  "recipe": "recipes/phase-02-fixtures-v1.json",\n'
  printf '  "entries": [\n'
  write_entry "recipes/phase-02-fixtures-v1.json" "recipe" "fixture-corpus" \
    "$work_root/recipes/phase-02-fixtures-v1.json"; printf ',\n'
  write_entry "media/phase-02-offset-av-aac.mkv" "synthetic-media" "offset-av-aac" \
    "$work_root/media/phase-02-offset-av-aac.mkv" "$(media_provenance offset-av-aac)"; printf ',\n'
  write_entry "media/phase-02-gap-video.mkv" "synthetic-media" "gap-video" \
    "$work_root/media/phase-02-gap-video.mkv" "$(media_provenance gap-video)"; printf ',\n'
  write_entry "media/phase-02-aac-priming.m4a" "synthetic-media" "aac-priming" \
    "$work_root/media/phase-02-aac-priming.m4a" "$(media_provenance aac-priming)"; printf ',\n'
  write_entry "subtitles/phase-02-rolling.srt" "literal-subtitle" "rolling-srt" \
    "$work_root/subtitles/phase-02-rolling.srt"; printf ',\n'
  write_entry "subtitles/phase-02-out-of-range.srt" "literal-subtitle" "out-of-range-srt" \
    "$work_root/subtitles/phase-02-out-of-range.srt"; printf ',\n'
  write_entry "subtitles/phase-02-roundtrip.vtt" "literal-subtitle" "roundtrip-vtt" \
    "$work_root/subtitles/phase-02-roundtrip.vtt"; printf ',\n'
  write_entry "evidence/ffmpeg-version.txt" "tool-provenance" "fixture-toolchain" \
    "$work_root/evidence/ffmpeg-version.txt"; printf ',\n'
  write_entry "evidence/ffprobe-version.txt" "tool-provenance" "fixture-toolchain" \
    "$work_root/evidence/ffprobe-version.txt"; printf ',\n'
  write_entry "evidence/phase-02-offset-av-aac.ffprobe.json" "raw-probe-document" "offset-av-aac" \
    "$work_root/evidence/phase-02-offset-av-aac.ffprobe.json" "$(probe_provenance offset-av-aac)"; printf ',\n'
  write_entry "evidence/phase-02-gap-video.ffprobe.json" "raw-probe-document" "gap-video" \
    "$work_root/evidence/phase-02-gap-video.ffprobe.json" "$(probe_provenance gap-video)"; printf ',\n'
  write_entry "evidence/phase-02-aac-priming.ffprobe.json" "raw-probe-document" "aac-priming" \
    "$work_root/evidence/phase-02-aac-priming.ffprobe.json" "$(probe_provenance aac-priming)"; printf '\n'
  printf '  ]\n}\n'
} > "$manifest"

if ! jq -e . "$manifest" > /dev/null; then
  printf 'Generated manifest is not valid JSON.\n' >&2
  exit 1
fi
while IFS=$'\t' read -r relative_path expected_bytes expected_hash; do
  actual_bytes="$(byte_count "$work_root/$relative_path")"
  actual_hash="$(sha256 "$work_root/$relative_path")"
  if [[ "$expected_bytes" != "$actual_bytes" || "$expected_hash" != "$actual_hash" ]]; then
    printf 'Manifest integrity mismatch: %s\n' "$relative_path" >&2
    exit 1
  fi
done < <(jq -r '.entries[] | [.path, .byte_count, .sha256] | @tsv' "$manifest")
check_limit "$manifest" 65536

total_bytes=0
for retained_relative_path in "${retained_paths[@]}"; do
  relative_path="${retained_relative_path#"$fixtures_root/"}"
  total_bytes=$((total_bytes + $(byte_count "$work_root/$relative_path")))
done
if (( total_bytes > 20971520 )); then
  printf 'Fixture corpus exceeds total size limit: %s\n' "$total_bytes" >&2
  exit 1
fi

for retained_path in "${retained_paths[@]}"; do
  require_absent "$retained_path"
done
mkdir -p "$fixtures_root/recipes" "$fixtures_root/media" "$fixtures_root/subtitles" "$fixtures_root/evidence"

declare -a relative_paths=(
  "recipes/phase-02-fixtures-v1.json"
  "media/phase-02-offset-av-aac.mkv"
  "media/phase-02-gap-video.mkv"
  "media/phase-02-aac-priming.m4a"
  "subtitles/phase-02-rolling.srt"
  "subtitles/phase-02-out-of-range.srt"
  "subtitles/phase-02-roundtrip.vtt"
  "evidence/ffmpeg-version.txt"
  "evidence/ffprobe-version.txt"
  "evidence/phase-02-offset-av-aac.ffprobe.json"
  "evidence/phase-02-gap-video.ffprobe.json"
  "evidence/phase-02-aac-priming.ffprobe.json"
  "phase-02-manifest.json"
)

for relative_path in "${relative_paths[@]}"; do
  require_absent "$fixtures_root/$relative_path"
  cp -p "$work_root/$relative_path" "$fixtures_root/$relative_path"
done

printf 'Retained Phase 2 fixture corpus without overwriting existing evidence.\n'
