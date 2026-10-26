# Hermes Checkpoint

**A safety net for vibe coding in Hermes Agent: snapshot before you change,
verify after you change, roll back in one command when fixes fail.**

Vibe coding moves fast, and fast means breaking working code. Hermes
Checkpoint makes every risky change reversible: before a refactor, a
migration, or a dependency swap, the project state is snapshotted. After
the change, a verify command runs. If it passes, the snapshot becomes the
new last-known-good state (LKG). If fixes fail repeatedly, the project
rolls back to the LKG automatically. No manual git archaeology, no
half-restored trees, no lost afternoons.

> Formerly named SnaPeka. The repository is now `hermes-checkpoint`,
> and the skill and plugin are both named `hermes-checkpoint`.

---

## Why this exists

| Without a checkpoint | With Hermes Checkpoint |
|---|---|
| A refactor breaks login; nobody remembers the last good state | One command restores the LKG, verified green |
| Fixes are retried without limit while the project stays broken | Three failed verifications trigger automatic rollback |
| A secret file slips into a backup commit | Secret-like files refuse the snapshot before anything is stored |
| Two parallel tasks overwrite each other's recovery point | A lock rejects the second task instead of corrupting the first |

## Features

- **Deterministic snapshot, verify, rollback.** Plugin tools execute the
  flow exactly; the skill document governs the decisions.
- **Git backend, archive fallback.** Snapshots are commits on dedicated
  refs (`refs/hermes-checkpoint/<id>`), so your working branch history
  is untouched. Projects without git can snapshot as `.tgz` archives.
- **Automatic rollback after 3 failed fixes.** Configurable
  (`max_fix_attempts`, default 3), including a first-task fallback to
  the active snapshot when no promoted LKG exists yet.
- **LKG is never deleted.** Retention prunes old checkpoints but always
  keeps the LKG.
- **Secret safety.** Secret-like files are refused, never snapshotted,
  logged, or reported. Output redaction strips assignment-style secrets
  from replies.
- **Concurrency lock.** One active checkpoint per project; a second
  parallel task is rejected, never silently merged.
- **Rollback lessons.** Every rollback carries a ready-to-record memory
  note, and a lessons tool aggregates rollback history for the Hermes
  memory tool, so failure patterns stop repeating.
- **Plugin events.** Tool calls emit `checkpoint_created`,
  `checkpoint_promoted`, and `checkpoint_rolled_back` for subscribers.
- **Snapshot-first enforcement.** A `pre_tool_call` hook blocks file
  writes in projects that already use Hermes Checkpoint but have no
  active checkpoint.
- **Standard library only.** No third-party dependencies. Python 3.10+
  and git (for the primary backend).

## How it works

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

1. **Snapshot.** `checkpoint_create` records the pre-task state: a
   commit on `refs/hermes-checkpoint/<id>` (clean tree: the ref is HEAD
   itself, no new commit), or a `.tgz` archive for git-less projects.
2. **Work.** Change only in-scope files.
3. **Verify.** `checkpoint_verify` runs the configured command with a
   timeout (default 300 seconds). Pass promotes the snapshot to LKG and
   releases the lock. Fail counts toward the fix limit.
4. **Rollback.** The default target is the LKG. Tracked files are reset
   (`git reset --hard`); untracked files created during the task are
   listed, never deleted silently. The plugin verifies once more after
   rollback. If rollback itself fails, the project enters emergency
   state and the plugin stops touching it until you decide.

## Install

```bash
# Skill (the directory name must match the frontmatter name)
cp -r . ~/.hermes/skills/hermes-checkpoint

# Plugin
cp -r plugin ~/.hermes/plugins/hermes-checkpoint
hermes plugins enable hermes-checkpoint
```

