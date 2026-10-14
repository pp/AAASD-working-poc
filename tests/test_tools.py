"""Unit tests for repository tools: reads, searches, edits, and path rules."""
import unittest
from pathlib import Path

from harness.tools import DeniedAction, InvalidRequest, RepositoryTools
from tests.helpers import cleanup, make_repo


class RepositoryToolsTest(unittest.TestCase):
    def setUp(self):
        self.root = make_repo({
            "operations.py": "def sell():\n    return 1\n\ndef other():\n    return 1\n",
            "db.py": "class DB:\n    pass\n",
            "data/openstock.db": "secret",
        })
        self.outside = Path(self.root).parent / "outside-secret.txt"
        self.outside.write_text("nope")
        self.tools = RepositoryTools(
            root=self.root, allowed_write_paths=["operations.py"],
            denied_paths=[".git", "data"], max_read_chars=200, max_list_entries=50, max_search_hits=50,
        )

    def tearDown(self):
        cleanup(self.root)
        self.outside.unlink(missing_ok=True)

    def test_read_file_numbers_lines(self):
        out = self.tools.read_file("operations.py", 1, 2)
        self.assertIn("1 | def sell():", out)
        self.assertIn("2 |     return 1", out)
        self.assertNotIn("def other", out)

    def test_search_finds_lines_in_repo_only(self):
        out = self.tools.search("return 1")
        self.assertIn("operations.py:2:", out)
        self.assertNotIn("data/", out)
        self.assertNotIn(".git", out)

    def test_list_files_hides_denied_paths(self):
        out = self.tools.list_files(".")
        self.assertIn("operations.py", out)
        self.assertIn("db.py", out)
        self.assertNotIn("openstock.db", out)

    def test_edit_allowed_file(self):
        self.tools.edit_file("operations.py", "return 1\n\ndef other", "return 2\n\ndef other")
        self.assertIn("return 2", (Path(self.root) / "operations.py").read_text())

    def test_edit_rejects_file_outside_task_scope(self):
        with self.assertRaises(DeniedAction):
            self.tools.edit_file("db.py", "pass", "return 1")
        self.assertEqual((Path(self.root) / "db.py").read_text(), "class DB:\n    pass\n")

    def test_rejects_parent_directory_escape(self):
        with self.assertRaises(DeniedAction):
            self.tools.read_file("../outside-secret.txt")

    def test_rejects_absolute_path(self):
        with self.assertRaises(DeniedAction):
            self.tools.read_file(str(self.outside))

    def test_rejects_symlink_escape(self):
        link = Path(self.root) / "link.txt"
        link.symlink_to(self.outside)
        with self.assertRaises(DeniedAction):
            self.tools.read_file("link.txt")

    def test_rejects_denied_folders(self):
        with self.assertRaises(DeniedAction):
            self.tools.read_file("data/openstock.db")
        with self.assertRaises(DeniedAction):
            self.tools.read_file(".git/config")

    def test_edit_needs_exactly_one_match(self):
        with self.assertRaises(InvalidRequest):
            self.tools.edit_file("operations.py", "return 1", "return 2")
        with self.assertRaises(InvalidRequest):
            self.tools.edit_file("operations.py", "no such text", "x")

    def test_edit_refuses_invalid_python_and_leaves_file_unchanged(self):
        before = (Path(self.root) / "operations.py").read_text()
        with self.assertRaises(InvalidRequest) as ctx:
            self.tools.edit_file("operations.py", "    return 1\n\ndef other", "    if x <=:\n\ndef other")
        self.assertIn("would not be valid Python", str(ctx.exception))
        self.assertEqual((Path(self.root) / "operations.py").read_text(), before)

    def test_read_is_bounded_and_marked(self):
        Path(self.root, "big.py").write_text("\n".join("x = {}".format(i) for i in range(500)))
        out = self.tools.read_file("big.py")
        self.assertIn("file shortened", out)
        self.assertLessEqual(len(out), 200 + 200)


if __name__ == "__main__":
    unittest.main()
