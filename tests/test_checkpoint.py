"""Tests for the SnaPeka phase 2 plugin (PRD section 11, UT-01..UT-12).

Standard library only (unittest). The core module is loaded by file path,
exactly like the plugin wrapper does. A FakeCtx stands in for Hermes' ctx:
the real ``hermes plugins disable`` path (UT-12) cannot run on a machine
without the Hermes CLI, so UT-12 asserts registration mechanics instead and
is reported as partially verified.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugin"
sys.path.insert(0, str(PLUGIN_DIR))
import checkpoint as cp  # noqa: E402


def _run(cmd, cwd, **kwargs):
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, **kwargs)


class FakeCtx:
    def __init__(self):
        self.tools = {}
        self.hooks = {}

    def register_tool(self, name, toolset, schema, handler, description="", **kwargs):
        self.tools[name] = {"toolset": toolset, "schema": schema,
                            "handler": handler, "description": description}

    def register_hook(self, hook_name, callback):
        self.hooks.setdefault(hook_name, []).append(callback)


class SnapekaTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="snapeka-test-"))
        self.project = self.tmp / "proj"
        self.project.mkdir()
        _run(["git", "init", "-q"], self.project)
        _run(["git", "config", "user.email", "test@test"], self.project)
        _run(["git", "config", "user.name", "test"], self.project)
        (self.project / "app.py").write_text(
            'def greet():\n    return "hello"\n', encoding="utf-8")
        (self.project / "test_app.py").write_text(
            'from app import greet\nassert greet() == "hello"\nprint("test ok")\n',
            encoding="utf-8")
        _run(["git", "add", "-A"], self.project)
        _run(["git", "commit", "-qm", "initial known-good"], self.project)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_verify_config(self, command):
        state_dir = self.project / ".hermes-checkpoints"
        state_dir.mkdir(exist_ok=True)
        (state_dir / "config.json").write_text(
            json.dumps({"verify_command": command}), encoding="utf-8")

    def _break(self):
        (self.project / "app.py").write_text(
            'def greet():\n    return "broken"\n', encoding="utf-8")

    # UT-01: green task -> snapshot, promotion to LKG, short report
    def test_ut01_green_task_promotes_to_lkg(self):
        self._write_verify_config("python3 test_app.py")
        created = cp.checkpoint_create(str(self.project), task="refactor-auth")
        self.assertTrue(created["ok"])
        result = cp.checkpoint_verify(str(self.project))
        self.assertTrue(result["passed"])
        self.assertEqual(result["promoted_to_lkg"], created["checkpoint_id"])
        status = cp.checkpoint_status(str(self.project))
        self.assertEqual(status["lkg_id"], created["checkpoint_id"])
        self.assertIsNone(status["active_checkpoint_id"])

    # UT-02: 3 failed verifies -> automatic rollback, tree healthy again
    def test_ut02_three_failures_trigger_auto_rollback(self):
        self._write_verify_config("python3 test_app.py")
        created = cp.checkpoint_create(str(self.project), task="refactor-auth")
        self.assertTrue(created["ok"])
        self._break()
        for _ in range(2):
            result = cp.checkpoint_verify(str(self.project))
            self.assertFalse(result["passed"])
            self.assertNotIn("auto_rollback", result)
        result = cp.checkpoint_verify(str(self.project))
        self.assertFalse(result["passed"])
        self.assertTrue(result.get("auto_rollback"))
        self.assertEqual(result["rolled_back_to"], created["checkpoint_id"])
        self.assertEqual((self.project / "app.py").read_text(encoding="utf-8"),
                         'def greet():\n    return "hello"\n')
        status = cp.checkpoint_status(str(self.project))
        self.assertEqual(status["rollback_count"], 1)

    # UT-03: manual rollback in one command
    def test_ut03_manual_rollback(self):
        self._write_verify_config("python3 test_app.py")
        created = cp.checkpoint_create(str(self.project), task="feature-x")
        cp.checkpoint_verify(str(self.project))
        self._break()
        result = cp.checkpoint_rollback(str(self.project))
        self.assertTrue(result["ok"])
        self.assertEqual(result["rolled_back_to"], created["checkpoint_id"])
        self.assertIn("hello", (self.project / "app.py").read_text(encoding="utf-8"))

    # UT-04: project without git refuses, or archives when approved
    def test_ut04_no_git_project(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        (plain / "notes.txt").write_text("hello", encoding="utf-8")
        with self.assertRaises(cp.CheckpointError):
            cp.checkpoint_create(str(plain), task="no-git")
        created = cp.checkpoint_create(str(plain), task="no-git", allow_archive=True)
        self.assertTrue(created["ok"])
        self.assertEqual(created["backend"], "archive")
        self.assertTrue(Path(created["ref"]).is_file())

    # UT-05: dummy .env never enters any snapshot
    def test_ut05_env_file_excluded(self):
        (self.project / ".env").write_text("API_KEY=dummy", encoding="utf-8")
        with self.assertRaises(cp.CheckpointError):
            cp.checkpoint_create(str(self.project), task="with-env")
        (self.project / ".gitignore").write_text(".env\n", encoding="utf-8")
        created = cp.checkpoint_create(str(self.project), task="with-env")
        self.assertTrue(created["ok"])
        listing = _run(["git", "show", "--name-only", "--pretty=", created["ref"]],
                       self.project)
        self.assertNotIn(".env", listing.stdout.split())

    # UT-06: a second parallel task is rejected, never overwrites
    def test_ut06_parallel_task_rejected_by_lock(self):
        first = cp.checkpoint_create(str(self.project), task="task-one", session="s1")
        self.assertTrue(first["ok"])
        with self.assertRaises(cp.CheckpointError):
            cp.checkpoint_create(str(self.project), task="task-two", session="s2")
        status = cp.checkpoint_status(str(self.project))
        self.assertEqual(status["active_checkpoint_id"], first["checkpoint_id"])

    # UT-07: verify timeout is a clear failure, counted, never hangs
    def test_ut07_verify_timeout(self):
        self._write_verify_config("sleep 10")
        state_dir = self.project / ".hermes-checkpoints"
        config = json.loads((state_dir / "config.json").read_text(encoding="utf-8"))
        config["verify_timeout_sec"] = 1
        (state_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
        created = cp.checkpoint_create(str(self.project), task="slow")
        self.assertTrue(created["ok"])
        result = cp.checkpoint_verify(str(self.project))
        self.assertFalse(result["passed"])
        self.assertIn("timed out", result["error_tail"][0])
        self.assertEqual(result["failures"], 1)

    # UT-08: rollback failure stops safely and records an emergency.
    # Deleting refs alone does not break a reset (objects survive), so the
    # failure is forced by corrupting the LKG ref inside state.json.
    def test_ut08_failed_rollback_goes_to_emergency(self):
        self._write_verify_config("python3 test_app.py")
        created = cp.checkpoint_create(str(self.project), task="doomed")
        cp.checkpoint_verify(str(self.project))
        state_path = self.project / ".hermes-checkpoints" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        for record in state["checkpoints"]:
            if record["id"] == created["checkpoint_id"]:
                record["ref"] = "0" * 40
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(cp.CheckpointError):
            cp.checkpoint_rollback(str(self.project))
        status = cp.checkpoint_status(str(self.project))
        self.assertIsNotNone(status["emergency"])
        with self.assertRaises(cp.CheckpointError):
            cp.checkpoint_create(str(self.project), task="after-emergency")

    # UT-09: after 12 checkpoints, only 10 newest non-LKG plus the LKG remain
    def test_ut09_retention(self):
        self._write_verify_config("true")
        for i in range(12):
            (self.project / "app.py").write_text(f"# change {i}\n", encoding="utf-8")
            created = cp.checkpoint_create(str(self.project), task=f"task-{i}")
            self.assertTrue(created["ok"])
            result = cp.checkpoint_verify(str(self.project))
            self.assertTrue(result["passed"])
        status = cp.checkpoint_status(str(self.project))
        self.assertEqual(status["checkpoint_count"], 11)
        listing = cp.checkpoint_list(str(self.project))
        lkg = [c for c in listing["checkpoints"] if c["is_lkg"]]
        self.assertEqual(len(lkg), 1)

    # UT-10: a fresh process reads the same on-disk state (hibernation resume)
    def test_ut10_fresh_process_reads_state(self):
        self._write_verify_config("python3 test_app.py")
        created = cp.checkpoint_create(str(self.project), task="persist")
        cp.checkpoint_verify(str(self.project))
        code = (
            "import sys, json; sys.path.insert(0, %r); "
            "import checkpoint as c; "
            "print(json.dumps(c.checkpoint_status(%r)))" % (str(PLUGIN_DIR), str(self.project))
        )
        result = _run([sys.executable, "-c", code], self.tmp)
        self.assertEqual(result.returncode, 0, result.stderr)
        status = json.loads(result.stdout.strip())
        self.assertEqual(status["lkg_id"], created["checkpoint_id"])

    # UT-11: path traversal arguments are rejected
    def test_ut11_path_traversal_rejected(self):
        cp.checkpoint_create(str(self.project), task="guarded")
        cp.checkpoint_verify(str(self.project))
        with self.assertRaises(cp.CheckpointError):
            cp.checkpoint_rollback(str(self.project), target="../other")
        with self.assertRaises(cp.CheckpointError):
            cp.checkpoint_rollback(str(self.project), target="a/b")

    # UT-12 (partial): registration mechanics via a fake ctx.
    # The real `hermes plugins disable` path needs the Hermes CLI, which is
    # not installed on this machine, so full CLI disable is NOT verified here.
    def test_ut12_registration_mechanics(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "snapeka_plugin", PLUGIN_DIR / "__init__.py")
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        ctx = FakeCtx()
        plugin.register(ctx)
        for name in ("checkpoint_create", "checkpoint_verify", "checkpoint_rollback",
                     "checkpoint_list", "checkpoint_status", "checkpoint_dry_run"):
            self.assertIn(name, ctx.tools)
            self.assertEqual(ctx.tools[name]["toolset"], "checkpoint")
        self.assertIn("pre_tool_call", ctx.hooks)
        self.assertIn("transform_llm_output", ctx.hooks)
        # Simulate disable: dropping the registrations leaves nothing callable.
        ctx.tools.clear()
        ctx.hooks.clear()
        self.assertEqual(ctx.tools, {})
        self.assertEqual(ctx.hooks, {})

    def test_dry_run_changes_nothing(self):
        result = cp.checkpoint_dry_run(str(self.project), action="snapshot")
        self.assertTrue(result["ok"])
        self.assertFalse((self.project / ".hermes-checkpoints").exists())

    def test_archive_size_limit(self):
        plain = self.tmp / "plain2"
        plain.mkdir()
        (plain / "data.bin").write_bytes(b"x" * 1000)
        state_dir = plain / ".hermes-checkpoints"
        state_dir.mkdir(exist_ok=True)
        (state_dir / "config.json").write_text(
            json.dumps({"max_archive_bytes": 100}), encoding="utf-8")
        with self.assertRaises(cp.CheckpointError):
            cp.checkpoint_create(str(plain), task="too-big", allow_archive=True)

    def test_pre_tool_call_hook(self):
        # No state dir yet: writes pass through (opt-in by use).
        target = self.project / "app.py"
        self.assertIsNone(cp.pre_tool_call_hook("write_file", {"file_path": str(target)}))
        # Active checkpoint present: writes are covered by the snapshot.
        self._write_verify_config("python3 test_app.py")
        cp.checkpoint_create(str(self.project), task="guarded")
        self.assertIsNone(cp.pre_tool_call_hook("write_file", {"file_path": str(target)}))
        # Promoted (no active checkpoint) but state exists: writes blocked.
        cp.checkpoint_verify(str(self.project))
        blocked = cp.pre_tool_call_hook("write_file", {"file_path": str(target)})
        self.assertIsNotNone(blocked)
        self.assertEqual(blocked["action"], "block")
        # New active checkpoint: writes pass again.
        created = cp.checkpoint_create(str(self.project), task="guarded-2")
        self.assertTrue(created["ok"])
        self.assertIsNone(cp.pre_tool_call_hook("write_file", {"file_path": str(target)}))
        # Read-only tools and garbage never raise and never block.
        self.assertIsNone(cp.pre_tool_call_hook("read_file", {"file_path": str(target)}))
        self.assertIsNone(cp.pre_tool_call_hook("write_file", None))
        self.assertIsNone(cp.pre_tool_call_hook("", {}))

    def test_transform_llm_output_hook(self):
        text = 'api_key = "sk-secret-123"\nnormal line\n'
        redacted = cp.transform_llm_output_hook(text)
        self.assertIsNotNone(redacted)
        self.assertNotIn("sk-secret-123", redacted)
        self.assertIn("[REDACTED]", redacted)
        self.assertIsNone(cp.transform_llm_output_hook("nothing secret here\n"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
