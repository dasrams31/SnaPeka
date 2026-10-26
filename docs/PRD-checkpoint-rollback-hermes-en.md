# PRD: Checkpoint and Rollback Skill for Hermes Agent

**Document:** Full product analysis and specification
**Date:** October 27, 2026
**Status:** Draft for review
**Product:** Skill plus companion plugin for Hermes Agent (Nous Research)
**Purpose of this document:** To serve as the single reference for building a checkpoint and rollback skill that is deeply compatible with the Hermes architecture, not just a generic prompt skill ported from another ecosystem.

---

## 1. Executive summary

Vibe coding does not fail because the model cannot write code. It fails because there is no safety net when the model gets it wrong. One failed refactor can pile fixes on top of broken code until the project cannot return to a working state. This skill gives Hermes save points: before a risky change, the last verified working state of the project is saved. After the change, build and tests are verified. If green, the save point is updated. If red repeatedly, the project is restored to the last save point with a single command.

This skill is designed specifically for Hermes, not ported raw from another ecosystem. It uses two layers Hermes actually provides: a procedural SKILL.md following the agentskills.io standard for model behavior, and a deterministic plugin tool layer via `ctx.register_tool()` for operations that must never be wrong (snapshot, verification, rollback). State is stored on disk so it survives serverless backend hibernation.

## 2. Background and problem

### 2.1 How vibe coding fails

1. **Fixes pile up.** The model fixes errors by changing already broken code instead of returning to a safe point. Each iteration adds damage.
2. **No definition of done.** A task is considered finished because the code "looks done", even though build or tests were never run.
3. **The safe point is lost.** The user does not remember which commit last worked, so manual recovery takes time and often fails.
4. **Silent scope creep.** Files outside the task scope change along the way (config, lockfiles, secret files) without anyone noticing.
5. **Unbounded loop cost.** The agent retries fixes without limit until tokens and time run out.

### 2.2 Evidence of scarcity

GitHub API searches on October 27, 2026 show Hermes skills in general are abundant (6,466 repos for the keyword "hermes skill"), but there is no Hermes-specific checkpoint and rollback skill (0 results for "hermes checkpoint rollback"). A Hermes-native autonomous loop skill also does not exist yet (13 results, mostly generic loops). This is a real gap, not just a variation of an existing skill.

### 2.3 Design principles

1. **The cost of failure must be near zero.** If rollback is cheap and fast, the agent can be ambitious.
2. **Critical decisions must be deterministic.** Snapshot, verification, and rollback are executed by code, not promised by a prompt.
3. **The last-known-good is an asset.** Last-known-good (LKG) is never deleted automatically.
4. **Transparent to the user.** Every snapshot, verification, and rollback is reported briefly to chat or dashboard.
5. **Safe by default.** Secrets never enter snapshots, logs, or model context.

## 3. Goals and non-goals

### 3.1 Goals

1. Hermes can create automatic checkpoints before risky changes on any project.
2. Hermes verifies work with configurable build, test, or lint commands.
3. Hermes restores a project to the last working checkpoint with a single command, without manual intervention.
4. The user always knows the active checkpoint, the latest verification status, and the rollback history.
5. The skill runs on all Hermes backends (local, Docker, SSH, Modal, Daytona, Vercel Sandbox) and all chat surfaces (Telegram, desktop, web).

### 3.2 Non-goals (version 1)

1. Not a CI/CD system and not a replacement for GitHub Actions.
2. Not a cloud backup and not cross-machine sync.
3. Not UI visual review (a separate skill, planned after this one).
4. Not a test generator (a separate skill).
5. Not multi-repo monorepo orchestration. One project per active checkpoint.

## 4. Users and usage context

### 4.1 Primary user

A solo developer and vibe coder running Hermes on a cheap VPS or laptop, chatting via Telegram, and delegating medium-sized coding tasks (one feature, one refactor, one bug fix) without watching every step.

### 4.2 Core scenarios

1. **Risky refactor.** "Refactor the auth module." Hermes snapshots, refactors, tests fail three times, rolls back automatically, then tries a different approach from the safe point.
2. **New feature.** "Add a report page." Hermes snapshots, implements, verification goes green, the snapshot is promoted to the new LKG.
3. **Late-night bug fix.** The user wakes up to a report: two rollbacks happened, the project still works, and the failure causes are recorded.
4. **Experiment.** "Try swapping library X." If the experiment fails, one command restores everything.

