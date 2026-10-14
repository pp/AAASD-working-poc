"""The acceptance check must fail on the starting code and pass after a correct fix.

Needs the target repository. Set HARNESS_TARGET_REPO to the local clone of
https://github.com/TMBeaver/openstock (at commit abc87dc...).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HARNESS = Path(__file__).resolve().parent.parent
TARGET = os.environ.get("HARNESS_TARGET_REPO")

ANCHOR = (
    "    def _sell(self, *, customer_id, invoice_date, lines, operator,\n"
    "              stock_no, payment_method, invoice_no, allow_below_min=False):\n"
    "        vat_rate = self.db.get_vat_rate()\n"
)
GUARD = ANCHOR + (
    "        for e in lines:\n"
    "            if float(e[\"qty\"]) <= 0:\n"
    "                raise OperationError(\"Quantity must be greater than zero.\")\n"
)


@unittest.skipUnless(TARGET and Path(TARGET).exists(), "set HARNESS_TARGET_REPO to run")
class AcceptanceCheckTest(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="acc-repo-"))
        shutil.copytree(TARGET, self.repo / "r", ignore=shutil.ignore_patterns("venv", "vendor", "__pycache__", "backups"))
        self.repo = self.repo / "r"
        self.check = HARNESS / "acceptance" / "check_sale_quantity.py"

    def tearDown(self):
        shutil.rmtree(self.repo.parent, ignore_errors=True)

    def run_check(self):
        env = dict(os.environ, REPO_DIR=str(self.repo))
        return subprocess.run([sys.executable, str(self.check)], env=env, capture_output=True, text=True, timeout=120)

    def test_fails_on_starting_code(self):
        proc = self.run_check()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("ACCEPTANCE FAILED", proc.stdout)

    def test_passes_after_reference_fix(self):
        ops = self.repo / "operations.py"
        src = ops.read_text(encoding="utf-8")
        self.assertIn(ANCHOR, src)
        ops.write_text(src.replace(ANCHOR, GUARD, 1), encoding="utf-8")
        proc = self.run_check()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ACCEPTANCE PASSED", proc.stdout)


if __name__ == "__main__":
    unittest.main()
