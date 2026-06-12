"""Regression test for COM apartment-thread affinity.

SW Electrical hands out STA-bound COM objects. The MCP server runs each tool
call on an anyio worker thread, so a cached COM object created on one thread
used to fail from another with RPC_E_WRONG_THREAD (surfacing as AttributeError
via pywin32). The server funnels all COM work onto one dedicated STA thread to
keep objects on their home apartment.

This test drives the real server over stdio and calls an application-root
method several times through the threadpool. Each must return a real scalar
value (not an error), proving no wrong-thread failure. If the licensed
application can't attach in this environment, the test reports SKIP.

Run directly:
    .venv/Scripts/python.exe tests/test_com_threading.py
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
            print(f"SKIP: application did not attach "
                  f"({conn.get('licence_error', conn)})")
            return 0

        ok = True
        for i in range(5):
            r = tool("call", {"path": "getApplicationVersion",
                              "root": "application", "args": []})
            val = r.get("value")
            good = r.get("ok") and isinstance(val, str) and val
            print(f"call #{i}: ok={r.get('ok')} value={val!r}")
            if not good:
                ok = False
                if "error" in r and "thread" in str(r["error"]).lower():
                    print("  >>> WRONG-THREAD REGRESSION")
        if ok:
            print("PASS: application-root calls returned real values across the "
                  "threadpool (no wrong-thread errors).")
            return 0
        print("FAIL: an application-root call did not return a scalar value.")
        return 1
    finally:
        if proc.poll() is None:
            proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
