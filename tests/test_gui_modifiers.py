"""gui.click holds modifier keys around the mouse click.

A plain click on a SOLIDWORKS drawing replaces the selection; selecting
several wires for one command needs ctrl+click. The modifier must go down
before the button and come up after it, also when the click itself fails.
Drives fakes for the Win32 calls, so no application is needed.

Run directly:
    .venv/Scripts/python.exe tests/test_gui_modifiers.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import win32con  # noqa: E402
from solidworks_electrical_mcp import gui  # noqa: E402


def main() -> int:
    events = []
    info = {"rect": [100, 50, 900, 650]}
    gui._guard = lambda title_hint: (1, info)
    gui.win32api.SetCursorPos = lambda xy: events.append(("pos", xy))
    gui.win32api.keybd_event = lambda vk, scan, flags, extra: events.append(
        ("key", vk, "up" if flags & win32con.KEYEVENTF_KEYUP else "down"))
    gui.win32api.mouse_event = lambda flag, *a: events.append(("mouse", flag))
    gui.time.sleep = lambda s: None

    r = gui.click(10, 20, modifiers="ctrl")
    expected = [("pos", (110, 70)),
                ("key", win32con.VK_CONTROL, "down"),
                ("mouse", win32con.MOUSEEVENTF_LEFTDOWN),
                ("mouse", win32con.MOUSEEVENTF_LEFTUP),
                ("key", win32con.VK_CONTROL, "up")]
    fails = []
    if events != expected:
        fails.append(f"ctrl+click events {events}")
    if r.get("modifiers") != "ctrl":
        fails.append(f"result {r}")

    events.clear()
    gui.click(10, 20)
    if any(e[0] == "key" for e in events):
        fails.append(f"plain click pressed a key: {events}")

    events.clear()

    def boom(flag, *a):
        raise OSError("input blocked")
    gui.win32api.mouse_event = boom
    try:
        gui.click(10, 20, modifiers="ctrl+shift")
    except OSError:
        pass
    ups = [e for e in events if e[0] == "key" and e[2] == "up"]
    if ups != [("key", win32con.VK_SHIFT, "up"), ("key", win32con.VK_CONTROL, "up")]:
        fails.append(f"modifiers not released after a failed click: {events}")

    for f in fails:
        print("FAIL:", f)
    print("OK" if not fails else f"{len(fails)} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
