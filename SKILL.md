---
name: hermes-checkpoint
description: Checkpoint and rollback for Hermes Agent. Create a save point before risky changes, verify afterwards, and restore the last-known-good state in one command when fixes fail repeatedly. Deterministic plugin tools with a git backend and an archive fallback.
license: MIT
compatibility: Requires git for the primary backend and Python 3.10+ for the plugin. Works on Linux and macOS.
metadata:
  author: dasrams31
  version: 0.3.0
---

# Hermes Checkpoint

Checkpoint and rollback skill for Hermes Agent. One sentence:
**no risky work without a save point, no promotion without a passing
verification, and rollback always goes to the last-known-good state (LKG).**

This skill has two layers. The plugin tools in this repository execute
snapshot, verify, and rollback deterministically. This document governs
decisions: when a snapshot is required, how many fix attempts are allowed,
and when to stop. If the plugin is unavailable, use the raw git fallback
at the end of this document.

Install the skill directory as `~/.hermes/skills/hermes-checkpoint/` (the directory
name must match the `name` field above) and the plugin directory as
`~/.hermes/plugins/hermes-checkpoint/`, then run `hermes plugins enable hermes-checkpoint`.

## When this skill is active

Active for risky work on any project:

- Refactors, restructures, migrations, and multi-file changes.
- Dependency or framework swaps, or build configuration changes.
- Any change to code that is currently running in production or a demo.

Not active for read-only work (exploring code, answering questions, writing
documentation without changing code) or for changes the user explicitly
marks as safe.

## Hard rules

These rules are never violated, including when the user pushes for speed.

1. **No work without a snapshot.** Before changing a single file in a risky
   task, call `checkpoint_create`. If snapshot creation fails, stop and
   report the reason. Never start work on the promise of a later snapshot.
2. **No promotion without a passing verification.** A checkpoint becomes
   the LKG only after `checkpoint_verify` passes. A verification that was
   never run counts as failed.
3. **Automatic rollback after 3 failed fixes.** The plugin counts failed
   verifications on the active checkpoint. The third consecutive failure
   triggers an automatic rollback to the LKG (or, for a first task with no
   promoted LKG yet, to the active snapshot itself). The limit is
   configurable (`max_fix_attempts`, default 3).
4. **Rollback targets the LKG.** Never roll back to an arbitrary checkpoint,
   except through `/rollback <label>` with explicit user approval, and
   except for the first-task fallback in rule 3.
5. **The LKG is never deleted.** Retention may prune old checkpoints, but
   the LKG is always kept, even after a rollback has happened.
6. **Secrets never enter a snapshot.** `.env`, key files, credentials,
   `node_modules/`, build directories, and `.gitignore` patterns are never
   snapshotted, logged, or reported. If a secret-like file shows up in
   `git status` and is not gitignored, the snapshot is refused: ask the
   user to gitignore it first.
7. **Do not change files outside the task scope.** If the task does not
   mention config or lockfiles, do not touch them. Report when the scope
   is forced to widen.
8. **Never guess the verify command.** Use `.hermes-checkpoint/config.json`
   when present. Otherwise the plugin auto-detects (`npm test`, `pytest`,
   `cargo test`, `go test`). When nothing is detected, ask the user.
9. **Verification has a time limit.** Default 300 seconds
   (`verify_timeout_sec`). A timeout is a failure, not a hung session.
10. **Retention is bounded.** Keep the 10 newest non-LKG checkpoints plus
    the LKG. Older checkpoint records and their snapshot refs are pruned.
11. **Dry-run changes nothing.** `checkpoint_dry_run` only shows what
    would happen.
12. **Emergency stops all work.** If a rollback fails (locked file,
    conflict, missing ref), the project enters emergency state: stop
    completely, give a clear message and manual instructions, and do not
    touch the project again until the user decides.

## Tools

