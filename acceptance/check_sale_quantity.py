"""Acceptance check: a sale must reject zero or negative quantities.

Kept outside the agent's writable area (mounted read-only in the sandbox).
Builds its own demo database in a temporary folder, so the agent cannot change the data it is judged on.
Run inside the sandbox with REPO_DIR pointing at the repository (default /work).
Exit code 0 = pass, 1 = fail, 2 = setup problem.
"""
import os
import subprocess
import sys
import tempfile

REPO = os.environ.get("REPO_DIR", "/work")
sys.path.insert(0, REPO)

workdir = tempfile.mkdtemp(prefix="acceptance-")
os.environ["OPENSTOCK_DB"] = os.path.join(workdir, "acceptance.db")
seed = subprocess.run(
    [sys.executable, os.path.join(REPO, "seed_demo.py"), "--password", "acceptance-only-password"],
    cwd=REPO, capture_output=True, text=True,
)
if seed.returncode != 0:
    print("SETUP FAILED: seed_demo.py did not run")
    print(seed.stdout)
    print(seed.stderr)
    sys.exit(2)

from db import DB, load_config  # noqa: E402
from operations import Operations, OperationError  # noqa: E402

db = DB(load_config())
db.connect()
ops = Operations(db)
cur = db.cursor()

cur.execute("SELECT item_code, supplier_code, cost_price, retail_price FROM products "
            "WHERE stock1 >= 5 AND cost_price > 0 AND supplier_code <> '' ORDER BY item_code LIMIT 1")
product = cur.fetchone()
cur.execute("SELECT code FROM companies ORDER BY code LIMIT 1")
customer = cur.fetchone()[0]


def snapshot():
    cur.execute("SELECT item_code, supplier_code, stock1, stock2, stock3, stock4, stock_production FROM products "
                "ORDER BY item_code, supplier_code")
    stock = [tuple(r) for r in cur.fetchall()]
    cur.execute("SELECT COUNT(*) FROM documents")
    return stock, cur.fetchone()[0]


def line(qty):
    price = float(product["retail_price"] or product["cost_price"] * 2)
    return {"code": product["item_code"], "supplier_code": product["supplier_code"], "price": price, "qty": qty}


def attempt(qty):
    try:
        res = ops.sell(customer_id=customer, invoice_date="2026-10-14", payment_method="cash",
                       lines=[line(qty)], allow_below_min=True)
    except OperationError as exc:
        return False, str(exc)
    return bool(res.get("ok")), str(res)


failures = []
for qty in (-3, 0):
    before = snapshot()
    accepted, detail = attempt(qty)
    after = snapshot()
    if accepted:
        failures.append("sale with qty={} was accepted".format(qty))
    if before != after:
        failures.append("sale with qty={} changed stock or wrote a document".format(qty))
    print("{} qty={}: {}".format("PASS" if not accepted and before == after else "FAIL", qty,
                                 "rejected" if not accepted else "accepted"))

before = snapshot()
accepted, detail = attempt(1)
after = snapshot()
if not accepted:
    failures.append("valid sale with qty=1 was rejected: {}".format(detail))
else:
    stock_before = {(r[0], r[1]): r[2] for r in before[0]}
    stock_after = {(r[0], r[1]): r[2] for r in after[0]}
    key = (product["item_code"], product["supplier_code"])
    if abs((stock_before[key] - stock_after[key]) - 1) > 1e-6:
        failures.append("valid sale with qty=1 did not reduce stock by 1")
print("{} valid sale qty=1 {}".format("PASS" if accepted else "FAIL", "accepted" if accepted else "rejected"))

if failures:
    print("\nACCEPTANCE FAILED")
    for f in failures:
        print(" - " + f)
    sys.exit(1)
print("\nACCEPTANCE PASSED")
sys.exit(0)
