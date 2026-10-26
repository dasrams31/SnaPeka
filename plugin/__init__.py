"""SnaPeka plugin for Hermes Agent: deterministic checkpoint tools.

Registered tools (toolset ``checkpoint``):

* ``checkpoint_create``   - snapshot before a risky change
* ``checkpoint_verify``   - run the verify command; pass promotes to LKG,
  ``max_fix_attempts`` failures trigger automatic rollback
* ``checkpoint_rollback`` - restore the last-known-good checkpoint
* ``checkpoint_list``     - list checkpoints and the LKG marker
* ``checkpoint_status``   - active checkpoint, LKG, last verify, rollbacks
* ``checkpoint_dry_run``  - preview a snapshot or rollback without executing

Registered hooks:

* ``pre_tool_call``       - blocks file writes in SnaPeka projects that have
  no active checkpoint (fail-open: anything unexpected passes through)
* ``transform_llm_output``- redacts assignment-style secrets from replies

The plugin loads its core module by file path so it works whether Hermes
imports this directory as a package or loads ``__init__.py`` directly.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DIR = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location("snapeka_checkpoint", _DIR / "checkpoint.py")
_core = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_core)

CheckpointError = _core.CheckpointError

_TOOL_SPECS = (
    ("checkpoint_create",
     {"type": "object",
      "properties": {
          "task": {"type": "string", "description": "Short task description, for example 'refactor-auth'."},
          "label": {"type": "string", "description": "Human label for the checkpoint (optional)."},
          "project_dir": {"type": "string", "description": "Project directory (optional, defaults to the working directory)."},
          "session": {"type": "string", "description": "Hermes session id (optional)."},
          "allow_archive": {"type": "boolean", "description": "Allow the tar.gz archive backend for projects without git."},
      },
      "required": ["task"]},
     lambda args: _core.checkpoint_create(
         project_dir=args.get("project_dir"), task=args["task"], label=args.get("label"),
         session=args.get("session", "default"), allow_archive=bool(args.get("allow_archive", False))),
     "Create a checkpoint snapshot before a risky change."),
    ("checkpoint_verify",
     {"type": "object",
      "properties": {
          "command": {"type": "string", "description": "Verify command override (optional, defaults to project config or auto-detection)."},
          "project_dir": {"type": "string", "description": "Project directory (optional)."},
          "session": {"type": "string", "description": "Hermes session id (optional)."},
      }},
     lambda args: _core.checkpoint_verify(
         project_dir=args.get("project_dir"), command=args.get("command"),
         session=args.get("session", "default")),
     "Run the verify command. A pass promotes the active checkpoint to LKG; repeated failures trigger automatic rollback."),
    ("checkpoint_rollback",
     {"type": "object",
      "properties": {
          "target": {"type": "string", "description": "Checkpoint id or label (optional, defaults to the last-known-good checkpoint)."},
          "project_dir": {"type": "string", "description": "Project directory (optional)."},
          "session": {"type": "string", "description": "Hermes session id (optional)."},
      }},
     lambda args: _core.checkpoint_rollback(
         project_dir=args.get("project_dir"), target=args.get("target"),
         session=args.get("session", "default")),
     "Restore the project to the last-known-good checkpoint (or a given checkpoint)."),
    ("checkpoint_list",
     {"type": "object",
      "properties": {
          "project_dir": {"type": "string", "description": "Project directory (optional)."},
      }},
     lambda args: _core.checkpoint_list(project_dir=args.get("project_dir")),
     "List checkpoints with verify status and the LKG marker."),
    ("checkpoint_status",
     {"type": "object",
      "properties": {
          "project_dir": {"type": "string", "description": "Project directory (optional)."},
      }},
     lambda args: _core.checkpoint_status(project_dir=args.get("project_dir")),
     "Show the active checkpoint, the LKG, the last verify status, and the rollback count."),
    ("checkpoint_dry_run",
     {"type": "object",
      "properties": {
          "action": {"type": "string", "enum": ["snapshot", "rollback"], "description": "What to preview."},
          "project_dir": {"type": "string", "description": "Project directory (optional)."},
      },
      "required": ["action"]},
     lambda args: _core.checkpoint_dry_run(
         project_dir=args.get("project_dir"), action=args["action"]),
     "Preview a snapshot or rollback without changing any file."),
)


def _wrap(handler):
    """Convert core results to tool results; errors become structured errors."""
    def wrapper(args=None, **kwargs):
        merged = dict(args or {})
        merged.update(kwargs)
        try:
            return handler(merged)
        except CheckpointError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # never break the turn on an unexpected error
            logger.debug("snapeka tool failed: %s", exc)
            return {"ok": False, "error": f"unexpected error: {exc}"}
    return wrapper


def register(ctx) -> None:
    """Register SnaPeka tools and hooks (called once by the plugin loader)."""
    for name, schema, handler, description in _TOOL_SPECS:
        ctx.register_tool(name=name, toolset="checkpoint", schema=schema,
                          handler=_wrap(handler), description=description)
    ctx.register_hook("pre_tool_call", _core.pre_tool_call_hook)
    ctx.register_hook("transform_llm_output", _core.transform_llm_output_hook)
