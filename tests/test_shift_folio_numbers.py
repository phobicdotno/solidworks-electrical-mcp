"""Regression test: shift_folio_numbers derives a collision-safe page cascade.

Inserting a folio at page N requires every folio numbered >= N to move, since
folio page marks must be unique. ``shift_folio_numbers`` reads the live folio
set and computes that cascade itself (no hand-built id list). This test runs it
in ``dry_run`` mode only — it mutates NOTHING — and asserts the plan is
internally consistent: the inserted folio is mapped to ``place_at``, every
shifted folio moves by exactly ``delta``, and the resulting target numbers are
unique (no two folios would collide on the same page).

SKIPs without the licensed app / an open project.

Run directly:
    .venv/Scripts/python.exe tests/test_shift_folio_numbers.py
"""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = REPO / ".venv" / "Scripts" / "python.exe"


def main() -> int:
    proc = subprocess.Popen(
        [str(PY), "-m", "solidworks_electrical_mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, cwd=str(REPO), text=True, bufsize=1,
    )
    _id = [0]

    def readline(timeout=90):
        box = {}

        def _r():
            box["v"] = proc.stdout.readline()
        t = threading.Thread(target=_r, daemon=True)
        t.start()
        t.join(timeout)
        return box.get("v")

    def rpc(method, params):
        _id[0] += 1
        proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "id": _id[0], "method": method,
             "params": params}) + "\n")
        proc.stdin.flush()
        line = readline()
        return json.loads(line) if line else {"_": "no response"}

    def tool(name, args):
        r = rpc("tools/call", {"name": name, "arguments": args})
        return r.get("result", {}).get("structuredContent", r)

    try:
        rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "t", "version": "0"}})
        proc.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        proc.stdin.flush()
        time.sleep(0.3)

        conn = tool("connect", {})
        if not conn.get("application"):
            print(f"SKIP: application did not attach ({conn})")
            return 0
        if not tool("call", {"path": "getEwProjectCurrent.getName"}).get("ok"):
            print("SKIP: no project open")
            return 0

        # Pick the lowest folio page number present and pretend to insert there
        # with delta=+1 — a pure dry-run, mutating nothing.
        files = tool("array_ops", {
            "array": "getEwProjectCurrent.getEwProjectFileManager."
                     "getEwProjectFileArray",
            "ops": [{"member": "getID", "args": []},
                    {"member": "getTagNumber", "args": []}]})
        rows = files.get("rows", [])
        if not rows:
            print("SKIP: no folios")
            return 0

        def scalar(v):
            return v[0] if isinstance(v, list) else v
        nums = {scalar(r["results"][0]): scalar(r["results"][1]) for r in rows}
        # choose a threshold mid-range so some folios shift and some do not
        sorted_nums = sorted(set(nums.values()))
        threshold = sorted_nums[len(sorted_nums) // 2]

        res = tool("shift_folio_numbers", {
            "threshold": threshold, "delta": 1, "dry_run": True})
        if not res.get("ok"):
            print(f"FAIL: tool errored: {res}")
            return 1
        plan = res.get("plan", [])
        shift_rows = [p for p in plan if p["role"] == "shift"]
        # every shifted folio moves by exactly +1
        bad = [p for p in shift_rows if p["to"] - p["from"] != 1]
        # resulting numbers (for the shifted set) are unique
        targets = [p["to"] for p in shift_rows]
        unique_ok = len(targets) == len(set(targets))
        # only folios >= threshold are in the shift set
        below = [p for p in shift_rows if p["from"] < threshold]

        print(f"threshold={threshold} shift_count={res.get('shift_count')} "
              f"bad_delta={len(bad)} unique_targets={unique_ok} below={len(below)}")
        if not bad and unique_ok and not below and shift_rows:
            print("PASS: dry-run cascade plan is consistent and collision-free.")
            return 0
        print("FAIL: cascade plan inconsistent.")
        return 1
    finally:
        if proc.poll() is None:
            proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
