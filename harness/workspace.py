"""Disposable copy of the target repository and host-side git reads."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple


class WorkspaceError(Exception):
    pass


COPY_IGNORE = shutil.ignore_patterns(
    "venv", ".venv", "vendor", "__pycache__", "*.pyc", "backups", "documents", "logs",
)


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(root), *args],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise WorkspaceError("git {} failed: {}".format(" ".join(args), proc.stderr.strip()))
    return proc.stdout


def create_workspace(source: Path, dest: Path, expected_commit: Optional[str]) -> str:
    """Copy the target repository into dest and return its commit hash."""
    source = Path(source).resolve()
    if not (source / ".git").exists():
        raise WorkspaceError("{} is not a git repository".format(source))
    head = _git(source, "rev-parse", "HEAD").strip()
    if expected_commit and head != expected_commit:
        raise WorkspaceError(
            "Target repo is at {} but the task expects {}".format(head, expected_commit))
    if dest.exists():
        raise WorkspaceError("Workspace already exists: {}".format(dest))
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, dest, ignore=COPY_IGNORE, symlinks=True)
    return head


def changed_files(workspace: Path) -> List[str]:
    out = _git(workspace, "status", "--porcelain")
    return [line[3:].strip() for line in out.splitlines() if line.strip()]


def diff(workspace: Path) -> str:
    return _git(workspace, "diff")


def changed_state(workspace: Path) -> Tuple[List[str], str]:
    return changed_files(workspace), diff(workspace)
