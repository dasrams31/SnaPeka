# Hermes Checkpoint user guide

Checkpoint and rollback for Hermes Agent. This guide covers installation,
everyday use, configuration, and troubleshooting. For the rules the agent
follows, see `SKILL.md`. For the full specification, see
`docs/PRD-checkpoint-rollback-hermes-en.md`.

## Install

Requirements: Python 3.10 or newer, and git for the primary backend
(Linux and macOS supported; Windows compatibility is code-reviewed, see
the Windows note below).

```bash
# Skill (the directory name must match the frontmatter name)
cp -r . ~/.hermes/skills/hermes-checkpoint

# Plugin
cp -r plugin ~/.hermes/plugins/hermes-checkpoint
hermes plugins enable hermes-checkpoint
```

No third-party dependencies: the plugin uses the Python standard library
only.

## Quickstart

Hermes Checkpoint activates for risky work: refactors, migrations,
dependency swaps, and multi-file changes. The flow is always the same:

1. **Snapshot before changing anything.** The agent calls
   `checkpoint_create` with a short task name, for example
   `refactor-auth`. This records the pre-task state on a dedicated ref
   (`refs/hermes-checkpoint/<id>`) without touching your working branch
   history. A clean tree needs no new commit: the snapshot ref is the
   current HEAD.
2. **Work.** Change only the files in scope.
3. **Verify.** The agent calls `checkpoint_verify`. A pass promotes the
   snapshot to the last-known-good state (LKG) and releases the lock.
   A failure counts toward the fix limit (default 3). After the third
   consecutive failure, the plugin rolls back automatically.
4. **Rollback, if needed.** One command restores the LKG:
   `/rollback`, or `/rollback <label>` for a labeled checkpoint with
   your approval. Tracked files are reset; files created during the
   task that were never tracked are listed, never deleted silently.
   The plugin verifies once more after rollback.

## Chat commands

| Command | Function |
|---|---|
| `/checkpoint` | Create a manual snapshot for the running task |
| `/checkpoint list` | Show checkpoints and the LKG |
| `/checkpoint status` | Show the active checkpoint and last verification |
| `/rollback` | Restore the project to the LKG |
| `/rollback <label>` | Restore to a labeled checkpoint (LKG or user-approved only) |

## Tools

| Tool | Purpose |
|---|---|
| `checkpoint_create` | Snapshot before a risky change (`task` required) |
| `checkpoint_verify` | Verify; pass promotes to LKG, repeated failures auto-roll back |
| `checkpoint_rollback` | Restore the LKG (or a given checkpoint) |
| `checkpoint_list` | List checkpoints and the LKG marker |
| `checkpoint_status` | Active checkpoint, LKG, last verify, rollback count |
| `checkpoint_lessons` | Rollback history for the memory tool (counts by reason, recent rollbacks) |
| `checkpoint_dry_run` | Preview a snapshot or rollback without executing |

After a rollback, record the lesson so the pattern is not repeated:
the rollback result carries a `memory_note`, and
`checkpoint_lessons` returns a `memory_text`. Record either with the
Hermes memory tool (approval-gated). The plugin never writes memories
itself, and never write into `memories/MEMORY.md` directly.

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

State lives in the project, in `.hermes-checkpoint/`:

- `state.json`: checkpoint records, active checkpoint, rollback count,
  emergency state.
- `config.json`: the table above.
- `audit.jsonl`: one JSON line per event (`snapshot`, `verify`,
  `promote`, `rollback`, `cleanup`). Paths only, never file contents.
- `lock`: held from snapshot until promotion or rollback, so a second
  parallel task on the same project is rejected.

## Projects without git

When the project is not a git repository, the plugin refuses, unless
you pass `allow_archive=true`. Archive mode writes a compressed `.tgz`
snapshot that excludes `.git`, `node_modules/`, build directories,
secret-like files, and `.gitignore` patterns. It warns above 100 MB
and refuses above 500 MB compressed (both configurable).

## Backends

The plugin runs in-process in the agent host, not inside the execution
backend. Local and Docker setups with a bind-mounted working directory
see the same files, so snapshots are consistent. On SSH and Daytona,
agent edits reach the host only through file sync: confirm the host
copy is synced before trusting a verify or rollback, and pass the
project directory explicitly. `checkpoint_status` returns the resolved
`project_dir` for comparison with the backend working directory. Git
availability depends on the host or sandbox image on every backend.

## Security notes

- Secret-like files (`.env*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`,
  `id_rsa*`, `id_ed25519*`, `*credentials*`) are never snapshotted,
  logged, or reported. If such a file shows up in `git status` and is
  not gitignored, the snapshot is refused: gitignore it first.
- `.env.example` is exempt: it is the conventional committed template
  (placeholders, not values).
- Files already committed that match secret-like names do not block
  snapshots (they are already in repo history), but the create result
  lists them under `tracked_secret_like` so you know they are there.
- Snapshots, logs, and reports never contain file contents or full
  diffs. Tool arguments reject path traversal (`../`).
- This is a project save point, not a backup. Retention is limited
  and local.

## Troubleshooting

- **"Another task holds the checkpoint lock."** A previous task still
  has an active checkpoint. Wait for it, or if the holder crashed,
  delete `.hermes-checkpoint/lock` yourself.
- **"Project is in emergency state after a failed rollback."** A
  rollback failed partway (locked file, conflict, missing ref). The
  plugin stopped touching the project on purpose. Inspect the state
  manually, resolve it, and clear the emergency entry in
  `.hermes-checkpoint/state.json` before creating new checkpoints.
- **"Not a git repository and archive mode was not approved."**
  Either run `git init` first or approve an archive snapshot.
- **"Refusing snapshot: secret-like files would be included."** Add
  the listed files to `.gitignore` first. Never force a snapshot past
  this refusal by editing state by hand.
- **Rollback left untracked files behind.** That is intentional: files
  created during the task that were never tracked are reported in
  `leftovers_untracked`, never deleted silently. Delete them yourself
  if you do not want them.
- **Verify passes locally but the project still misbehaves.** Check
  that the verify command tests what actually broke, and that on
  SSH/Daytona backends the host copy was synced (see Backends above).

## Windows note

The plugin code uses only portable standard-library modules and list
argument subprocess calls (no shell, no POSIX-only calls), and the
concurrency lock uses atomic file creation, which works on Windows.
No live Windows run has been performed yet: Windows support is
code-reviewed, not machine-verified. Git must be on `PATH`.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Coverage: UT-01 through UT-12 (green task, auto-rollback, manual
rollback, no git, secret exclusion, lock, verify timeout, failed
rollback emergency, retention, fresh-process reload, path traversal,
registration mechanics), plus hook tests, dry-run, archive limits,
rollback lessons, plugin events, cwd parity, a 2,500-file performance
test, and tracked-secret warnings. UT-12 is partially verified: the
full `hermes plugins disable` path needs the Hermes CLI, which is not
installed on the development machine.
