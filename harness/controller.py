"""Agent controller: asks the model for actions, validates them, runs allowed ones, and checks the result."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import workspace
from .actions import ActionValidator, build_specs
from .model import ModelError, OllamaClient, extract_tool_calls
from .sandbox import CheckResult, ExecutionEnvironment
from .tasks import Task
from .tools import DeniedAction, InvalidRequest, RepositoryTools, ToolError

SYSTEM_PROMPT = (
    "You are a coding agent working in a Python repository. You can only act through the provided tools. "
    "Work in small steps: search for the relevant code, read it, make one exact edit, then run the checks. "
    "Read the output of each check before deciding what to do next. "
    "Never say that a check passed unless run_check reported passed. "
    "When the change is complete, call finish."
)


@dataclass
class Counters:
    actions: int = 0
    invalid_requests: int = 0
    denied_actions: int = 0
    model_retries: int = 0
    shortened_outputs: int = 0


@dataclass
class RunReport:
    task: Task
    baseline: Optional[CheckResult] = None
    stop_reason: str = ""
    finish_summary: str = ""
    checks: List[CheckResult] = field(default_factory=list)
    changed_files: List[str] = field(default_factory=list)
    diff: str = ""
    counters: Counters = field(default_factory=Counters)
    status: str = ""

    @property
    def verified(self) -> bool:
        return bool(self.checks) and all(c.passed for c in self.checks)


EventFn = Callable[[str, Dict[str, Any]], None]


class AgentController:
    def __init__(self, *, model: OllamaClient, tools: RepositoryTools, env: ExecutionEnvironment,
                 settings: Any, on_event: EventFn):
        self.model = model
        self.tools = tools
        self.env = env
        self.settings = settings
        self.on_event = on_event
        self.checks_names = list(env.checks)
        self.specs = build_specs(self.checks_names)
        self.validator = ActionValidator(self.specs, self.checks_names)
        self.counters = Counters()

    # ── one action ──────────────────────────────────────────
    def validate_action(self, name: Any, args: Any) -> Dict[str, Any]:
        return self.validator.validate(name, args)

    def execute(self, name: str, args: Dict[str, Any]) -> str:
        if name == "list_files":
            return self.tools.list_files(args.get("path", "."))
        if name == "read_file":
            return self.tools.read_file(args["path"], args.get("start_line"), args.get("end_line"))
        if name == "search":
            return self.tools.search(args["query"], args.get("path", "."))
        if name == "edit_file":
            return self.tools.edit_file(args["path"], args["old_string"], args["new_string"])
        if name == "run_check":
            result = self.env.run_check(args["name"])
            return self.format_check(result)
        raise InvalidRequest("Unknown tool {!r}".format(name))

    @staticmethod
    def format_check(result: CheckResult) -> str:
        code = "none" if result.exit_code is None else str(result.exit_code)
        return "check {} {} (exit code {})\n{}".format(result.name, result.status.upper(), code, result.output)

    def handle(self, name: Any, args: Any) -> str:
        """Validate, execute, and return the text the model will see."""
        self.counters.actions += 1
        try:
            clean = self.validate_action(name, args)
            result = self.execute(name, clean)
        except InvalidRequest as exc:
            self.counters.invalid_requests += 1
            return "ERROR (invalid request, nothing was run): {}".format(exc)
        except DeniedAction as exc:
            self.counters.denied_actions += 1
            return "ERROR (denied): {}".format(exc)
        except ToolError as exc:
            return "ERROR: {}".format(exc)
        except KeyError as exc:
            self.counters.invalid_requests += 1
            return "ERROR (invalid request, nothing was run): missing argument {}".format(exc)
        self.counters.shortened_outputs = self.tools.shortened
        return result

    # ── the loop ────────────────────────────────────────────
    def run(self, task: Task) -> RunReport:
        report = RunReport(task=task)
        report.baseline = self.env.run_check("acceptance") if "acceptance" in self.env.checks else None
        if report.baseline:
            self.emit("baseline", {"check": report.baseline.name, "status": report.baseline.status})

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task.prompt()},
        ]
        finished = False
        while not finished:
            if self.counters.actions >= self.settings.max_actions:
                report.stop_reason = "action limit reached ({})".format(self.settings.max_actions)
                break
            try:
                message = self.model.chat(messages, self.specs)
            except ModelError as exc:
                self.counters.model_retries += 1
                self.emit("model_error", {"error": str(exc)})
                if self.counters.model_retries > self.settings.max_retries:
                    report.stop_reason = "model error: {}".format(exc)
                    break
                continue

            calls, from_text = extract_tool_calls(message)
            if not calls:
                self.counters.model_retries += 1
                self.emit("no_tool_call", {"text": (message.get("content") or "")[:200]})
                if self.counters.model_retries > self.settings.max_retries:
                    report.stop_reason = "model stopped without calling a tool"
                    break
                messages.append({"role": "assistant", "content": message.get("content") or ""})
                messages.append({"role": "user", "content": "Reply with a tool call. Call finish when the change is complete."})
                continue

            messages.append({"role": "assistant", "content": message.get("content") or "",
                             "tool_calls": message.get("tool_calls") or []})
            for name, args in calls:
                if self.counters.actions >= self.settings.max_actions:
                    break
                if name == "finish":
                    self.counters.actions += 1
                    summary = args.get("summary") if isinstance(args, dict) else None
                    if not isinstance(summary, str) or not summary.strip():
                        self.counters.invalid_requests += 1
                        text = "ERROR (invalid request, nothing was run): finish needs a summary string"
                        messages.append({"role": "tool", "name": "finish", "content": text})
                        self.emit("tool", {"name": "finish", "result": text, "from_text": from_text})
                        continue
                    report.finish_summary = summary.strip()
                    report.stop_reason = "agent finished"
                    self.emit("tool", {"name": "finish", "result": "finished", "from_text": from_text})
                    finished = True
                    break
                text = self.handle(name, args)
                self.emit("tool", {"name": name, "args": args, "result": text[:300],
                                   "from_text": from_text, "step": self.counters.actions})
                messages.append({"role": "tool", "name": name, "content": text})

        if not report.stop_reason:
            report.stop_reason = "stopped"

        report.checks = [self.env.run_check(name) for name in task.required_checks]
        report.changed_files, report.diff = workspace.changed_state(self.tools.root)
        report.counters = self.counters
        report.status = self.status_line(report)
        return report

    def status_line(self, report: RunReport) -> str:
        if report.stop_reason != "agent finished":
            return "STOPPED: {} (checks shown below are not a completed run)".format(report.stop_reason)
        if not report.checks:
            return "NOT VERIFIED: no checks configured"
        failed = [c.name for c in report.checks if c.status != "passed"]
        if failed:
            return "NOT VERIFIED: agent finished but these checks did not pass: {}".format(", ".join(failed))
        return "PASSED: agent finished and all checks passed"

    def emit(self, kind: str, data: Dict[str, Any]) -> None:
        self.on_event(kind, data)
