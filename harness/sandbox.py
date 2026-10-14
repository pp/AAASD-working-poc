"""Contained execution of configured checks, using a disposable Docker container per run."""
from __future__ import annotations

import os
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class CheckResult:
    name: str
    argv: List[str]
    status: str                      # passed | failed | timed_out | unavailable
    exit_code: Optional[int]
    output: str
    output_shortened: bool = False

    @property
    def passed(self) -> bool:
        return self.status == "passed"


def bound_output(text: str, limit: int) -> Tuple[str, bool]:
    """Keep the start and end of long output and mark that it was shortened."""
    if len(text) <= limit:
        return text, False
    head = limit * 3 // 4
    tail = limit - head
    marker = "\n... [output shortened: {} of {} characters shown] ...\n".format(limit, len(text))
    return text[:head] + marker + text[-tail:], True


@dataclass
class ExecutionEnvironment:
    workspace: Path
    acceptance_dir: Path
    checks: Dict[str, List[str]]
    image: str
    timeout_s: int
    max_output_chars: int
    memory: str
    pids: int
    _running: Set[str] = field(default_factory=set)

    def docker_available(self) -> Tuple[bool, str]:
        try:
            proc = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                                  capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip().splitlines()
            return False, detail[-1] if detail else "docker info failed"
        return True, proc.stdout.strip()

    def run_check(self, name: str) -> CheckResult:
        if name not in self.checks:
            raise KeyError(name)
        argv = list(self.checks[name])
        ok, info = self.docker_available()
        if not ok:
            return CheckResult(name, argv, "unavailable", None,
                               "Docker is not available, so the check did not run: {}".format(info))
        container = "harness-{}".format(uuid.uuid4().hex[:12])
        cmd = [
            "docker", "run", "--rm", "--name", container,
            "--network", "none",
            "--read-only", "--tmpfs", "/tmp:rw,size=256m",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--memory", self.memory, "--pids-limit", str(self.pids),
            "--user", "{}:{}".format(os.getuid(), os.getgid()),
            "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "REPO_DIR=/work",
            "-v", "{}:/work".format(self.workspace),
            "-v", "{}:/acceptance:ro".format(self.acceptance_dir),
            "-w", "/work",
            self.image, *argv,
        ]
        self._running.add(container)
        timed_out = False
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=self.timeout_s)
            stdout = proc.stdout.decode("utf-8", errors="replace")
            stderr = proc.stderr.decode("utf-8", errors="replace")
            exit_code = proc.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = None
            stdout = (exc.stdout or b"").decode("utf-8", errors="replace")
            stderr = (exc.stderr or b"").decode("utf-8", errors="replace")
            self._remove(container)
        finally:
            self._remove(container)
            self._running.discard(container)
        text = (stdout + stderr).strip() or "(no output)"
        text, shortened = bound_output(text, self.max_output_chars)
        if timed_out:
            status = "timed_out"
            text = "Check stopped after {} s and its container was removed.\n{}".format(self.timeout_s, text)
        elif exit_code == 0:
            status = "passed"
        else:
            status = "failed"
        return CheckResult(name, argv, status, exit_code, text, shortened)

    def _remove(self, container: str) -> None:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=60)

    def stop_processes(self) -> None:
        """Remove every container this run started. Used on interrupt."""
        for container in list(self._running):
            self._remove(container)
            self._running.discard(container)
