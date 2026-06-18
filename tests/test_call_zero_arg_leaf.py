"""Regression test: ``call`` must auto-call a zero-arg getter leaf.

The server's own instructions document ``call('getEwProjectCurrent.getName')``
(no ``args``) as the way to read a value. Before the fix, the leaf segment was
only invoked when ``args`` was supplied, so a no-args call returned a
bound-method repr string (``"<bound method ...>"``) instead of the value, while
intermediate path segments were auto-called by ``_navigate``. This drives the
real server over stdio and asserts a no-args ``call`` returns a real scalar.

If the licensed application can't attach in this environment, the test SKIPs.

Run directly:
    .venv/Scripts/python.exe tests/test_call_zero_arg_leaf.py
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

        # No args -> must invoke the zero-arg getter, not return its method.
        no_args = tool("call", {"path": "getApplicationVersion",
                                 "root": "application"})
        # Explicit empty args -> the long-standing way; must match no-args.
        explicit = tool("call", {"path": "getApplicationVersion",
                                  "root": "application", "args": []})

        nv = no_args.get("value")
        ev = explicit.get("value")
        print(f"no-args : ok={no_args.get('ok')} value={nv!r}")
        print(f"args=[] : ok={explicit.get('ok')} value={ev!r}")

        ok = True
        if not (no_args.get("ok") and isinstance(nv, str) and nv):
            ok = False
            print("  >>> FAIL: no-args call did not return a scalar value")
        if isinstance(nv, str) and "bound method" in nv:
            ok = False
            print("  >>> REGRESSION: leaf returned a bound-method repr")
        if nv != ev:
            ok = False
            print("  >>> FAIL: no-args result differs from args=[] result")

        if ok:
            print("PASS: no-args call auto-invokes the zero-arg getter leaf.")
            return 0
        return 1
    finally:
        if proc.poll() is None:
            proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
