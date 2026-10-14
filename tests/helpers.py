"""Shared test helpers: scripted model and a fake execution environment."""
import json
import shutil
import tempfile
from pathlib import Path

from harness.sandbox import CheckResult


class ScriptedModel:
    """Returns scripted messages in order. With repeat=True the last message repeats forever."""

    def __init__(self, replies, repeat=False):
        self.replies = list(replies)
        self.repeat = repeat
        self.calls = 0
        self.seen_messages = []

    def chat(self, messages, tools):
        self.calls += 1
        self.seen_messages.append([dict(m) for m in messages])
        if not self.replies:
            raise AssertionError("ScriptedModel ran out of replies")
        if self.repeat and len(self.replies) == 1:
            return self.replies[0]
        return self.replies.pop(0)


def call(_tool, **args):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": _tool, "arguments": args}}]}


def text(content):
    return {"role": "assistant", "content": content}


class FakeEnv:
    """Stands in for ExecutionEnvironment. results maps check name -> CheckResult."""

    def __init__(self, checks=None, results=None):
        self.checks = checks or {"acceptance": ["x"], "regression_core": ["y"]}
        self.results = results or {}
        self.ran = []

    def run_check(self, name):
        self.ran.append(name)
        if name in self.results:
            return self.results[name]
        return CheckResult(name, [], "passed", 0, "ok")


def make_repo(files):
    """Create a throwaway git repo with the given files. Returns its path."""
    import subprocess
    root = Path(tempfile.mkdtemp(prefix="harness-test-"))
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "start"],
                   cwd=root, check=True)
    return root


def cleanup(path):
    shutil.rmtree(path, ignore_errors=True)