### 4.3 Deliberately unsupported scenarios

1. Selective per-file rollback (version 1 only supports full rollback to a checkpoint).
2. Cross-repo checkpoints in a single command.
3. Recovery of files that were never committed and never entered any snapshot.

## 5. Hermes compatibility analysis

This section is the core of the PRD. Every design decision is mapped to a Hermes surface that actually exists, verified from the official repos on October 27, 2026.

### 5.1 Relevant Hermes architecture facts

| Fact | Source | Design implication |
|---|---|---|
| Skills follow the open agentskills.io standard, managed via Skills Hub, and can be created autonomously by the agent | hermes-agent README | The skill is written as a standard SKILL.md, so it is portable and importable from other ecosystems |
| The agent has a learning loop: skills are created from experience and improve during use, plus cross-session memory | hermes-agent README | Every rollback is learning data: failure causes are recorded to memory so they are not repeated |
| A plugin is a self-contained directory with `plugin.yaml` and `register(ctx)`, installed to `~/.hermes/plugins/`, enabled with `hermes plugins enable` | hermes-example-plugins | Distribution follows the official pattern, with no Hermes fork |
| Model-callable tools are registered via `ctx.register_tool()` with JSON Schema, argument validation, and structured `tool_result()` / `tool_error()` responses | plugin-tool-example | Critical operations are exposed as tools, not text instructions |
| Hooks are available via `ctx.register_hook()`, with the official example `transform_llm_output` for masking sensitive data | plugin-hook-example | Secret masking and auditing can attach to the same hook |
| Backends: local, Docker, SSH, Modal, Daytona, Vercel Sandbox; serverless backends can hibernate when idle | hermes-agent README | State must live on disk inside the project directory, not in process memory |
| Chat surfaces: Telegram, desktop, web UI, plus dashboard plugins | hermes-agent README, example-dashboard | Checkpoint status must be visible in chat and on the dashboard |
| Bundled core plugins already exist: memory providers, disk-cleanup hook, platform adapters | hermes-example-plugins README | This skill does not need to rebuild memory or cleanup |

### 5.2 Feature to Hermes surface mapping

| Skill feature | Hermes surface | Reason |
|---|---|---|
| Agent behavior (when to snapshot, when to roll back, when to stop) | SKILL.md (agentskills.io) in `~/.hermes/skills/` | Procedural behavior belongs in a skill, and it can be read and improved by the agent itself |
| Snapshot, verification, rollback execution | Plugin tool `ctx.register_tool()` | File and git operations must not depend on the model typing commands correctly |
| Argument validation and structured results | JSON Schema plus `tool_result()` / `tool_error()` | The model receives machine-readable status, not free prose it can misread |
| Secret masking in output | Hook `transform_llm_output` | The officially demonstrated pattern for SSNs and card numbers |
| Recording failures to memory | Hermes memory provider | Rollback becomes learning loop input, not a dead log |
| Dashboard status | Dashboard plugin (manifest plus slot) | Optional version 2, following the example-dashboard pattern |
| Installation | `~/.hermes/plugins/` plus `hermes plugins enable` | No changes to the user's Hermes installation |

### 5.3 Architecture decisions

**Decision 1: Two layers, not one.**
The skill layer (SKILL.md) governs decisions: when a snapshot is mandatory, how many fix attempts are allowed, when to roll back. The plugin layer governs execution: git commands, file copies, and testing. Reason: a prompt can be ignored by the model when context gets long, while a tool handler always behaves the same. A skill without a plugin fails on mistyped commands. A plugin without a skill gets used at the wrong time.

