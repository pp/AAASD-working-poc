"""Controller tests with a scripted model. They need no Ollama, Docker, or API key."""
import json
import types
import unittest
from pathlib import Path

from harness.controller import AgentController
from harness.model import extract_tool_calls
from harness.sandbox import CheckResult
from harness.tasks import Task
from harness.tools import RepositoryTools
from tests.helpers import FakeEnv, ScriptedModel, call, cleanup, make_repo, text

SOURCE = "def sell():\n    return 1\n\ndef other():\n    return 1\n"


def settings(**over):
    base = dict(max_actions=10, max_retries=2)
    base.update(over)
    return types.SimpleNamespace(**base)


def task(commit):
    return Task(id="t", title="Test task", request="Change sell.", starting_commit=commit,
                allowed_write_paths=("operations.py",), required_checks=("acceptance", "regression_core"))


class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.root = make_repo({"operations.py": SOURCE, "db.py": "class DB:\n    pass\n"})
        self.events = []

    def tearDown(self):
        cleanup(self.root)

    def build(self, model, env=None, **over):
        tools = RepositoryTools(root=self.root, allowed_write_paths=["operations.py"],
                                denied_paths=[".git", "data"], max_read_chars=2000,
                                max_list_entries=50, max_search_hits=50)
        env = env or FakeEnv()
        ctrl = AgentController(model=model, tools=tools, env=env, settings=settings(**over),
                               on_event=lambda k, d: self.events.append((k, d)))
        return ctrl, env

    def run_task(self, model, env=None, **over):
        import subprocess
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root, capture_output=True,
                              text=True).stdout.strip()
        ctrl, env = self.build(model, env, **over)
        return ctrl, ctrl.run(task(head))

    def test_calls_correct_tool_and_receives_result(self):
        model = ScriptedModel([
            call("read_file", path="operations.py"),
            call("finish", summary="Read the file."),
        ])
        ctrl, report = self.run_task(model)
        self.assertEqual(report.finish_summary, "Read the file.")
        second_request = model.seen_messages[1]
        tool_msgs = [m for m in second_request if m["role"] == "tool"]
        self.assertEqual(len(tool_msgs), 1)
        self.assertEqual(tool_msgs[0]["name"], "read_file")
        self.assertIn("1 | def sell():", tool_msgs[0]["content"])

    def test_unknown_tool_is_refused_without_action(self):
        model = ScriptedModel([call("delete_repo", path="."), call("finish", summary="x")])
        ctrl, report = self.run_task(model)
        self.assertEqual(report.counters.invalid_requests, 1)
        self.assertEqual(report.changed_files, [])
        tool_msgs = [m for m in model.seen_messages[1] if m["role"] == "tool"]
        self.assertIn("Unknown tool", tool_msgs[0]["content"])

    def test_invalid_arguments_are_refused(self):
        model = ScriptedModel([
            call("read_file", path=5),
            call("edit_file", path="operations.py", old_string="return 1"),
            call("run_check", name="rm -rf /"),
            call("finish", summary="x"),
        ])
        ctrl, report = self.run_task(model)
        self.assertEqual(report.counters.invalid_requests, 3)
        self.assertEqual(report.changed_files, [])
        self.assertEqual(report.counters.denied_actions, 0)

    def test_edit_outside_scope_is_denied_and_counted(self):
        model = ScriptedModel([
            call("edit_file", path="db.py", old_string="pass", new_string="return 1"),
            call("finish", summary="x"),
        ])
        ctrl, report = self.run_task(model)
        self.assertEqual(report.counters.denied_actions, 1)
        self.assertEqual(report.changed_files, [])
        self.assertIn("pass", (Path(self.root) / "db.py").read_text())

    def test_failed_command_stays_visible_and_is_not_reported_as_passed(self):
        failing = CheckResult("regression_core", [], "failed", 1, "FAILED: sales total wrong")
        env = FakeEnv(results={"regression_core": failing})
        model = ScriptedModel([call("run_check", name="regression_core"), call("finish", summary="All good.")])
        ctrl, report = self.run_task(model, env=env)
        tool_msgs = [m for m in model.seen_messages[1] if m["role"] == "tool"]
        self.assertIn("exit code 1", tool_msgs[0]["content"])
        self.assertIn("FAILED: sales total wrong", tool_msgs[0]["content"])
        self.assertTrue(report.status.startswith("NOT VERIFIED"))
        self.assertIn("regression_core", report.status)

    def test_action_limit_stops_repeating_reply(self):
        model = ScriptedModel([call("list_files", path=".")], repeat=True)
        ctrl, report = self.run_task(model, max_actions=3)
        self.assertEqual(report.counters.actions, 3)
        self.assertIn("action limit", report.stop_reason)
        self.assertTrue(report.status.startswith("STOPPED"))

    def test_model_that_never_calls_a_tool_is_stopped(self):
        model = ScriptedModel([text("I think I am done."),
                               text("Still done."),
                               text("Done again.")], repeat=True)
        ctrl, report = self.run_task(model, max_retries=2)
        self.assertIn("without calling a tool", report.stop_reason)
        self.assertEqual(report.counters.model_retries, 3)

    def test_json_tool_call_written_as_text_is_accepted(self):
        content = json.dumps({"name": "finish", "arguments": {"summary": "from text"}})
        model = ScriptedModel([{"role": "assistant", "content": content}])
        ctrl, report = self.run_task(model)
        self.assertEqual(report.finish_summary, "from text")
        self.assertTrue(any(k == "tool" and d.get("from_text") for k, d in self.events))

    def test_passing_run_reports_passed(self):
        model = ScriptedModel([
            call("edit_file", path="operations.py", old_string="    return 1\n\ndef other", new_string="    return 2\n\ndef other"),
            call("finish", summary="Changed return value."),
        ])
        ctrl, report = self.run_task(model)
        self.assertEqual(report.changed_files, ["operations.py"])
        self.assertIn("return 2", report.diff)
        self.assertEqual(report.status, "PASSED: agent finished and all checks passed")

    def test_finish_without_checks_is_not_passed(self):
        env = FakeEnv(results={"acceptance": CheckResult("acceptance", [], "unavailable", None, "docker off")})
        model = ScriptedModel([call("finish", summary="Done.")])
        ctrl, report = self.run_task(model, env=env)
        self.assertTrue(report.status.startswith("NOT VERIFIED"))


class ToolCallExtractionTest(unittest.TestCase):
    def test_native_tool_call_with_string_arguments(self):
        msg = {"content": "", "tool_calls": [{"function": {"name": "read_file", "arguments": '{"path": "a.py"}'}}]}
        calls, from_text = extract_tool_calls(msg)
        self.assertEqual(calls, [("read_file", {"path": "a.py"})])
        self.assertFalse(from_text)

    def test_no_calls_returns_empty(self):
        self.assertEqual(extract_tool_calls({"content": "hello"}), ([], False))


if __name__ == "__main__":
    unittest.main()
