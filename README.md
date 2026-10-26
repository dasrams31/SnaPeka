# Hermes Checkpoint

Checkpoint and rollback skill + plugin for Hermes Agent (Nous Research).

Hermes Checkpoint gives Hermes a safety net for vibe coding: before a risky change,
the project state is snapshotted. After the change, a verify command runs.
When it passes, the snapshot becomes the new last-known-good state (LKG).
When fixes fail repeatedly, the project rolls back to the LKG in one
command. All project artifacts in this repository are written in English.

## Status: phase 3 complete

- **Phase 1 (skill only):** procedural `SKILL.md` using raw git commands.
  Done and tested on clean trees, dirty trees, and the secret guard.
- **Phase 2 (plugin tools):** deterministic plugin with six tools, a
  `pre_tool_call` guard, secret redaction, on-disk state, a concurrency
  lock, and an archive backend for projects without git. All 16 tests
  passed at the time.
- **Phase 3 (Hermes integration):** done, with the platform limits
  documented honestly below. Rollback results carry a ready-to-record
  `memory_note`, a new `checkpoint_lessons` tool aggregates rollback
  history for the Hermes memory tool, tool calls emit plugin events
  (`checkpoint_created`, `checkpoint_promoted`,
  `checkpoint_rolled_back`), and backend behavior (Docker/SSH/Daytona)
  is documented in `SKILL.md` with a cwd-parity test. 20 tests pass.
- **Phase 4 (hardening):** large-project tests, Windows tests, user docs.
  Not started.

## Layout

```
SKILL.md                  Skill: decisions, hard rules, workflow (agentskills.io)
plugin/
  plugin.yaml             Plugin manifest (name hermes-checkpoint, version 0.3.0)
  __init__.py             register(ctx): 7 tools + 2 hooks + plugin events
  checkpoint.py           Core logic, standard library only
tests/
  test_checkpoint.py      unittest suite for PRD scenarios UT-01..UT-12
docs/
  PRD-checkpoint-rollback-hermes-en.md   Canonical English PRD
  PRD-checkpoint-rollback-hermes-id.md   Legacy Indonesian PRD (archive)
```

## Install

```bash
# Skill (directory name must match the frontmatter name: hermes-checkpoint)
cp -r . ~/.hermes/skills/hermes-checkpoint

# Plugin
cp -r plugin ~/.hermes/plugins/hermes-checkpoint
hermes plugins enable hermes-checkpoint
```

No dependencies beyond Python 3.10+ and git (for the primary backend).

## Tools (toolset `checkpoint`)

| Tool | Purpose |
|---|---|
| `checkpoint_create` | Snapshot before a risky change |
| `checkpoint_verify` | Verify; pass promotes to LKG, repeated failures auto-roll back |
| `checkpoint_rollback` | Restore the LKG (or a given checkpoint) |
| `checkpoint_list` | List checkpoints and the LKG marker |
| `checkpoint_status` | Active checkpoint, LKG, last verify, rollback count |
| `checkpoint_lessons` | Rollback history aggregated for the memory tool |
| `checkpoint_dry_run` | Preview a snapshot or rollback without executing |

State lives in the project, in `.hermes-checkpoint/` (`state.json`,
`config.json`, `audit.jsonl`, `lock`). Snapshots are commits on dedicated
refs (`refs/hermes-checkpoint/<id>`) so the user's working branch history is
untouched; the LKG is additionally tracked at `refs/hermes-checkpoint/lkg`. The
archive backend (projects without git) writes a `.tgz` snapshot with a
warning above 100 MB and a refusal above 500 MB compressed (both
configurable).

## Phase 3 notes: what the platform does and does not offer

Verified against the local Hermes Agent checkout before building:

- **No plugin-facing memory-write API exists.** `PluginContext`
  exposes tools, hooks, events, and state, but no memory method, and
  `register_memory_provider` is inert. The real memory-write path
  belongs to the agent (the `memory` toolset, approval-gated). So the
  plugin does not write memories itself: rollback results carry a
  `memory_note` and `checkpoint_lessons` returns a `memory_text`, and
  the agent records them. Writing directly into `memories/MEMORY.md`
  would bypass the approval gate and is deliberately refused.
- **No plugin-to-dashboard channel exists.** `ctx.emit` reaches plugin
  subscribers only; `gateway_platform_event` is an inbound observer
  hook, not an outbound status API. Dashboard-visible status is the
  tool result text in the session transcript, plus the plugin events
  for any subscriber that listens.
- **Backends host terminal commands, not plugin code.** The plugin
  runs in-process in the agent host. Docker with a bind-mounted working
  directory is consistent; SSH and Daytona deliver agent edits through
  file sync, so host-side snapshots can lag unsynced backend edits.
  This is documented in `SKILL.md` under Backend behavior.
- **Docker and Daytona could not be tested on the development machine**
  (neither binary is installed); SSH is testable only in principle
  (it needs a reachable sshd plus keys, not attempted). Backend parity
  is therefore covered by the cwd-parity test and the documented
  caveats, not by live backend runs.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Coverage: UT-01 (green task), UT-02 (3 failures auto-rollback), UT-03
(manual rollback), UT-04 (no git), UT-05 (secret exclusion), UT-06
(lock), UT-07 (verify timeout), UT-08 (failed rollback emergency),
UT-09 (retention), UT-10 (fresh-process state reload), UT-11 (path
traversal), UT-12 (registration mechanics). UT-12 is partially verified:
the full `hermes plugins disable` path needs the Hermes CLI, which is not
installed on the development machine, so the suite asserts registration
mechanics through a fake context instead.

## License

MIT. See `LICENSE`.
