#!/bin/zsh
# Regenerates only the Phase 1 path manifest and machine inventory in-project.

set -eu
setopt pipe_fail

script_dir=${0:A:h}
project_root=${script_dir:h}
manifest_path=${project_root}/docs/PHASE_01_FILE_MANIFEST.tsv
inventory_path=${project_root}/docs/PHASE_01_INVENTORY.json
tmp_dir=${project_root}/tmp
generated_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
stage_token=$(date -u '+%Y%m%dT%H%M%SZ')
manifest_stage=${tmp_dir}/phase-01-file-manifest.next-${stage_token}.tsv
inventory_stage=${tmp_dir}/phase-01-inventory.next-${stage_token}.json

export VCP_PROJECT_ROOT=${project_root}
export TMPDIR=${tmp_dir}

typeset -A path_sizes
typeset -A file_hashes

for required_command in find jq readlink rg sed shasum stat; do
  if ! command -v "${required_command}" >/dev/null 2>&1; then
    print -u2 -- "Missing required command: ${required_command}"
    exit 69
  fi
done

if [[ -e ${manifest_stage} || -e ${inventory_stage} ]]; then
  print -u2 -- "Refusing to overwrite retained audit staging artifact in ${tmp_dir}."
  exit 70
fi

classify_path() {
  local relative_path=$1

  record_action=created
  record_purpose='Phase 1 source, policy, configuration, or audit artifact'
  record_used_by='project_governance'
  record_rebuildable=true
  record_deletion_class=keep_for_audit
  record_deletion_consequence='Phase 1 reproducibility or audit evidence would be lost'

  case ${relative_path} in
    docs/PHASED_EXECUTION_PLAN.md)
      record_action=read
      record_purpose='Locked Phase 1 execution source'
      record_used_by='planning,phase_specification'
      record_rebuildable=false
      record_deletion_consequence='Locked requirements evidence would be lost'
      ;;
    research/qwen3-vl-8b-capability-assessment.md)
      record_action=read
      record_purpose='Pre-existing research preservation verification'
      record_used_by='preservation_audit'
      record_rebuildable=false
      record_deletion_consequence='Existing research would be lost'
      ;;
    */__pycache__|*/__pycache__/*)
      record_purpose='Project-local Python bytecode cache'
      record_used_by='Python_imports'
      record_deletion_class=safe_to_delete
      record_deletion_consequence='Python regenerates bytecode on import'
      ;;
    .git|.git/*)
      record_purpose='Local Git repository metadata'
      record_used_by='Git'
      record_rebuildable=true
      record_deletion_consequence='Local repository audit state would be reset'
      ;;
    tools|tools/uv|tools/uv/*)
      record_action=downloaded
      record_purpose='Project-local uv distribution'
      record_used_by='uv,runtime_management,dependency_locking'
      record_deletion_class=rebuildable
      record_deletion_consequence='Re-download and checksum verification are required before uv commands work'
      ;;
    runtime/python/*__pycache__*|runtime/python/*__pycache__/*)
      record_action=created
      record_purpose='Project-local Python runtime bytecode cache'
      record_used_by='Python_imports'
      record_deletion_class=safe_to_delete
      record_deletion_consequence='Python regenerates bytecode on import'
      ;;
    runtime|runtime/python)
      record_action=downloaded
      record_purpose='Managed CPython 3.12.13 runtime directory'
      record_used_by='project_venv'
      record_deletion_class=rebuildable
      record_deletion_consequence='The managed runtime must be reinstalled before the virtual environment can run'
      ;;
    runtime/python/*)
      record_action=downloaded
      record_purpose='Managed CPython 3.12.13 runtime'
      record_used_by='project_venv'
      record_deletion_class=rebuildable
      record_deletion_consequence='The managed runtime must be reinstalled before the virtual environment can run'
      ;;
    .venv/*__pycache__*|.venv/*__pycache__/*)
      record_action=created
      record_purpose='Project virtual-environment bytecode cache'
      record_used_by='Python_imports'
      record_deletion_class=safe_to_delete
      record_deletion_consequence='Python regenerates bytecode on import'
      ;;
    .venv|.venv/*)
      record_action=created
      record_purpose='Sole project Python virtual environment'
      record_used_by='tests,lint,type_checking,CLI'
      record_deletion_class=rebuildable
      record_deletion_consequence='Locked packages and the project CLI must be synchronized again'
      ;;
    cache|cache/uv|cache/uv/*)
      record_action=downloaded
      record_purpose='Project-local uv cache artifact'
      record_used_by='uv,offline_sync'
      record_deletion_class=rebuildable
      record_deletion_consequence='Required artifacts must be fetched again and offline sync will not work until then'
      ;;
    cache/python|cache/python/*)
      record_purpose='Reserved project-local Python package cache'
      record_used_by='PIP_CACHE_DIR'
      record_deletion_class=safe_to_delete
      record_deletion_consequence='The empty cache can be recreated when needed'
      ;;
    .mypy_cache|.mypy_cache/*)
      record_purpose='Mypy incremental cache'
      record_used_by='mypy'
      record_deletion_class=safe_to_delete
      record_deletion_consequence='The next type check is slower while cache data is rebuilt'
      ;;
    .pytest_cache|.pytest_cache/*)
      record_purpose='Pytest cache'
      record_used_by='pytest'
      record_deletion_class=safe_to_delete
      record_deletion_consequence='Pytest recreates cache data'
      ;;
    .ruff_cache|.ruff_cache/*)
      record_purpose='Ruff cache'
      record_used_by='ruff'
      record_deletion_class=safe_to_delete
      record_deletion_consequence='Ruff recreates cache data'
      ;;
    tmp|tmp/*)
      record_purpose='Project-local temporary or staging artifact'
      record_used_by='uv,audit_generation'
      record_deletion_class=safe_to_delete
      record_deletion_consequence='Transient data can be recreated; retain tracked tmp/.gitkeep'
      ;;
    models|models/*)
      record_purpose='Model registry boundary with no model payload'
      record_used_by='future_model_management'
      record_deletion_consequence='Model policy metadata or boundary would be lost'
      ;;
    input|input/*|work|work/*|outputs|outputs/*)
      record_purpose='Reserved project data boundary'
      record_used_by='future_pipeline_stages'
      record_deletion_consequence='The approved project data boundary would be lost'
      ;;
    config/tools.json)
      record_action=modified
      record_purpose='Tool registry with initial and final managed-Python measurements'
      record_used_by='runtime_audit,phase_handoff'
      record_deletion_consequence='Tool provenance and authoritative runtime measurements would be lost'
      ;;
    config|config/*)
      record_purpose='Runtime policy or tool registry configuration'
      record_used_by='environment_gate,runtime_audit'
      record_deletion_consequence='Runtime boundary validation or audit metadata would be lost'
      ;;
    src|src/*)
      record_purpose='Phase 1 package and environment gate implementation'
      record_used_by='CLI,tests'
      record_deletion_consequence='The environment gate implementation would be unavailable'
      ;;
    tests|tests/*)
      record_purpose='Phase 1 environment-gate verification'
      record_used_by='pytest'
      record_deletion_consequence='Phase 1 behavioral verification would be lost'
      ;;
    scripts|scripts/*)
      record_purpose='Project shell boundary or audit generation command'
      record_used_by='developers,CLI,audit'
      record_deletion_consequence='Environment or audit workflow enforcement would be unavailable'
      ;;
    plans|plans/*)
      record_purpose='Phase 1 atomic work plan'
      record_used_by='planning,phase_handoff'
      record_deletion_consequence='Phase 1 work-item evidence would be lost'
      ;;
    docs|docs/*)
      record_purpose='Phase 1 specification, report, inventory, or glossary'
      record_used_by='audit,handoff'
      record_deletion_consequence='Phase 1 documentation or audit evidence would be lost'
      ;;
    AGENTS.md)
      record_action=modified
      record_purpose='Project-local phase and runtime operating rules'
      record_used_by='developers,agents'
      record_deletion_consequence='Project execution boundaries would be lost'
      ;;
    project-state.json)
      record_action=modified
      record_purpose='Machine-readable project phase state'
      record_used_by='project_governance,phase_handoff'
      record_deletion_consequence='Current phase status would be lost'
      ;;
    .gitignore)
      record_purpose='Git ignore policy for local runtime and generated data'
      record_used_by='Git'
      record_deletion_consequence='Local generated artifacts could be staged unintentionally'
      ;;
    .python-version)
      record_purpose='Pinned managed Python minor version'
      record_used_by='uv'
      record_deletion_consequence='Python runtime pin evidence would be lost'
      ;;
    pyproject.toml)
      record_purpose='Fixed project package and development dependency declaration'
      record_used_by='uv,build,tests'
      record_deletion_consequence='Dependency declaration would be lost'
      ;;
    uv.lock)
      record_purpose='Locked Python dependency graph'
      record_used_by='uv,offline_sync'
      record_deletion_consequence='Reproducible dependency resolution would be lost'
      ;;
    README.md)
      record_purpose='Project entry-point documentation'
      record_used_by='developers'
      record_deletion_consequence='Project usage and boundary documentation would be lost'
      ;;
  esac
}

emit_records() {
  local entry relative_path absolute_path kind size_bytes sha256 hash_scope link_target

  cd "${project_root}"
  path_sizes=()
  file_hashes=()

  while IFS=$'\t' read -r size_bytes absolute_path; do
    relative_path=${absolute_path#./}
    path_sizes[${relative_path}]=${size_bytes}
  done < <(
    find . -mindepth 1 \
      ! -path './docs/PHASE_01_INVENTORY.json' \
      ! -path "./tmp/${manifest_stage:t}" \
      ! -path "./tmp/${inventory_stage:t}" \
      \( -type d -o -type f -o -type l \) -exec stat -f $'%z\t%N' {} +
  )

  while read -r sha256 absolute_path; do
    relative_path=${absolute_path#./}
    file_hashes[${relative_path}]=${sha256}
  done < <(
    find . -mindepth 1 \
      ! -path './docs/PHASE_01_INVENTORY.json' \
      ! -path "./tmp/${manifest_stage:t}" \
      ! -path "./tmp/${inventory_stage:t}" \
      -type f -exec shasum -a 256 {} +
  )

  find . -mindepth 1 \
    ! -path './docs/PHASE_01_INVENTORY.json' \
    ! -path "./tmp/${manifest_stage:t}" \
    ! -path "./tmp/${inventory_stage:t}" \
    \( -type d -o -type f -o -type l \) -print |
    while IFS= read -r entry; do
      relative_path=${entry#./}
      absolute_path=${project_root}/${relative_path}
      size_bytes=${path_sizes[${relative_path}]:-}

      if [[ -z ${size_bytes} ]]; then
        print -u2 -- "Missing size metadata for ${relative_path}"
        return 71
      fi

      if [[ -d ${absolute_path} && ! -L ${absolute_path} ]]; then
        kind=directory
        sha256=''
        hash_scope=not_applicable_directory
      elif [[ -L ${absolute_path} ]]; then
        kind=symlink
        link_target=$(readlink "${absolute_path}")
        sha256=$(printf '%s' "${link_target}" | shasum -a 256 | cut -d ' ' -f 1)
        hash_scope=symlink_target_value
      else
        kind=file
        sha256=${file_hashes[${relative_path}]:-}
        if [[ -z ${sha256} ]]; then
          print -u2 -- "Missing content hash for ${relative_path}"
          return 72
        fi
        hash_scope=file_contents
      fi

      classify_path "${relative_path}"
      printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "${relative_path}" "${kind}" "${record_action}" "${record_purpose}" \
        "${size_bytes}" "${sha256}" "${hash_scope}" "${record_used_by}" \
        "${record_rebuildable}" "${record_deletion_class}" "${record_deletion_consequence}"
    done
}

cd "${project_root}"

{
  print -- '# Phase 1 project file manifest'
  print -- "# Generated: ${generated_at}"
  print -- '# Scope: every project-root entry at generation time, excluding this manifest'
  print -- '# itself. Type values are directory, file, or symlink. The machine-readable'
  print -- '# inventory records per-path hashes, purposes, and deletion classifications.'
  print -- $'path\ttype'
  find . -mindepth 1 ! -path './docs/PHASE_01_FILE_MANIFEST.tsv' \
    ! -path "./tmp/${manifest_stage:t}" \
    ! -path "./tmp/${inventory_stage:t}" -type d -print |
    sed 's#^\./##; s#$#\tdirectory#'
  find . -mindepth 1 ! -path './docs/PHASE_01_FILE_MANIFEST.tsv' \
    ! -path "./tmp/${manifest_stage:t}" \
    ! -path "./tmp/${inventory_stage:t}" -type f -print |
    sed 's#^\./##; s#$#\tfile#'
  find . -mindepth 1 ! -path './docs/PHASE_01_FILE_MANIFEST.tsv' \
    ! -path "./tmp/${manifest_stage:t}" \
    ! -path "./tmp/${inventory_stage:t}" -type l -print |
    sed 's#^\./##; s#$#\tsymlink#'
} > "${manifest_stage}"

mv -f "${manifest_stage}" "${manifest_path}"

manifest_directories=$(rg -c $'\tdirectory$' "${manifest_path}")
manifest_files=$(rg -c $'\tfile$' "${manifest_path}")
manifest_symlinks=$(rg -c $'\tsymlink$' "${manifest_path}")
manifest_entries=$((manifest_directories + manifest_files + manifest_symlinks))

emit_records |
  jq -Rn '
    [inputs | split("\t") | {
      path: .[0],
      kind: .[1],
      action: .[2],
      purpose: .[3],
      size_bytes: (.[4] | tonumber),
      sha256: (if .[5] == "" then null else .[5] end),
      hash_scope: .[6],
      stage: "PHASE-01",
      used_by: (.[7] | split(",")),
      rebuildable: (.[8] == "true"),
      deletion_class: .[9],
      deletion_consequence: .[10]
    }]' |
  jq --slurpfile base "${inventory_path}" \
  --arg generated_at "${generated_at}" \
  --argjson manifest_entries "${manifest_entries}" \
  --argjson manifest_directories "${manifest_directories}" \
  --argjson manifest_files "${manifest_files}" \
  --argjson manifest_symlinks "${manifest_symlinks}" \
  '
    . as $path_entries
    | $base[0]
    | .schema_version = 2
    | .generated_at = $generated_at
    | .inventory_contract = {
        path_granularity: "one independent entry per project path; no aggregate file_set entries",
        hash_policy: "regular files use SHA-256 of contents; symlinks use SHA-256 of target value; directories have no content hash and state hash_scope accordingly",
        self_reference: "docs/PHASE_01_INVENTORY.json has a documented null hash because a final full-file hash cannot be embedded without changing that file"
      }
    | .summary.project_entry_manifest = {
        path: "docs/PHASE_01_FILE_MANIFEST.tsv",
        entries: $manifest_entries,
        directories: $manifest_directories,
        files: $manifest_files,
        symlinks: $manifest_symlinks,
        self_representation: "manifest excludes itself; its independent inventory entry records its hash"
      }
    | .entries = ($path_entries + [{
        path: "docs/PHASE_01_INVENTORY.json",
        kind: "file",
        action: "modified",
        purpose: "Machine-readable complete Phase 1 path inventory",
        size_bytes: null,
        sha256: null,
        hash_scope: "not_applicable_self_referential",
        hash_note: "A verifier must calculate the final full-file checksum externally because embedding it here would change this file.",
        stage: "PHASE-01",
        used_by: ["audit", "handoff", "cleanup_planning"],
        rebuildable: false,
        deletion_class: "keep_for_audit",
        deletion_consequence: "Machine-readable Phase 1 audit evidence would be lost"
      }])
    | .summary.path_record_count = (.entries | length)
  ' > "${inventory_stage}"

jq empty "${inventory_stage}"
mv -f "${inventory_stage}" "${inventory_path}"

print -- "Regenerated ${manifest_path} and ${inventory_path} without Python or network access."
