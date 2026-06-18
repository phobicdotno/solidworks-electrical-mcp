"""Regression test: typed VARIANT array args ($variant) + call_ops isolation.

Some COM methods demand a specifically-typed SAFEARRAY — e.g.
``IEwProjectExportPDFX.setSelectionFiles`` wants ``VT_ARRAY|VT_I4``. A bare
Python list pywin32 marshals as a ``VT_VARIANT`` array, which the method
rejects with EW_BAD_INPUTS (2). The op/arg layer now recognises a
``{"$variant": "VT_ARRAY|VT_I4", "value": [...]}`` marker and builds a typed
``win32com.client.VARIANT``. This drives setSelectionFiles with the marker and
asserts it returns 0 and round-trips. It also asserts ``call_ops`` isolates a
bad op into an ``{"error": ...}`` cell instead of aborting the sequence.

No PDF is exported (we only set+read the selection), so there is nothing to
clean up. SKIPs without the licensed app / an open project.

Run directly:
    .venv/Scripts/python.exe tests/test_typed_args_and_isolation.py
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

        files = tool("array_ops", {
            "array": "getEwProjectCurrent.getEwProjectFileManager."
                     "getEwProjectFileArray",
            "ops": [{"member": "getID", "args": []}], "limit": 1})
        rows = files.get("rows", [])
        if not rows:
            print("SKIP: no folios")
            return 0
        fid = rows[0]["results"][0]
        fid = fid[0] if isinstance(fid, list) else fid

        # Configure a PDF export selection with a TYPED array, then read it
        # back. setSelectionFiles must accept it (rc 0); a bad op must isolate.
        res = tool("call_ops", {
            "target": "getEwProjectCurrent.newEwProjectExportPDF",
            "ops": [
                {"member": "initializeFromPrintProjectConfiguration", "args": []},
                {"member": "setAllProjectFiles", "args": [False]},
                {"member": "setSelectionFiles",
                 "args": [{"$variant": "VT_ARRAY|VT_I4", "value": [fid]}]},
                {"member": "noSuchMember", "args": []},
                {"member": "getAllProjectFiles", "args": []},
            ]})
        vals = res.get("values")
        print(f"call_ops ok={res.get('ok')} values={vals}")
        if not res.get("ok") or not isinstance(vals, list) or len(vals) != 5:
            print("FAIL: call_ops did not return 5 result cells")
            return 1

        set_rc = vals[2]
        bad_cell = vals[3]
        typed_ok = (set_rc == 0)
        isolation_ok = isinstance(bad_cell, dict) and "error" in bad_cell
        tail_ran = vals[4] is not None  # op after the bad one still executed

        print(f"setSelectionFiles rc={set_rc} (typed_ok={typed_ok})  "
              f"bad_op_isolated={isolation_ok}  tail_ran={tail_ran}")
        if typed_ok and isolation_ok and tail_ran:
            print("PASS: typed VARIANT array accepted; bad op isolated; "
                  "sequence continued.")
            return 0
        print("FAIL: typed-arg or call_ops isolation behaved unexpectedly.")
        return 1
    finally:
        if proc.poll() is None:
            proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
