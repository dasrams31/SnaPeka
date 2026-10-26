"""Hermes Checkpoint checkpoint core: deterministic snapshot, verify, and rollback.

Standard library only. No network, no third-party imports, no Hermes imports:
the plugin wrapper (``__init__.py``) adapts these functions to ``ctx``.

Design notes (see the English PRD for the full specification):

* Git is the primary snapshot backend. A snapshot commit is created with
  plumbing (``write-tree`` + ``commit-tree``) on a dedicated ref
  (``refs/hermes-checkpoint/<id>``) so the user's working branch history is untouched.
  A clean tree needs no new commit: the snapshot ref is the HEAD hash.
* Projects without git use a tar.gz archive backend, only with explicit
  user approval (``allow_archive=True``).
* All state lives on disk in ``.hermes-checkpoint/`` so a new process
  (for example after backend hibernation) resumes from the same state.
* Tool results never contain file contents, only paths and counts.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import subprocess
import tarfile
import time
from pathlib import Path

STATE_DIRNAME = ".hermes-checkpoint"
STATE_FILENAME = "state.json"
CONFIG_FILENAME = "config.json"
AUDIT_FILENAME = "audit.jsonl"
LOCK_FILENAME = "lock"
REF_PREFIX = "refs/hermes-checkpoint/"
LKG_REF = "refs/hermes-checkpoint/lkg"

DEFAULT_CONFIG = {
    "verify_command": None,  # None means auto-detect
    "verify_timeout_sec": 300,
    "max_fix_attempts": 3,
    "retention_count": 10,
    "exclude_patterns": [],
    "auto_rollback": True,
    "require_confirm_for_manual_rollback": False,
    "max_archive_bytes": 524288000,  # 500 MB compressed archive
    "warn_archive_bytes": 104857600,  # 100 MB compressed archive
    "enforced_write_tools": ["write_file", "patch", "terminal"],
}

# Basename patterns that must never enter a snapshot. A match aborts the
# snapshot with a message telling the user to gitignore the file first.
SECRET_BASENAME_PATTERNS = (
    ".env*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    "*credentials*",
)

# Directories that are never archived, even for the archive backend.
ARCHIVE_ALWAYS_EXCLUDE = {
    ".git",
    ".hermes-checkpoint",
    "node_modules",
    "dist",
    "build",
    "target",
    "out",
    ".next",
    "__pycache__",
}

# Assignment-style secret values redacted from LLM output by the hook.
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|passwd|auth[_-]?token)\b\s*=\s*"
    r"(\"[^\"]*\"|'[^']*'|\S+)"
)

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class CheckpointError(Exception):
    """A structured, user-facing checkpoint failure."""


# ---------------------------------------------------------------------------
# Paths, state, config, audit
# ---------------------------------------------------------------------------

def _project_dir(project_dir: str | None) -> Path:
    path = Path(project_dir).expanduser().resolve() if project_dir else Path.cwd().resolve()
    if not path.is_dir():
        raise CheckpointError(f"project directory does not exist: {path}")
    return path


def _state_dir(project: Path) -> Path:
    return project / STATE_DIRNAME


def _fingerprint(project: Path) -> str:
    return hashlib.sha256(str(project).encode("utf-8")).hexdigest()[:16]


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def load_config(project: Path) -> dict:
    config = dict(DEFAULT_CONFIG)
    stored = _load_json(_state_dir(project) / CONFIG_FILENAME, {})
    if isinstance(stored, dict):
        config.update(stored)
    return config


def load_state(project: Path, create_dirs: bool = False) -> dict:
    """Load state without creating anything (dry-run safe) unless asked."""
    state_path = _state_dir(project) / STATE_FILENAME
    state = _load_json(state_path, None)
    if state is None:
        state = {
            "version": 1,
            "project": str(project),
            "project_fingerprint": _fingerprint(project),
            "checkpoints": [],
            "active_checkpoint_id": None,
            "rollback_count": 0,
            "emergency": None,
        }
        if create_dirs:
            save_state(project, state)
    if state.get("project_fingerprint") != _fingerprint(project):
        raise CheckpointError(
            "checkpoint state belongs to a different project directory; "
            "refusing to operate across projects"
        )
    return state


def save_state(project: Path, state: dict) -> None:
    _save_json(_state_dir(project) / STATE_FILENAME, state)


def audit(project: Path, event: str, checkpoint_id, result: dict) -> None:
    line = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": event,
        "checkpoint_id": checkpoint_id,
        "result": result,
    }
    audit_path = _state_dir(project) / AUDIT_FILENAME
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, sort_keys=True) + "\n")


def _read_checkpointignore(project: Path) -> list[str]:
    path = project / ".checkpointignore"
    if not path.is_file():
        return []
    patterns = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def _exclude_patterns(project: Path, config: dict) -> list[str]:
    patterns = list(config.get("exclude_patterns") or [])
    patterns.extend(_read_checkpointignore(project))
    return patterns


def _is_secret_basename(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in SECRET_BASENAME_PATTERNS)


def _validate_id_or_label(value: str, what: str = "target") -> str:
    if not value or "/" in value or "\\" in value or ".." in value:
        raise CheckpointError(f"invalid {what}: {value!r} (path traversal rejected)")
    return value


# ---------------------------------------------------------------------------
# Git helpers (list argv only, never shell)
# ---------------------------------------------------------------------------

def _git(project: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args],
        cwd=str(project),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if check and result.returncode != 0:
        raise CheckpointError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def _is_git_repo(project: Path) -> bool:
    result = _git(project, "rev-parse", "--is-inside-work-tree", check=False)
    return result.returncode == 0 and result.stdout.strip() == "true"


def _git_status_paths(project: Path) -> list[str]:
    """Relative paths from porcelain status (tracked changes + untracked)."""
    result = _git(project, "status", "--porcelain")
    paths = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        entry = line[3:]
        if " -> " in entry:  # rename: keep the new path
            entry = entry.split(" -> ", 1)[1]
        paths.append(entry.strip('"'))
    return paths


def _guard_secrets(project: Path) -> None:
    offenders = [p for p in _git_status_paths(project) if _is_secret_basename(Path(p).name)]
    if offenders:
        raise CheckpointError(
            "refusing snapshot: secret-like files would be included: "
            + ", ".join(offenders)
            + ". Add them to .gitignore first."
        )


def _git_snapshot_commit(project: Path, checkpoint_id: str, exclude: list[str]) -> tuple[str, list[str]]:
    """Create a snapshot commit on refs/hermes-checkpoint/<id>. Returns (ref, files)."""
    files = _git_status_paths(project)
    _guard_secrets(project)
    args = ["add", "-A", "--"]
    for pattern in exclude:
        args.append(f":!{pattern}")
    _git(project, *args)
    tree = _git(project, "write-tree").stdout.strip()
    head = _git(project, "rev-parse", "HEAD", check=False).stdout.strip()
    if head:
        head_tree = _git(project, "rev-parse", f"{head}^{{tree}}").stdout.strip()
        if head_tree == tree:
            return head, files  # clean tree: snapshot is HEAD itself
    parents = ["-p", head] if head else []
    commit = _git(
        project, "commit-tree", tree, *parents,
        "-m", f"hermes-checkpoint: {checkpoint_id}",
    ).stdout.strip()
    _git(project, "update-ref", f"{REF_PREFIX}{checkpoint_id}", commit)
    return commit, files


def _git_reset_hard(project: Path, ref: str) -> None:
    _git(project, "reset", "--hard", ref)


def _untracked_leftovers(project: Path) -> list[str]:
    result = _git(project, "status", "--porcelain")
    leftovers = []
    for line in result.stdout.splitlines():
        if line.startswith("??"):
            leftovers.append(line[3:].strip('"'))
    return leftovers


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

def detect_verify_command(project: Path) -> str | None:
    package_json = project / "package.json"
    if package_json.is_file():
        try:
            scripts = json.loads(package_json.read_text(encoding="utf-8")).get("scripts", {})
        except ValueError:
            scripts = {}
        if isinstance(scripts, dict) and scripts.get("test"):
            return "npm test"
    for marker in ("tests",):
        if (project / marker).is_dir():
            return "pytest -q"
    if list(project.glob("test_*.py")) or list(project.glob("*_test.py")) or (project / "conftest.py").is_file():
        return "pytest -q"
    if (project / "Cargo.toml").is_file():
        return "cargo test"
    if (project / "go.mod").is_file():
        return "go test"
    return None


def run_verify(project: Path, command: str, timeout_sec: int) -> dict:
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            cwd=str(project),
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        output = (result.stdout + "\n" + result.stderr).strip()
        tail = output.splitlines()[-20:] if output else []
        return {
            "passed": result.returncode == 0,
            "command": command,
            "duration_sec": round(time.monotonic() - started, 2),
            "error_tail": tail if result.returncode != 0 else [],
        }
    except subprocess.TimeoutExpired:
        return {
            "passed": False,
            "command": command,
            "duration_sec": round(time.monotonic() - started, 2),
            "error_tail": [f"verify timed out after {timeout_sec} seconds"],
        }

# ---------------------------------------------------------------------------
# Lock
# ---------------------------------------------------------------------------

def _acquire_lock(project: Path, session: str, checkpoint_id: str) -> None:
    lock_path = _state_dir(project) / LOCK_FILENAME
    if lock_path.exists():
        try:
            holder = json.loads(lock_path.read_text(encoding="utf-8"))
        except ValueError:
            holder = {}
        raise CheckpointError(
            "another task holds the checkpoint lock"
            + (f" (session {holder.get('session')}, checkpoint {holder.get('checkpoint_id')})" if holder else "")
            + ". Wait for it to finish, or delete .hermes-checkpoint/lock if the holder crashed."
        )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(
        json.dumps({"session": session, "checkpoint_id": checkpoint_id,
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}),
        encoding="utf-8",
    )


def _release_lock(project: Path) -> None:
    try:
        (_state_dir(project) / LOCK_FILENAME).unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Checkpoint records
# ---------------------------------------------------------------------------

def _next_id(state: dict, task: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", task.lower()).strip("-")[:30] or "task"
    taken = set()
    for record in state["checkpoints"]:
        record_id = record.get("id", "")
        if record_id.startswith(f"cp-{slug}-"):
            suffix = record_id.rsplit("-", 1)[-1]
            if suffix.isdigit():
                taken.add(int(suffix))
    number = (max(taken) + 1) if taken else 1
    return f"cp-{slug}-{number:03d}"


def _find_checkpoint(state: dict, target: str) -> dict | None:
    for record in state["checkpoints"]:
        if record["id"] == target:
            return record
    for record in state["checkpoints"]:
        if record.get("label") == target:
            return record
    return None


def _lkg(state: dict) -> dict | None:
    for record in state["checkpoints"]:
        if record.get("is_lkg"):
            return record
    return None


def _prune_retention(project: Path, state: dict, config: dict) -> list[str]:
    """Keep retention_count newest non-LKG checkpoints plus the LKG."""
    keep = int(config.get("retention_count", 10))
    ordered = sorted(state["checkpoints"], key=lambda r: r.get("created_at", ""), reverse=True)
    lkg = _lkg(state)
    kept_ids = set()
    non_lkg_seen = 0
    for record in ordered:
        if record is lkg:
            kept_ids.add(record["id"])
            continue
        if non_lkg_seen < keep:
            kept_ids.add(record["id"])
            non_lkg_seen += 1
    pruned = []
    remaining = []
    for record in state["checkpoints"]:
        if record["id"] in kept_ids:
            remaining.append(record)
        else:
            pruned.append(record["id"])
            if record.get("backend") == "git":
                _git(project, "update-ref", "-d", f"{REF_PREFIX}{record['id']}", check=False)
            elif record.get("backend") == "archive":
                try:
                    Path(record["ref"]).unlink()
                except OSError:
                    pass
    state["checkpoints"] = remaining
    return pruned


def _set_lkg(project: Path, state: dict, record: dict) -> None:
    for other in state["checkpoints"]:
        other["is_lkg"] = other is record
    if record.get("backend") == "git":
        _git(project, "update-ref", LKG_REF, record["ref"], check=False)


# ---------------------------------------------------------------------------
# Archive backend
# ---------------------------------------------------------------------------

def _archive_snapshot(project: Path, checkpoint_id: str, config: dict) -> str:
    exclude = set(ARCHIVE_ALWAYS_EXCLUDE)
    exclude.update(_exclude_patterns(project, config))
    archive_name = f"{checkpoint_id}.tgz"
    archive_path = _state_dir(project) / archive_name
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    warn_at = int(config.get("warn_archive_bytes", 104857600))
    max_at = int(config.get("max_archive_bytes", 524288000))

    def _skip(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
        parts = Path(tarinfo.name).parts
        if any(part in exclude for part in parts):
            return None
        if _is_secret_basename(Path(tarinfo.name).name):
            return None
        if any(fnmatch.fnmatch(tarinfo.name, pat) or fnmatch.fnmatch(Path(tarinfo.name).name, pat)
               for pat in exclude):
            return None
        return tarinfo

    with tarfile.open(archive_path, "w:gz") as tar:
        for root, dirs, files in os.walk(project):
            root_path = Path(root)
            dirs[:] = [d for d in dirs if d not in exclude and not _is_secret_basename(d)]
            for name in files:
                if _is_secret_basename(name):
                    continue
                full = root_path / name
                rel = full.relative_to(project)
                if any(part in exclude for part in rel.parts):
                    continue
                if any(fnmatch.fnmatch(str(rel), pat) or fnmatch.fnmatch(name, pat) for pat in exclude):
                    continue
                tar.add(full, arcname=str(rel), filter=_skip)
    size = archive_path.stat().st_size
    if size > max_at:
        archive_path.unlink()
        raise CheckpointError(
            f"archive snapshot rejected: {size} bytes exceeds the limit of {max_at} bytes. "
            "Use the git backend or add exclude patterns."
        )
    warning = None
    if size > warn_at:
        warning = f"archive snapshot is large: {size} bytes (warning threshold {warn_at})"
    return str(archive_path), warning


def _archive_rollback(project: Path, archive_path: str) -> list[str]:
    """Restore archived files over the project. Returns leftover paths."""
    if not Path(archive_path).is_file():
        raise CheckpointError(f"archive snapshot missing: {archive_path}")
    archived = set()
    with tarfile.open(archive_path, "r:gz") as tar:
        for member in tar.getmembers():
            if member.isfile():
                archived.add(member.name)
        tar.extractall(path=str(project), filter="data")
    leftovers = []
    for root, dirs, files in os.walk(project):
        root_path = Path(root)
        if STATE_DIRNAME in root_path.parts and root_path != project:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d != STATE_DIRNAME]
        for name in files:
            rel = str((root_path / name).relative_to(project))
            if rel not in archived:
                leftovers.append(rel)
    return sorted(leftovers)


# ---------------------------------------------------------------------------
# Public tool functions. Each returns a plain dict: {"ok": bool, ...}.
# project_dir defaults to the current working directory.
# ---------------------------------------------------------------------------

def checkpoint_create(project_dir=None, task="", label=None, session="default",
                      allow_archive=False) -> dict:
    if not task:
        raise CheckpointError("task is required")
    project = _project_dir(project_dir)
    state = load_state(project, create_dirs=True)
    if state.get("emergency"):
        raise CheckpointError(
            "project is in emergency state after a failed rollback: "
            + str(state["emergency"].get("message", ""))
            + " Resolve it manually before creating new checkpoints."
        )
    config = load_config(project)
    checkpoint_id = _next_id(state, task)
    _acquire_lock(project, session, checkpoint_id)
    try:
        verify_command = config.get("verify_command") or detect_verify_command(project)
        record = {
            "id": checkpoint_id,
            "label": label or checkpoint_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "backend": None,
            "ref": None,
            "files_changed": [],
            "verify_command": verify_command,
            "verify_status": "pending",
            "is_lkg": False,
            "task": task,
            "session": session,
            "failures": 0,
        }
        warning = None
        if _is_git_repo(project):
            ref, files = _git_snapshot_commit(project, checkpoint_id, _exclude_patterns(project, config))
            record["backend"] = "git"
            record["ref"] = ref
            record["files_changed"] = files
        elif allow_archive:
            ref, warning = _archive_snapshot(project, checkpoint_id, config)
            record["backend"] = "archive"
            record["ref"] = ref
            record["files_changed"] = []
        else:
            raise CheckpointError(
                "not a git repository and archive mode was not approved. "
                "Pass allow_archive=true to snapshot as an archive, or init git first."
            )
        state["checkpoints"].append(record)
        state["active_checkpoint_id"] = checkpoint_id
        save_state(project, state)
        audit(project, "snapshot", checkpoint_id, {"ok": True, "backend": record["backend"]})
        result = {"ok": True, "checkpoint_id": checkpoint_id, "label": record["label"],
                  "backend": record["backend"], "ref": record["ref"],
                  "files_changed_count": len(record["files_changed"]),
                  "verify_command": verify_command}
        if warning:
            result["warning"] = warning
        return result
    except Exception:
        _release_lock(project)
        raise


def checkpoint_verify(project_dir=None, command=None, session="default") -> dict:
    project = _project_dir(project_dir)
    state = load_state(project)
    config = load_config(project)
    active = None
    if state.get("active_checkpoint_id"):
        active = _find_checkpoint(state, state["active_checkpoint_id"])
    verify_command = command or config.get("verify_command") or detect_verify_command(project)
    if not verify_command:
        raise CheckpointError(
            "no verify command configured and none detected. Set verify_command in "
            ".hermes-checkpoint/config.json or pass a command."
        )
    timeout = int(config.get("verify_timeout_sec", 300))
    outcome = run_verify(project, verify_command, timeout)
    if active is None:
        # Standalone check: no state changes.
        audit(project, "verify", None, outcome)
        return {"ok": True, "passed": outcome["passed"], **outcome}
    active["verify_command"] = verify_command
    if outcome["passed"]:
        active["verify_status"] = "passed"
        active["failures"] = 0
        _set_lkg(project, state, active)
        state["active_checkpoint_id"] = None
        pruned = _prune_retention(project, state, config)
        _release_lock(project)
        save_state(project, state)
        audit(project, "verify", active["id"], outcome)
        audit(project, "promote", active["id"], {"ok": True})
        return {"ok": True, "passed": True, "promoted_to_lkg": active["id"],
                "pruned": pruned, **outcome}
    active["verify_status"] = "failed"
    active["failures"] = int(active.get("failures", 0)) + 1
    audit(project, "verify", active["id"], outcome)
    max_attempts = int(config.get("max_fix_attempts", 3))
    if active["failures"] >= max_attempts and config.get("auto_rollback", True):
        save_state(project, state)
        rolled = _do_rollback(project, state, config, target=None,
                              reason=f"automatic: {max_attempts} failed verify attempts",
                              session=session)
        rolled["auto_rollback"] = True
        return {"ok": True, "passed": False, **outcome, **rolled}
    save_state(project, state)
    return {"ok": True, "passed": False,
            "failures": active["failures"], "max_fix_attempts": max_attempts, **outcome}


def _do_rollback(project: Path, state: dict, config: dict, target, reason: str,
                 session: str) -> dict:
    fallback_to_active = False
    if target is None:
        record = _lkg(state)
        if record is None and state.get("active_checkpoint_id"):
            # First-task case: no promoted LKG exists yet. The active
            # snapshot (the pre-task state) is the only safe restore point.
            record = _find_checkpoint(state, state["active_checkpoint_id"])
            fallback_to_active = record is not None
        if record is None:
            raise CheckpointError("no last-known-good checkpoint to roll back to")
    else:
        _validate_id_or_label(target)
        record = _find_checkpoint(state, target)
        if record is None:
            raise CheckpointError(f"checkpoint not found: {target}")
        if not record.get("is_lkg") and config.get("require_confirm_for_manual_rollback", False):
            raise CheckpointError(
                f"refusing manual rollback to non-LKG checkpoint {target}: "
                "manual rollback to non-LKG targets requires confirmation"
            )
    leftovers: list[str] = []
    try:
        if record["backend"] == "git":
            _git_reset_hard(project, record["ref"])
            leftovers = _untracked_leftovers(project)
        elif record["backend"] == "archive":
            leftovers = _archive_rollback(project, record["ref"])
        else:
            raise CheckpointError(f"unknown checkpoint backend: {record.get('backend')}")
    except CheckpointError:
        state["emergency"] = {
            "message": f"rollback to {record['id']} failed; project left untouched after the failure point",
            "checkpoint_id": record["id"],
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        save_state(project, state)
        audit(project, "rollback", record["id"], {"ok": False, "reason": reason})
        raise
    verify_command = config.get("verify_command") or record.get("verify_command") or detect_verify_command(project)
    verify_outcome = None
    if verify_command:
        verify_outcome = run_verify(project, verify_command, int(config.get("verify_timeout_sec", 300)))
    if fallback_to_active and verify_outcome and verify_outcome.get("passed"):
        # The restored pre-task state just verified green, so it is now the
        # known-good state. Promote it so future rollbacks target it.
        _set_lkg(project, state, record)
    state["active_checkpoint_id"] = None
    state["rollback_count"] = int(state.get("rollback_count", 0)) + 1
    _release_lock(project)
    save_state(project, state)
    audit(project, "rollback", record["id"],
          {"ok": True, "reason": reason, "verify": verify_outcome})
    return {"ok": True, "rolled_back_to": record["id"], "label": record.get("label"),
            "leftovers_untracked": leftovers, "verify_after_rollback": verify_outcome}


def checkpoint_rollback(project_dir=None, target=None, session="default") -> dict:
    project = _project_dir(project_dir)
    state = load_state(project)
    config = load_config(project)
    return _do_rollback(project, state, config, target=target,
                        reason="manual", session=session)


def checkpoint_list(project_dir=None) -> dict:
    project = _project_dir(project_dir)
    state = load_state(project)
    ordered = sorted(state["checkpoints"], key=lambda r: r.get("created_at", ""), reverse=True)
    return {"ok": True, "checkpoints": [
        {"id": r["id"], "label": r.get("label"), "created_at": r.get("created_at"),
         "backend": r.get("backend"), "verify_status": r.get("verify_status"),
         "is_lkg": bool(r.get("is_lkg")), "task": r.get("task")}
        for r in ordered
    ]}


def checkpoint_status(project_dir=None) -> dict:
    project = _project_dir(project_dir)
    state = load_state(project)
    active = _find_checkpoint(state, state["active_checkpoint_id"]) if state.get("active_checkpoint_id") else None
    lkg = _lkg(state)
    last_verify = None
    if active is not None:
        last_verify = active.get("verify_status")
    elif lkg is not None:
        last_verify = lkg.get("verify_status")
    return {"ok": True,
            "active_checkpoint_id": state.get("active_checkpoint_id"),
            "lkg_id": lkg["id"] if lkg else None,
            "last_verify_status": last_verify,
            "rollback_count": int(state.get("rollback_count", 0)),
            "checkpoint_count": len(state["checkpoints"]),
            "emergency": state.get("emergency")}


def checkpoint_dry_run(project_dir=None, action="snapshot") -> dict:
    project = _project_dir(project_dir)
    state = load_state(project)  # read-only: never creates the state dir
    config = load_config(project)
    if action == "snapshot":
        if _is_git_repo(project):
            files = _git_status_paths(project)
            try:
                _guard_secrets(project)
            except CheckpointError as exc:
                return {"ok": False, "error": str(exc)}
            return {"ok": True, "action": "snapshot", "backend": "git",
                    "would_snapshot_files": files,
                    "verify_command": config.get("verify_command") or detect_verify_command(project)}
        return {"ok": True, "action": "snapshot", "backend": "archive (if approved)",
                "note": "not a git repository; archive mode requires explicit approval"}
    if action == "rollback":
        lkg = _lkg(state)
        if lkg is None:
            return {"ok": False, "error": "no last-known-good checkpoint to roll back to"}
        return {"ok": True, "action": "rollback", "target": lkg["id"],
                "ref": lkg.get("ref"), "backend": lkg.get("backend")}
    raise CheckpointError(f"unknown dry-run action: {action}")


# ---------------------------------------------------------------------------
# Hooks (fail-open: any unexpected condition returns None, never raises)
# ---------------------------------------------------------------------------

def pre_tool_call_hook(tool_name: str = "", args=None, **_) -> dict | None:
    """Block file writes in Hermes Checkpoint projects that have no active checkpoint.

    Only enforced when the target file sits inside a project that already has
    a ``.hermes-checkpoint`` state directory (opt-in by use). Read-only
    tools, Hermes Checkpoint's own tools, and unresolvable targets always pass through.
    """
    try:
        if not tool_name or tool_name.startswith("checkpoint_"):
            return None
        enforced = {"write_file", "patch"}
        if tool_name not in enforced:
            return None  # terminal commands cannot be attributed to a project reliably
        if not isinstance(args, dict):
            return None
        path_value = None
        for key in ("file_path", "path", "target_file"):
            if args.get(key):
                path_value = args[key]
                break
        if not path_value:
            return None
        target = Path(str(path_value)).expanduser()
        if not target.is_absolute():
            target = (Path.cwd() / target).resolve()
        else:
            target = target.resolve()
        parent = target.parent
        while True:
            state_path = parent / STATE_DIRNAME / STATE_FILENAME
            if state_path.is_file():
                state = _load_json(state_path, {})
                if isinstance(state, dict) and not state.get("active_checkpoint_id"):
                    return {
                        "action": "block",
                        "message": (
                            "Hermes Checkpoint: no active checkpoint for this project. "
                            "Create one first with checkpoint_create before modifying files, "
                            "so a failed change can be rolled back."
                        ),
                    }
                return None
            if parent.parent == parent:
                return None
            parent = parent.parent
    except Exception:
        return None


def transform_llm_output_hook(output: str = "", **_) -> str | None:
    """Redact assignment-style secrets from the final reply. None = unchanged."""
    try:
        if not isinstance(output, str) or not output:
            return None
        redacted = _SECRET_ASSIGN_RE.sub(lambda m: m.group(0).split("=", 1)[0] + "= [REDACTED]", output)
        return redacted if redacted != output else None
    except Exception:
        return None
