# Hermes Checkpoint (SnaPeka)

Checkpoint and rollback skill + plugin for Hermes Agent (Nous Research).

SnaPeka gives Hermes a safety net for vibe coding: before a risky change,
the project state is snapshotted. After the change, a verify command runs.
When it passes, the snapshot becomes the new last-known-good state (LKG).
When fixes fail repeatedly, the project rolls back to the LKG in one
command. All project artifacts in this repository are written in English.

## Status: phase 2 complete

- **Phase 1 (skill only):** procedural `SKILL.md` using raw git commands.
  Done and tested on clean trees, dirty trees, and the secret guard.
- **Phase 2 (plugin tools):** deterministic plugin with six tools, a
  `pre_tool_call` guard, secret redaction, on-disk state, a concurrency
  lock, and an archive backend for projects without git. All 16 tests
  pass (see `tests/`).
- **Phase 3 (Hermes integration):** rollback notes into Hermes memory,
  dashboard status, backend matrix testing. Not started.
- **Phase 4 (hardening):** large-project tests, Windows tests, user docs.
  Not started.

## Layout

```
SKILL.md                  Skill: decisions, hard rules, workflow (agentskills.io)
plugin/
  plugin.yaml             Plugin manifest (name snapeka, version 0.2.0)
  __init__.py             register(ctx): 6 tools + 2 hooks
  checkpoint.py           Core logic, standard library only
tests/
  test_checkpoint.py      unittest suite for PRD scenarios UT-01..UT-12
docs/
  PRD-checkpoint-rollback-hermes-en.md   Canonical English PRD
  PRD-checkpoint-rollback-hermes-id.md   Legacy Indonesian PRD (archive)
```

## Install

```bash
# Skill (directory name must match the frontmatter name: snapeka)
cp -r . ~/.hermes/skills/snapeka

# Plugin
cp -r plugin ~/.hermes/plugins/snapeka
hermes plugins enable snapeka
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
| `checkpoint_dry_run` | Preview a snapshot or rollback without executing |

State lives in the project, in `.hermes-checkpoints/` (`state.json`,
`config.json`, `audit.jsonl`, `lock`). Snapshots are commits on dedicated
refs (`refs/snapeka/<id>`) so the user's working branch history is
untouched; the LKG is additionally tracked at `refs/snapeka/lkg`. The
archive backend (projects without git) writes a `.tgz` snapshot with a
warning above 100 MB and a refusal above 500 MB compressed (both
configurable).

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
