"""Regression test: chained (dotted) member ops + per-op error isolation.

``array_ops``/``call_ops`` ops historically took a single member name, so an
op could only read a member of the array element itself — it could not follow
an object the element returns (e.g. a manufacturer part -> its component ->
that component's parent). The op evaluator now accepts a DOTTED member path:
intermediate segments are auto-called and their ``(obj, errorCode)`` tuples
unwrapped, the final segment is called with ``args``. And each op is isolated:
one element missing a chained object (NULL) yields an error cell instead of
aborting the whole enumeration.

Drives the real server over stdio against the open project; SKIPs without the
licensed app or an open project.

Run directly:
    .venv/Scripts/python.exe tests/test_chained_member_ops.py
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
        name = tool("call", {"path": "getEwProjectCurrent.getName"})
        if not name.get("ok"):
            print(f"SKIP: no project open ({name})")
            return 0

        # Each project file -> its location -> that location's tag (a true
        # object->object->value chain), plus the file's own tag, plus a
        # deliberately-bad op to prove per-op isolation.
        res = tool("array_ops", {
            "array": "getEwProjectCurrent.getEwProjectFileManager."
                     "getEwProjectFileArray",
            "ops": [
                {"member": "getTag", "args": []},
                {"member": "getEwProjectLocation.getTag", "args": []},
                {"member": "noSuchMember", "args": []},
            ],
        })
        if not res.get("ok"):
            print(f"FAIL: array_ops errored wholesale: {res}")
            return 1
        rows = res.get("rows", [])
        if not rows:
            print("SKIP: project has no files to chain through")
            return 0

        chained_ok = False
        isolation_ok = True
        for row in rows:
            r = row["results"]
            # col 2 is the chained location tag; some files legitimately have
            # no location (-> error cell), but at least one must resolve.
            if not isinstance(r[1], dict):
                chained_ok = True
            # col 3 must ALWAYS be an isolated error cell, never crash the row.
            if not (isinstance(r[2], dict) and "error" in r[2]):
                isolation_ok = False

        print(f"rows={len(rows)} chained_value_seen={chained_ok} "
              f"per_op_isolation={isolation_ok}")
        if chained_ok and isolation_ok:
            print("PASS: dotted-member chaining returns values; bad ops are "
                  "isolated to their cell.")
            return 0
        print("FAIL: chaining or per-op isolation did not behave as expected.")
        return 1
    finally:
        if proc.poll() is None:
            proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
