# Savepoint

Checkpoint and rollback skill for Hermes Agent (Nous Research).

Vibe coding fails when one bad change piles fixes on top of broken code until the
project cannot return to a working state. Savepoint gives Hermes save points:
before a risky change, the last verified working state is saved. After the
change, build and tests are verified. If green, the save point is promoted. If
red repeatedly, the project is restored to the last save point with a single
command.

## Status

PRD stage. Implementation follows the PRD in `docs/`. No skill code exists yet.

## Documents

- `docs/PRD-checkpoint-rollback-hermes-en.md` — full product requirements
  document (English, canonical).
- `docs/PRD-checkpoint-rollback-hermes-id.md` — same PRD in Indonesian.

## Design in one paragraph

Two layers, matching the Hermes architecture: a procedural SKILL.md following
the agentskills.io standard for model behavior, and a deterministic plugin tool
layer via `ctx.register_tool()` for operations that must never be wrong
(snapshot, verification, rollback). Git is the primary snapshot backend, state
is stored on disk so it survives serverless backend hibernation, and the
last-known-good checkpoint is never deleted automatically.

## Planned phases

1. Skill only (SKILL.md with direct git commands).
2. Plugin tools (`checkpoint_create`, `checkpoint_verify`, `checkpoint_rollback`,
   `checkpoint_list`, `checkpoint_status`, `checkpoint_dry_run`).
3. Hermes integration (memory learning loop, dashboard status, all backends).
4. Hardening (large projects, Windows, documentation).

See the PRD for requirements, acceptance criteria, data model, and open
questions.