| Tool | Purpose |
|---|---|
| `checkpoint_create` | Snapshot before a risky change. Args: `task` (required), `label`, `project_dir`, `session`, `allow_archive`. |
| `checkpoint_verify` | Run the verify command. A pass promotes the active checkpoint to LKG; repeated failures trigger automatic rollback. |
| `checkpoint_rollback` | Restore the LKG (or a given checkpoint id/label). |
| `checkpoint_list` | List checkpoints with verify status and the LKG marker. |
| `checkpoint_status` | Active checkpoint, LKG, last verify status, rollback count, emergency state. |
| `checkpoint_lessons` | Rollback history aggregated for the memory tool: counts by reason, recent rollbacks, and a ready-to-record summary. |
| `checkpoint_dry_run` | Preview a snapshot or rollback without executing. |

## Hooks

- `pre_tool_call` blocks `write_file` and `patch` calls inside a project
  that already uses Hermes Checkpoint but has no active checkpoint, forcing the
  snapshot-first flow. Projects without a `.hermes-checkpoint` directory
  are untouched, read-only tools always pass, and the hook never raises:
  anything unexpected passes through.
- `transform_llm_output` redacts assignment-style secrets
  (`api_key = ...`, `token = ...`, `password = ...`) from replies.

## Workflow

Follow this state machine exactly, with no jumps:

```
IDLE -> SNAPSHOT -> WORK -> VERIFY -> PROMOTE -> IDLE
                                   |
                                failed
                                   v
                        FIX (at most 3 attempts)
                           |             |
                        passed      failed 3rd time
                           v             v
                       PROMOTE       ROLLBACK -> IDLE + report
                                          |
                                     rollback failed
                                          v
                                       EMERGENCY
```

### Step 1: SNAPSHOT

1. Call `checkpoint_create` with a short task name. The plugin refuses
   when the project is not a git repository and archive mode is not
   approved: report that clearly instead of creating an empty snapshot.
2. The plugin records the checkpoint id, label, timestamp, backend
   (`git` or `archive`), ref, changed files (paths only), verify
   command, and session in `.hermes-checkpoint/state.json`, and appends
   a line to `audit.jsonl`.
3. Report briefly: checkpoint id, file count, and the verify command.

### Step 2: WORK

Do the task, touching only in-scope files. Report before widening scope.

### Step 3: VERIFY

1. Call `checkpoint_verify`. The plugin runs in the agent host process
   against the project directory it is given, not inside the execution
   backend. On SSH and Daytona backends the host copy can lag behind
   backend edits until sync runs (see Backend behavior below), so
   confirm the host copy is current before trusting a verify result.
2. A pass promotes the checkpoint to LKG and releases the lock.
3. A failure counts toward the fix limit. The plugin reports the
   command, duration, and the last 20 lines of error output. Fix the
   error and verify again, at most 3 times in total.

### Step 4: ROLLBACK

1. The default target is the LKG. Another target needs explicit user
   approval via `/rollback <label>`.
2. Tracked files are reset to the LKG state (`git reset --hard`). Files
   created during the task that were never tracked are listed in the
   result, never deleted silently. Never run `git clean` without an
   explicit user command.
3. The plugin verifies once more after rollback. On success it reports
   the target, the reason, and the leftover untracked files. The LKG
   remains available for another rollback.
4. On failure the project enters emergency state: stop, report clearly,
   and wait for the user.
5. After any rollback, record the lesson so the failure pattern is not
   repeated. The rollback result carries a ready-to-record
   `memory_note`; for a pattern across rollbacks, call
   `checkpoint_lessons` and use its `memory_text`. Record it with the
   Hermes memory tool, which is approval-gated. Never write into
   `memories/MEMORY.md` directly: that bypasses the approval gate, and
   the plugin deliberately has no memory-write path of its own.

## Backend behavior

The plugin loads in-process into the agent, so its git and verify
subprocesses run in the agent host process, not inside the execution
backend. Consequences per backend:

- **Local and Docker with a bind-mounted working directory:** host and
  backend see the same files, so snapshots, verify, and rollback are
  consistent. This is the safe case.