**Decision 2: Git as the primary snapshot backend.**
Git is already present in almost every vibe coding project, giving diff, labels, and tested restore. A checkpoint is implemented as a special commit on a separate ref or branch (not the user's working branch), so the user's history is not polluted. For projects without git, the plugin creates a file archive snapshot (zip or tar) in `.hermes-checkpoints/` and still refuses to work without explicit user confirmation.

**Decision 3: On-disk state, not process memory.**
Hermes serverless backends can hibernate. All state (active checkpoint, LKG, history, lock) is written to a JSON file in the project directory (`.hermes-checkpoints/state.json`). A new process waking from hibernation reads the same state.

**Decision 4: Verification as a gate, not a suggestion.**
A checkpoint is only promoted to LKG after the verification command returns exit code 0. There is no "assume it passed" path. Verification commands are configurable per project (for example `npm test`, `pytest`, `cargo test`, or combined build plus lint plus test).

**Decision 5: One active checkpoint per project, with a lock.**
Parallel tasks on the same project could overwrite each other's snapshots. The plugin uses a file lock: a second task attempting a snapshot while the lock is held must queue or be rejected with a clear message, never silently overwrite.

**Decision 6: Skill language in English, user reports may be in Indonesian.**
Hermes models behave most reliably with English instructions. SKILL.md, tool names, and machine error messages are written in English. Brief reports to the user (Telegram) may be in Indonesian because that is the user's preference, and report text does not affect logic.

### 5.4 Hermes constraints that shape the design

1. **Backend hibernation.** Never store status in memory. Everything important must be re-readable from disk.
2. **The model can mis-call tools.** Handlers must validate arguments, reject dangerous commands (for example rolling back to another project's checkpoint), and return structured errors.
3. **Limited model context.** Tool reports must be brief: status, checkpoint name, diff summary (file count, not full contents), and next step. Full diffs are only opened on explicit request.
4. **Multiple surfaces.** Chat commands must behave identically on Telegram, desktop, and web. No feature exists on only one surface.
5. **Skills can be self-authored by the agent.** SKILL.md must be written so Hermes itself can read and improve it (clear structure, concrete examples, no unnecessary jargon).

## 6. Functional requirements

Each requirement comes with testable acceptance criteria.

### 6.1 Snapshot (FR-S)

- **FR-S1.** Before a risky change (refactor, dependency swap, restructuring), the agent must call snapshot. Criteria: no project file changes without an active checkpoint, except changes the user explicitly marks as safe.
- **FR-S2.** A snapshot contains: label (format `cp-<task>-<number>`), timestamp, list of changed files, commit hash or archive, and the verification command in effect. Criteria: a snapshot is uniquely identifiable and inspectable without opening file contents.
- **FR-S3.** Excluded files never enter a snapshot: `.env`, key files, credentials, `node_modules/`, build directories, plus patterns from `.gitignore` and project `.checkpointignore`. Criteria: a test with a dummy `.env` file proves it appears in no snapshot.
- **FR-S4.** Snapshot fails explicitly when git is unavailable and archive mode is not permitted by the user. Criteria: the user receives a clear message, not a silently empty snapshot.
- **FR-S5.** Snapshot must complete within a reasonable time for small to medium projects (target under 10 seconds for projects under 500 files). Criteria: measured on a test project.

### 6.2 Verification (FR-V)

- **FR-V1.** Verification commands are configurable per project in `.hermes-checkpoints/config.json` (default: auto-detect `package.json` test, `pytest`, `cargo test`, or `go test`). Criteria: a project with no recognized command asks the user to define one instead of guessing.
- **FR-V2.** Verification has a default timeout of 300 seconds, configurable. Criteria: a timeout returns a clear failure status instead of hanging the session.
- **FR-V3.** Verification results are structured: pass or fail, command run, duration, and error summary (at most the last 20 lines of output). Criteria: the model can decide the next step without reading the full log.
- **FR-V4.** Verification runs on the same backend where the code was changed (local, Docker, SSH, or sandbox). Criteria: verification never tests a different copy of the code.

### 6.3 Rollback (FR-R)

- **FR-R1.** One command restores the project to the last working checkpoint (LKG). Criteria: after rollback, the working tree matches the LKG state and verification passes.
- **FR-R2.** Automatic rollback triggers after fix attempts fail as many times as configured (default 3). Criteria: in a test scenario, the third failure triggers rollback without a user command.
- **FR-R3.** Manual rollback is available via chat command and tool. Criteria: the user can trigger rollback at any time, including when the agent is idle.
- **FR-R4.** Rollback never deletes the LKG checkpoint. Criteria: after rollback, the LKG still exists and can be selected again.
- **FR-R5.** A failed rollback (for example locked files or conflicts) stops with a clear message and does not leave the project half-restored. Criteria: tested with artificially locked files.

### 6.4 History and retention (FR-H)

- **FR-H1.** The user can list checkpoints: label, timestamp, verification status, and whether it is the LKG. Criteria: the list is readable from chat and dashboard.
- **FR-H2.** Default retention: keep the 10 most recent checkpoints plus the LKG. Older checkpoints are deleted automatically. Criteria: checkpoint count never grows unbounded.
- **FR-H3.** Every rollback is recorded: time, target checkpoint, and reason (automatic or manual, plus triggering error summary). Criteria: a weekly report can answer "how many rollbacks happened and why".

### 6.5 Security and privacy (FR-K)

- **FR-K1.** Snapshots, logs, and reports never contain secret file contents. Criteria: scanning snapshots for API key and token patterns finds nothing.
- **FR-K2.** Tools reject arguments pointing outside the project directory (path traversal). Criteria: a test with `../` is rejected.
- **FR-K3.** Rollback cannot target another project's checkpoint. Criteria: every checkpoint is bound to a project directory fingerprint.
- **FR-K4.** A local audit log records which Hermes session performed each snapshot, verification, and rollback, and when. Criteria: the log is readable without special tools (JSON Lines file).

### 6.6 Reporting and configuration (FR-P)

- **FR-P1.** Every important event is reported briefly: snapshot created, verification passed or failed, rollback happened. Criteria: a user reading only Telegram still knows the project state.
- **FR-P2.** Per-project configuration: verification command, attempt limit, retention count, and extra exclusion patterns. Criteria: changing config requires no plugin reinstall.
- **FR-P3.** A dry-run mode is available: the agent can preview what would be snapshotted or rolled back without executing. Criteria: dry-run changes no file.

## 7. Non-functional requirements

1. **Performance.** Snapshot of a small to medium project under 10 seconds. Verification adds no overhead beyond the command itself.
2. **Reliability.** Snapshot and rollback operations are atomic as far as git allows: a mid-operation failure does not corrupt the working tree beyond the case described in FR-R5.
3. **Portability.** Runs on Linux, macOS, and Windows as far as git and verification commands are available. Paths are handled cross-platform.
4. **Compatibility.** Requires no Hermes fork, modifies no core plugin, and can be fully disabled with `hermes plugins disable`.
5. **Observability.** All events are recorded in a local audit log in a machine-readable format.
6. **Privacy.** No project data is sent to any third-party server by this skill. All operations are local to the user's project.

## 8. Workflow and state machine

```
IDLE (no active checkpoint)
  │  risky task starts
  ▼
SNAPSHOT ──fails──▶ IDLE + clear error message
  │  snapshot succeeds
  ▼
WORK (agent changes code)
  │  task finishes
  ▼
VERIFY ──passes──▶ PROMOTE (checkpoint becomes LKG) ──▶ IDLE
  │  fails
  ▼
FIX (at most N attempts, default 3)
  │  passes ──▶ PROMOTE ──▶ IDLE
  │  Nth failure
  ▼
ROLLBACK ──succeeds──▶ IDLE + report (project back at LKG)
  │  fails ──▶ EMERGENCY (clear message + manual instructions, project untouched)
```

Binding transition rules:

1. No transition to WORK without SNAPSHOT.
2. PROMOTE only from a VERIFY that passed.
3. ROLLBACK only targets the LKG, not an arbitrary checkpoint.
4. EMERGENCY stops the agent from changing the project until the user decides.

## 9. Data model

### 9.1 Checkpoint record (stored in `.hermes-checkpoints/state.json`)

| Field | Type | Description |
|---|---|---|
| id | string | `cp-<task>-<number>`, unique per project |
| label | string | Human label, for example `before-auth-refactor` |
| created_at | string (ISO 8601) | Snapshot creation time |
| backend | string | `git` or `archive` |
| ref | string | Commit hash or archive path |
| files_changed | array of strings | Relative paths changed (not contents) |
| verify_command | string | Verification command in effect at the time |
| verify_status | string | `pending`, `passed`, `failed` |
| is_lkg | boolean | True for exactly one checkpoint per project |
| task | string | Short description of the triggering task |
| session | string | Triggering Hermes session ID |

### 9.2 Project configuration (`.hermes-checkpoints/config.json`)

| Field | Default | Description |
|---|---|---|
| verify_command | auto-detect | Verification command, multiple commands joined with `&&` |
| verify_timeout_sec | 300 | Verification time limit |
| max_fix_attempts | 3 | Fix attempts before automatic rollback |
| retention_count | 10 | Non-LKG checkpoints kept |
| exclude_patterns | built-in list plus `.gitignore` | Extra patterns excluded from snapshots |
| auto_rollback | true | Automatic rollback when the attempt limit is reached |
| require_confirm_for_manual_rollback | false | Ask for confirmation on manual rollback |

### 9.3 Audit log (`.hermes-checkpoints/audit.jsonl`)

One JSON line per event: time, session, event type (`snapshot`, `verify`, `promote`, `rollback`, `cleanup`), related checkpoint, and result. Never contains file contents or full diffs.

## 10. Interfaces

### 10.1 Plugin tools (abridged JSON Schema)

These tools are registered via `ctx.register_tool()` in a single toolset, for example `checkpoint`:

1. **checkpoint_create**: arguments: `task` (string, required), `label` (string, optional). Result: checkpoint id, file count, status.
2. **checkpoint_verify**: arguments: `command` (string, optional, defaults from config). Result: pass or fail, duration, error summary.
3. **checkpoint_rollback**: arguments: `target` (string, optional, defaults to LKG). Result: status, target checkpoint, count of files restored.
4. **checkpoint_list**: arguments: none. Result: checkpoint list with status and LKG marker.
5. **checkpoint_status**: arguments: none. Result: active checkpoint, LKG, latest verification, total rollback count.
6. **checkpoint_dry_run**: arguments: `action` (`snapshot` or `rollback`). Result: what would happen, without executing.

All handlers validate arguments, reject paths outside the project, and return structured `tool_error()` on failure instead of free prose.

### 10.2 Chat commands

| Command | Function |
|---|---|
| `/checkpoint` | Create a manual snapshot for the running task |
| `/checkpoint list` | Show checkpoint list and LKG |
| `/checkpoint status` | Show active checkpoint and latest verification status |
| `/rollback` | Restore the project to LKG |
| `/rollback <label>` | Restore to a labeled checkpoint (only if it is the LKG or the user approves) |

Chat commands call the same tools the model uses, so behavior is identical on every surface.

### 10.3 SKILL.md outline

SKILL.md follows the agentskills.io standard and contains, in order: when the skill activates (risky tasks), hard rules (no work without snapshot, no promotion without passing verification, rollback after N failures), step sequence (snapshot, work, verify, fix, rollback), a short example conversation, and common mistakes (changing files outside scope, skipping verification, deleting the LKG). Target length: complete enough for the model to follow without re-reading, roughly 150 to 250 lines.

## 11. Test scenarios and acceptance criteria

| ID | Scenario | Expected result |
|---|---|---|
| UT-01 | Refactor task, tests green | Snapshot created, promoted to LKG, brief report |
| UT-02 | Tests fail 3 times | Automatic rollback to LKG, project passes verification, report includes reason |
| UT-03 | Manual rollback via chat | Project returns to LKG with one command |
| UT-04 | Project without git | Plugin refuses with a clear message, or creates an archive if the user allows |
| UT-05 | Dummy `.env` file in project | File appears in no snapshot |
| UT-06 | Two parallel tasks on the same project | Second task queues or is rejected, never overwrites the first task's snapshot |
| UT-07 | Verification timeout | Clear failure status, attempt counted, session does not hang |
| UT-08 | Rollback with locked files | Safe stop, clear message, project not half-restored |
| UT-09 | Retention | After 12 checkpoints, only the 10 most recent plus LKG remain |
| UT-10 | Backend hibernation | A new session reads state from disk and resumes from the correct checkpoint |
| UT-11 | Path traversal | Argument with `../` is rejected by the handler |
| UT-12 | Plugin disabled | `hermes plugins disable` stops all tools with no leftover process |

## 12. Phased release plan

**Phase 1, skill only (week 1).** Procedural SKILL.md using direct git commands. Goal: prove the snapshot, verify, and rollback flow on a test project. Acknowledged limitation: commands can still be mistyped by the model.

**Phase 2, plugin tools (weeks 2 to 3).** `checkpoint` plugin with the six tools above, argument validation, audit log, and concurrency lock. Goal: all scenarios UT-01 through UT-12 pass.

**Phase 3, Hermes integration (week 4).** Rollback records flow into Hermes memory so the learning loop avoids the same failure patterns, status reporting reaches the dashboard, and testing covers Docker, SSH, and Daytona backends. Goal: identical behavior on all backends and surfaces.

**Phase 4, hardening (week 5).** Large project testing (over 2,000 files), Windows testing, and Indonesian user documentation. Goal: no performance regression and no secret leakage.

## 13. Success metrics

1. **Recovery rate.** Percentage of failed tasks restored to a working state without manual intervention. Version 1 target: above 95 percent on test projects.
2. **Rollback time.** Median time from rollback command to passing verification. Target: under 30 seconds for small to medium projects.
3. **Automatic rollback ratio.** Automatic rollbacks versus total risky tasks. This measures how often the safety net actually works.
4. **Adoption.** Plugin and skill installs on the Skills Hub within the first 30 days.
5. **Zero secret incidents.** Reports of secret leakage via snapshots or logs. Target: zero, no tolerance.

## 14. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Model skips snapshot and edits code directly | No safety net exists | SKILL.md makes snapshot the mandatory first step; a pre-task hook is a second mitigation if available, plus a hard reminder in SKILL.md |
| Snapshots grow large on big projects | Slow and disk-hungry | Git as the primary backend; archive only for git-less projects; automatic retention |
| Slow or flaky verification | False rollback on unstable tests | Configurable timeout; build, lint, and test commands separated so the failure source is clear |
| Rollback deletes unrelated user work | Work loss | Rollback only restores files recorded in the checkpoint; unrelated new files are reported, never silently deleted |
| Behavior differs across backends | Snapshots not portable | On-disk state plus git backend; phase 3 testing on all backends |
| agentskills.io format changes | SKILL.md becomes outdated | Follow the latest specification at implementation time; record the spec version in the SKILL.md header |
| Users mistake the skill for a backup | Data loss outside the project | Documentation states clearly: this is a project save point, not a backup. Retention is limited and local |

## 15. Open questions (TBD before implementation)

1. The full list of available Hermes hooks (whether a pre-tool-execution hook exists, or only `transform_llm_output` as demonstrated). If only output transformation exists, snapshot enforcement stays in SKILL.md.
2. The latest SKILL.md frontmatter fields required by the agentskills.io specification (name, description, and other mandatory fields) so the skill passes Skills Hub validation.
3. Hibernation behavior of Daytona and Modal for files in `.hermes-checkpoints/` (whether the project directory fully survives wake).
4. Whether Hermes allows a plugin to write to the user's project directory without extra confirmation, or requires explicit permission in the manifest.
5. A reasonable archive snapshot size limit for git-less projects before the plugin must refuse.

## 16. Verified sources

1. Repo `NousResearch/hermes-agent` (README, directory structure, October 27, 2026): agentskills.io skills, learning loop, local, Docker, SSH, Modal, Daytona, Vercel Sandbox backends, Telegram, desktop, web surfaces.
2. Repo `NousResearch/hermes-example-plugins` (README and `plugin-tool-example`, October 27, 2026): `plugin.yaml`, `register(ctx)`, `ctx.register_tool()`, JSON Schema, `tool_result()` and `tool_error()`, `ctx.register_hook()`, installation to `~/.hermes/plugins/` and `hermes plugins enable`.
3. GitHub API searches (October 27, 2026): "hermes skill" 6,466 repos, "hermes checkpoint rollback" 0 repos, "hermes ralph" 13 repos, "hermes visual review" 1 repo, "hermes test generation" 7 repos.

---

*This document is a draft. Section 15 must be closed before phase 2 starts. Any change to the architecture decisions in section 5 must be updated in this document, not only in code.*

---

## 17. Phase 2 addendum: TBD resolutions and architecture changes

Section 15 is closed. All five open questions were resolved on October 27,
2026, against the official Hermes plugin documentation, the agentskills.io
specification, the Daytona sandbox documentation, and the local Hermes
checkout (`hermes_cli/plugins.py`, bundled plugins). The resolutions below
are the authoritative answers for phase 2.

### TBD 1: Hermes hooks — CLOSED

A pre-tool-execution hook exists: `pre_tool_call`. It receives
`(tool_name, args)` and may return `{"action": "block", "message": ...}`
to veto the call, or `{"action": "approve", ...}` to escalate to human
approval. Most other hooks are fire-and-forget observers; the exceptions
are `pre_llm_call` (context injection) and `pre_tool_call` (block/approve).
`transform_llm_output` rewrites the finished reply (first non-None return
wins) and is the right hook for secret redaction. `transform_tool_result`
is also used by the bundled `security-guidance` plugin.

**Architecture change (supersedes the section 15 assumption):** snapshot
enforcement is no longer SKILL.md-only. Phase 2 registers a `pre_tool_call`
hook that blocks `write_file` and `patch` calls inside a project that
already uses SnaPeka but has no active checkpoint. The hook is fail-open
(it never raises; anything unexpected passes through), applies only to
projects with a `.hermes-checkpoints` directory (opt-in by use), and does
not attempt to attribute `terminal` commands to a project. Verified
built-in write tool names: `write_file`, `patch`, `terminal`.

### TBD 2: SKILL.md frontmatter — CLOSED

Only `name` and `description` are required. Constraints: `name` is at most
64 characters, lowercase letters, digits, and hyphens only, never leading
or trailing with a hyphen; `description` is at most 1024 characters and
non-empty. Optional fields: `license`, `compatibility` (at most 500
characters), `metadata` (free string-to-string map), `allowed-tools`
(experimental, support varies by agent).

**Consequence:** the phase 1 SKILL.md violated the specification with
`name: SnaPeka` (uppercase). Phase 2 renames it to `snapeka`, and the
skill directory must be named exactly `snapeka`. The phase 2 SKILL.md
also adds `license` (MIT), `compatibility` (requires git, Python 3.10+),
and `metadata` (author, version).

### TBD 3: Daytona and Modal hibernation — CLOSED

On both backends, `.hermes-checkpoints/` survives hibernation as long as
the sandbox is not permanently deleted. Only in-memory and process state
is lost, never files. Daytona defaults to a persistent filesystem
(`persistent_filesystem: True` in the Hermes Daytona backend); stopping a
sandbox preserves the filesystem, only deleting destroys it, and the disk
is capped at 10 GB. Modal keeps persistent filesystem snapshots across
sessions (snapshot ids stored per task).

**Design note:** `state.json` is reconstructible from git history
(snapshot commits carry `snapeka: cp-<task>-<number>` messages and the LKG
is tracked at `refs/snapeka/lkg`), so losing `state.json` only loses
pointers, not snapshots. The archive backend is higher-risk because the
`.tgz` file is the only copy, which is why TBD 5 sets a size limit.

### TBD 4: Plugin write permission — CLOSED

Yes: a plugin may write to the user's project directory with no explicit
manifest permission and no sandbox. Capabilities are consent plus audit,
not a sandbox: they gate host API surfaces only. No filesystem capability
exists. The gates are install/enable consent, operator opt-in for
overriding a built-in tool, and escalation through `pre_tool_call` by
another plugin (not a default). On remote backends, writes land in the
sandbox/container filesystem, which is exactly where the agent's project
lives, so SnaPeka's on-disk state works on every backend.

### TBD 5: Archive snapshot size limit — CLOSED as a design recommendation

No canonical limit exists in comparable tools (git has no hard stash
limit; tar has none), so this is a design decision: warn above 100 MB
and refuse above 500 MB of compressed `.tgz` size, configurable via
`warn_archive_bytes` and `max_archive_bytes`. Rationale: 100 MB is the
widely known GitHub push cap (a familiar "large" threshold); 10 retained
archives at 500 MB equal 5 GB, under the 10 GB Daytona disk cap; healthy
code archives with default excludes are tens of MB, so the limit only
catches misconfiguration (datasets or media being archived). Refusal is
explicit, never a silent empty snapshot.

### Additional phase 2 refinements

* **Snapshot commits use plumbing on dedicated refs.** Snapshots are
  created with `write-tree` + `commit-tree` on `refs/snapeka/<id>` and
  never move the user's working branch. A clean tree needs no new commit:
  the snapshot ref is the HEAD hash. The LKG is tracked at
  `refs/snapeka/lkg`.
* **First-task rollback fallback.** Section 8 states rollback goes only
  to the LKG. When no promoted LKG exists yet (the very first task), an
  automatic or manual rollback with no explicit target restores the
  active snapshot itself (the pre-task state). If the post-rollback
  verification passes, that snapshot is promoted to LKG, since the
  restored tree just verified green.
* **Promotion lives inside verify.** The six tools in section 10.1
  contain no separate promote tool, so `checkpoint_verify` promotes the
  active checkpoint to LKG on pass (and prunes retention), and triggers
  automatic rollback on the Nth consecutive failure.
* **Language rule (October 27, 2026, user directive).** All project
  artifacts are written in English, without Indonesian. The English PRD
  is canonical; the Indonesian PRD is kept as an archive reference only.
* **UT-12 verification scope.** The full `hermes plugins disable` path
  needs the Hermes CLI, which is not installed on the development
  machine. Phase 2 verifies registration mechanics through a fake
  context; CLI disable remains to be verified on a machine with Hermes
  installed.

### Rename: SnaPeka becomes Hermes Checkpoint (October 27, 2026, user directive)

The product name SnaPeka is retired. The skill, the plugin, and all
identifiers are now Hermes Checkpoint:

* Skill frontmatter name: `hermes-checkpoint`; install directory:
  `~/.hermes/skills/hermes-checkpoint/`.
* Plugin manifest name: `hermes-checkpoint`; install directory:
  `~/.hermes/plugins/hermes-checkpoint/`; enable with
  `hermes plugins enable hermes-checkpoint`.
* Snapshot refs: `refs/hermes-checkpoint/<id>`; LKG ref:
  `refs/hermes-checkpoint/lkg`; snapshot commit messages:
  `hermes-checkpoint: cp-<task>-<number>`.
* Project state directory: `.hermes-checkpoint/` (previously
  `.hermes-checkpoints/`).
* The repository was already renamed to `hermes-checkpoint` before this
  change; the tool names (`checkpoint_*`) are unchanged.

Nothing was ever installed under the old name, so no migration is needed.
Earlier sections of this document still say SnaPeka; read them as Hermes
Checkpoint.

### Phase 3 addendum: Hermes integration (October 27, 2026)

Phase 3 was built after verifying the integration points against the
local Hermes Agent checkout. Two PRD goals turned out to have no
plugin-facing API, so the implementation takes the supported path and
says so instead of pretending otherwise:

* **Memory (agent-mediated, not plugin-written).** `PluginContext`
  offers no memory-write method and `register_memory_provider` is
  inert; the memory-write path belongs to the agent's `memory` tool
  and is approval-gated. The plugin therefore emits recordable text:
  every rollback result carries a `memory_note`, and the new seventh
  tool `checkpoint_lessons` aggregates the audit log (counts by
  reason, recent rollbacks) into a `memory_text`. The agent records
  these with the memory tool. Writing directly into
  `memories/MEMORY.md` would bypass the approval gate and is refused
  by design.
* **Status (plugin events + transcript, no dashboard API).**
  `ctx.emit` publishes to plugin subscribers only, and
  `gateway_platform_event` is inbound-only, so there is no
  plugin-to-dashboard channel. Tool calls now emit
  `checkpoint_created`, `checkpoint_promoted`, and
  `checkpoint_rolled_back` (fail-open), and the dashboard-visible
  status remains the tool result text in the session transcript.
* **Backends (documented parity, not live matrix runs).** The plugin
  runs in-process in the agent host, not inside the backend. Docker
  with a bind-mounted working directory is consistent; SSH and
  Daytona sync files to the host, so host-side snapshots can lag
  unsynced backend edits. `SKILL.md` documents this per backend,
  `checkpoint_status` returns the resolved `project_dir` for
  comparison with the backend working directory, and a test proves
  explicit project paths work from any host working directory.
  Docker and Daytona binaries are absent from the development
  machine, so live backend runs were not possible; SSH was not
  attempted (needs a reachable sshd plus keys).

Version bumped to 0.3.0. Test count: 20 (16 phase 2 + 4 phase 3).
