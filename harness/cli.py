"""Command-line entry point: prepare a workspace, run the agent, and show the result."""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

from .config import Settings
from .controller import AgentController, RunReport
from .model import ModelError, OllamaClient
from .sandbox import ExecutionEnvironment
from .tasks import Task
from .tools import RepositoryTools
from . import workspace

PROJECT_DIR = Path(__file__).resolve().parent.parent


def _print_event(kind: str, data: dict) -> None:
    if kind == "baseline":
        print("[baseline] check {} on starting code: {}".format(data["check"], data["status"].upper()))
    elif kind == "tool":
        tag = " (parsed from text)" if data.get("from_text") else ""
        first = (data.get("result") or "").splitlines()[:1]
        print("[step {}] {}{} -> {}".format(data.get("step", "-"), data["name"], tag, first[0] if first else ""))
    elif kind == "no_tool_call":
        print("[warn] model replied without a tool call; asking again")
    elif kind == "model_error":
        print("[warn] model error: {}".format(data["error"]))


def _report_markdown(report: RunReport, events_path: Path) -> str:
    c = report.counters
    lines = [
        "# Run report: {}".format(report.task.title),
        "",
        "Starting commit: `{}`".format(report.task.starting_commit),
        "",
        "**Status:** {}".format(report.status),
        "",
        "Agent summary: {}".format(report.finish_summary or "(none)"),
        "",
        "## Counters",
        "- actions: {}".format(c.actions),
        "- invalid requests: {}".format(c.invalid_requests),
        "- denied actions: {}".format(c.denied_actions),
        "- model retries: {}".format(c.model_retries),
        "- shortened outputs: {}".format(c.shortened_outputs),
        "",
        "## Checks",
    ]
    if report.baseline:
        lines.append("- baseline {}: {} (exit {})".format(
            report.baseline.name, report.baseline.status, report.baseline.exit_code))
    for ch in report.checks:
        lines.append("- {}: {} (exit {})".format(ch.name, ch.status, ch.exit_code))
    lines += ["", "## Changed files"] + (["- " + f for f in report.changed_files] or ["(none)"])
    lines += ["", "Events: `{}`".format(events_path), ""]
    return "\n".join(lines)


def cmd_run(args: argparse.Namespace) -> int:
    settings = Settings.load(Path(args.config))
    if args.model:
        settings.model = args.model
    if args.max_actions:
        settings.max_actions = args.max_actions
    task = Task.load(Path(args.task))
    source = Path(args.repo).resolve()

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = (PROJECT_DIR / settings.runs_dir / "{}-{}".format(stamp, task.id)).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    events_path = run_dir / "events.jsonl"

    def on_event(kind: str, data: dict) -> None:
        with events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"event": kind, **data}, ensure_ascii=False) + "\n")
        _print_event(kind, data)

    model = OllamaClient(settings.ollama_url, settings.model, settings.model_timeout_s, settings.num_ctx)
    try:
        model.check_model()
    except ModelError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2

    try:
        head = workspace.create_workspace(source, run_dir / "repo", task.starting_commit)
    except workspace.WorkspaceError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    print("Task: {}".format(task.title))
    print("Starting commit: {}".format(head))
    print("Workspace: {}".format(run_dir / "repo"))

    env = ExecutionEnvironment(
        workspace=run_dir / "repo", acceptance_dir=(PROJECT_DIR / "acceptance").resolve(),
        checks=settings.checks, image=settings.docker_image, timeout_s=settings.check_timeout_s,
        max_output_chars=settings.max_output_chars, memory=settings.container_memory,
        pids=settings.container_pids,
    )
    tools = RepositoryTools(
        root=run_dir / "repo", allowed_write_paths=task.allowed_write_paths,
        denied_paths=settings.denied_paths, max_read_chars=settings.max_read_chars,
        max_list_entries=settings.max_list_entries, max_search_hits=settings.max_search_hits,
    )
    controller = AgentController(model=model, tools=tools, env=env, settings=settings, on_event=on_event)
    try:
        report = controller.run(task)
    except KeyboardInterrupt:
        env.stop_processes()
        print("\nInterrupted. Check containers were removed.", file=sys.stderr)
        return 130

    diff_path = run_dir / "diff.patch"
    diff_path.write_text(report.diff, encoding="utf-8")
    (run_dir / "report.md").write_text(_report_markdown(report, events_path), encoding="utf-8")

    print("\n=== RESULT ===")
    print(report.status)
    print("\nAgent summary: {}".format(report.finish_summary or "(none)"))
    print("\nChanged files:")
    for f in report.changed_files or ["(none)"]:
        print("  " + f)
    print("\n=== DIFF ===")
    print(report.diff or "(no changes)")
    print("\n=== CHECKS ===")
    for ch in report.checks:
        print("{:<22} {:<10} exit={}".format(ch.name, ch.status, ch.exit_code))
    print("\nCounters: {}".format(report.counters))
    print("Report: {}".format(run_dir / "report.md"))
    return 0 if report.verified else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="harness", description="Coding harness for a local Ollama model.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run a task on a disposable copy of the repository.")
    run.add_argument("--task", required=True, help="Task JSON file, e.g. tasks/negative_sale_quantity.json")
    run.add_argument("--repo", required=True, help="Path to the local target repository clone")
    run.add_argument("--config", default=str(PROJECT_DIR / "harness.json"), help="Settings file (optional)")
    run.add_argument("--model", help="Override the Ollama model")
    run.add_argument("--max-actions", type=int, help="Override the action limit")
    run.set_defaults(func=cmd_run)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