- **SSH and Daytona:** agent edits land in the remote environment and
  reach the host only through file sync. A snapshot or rollback taken
  on the host can lag behind unsynced backend edits, and the
  `.hermes-checkpoint` state itself is subject to sync timing. Before
  trusting a verify or rollback on these backends, confirm the host
  copy is synced, and pass the project directory explicitly instead of
  relying on the host working directory. `checkpoint_status` returns
  the resolved `project_dir` so the agent can compare it with the
  backend working directory.
- **Git availability** depends on the host or sandbox image and is not
  guaranteed on any backend. When git is missing and archive mode is
  not approved, the plugin refuses with a clear message.

## State and config

All state lives inside the project, in `.hermes-checkpoint/`:

- `state.json`: checkpoint records (`id` as `cp-<task>-<number>`,
  `label`, `created_at` in ISO 8601, `backend`, `ref`, `files_changed`
  as relative paths only, `verify_command`, `verify_status` as
  `pending`/`passed`/`failed`, `is_lkg` true for exactly one checkpoint
  per project, `task`, `session`), plus `active_checkpoint_id`,
  `rollback_count`, and `emergency`.
- `config.json`: `verify_command`, `verify_timeout_sec` (default 300),
  `max_fix_attempts` (default 3), `retention_count` (default 10),
  `exclude_patterns`, `auto_rollback` (default true),
  `require_confirm_for_manual_rollback` (default false),
  `max_archive_bytes` (default 500 MB compressed),
  `warn_archive_bytes` (default 100 MB compressed).
- `audit.jsonl`: one JSON line per event (`snapshot`, `verify`,
  `promote`, `rollback`, `cleanup`). Never file contents or full diffs.
- `lock`: held from snapshot until promotion or rollback, so a second
  parallel task on the same project is rejected instead of silently
  overwriting the first task's snapshot.

Checkpoints are bound to their project directory (fingerprint in state).
The plugin refuses to operate on another project's checkpoints, and
rejects path traversal (`../`) in tool arguments.

## Chat commands

These commands call the same tools the model uses, so behavior is
identical on every surface:

| Command | Function |
|---|---|
| `/checkpoint` | Create a manual snapshot for the running task |
| `/checkpoint list` | Show checkpoints and the LKG |
| `/checkpoint status` | Show the active checkpoint and last verification |
| `/rollback` | Restore the project to the LKG |
| `/rollback <label>` | Restore to a labeled checkpoint (LKG or user-approved only) |

## Short example

User: refactor the auth module, but do not break the login that works.

Agent: Snapshot first: `cp-refactor-auth-001` (12 files, verify
`npm test`). Then refactor. Verification fails twice, the third fix
passes. The checkpoint is promoted to LKG. Done.

User: login is still broken after yesterday's refactor.

Agent: Verification failed 3 times, so automatic rollback to LKG
`cp-refactor-auth-001` ran. The project is back to a working state and
verification passes. The failed refactor changes were not kept.

## Common mistakes

- Changing files outside the task scope without reporting.
- Skipping verification or promoting a checkpoint that did not pass.
- Deleting or overwriting the LKG.
- Letting secret files into a snapshot, log, or report.
- Retrying fixes without limit instead of rolling back.
- Verifying a different copy of the code than the one that changed.
- Creating a silent empty snapshot when git is unavailable.

## Fallback when the plugin is unavailable

If the plugin tools are not installed, follow the same rules with raw
git commands:

1. Confirm a git repository (`git rev-parse --is-inside-work-tree`).
   Otherwise stop with a clear message, unless the user explicitly
   allows an archive snapshot.
2. Check `git status --porcelain` for secret-like files (`.env`, keys,
   credentials). If any would be added and are not gitignored, refuse
   and ask the user to gitignore them.
3. Clean tree: the snapshot ref is the current HEAD hash, no new commit.
   Dirty tree: `git add -A` (respecting `.gitignore`), then commit the
   pre-task state as `hermes-checkpoint: cp-<task>-<number>`.
4. Run the verify command with a timeout. Pass: the snapshot is the new
   LKG. Fail 3 times: `git reset --hard <LKG ref>` and report untracked
   leftovers. Never run `git clean` without an explicit user command.