Requirements: Python 3.10+, git for the primary backend. Linux and
macOS supported; Windows compatibility is code-reviewed (see
[Windows](#windows)).

## Usage

### Chat commands

These commands call the same tools the agent uses, so behavior is
identical on every surface:

| Command | Function |
|---|---|
| `/checkpoint` | Create a manual snapshot for the running task |
| `/checkpoint list` | Show checkpoints and the LKG |
| `/checkpoint status` | Show the active checkpoint and last verification |
| `/rollback` | Restore the project to the LKG |
| `/rollback <label>` | Restore to a labeled checkpoint (LKG or user-approved only) |

### A typical session

```
You:    refactor the auth module, but do not break the login that works.
Agent:  Snapshot first: cp-refactor-auth-001 (12 files, verify `npm test`).
        Then refactor. Verification fails twice, the third fix passes.
        The checkpoint is promoted to LKG. Done.

You:    login is still broken after yesterday's refactor.
Agent:  Verification failed 3 times, so automatic rollback to LKG
        cp-refactor-auth-001 ran. The project is back to a working
        state and verification passes. The failed refactor changes
        were not kept.
```

### Tools (toolset `checkpoint`)

| Tool | Purpose |
|---|---|
| `checkpoint_create` | Snapshot before a risky change (`task` required; `label`, `project_dir`, `session`, `allow_archive` optional) |
| `checkpoint_verify` | Run the verify command; pass promotes to LKG, repeated failures auto-roll back |
| `checkpoint_rollback` | Restore the LKG (or a given checkpoint id/label) |
| `checkpoint_list` | List checkpoints with verify status and the LKG marker |
| `checkpoint_status` | Active checkpoint, LKG, last verify status, rollback count, emergency state, resolved `project_dir` |
| `checkpoint_lessons` | Rollback history aggregated for the memory tool: counts by reason, recent rollbacks, ready-to-record summary |
| `checkpoint_dry_run` | Preview a snapshot or rollback without executing |

### Hooks

- `pre_tool_call` blocks `write_file` and `patch` calls inside a
  project that already uses Hermes Checkpoint but has no active
  checkpoint, forcing the snapshot-first flow. Projects without a
  `.hermes-checkpoint` directory are untouched, read-only tools always
  pass, and the hook never raises: anything unexpected passes through.
- `transform_llm_output` redacts assignment-style secrets
  (`api_key = ...`, `token = ...`, `password = ...`) from replies.

## Configuration

Per project, in `.hermes-checkpoint/config.json`:

| Key | Default | Meaning |
|---|---|---|
| `verify_command` | auto-detected (`npm test`, `pytest`, `cargo test`, `go test`) | Command that decides pass or fail |
| `verify_timeout_sec` | 300 | Verify timeout; a timeout counts as a failure |
| `max_fix_attempts` | 3 | Failed verifications before automatic rollback |
| `retention_count` | 10 | Newest non-LKG checkpoints kept, plus the LKG |
| `exclude_patterns` | built-in + `.gitignore` | Extra paths excluded from snapshots |
| `auto_rollback` | true | Roll back automatically after the fix limit |
| `require_confirm_for_manual_rollback` | false | Require approval for manual rollback to a non-LKG target |
| `warn_archive_bytes` | 100 MB | Archive snapshot size warning threshold |
| `max_archive_bytes` | 500 MB | Archive snapshot size refusal threshold |

## State files

All state lives inside the project, in `.hermes-checkpoint/`:

| File | Contents |
|---|---|
| `state.json` | Checkpoint records (`id` as `cp-<task>-<number>`, `label`, `created_at` in ISO 8601, `backend`, `ref`, `files_changed` as relative paths only, `verify_command`, `verify_status` as `pending`/`passed`/`failed`, `is_lkg`, `task`, `session`), plus `active_checkpoint_id`, `rollback_count`, `emergency` |
| `config.json` | The table above |
| `audit.jsonl` | One JSON line per event (`snapshot`, `verify`, `promote`, `rollback`, `cleanup`). Paths only, never file contents or full diffs |
| `lock` | Held from snapshot until promotion or rollback (atomic creation, so concurrent tasks cannot race) |

Checkpoints are bound to their project directory (fingerprint in state).
The plugin refuses to operate on another project's checkpoints, and
rejects path traversal (`../`) in tool arguments.

## Projects without git

When the project is not a git repository, the plugin refuses, unless
you pass `allow_archive=true`. Archive mode writes a compressed `.tgz`
snapshot that excludes `.git`, `node_modules/`, build directories,
secret-like files, and `.gitignore` patterns. It warns above 100 MB
and refuses above 500 MB compressed (both configurable).

## Backends

The plugin loads in-process into the agent, so its git and verify
subprocesses run in the agent host process, not inside the execution
backend:

- **Local and Docker with a bind-mounted working directory:** host and
  backend see the same files, so snapshots are consistent. This is the
  safe case.
- **SSH and Daytona:** agent edits land in the remote environment and
  reach the host only through file sync. A snapshot or rollback taken
  on the host can lag behind unsynced backend edits, and the
  `.hermes-checkpoint` state itself is subject to sync timing. Before
  trusting a verify or rollback on these backends, confirm the host
  copy is synced, and pass the project directory explicitly instead of
  relying on the host working directory.
- **Git availability** depends on the host or sandbox image and is not
  guaranteed on any backend. When git is missing and archive mode is
  not approved, the plugin refuses with a clear message.

## Rollback lessons and memory

Hermes exposes no plugin-facing memory-write API: the memory-write path
belongs to the agent's `memory` tool and is approval-gated. So the
plugin does not write memories itself. Instead:

- Every rollback result carries a `memory_note`: one paragraph with the
  task, the target checkpoint, and the reason.
- `checkpoint_lessons` aggregates the audit log into a `memory_text`
  (total rollbacks, counts by reason, most recent target).

Record either with the Hermes memory tool so the failure pattern is not
repeated. Never write into `memories/MEMORY.md` directly: that bypasses
the approval gate, and the plugin deliberately has no memory-write path
of its own. Likewise, there is no plugin-to-dashboard channel:
`ctx.emit` reaches plugin subscribers only, so dashboard-visible status
is the tool result text in the session transcript, plus the plugin
events for any subscriber that listens.

## Security

- Secret-like files (`.env*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`,
  `id_rsa*`, `id_ed25519*`, `*credentials*`) are never snapshotted,
  logged, or reported. If such a file shows up in `git status` and is
  not gitignored, the snapshot is refused: gitignore it first.
- `.env.example` is exempt: it is the conventional committed template
  (placeholders, not values).
- Files already committed that match secret-like names do not block
  snapshots (they are already in repo history, so snapshotting adds no
  new exposure), but the create result lists them under
  `tracked_secret_like` so you know they are there.
- Snapshots, logs, and reports never contain file contents or full
  diffs. Tool arguments reject path traversal (`../`).
- This is a project save point, not a backup. Retention is limited
  and local.

## Performance

Measured on the development machine, git backend, synthetic project:

| Operation | 2,500 small files |
|---|---|
| Snapshot (`checkpoint_create`) | 0.68-0.84 s |
| Rollback (`checkpoint_rollback`) | 0.06-0.08 s |

The suite includes a 2,500-file performance test that counts the
snapshot tree itself (porcelain status collapses untracked directories
and would undercount).

## Troubleshooting

- **"Another task holds the checkpoint lock."** A previous task still
  has an active checkpoint. Wait for it, or if the holder crashed,
  delete `.hermes-checkpoint/lock` yourself.
- **"Project is in emergency state after a failed rollback."** A
  rollback failed partway (locked file, conflict, missing ref). The
  plugin stopped touching the project on purpose. Inspect the state
  manually, resolve it, and clear the emergency entry in
  `.hermes-checkpoint/state.json` before creating new checkpoints.
- **"Not a git repository and archive mode was not approved."** Either
  run `git init` first or approve an archive snapshot.
- **"Refusing snapshot: secret-like files would be included."** Add the
  listed files to `.gitignore` first. Never force a snapshot past this
  refusal by editing state by hand.
- **Rollback left untracked files behind.** That is intentional: files
  created during the task that were never tracked are reported in
  `leftovers_untracked`, never deleted silently. Delete them yourself
  if you do not want them.
- **Verify passes locally but the project still misbehaves.** Check
  that the verify command tests what actually broke, and that on
  SSH/Daytona backends the host copy was synced (see
  [Backends](#backends)).

## Windows

The plugin code uses only portable standard-library modules and
list-argument subprocess calls (no shell, no POSIX-only calls), and the
concurrency lock uses atomic file creation, which works on Windows.
No live Windows run has been performed yet: Windows support is
code-reviewed, not machine-verified. Git must be on `PATH`.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

22 tests, standard library only:

- **UT-01 to UT-12** (PRD scenarios): green task promotion, 3-failure
  auto-rollback, manual rollback, no git, secret exclusion, lock,
  verify timeout, failed-rollback emergency, retention, fresh-process
  state reload, path traversal, registration mechanics.
- **Phase 3:** rollback memory notes, lessons aggregation, plugin
  events, cwd parity (explicit project paths from any working
  directory).
- **Phase 4:** 2,500-file performance, tracked-secret warnings with
  the `.env.example` exemption, plus hook tests, dry-run, and archive
  size limits.

UT-12 is partially verified: the full `hermes plugins disable` path
needs the Hermes CLI, which is not installed on the development
machine, so the suite asserts registration mechanics through a fake
context instead. Docker and Daytona backends could not be tested on
the development machine either (neither binary is installed); backend
parity is covered by the cwd-parity test and the documented caveats.

## Project status

All four planned phases are complete:

| Phase | Scope | Status |
|---|---|---|
| 1 | Skill only (procedural `SKILL.md` with raw git commands) | Done |
| 2 | Plugin tools (7 tools, 2 hooks, audit log, lock, archive backend) | Done |
| 3 | Hermes integration (memory lessons, plugin events, backend docs) | Done |
| 4 | Hardening (2,500-file tests, secret guard refinements, atomic lock, user guide) | Done |

Full documentation: `docs/USER-GUIDE.md` (install, quickstart, chat
commands, tools, configuration, backends, security, troubleshooting)
and `docs/PRD-checkpoint-rollback-hermes-en.md` (canonical spec,
including phase addenda).

## Repository layout

```
SKILL.md                  Skill: decisions, hard rules, workflow (agentskills.io)
plugin/
  plugin.yaml             Plugin manifest (name hermes-checkpoint, version 0.4.0)
  __init__.py             register(ctx): 7 tools + 2 hooks + plugin events
  checkpoint.py           Core logic, standard library only
tests/
  test_checkpoint.py      unittest suite (22 tests)
docs/
  USER-GUIDE.md           User documentation
  PRD-checkpoint-rollback-hermes-en.md   Canonical English PRD
  PRD-checkpoint-rollback-hermes-id.md   Legacy Indonesian PRD (archive)
```

## License

MIT. See `LICENSE`.
