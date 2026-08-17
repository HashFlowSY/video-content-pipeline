# 05 — RunBundle processing-report provenance

**What to build:** A published RunBundle's processing report tells the
truth about what produced it. Replace the conservative empty-inputs stub so
the report carries: every model actually used (name, revision, sha256,
size, purpose — from the model registry entries of the engines the run
selected), tools, environment, run parameters, and measured resource usage
(peak memory from the model-runtime-subprocess evidence, stage durations,
disk delta). This is what binds a Coverage-ledger entry to the model stack
that produced the outputs — the phase's acceptance item "输出文件、证据引用
和处理清单完整" cannot pass while these sections are empty.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] An offline golden run publishes a processing report with non-empty models, tools, environment, parameters, and resource-usage sections
- [ ] Model entries carry name, revision, sha256, size, and purpose consistent with the registry
- [ ] Resource usage reflects measured values (peak memory, durations, disk delta), not placeholders
- [ ] A run that used no model for a stage honestly omits it (no padding)
- [ ] Assertions run at the CLI command boundary against published RunBundle contents (Phase 9/10 golden-run prior art)
