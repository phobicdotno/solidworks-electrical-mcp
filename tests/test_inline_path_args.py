"""Regression test: inline args in path segments (fetch-by-id/index).

A path segment may now carry args — ``findEwProjectFileByID(42)`` or
``getEwProjectSymbolPointAt(0)`` — so a path can fetch an object by id/index
and keep navigating (e.g. ``…getEwProjectSymbolManager.findEwProjectSymbolByID(
42).getEwProjectSymbolPointArray``). Previously a path could only auto-call
no-arg getters, so by-id/by-index objects were unreachable.

This enumerates one folio, then re-fetches it by id through an inline-arg path
and asserts the tag round-trips. SKIPs without the licensed app / a project.

Run directly:
    .venv/Scripts/python.exe tests/test_inline_path_args.py
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

    def scalar(v):
        return v[0] if isinstance(v, list) else v

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
            "ops": [{"member": "getID", "args": []},
                    {"member": "getTag", "args": []}], "limit": 1})
        rows = files.get("rows", [])
        if not rows:
            print("SKIP: no folios")
            return 0
        fid = scalar(rows[0]["results"][0])
        enum_tag = scalar(rows[0]["results"][1])

        # Re-fetch the same file by id through an inline-arg path segment.
        byid = tool("call", {
            "path": f"getEwProjectCurrent.getEwProjectFileManager."
                    f"findEwProjectFileByID({fid}).getTag"})
        byid_tag = scalar(byid.get("value"))

        print(f"file id={fid}  enumerated tag={enum_tag!r}  "
              f"by-id tag={byid_tag!r}")
        if byid.get("ok") and byid_tag == enum_tag and byid_tag is not None:
            print("PASS: inline-arg path fetched the object by id and read it.")
            return 0
        print("FAIL: inline-arg path did not round-trip the tag.")
        return 1
    finally:
        if proc.poll() is None:
            proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
