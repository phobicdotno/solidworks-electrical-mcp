"""gui.type_text stops the moment focus leaves SOLIDWORKS Electrical.

Checking the foreground once before typing is not enough: the owner of the
PC switched windows half-way through a long string, and the rest of the
keystrokes went to whatever window came forward. type_text now checks the
foreground process before every character. A dialog SOLIDWORKS opened is a
different window of the same process and must not stop it.
Drives fakes for the Win32 calls, so no application is needed.

Run directly:
    .venv/Scripts/python.exe tests/test_gui_type_focus.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import win32con  # noqa: E402
from solidworks_electrical_mcp import gui  # noqa: E402

SWE, DIALOG, OTHER = 1, 2, 3
PIDS = {SWE: 100, DIALOG: 100, OTHER: 200}


def main() -> int:
    downs = []
    fg = {"w": DIALOG}
    gui._guard = lambda title_hint: (SWE, {"rect": [0, 0, 10, 10]})
    gui.win32process.GetWindowThreadProcessId = lambda w: (0, PIDS[w])
    gui.win32gui.GetForegroundWindow = lambda: fg["w"]
    gui.win32gui.GetWindowText = lambda w: {OTHER: "a terminal"}.get(w, "SWE")
    gui.win32api.VkKeyScan = lambda ch: ord(ch.upper())

    def key(vk, scan, flags, extra):
        if not flags & win32con.KEYEVENTF_KEYUP:
            downs.append(chr(vk))
            if len(downs) == 3:
                fg["w"] = OTHER  # the owner clicks another window
    gui.win32api.keybd_event = key
    gui.time.sleep = lambda s: None

    fails = []
    try:
        gui.type_text("abcdef")
        fails.append("typing went on after focus left")
    except RuntimeError as e:
        if "after 3 of 6" not in str(e):
            fails.append(f"wrong message: {e}")
    if downs != ["A", "B", "C"]:
        fails.append(f"keys sent: {downs}")

    downs.clear()
    fg["w"] = DIALOG
    gui.win32api.keybd_event = lambda vk, scan, flags, extra: None if flags else downs.append(vk)
    r = gui.type_text("xy")
    if r.get("typed") != 2 or len(downs) != 2:
        fails.append(f"a SOLIDWORKS dialog in front stopped typing: {r} {downs}")

    for f in fails:
        print("FAIL:", f)
    print("OK" if not fails else f"{len(fails)} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
