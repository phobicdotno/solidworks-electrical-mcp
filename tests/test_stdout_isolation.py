"""Regression test for the stdout-pollution disconnect bug.

SOLIDWORKS Electrical's COM DLLs write diagnostic lines (e.g.
"...Services are not initialized") straight to OS fd 1. The MCP stdio
transport multiplexes JSON-RPC over the same fd, so that raw text corrupts
the framing and the client drops the connection ("Connection closed").

This test drives the real server over stdio, triggers the offending COM call
(getEwApplication on the factory root, which logs when SW Electrical services
are not initialized), and asserts that EVERY line the client receives on
stdout is valid JSON-RPC. Native chatter must go to stderr instead.

Run directly (no pytest needed):
    .venv/Scripts/python.exe tests/test_stdout_isolation.py

Precondition: the SOLIDWORKS Electrical GUI should NOT be running, so the
"Services are not initialized" log path is exercised. If SW Electrical is
fully up the bug is dormant and the test trivially passes.
"""
import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = REPO / ".venv" / "Scripts" / "python.exe"


def _drain(proc, seconds=3.0):
    """Collect all stdout lines emitted within `seconds`."""
    lines = []
    deadline = time.monotonic() + seconds

    def _reader():
        while True:
            line = proc.stdout.readline()
            if not line:
                return
            lines.append(line)

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    while time.monotonic() < deadline:
        time.sleep(0.05)
    return lines


def main() -> int:
    stderr_file = tempfile.TemporaryFile(mode="w+")
    proc = subprocess.Popen(
        [str(PY), "-m", "solidworks_electrical_mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=stderr_file, cwd=str(REPO), text=True, bufsize=1,
    )

    def send(obj):
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "t", "version": "0"}}})
        time.sleep(0.5)
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        time.sleep(0.3)
        # Several times: the native log races the response, so repeat to make
        # the pollution near-certain when the bug is present.
        for i in range(6):
            send({"jsonrpc": "2.0", "id": 100 + i, "method": "tools/call",
                  "params": {"name": "call",
                             "arguments": {"path": "getEwApplication",
                                           "root": "factory", "args": [""]}}})
            time.sleep(0.1)
        lines = _drain(proc, seconds=3.0)
    finally:
        if proc.poll() is None:
            proc.terminate()

    bad = []
    seen = 0
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        seen += 1
        try:
            msg = json.loads(s)
        except json.JSONDecodeError:
            bad.append(s)
            continue
        if not (isinstance(msg, dict) and msg.get("jsonrpc") == "2.0"):
            bad.append(s)

    print(f"stdout lines seen: {seen}")
    if bad:
        print(f"FAIL: {len(bad)} non-JSON-RPC line(s) leaked onto stdout:")
        for b in bad[:5]:
            print("  | " + b[:160])
        return 1
    if seen == 0:
        print("INCONCLUSIVE: no stdout lines captured (handshake issue?)")
        return 2
    print("PASS: stdout carried only JSON-RPC; native logs stayed off the wire.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
