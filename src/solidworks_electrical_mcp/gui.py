"""Last-resort GUI driving for SOLIDWORKS Electrical.

Some operations have no COM API at all - associating a cable core with a
wire, creating a revision. These helpers let a caller look at the
SOLIDWORKS Electrical window and click or type in it.

Rules the helpers enforce, learned from driving the GUI on a PC the owner
was also using:

* every input is preceded by bringing SOLIDWORKS Electrical to the front and
  checking it really IS the foreground window; if another window holds focus
  the input is refused instead of typed into the wrong application;
* coordinates are relative to the SOLIDWORKS Electrical window, so a moved
  window does not send a click somewhere else;
* a screenshot is the way to see the result - nothing reports success by
  itself.
"""
from __future__ import annotations

import ctypes
import os
import tempfile
import time
from typing import Any

import win32api
import win32con
import win32gui
from PIL import ImageGrab

TITLE_HINT = "SOLIDWORKS Electrical"

# Make coordinates physical pixels on scaled displays.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # noqa: BLE001
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass


def _find_window(title_hint: str = TITLE_HINT) -> int:
    found: list[tuple[int, str]] = []

    def cb(hwnd: int, _: Any) -> None:
        if win32gui.IsWindowVisible(hwnd):
            t = win32gui.GetWindowText(hwnd)
            if title_hint.lower() in t.lower():
                found.append((hwnd, t))

    win32gui.EnumWindows(cb, None)
    if not found:
        raise LookupError(f"no visible window with {title_hint!r} in its title")
    # the main frame has the longest title (it carries the project name)
    found.sort(key=lambda h: -len(h[1]))
    return found[0][0]


def window_info(title_hint: str = TITLE_HINT) -> dict:
    hwnd = _find_window(title_hint)
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    fg = win32gui.GetForegroundWindow()
    return {"hwnd": hwnd, "title": win32gui.GetWindowText(hwnd),
            "rect": [l, t, r, b], "width": r - l, "height": b - t,
            "is_foreground": fg == hwnd,
            "foreground_title": win32gui.GetWindowText(fg)}


def _focus(hwnd: int) -> bool:
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

    def _ours(w: int) -> bool:
        # the main frame, or a dialog SOLIDWORKS opened on top of it. Pulling
        # the main frame forward while one of its dialogs is open moves the
        # keyboard focus off the dialog, and typed text is lost (2026-10-16:
        # cable-core filter and multiline text editor both stayed unchanged).
        return w == hwnd or win32gui.GetWindow(w, win32con.GW_OWNER) == hwnd \
            or win32gui.GetParent(w) == hwnd

    if not _ours(win32gui.GetForegroundWindow()):
        # Windows refuses SetForegroundWindow from a background process
        # unless an input event just happened. A SHIFT tap satisfies it; ALT
        # does too but leaves the SOLIDWORKS ribbon in keytip mode, which
        # then swallows the next wheel or click.
        win32api.keybd_event(win32con.VK_SHIFT, 0, 0, 0)
        win32api.keybd_event(win32con.VK_SHIFT, 0, win32con.KEYEVENTF_KEYUP, 0)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.25)
    return _ours(win32gui.GetForegroundWindow())


def screenshot(path: str | None = None, title_hint: str = TITLE_HINT,
               whole_screen: bool = False) -> dict:
    """Capture the SOLIDWORKS Electrical window (or the screen) to a PNG."""
    info = window_info(title_hint)
    bbox = None if whole_screen else tuple(info["rect"])
    img = ImageGrab.grab(bbox=bbox, all_screens=True)
    if path is None:
        fd, path = tempfile.mkstemp(prefix="swe_gui_", suffix=".png")
        os.close(fd)
    img.save(path)
    return {"ok": True, "path": path, "size": list(img.size),
            "origin": [0, 0] if whole_screen else info["rect"][:2],
            "window": info}


def _guard(title_hint: str) -> tuple[int, dict]:
    hwnd = _find_window(title_hint)
    if not _focus(hwnd):
        fg = win32gui.GetForegroundWindow()
        raise RuntimeError(
            "SOLIDWORKS Electrical is not the foreground window (foreground: "
            f"{win32gui.GetWindowText(fg)!r}); refusing to send input that "
            "would land in another application")
    return hwnd, window_info(title_hint)


def click(x: int, y: int, button: str = "left", double: bool = False,
          title_hint: str = TITLE_HINT) -> dict:
    """Click at (x, y) relative to the SOLIDWORKS Electrical window."""
    _, info = _guard(title_hint)
    sx, sy = info["rect"][0] + int(x), info["rect"][1] + int(y)
    win32api.SetCursorPos((sx, sy))
    time.sleep(0.05)
    down, up = {"left": (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP),
                "right": (win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP)}[button]
    for _ in range(2 if double else 1):
        win32api.mouse_event(down, 0, 0, 0, 0)
        win32api.mouse_event(up, 0, 0, 0, 0)
        time.sleep(0.08)
    return {"ok": True, "screen_xy": [sx, sy], "button": button,
            "double": double}


def move(x: int, y: int, title_hint: str = TITLE_HINT) -> dict:
    """Move the cursor to (x, y) in the window without clicking (hover;
    SOLIDWORKS shows the drawing coordinates under it in the status bar)."""
    _, info = _guard(title_hint)
    sx, sy = info["rect"][0] + int(x), info["rect"][1] + int(y)
    win32api.SetCursorPos((sx, sy))
    return {"ok": True, "screen_xy": [sx, sy]}


def scroll(x: int, y: int, clicks: int, title_hint: str = TITLE_HINT) -> dict:
    """Mouse-wheel at (x, y): positive zooms in on a SOLIDWORKS drawing."""
    _, info = _guard(title_hint)
    sx, sy = info["rect"][0] + int(x), info["rect"][1] + int(y)
    win32api.SetCursorPos((sx, sy))
    for _ in range(abs(int(clicks))):
        win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0,
                             120 if clicks > 0 else -120, 0)
        time.sleep(0.15)
    return {"ok": True, "screen_xy": [sx, sy], "clicks": clicks}


_VK = {"enter": win32con.VK_RETURN, "esc": win32con.VK_ESCAPE,
       "tab": win32con.VK_TAB, "up": win32con.VK_UP, "down": win32con.VK_DOWN,
       "left": win32con.VK_LEFT, "right": win32con.VK_RIGHT,
       "delete": win32con.VK_DELETE, "backspace": win32con.VK_BACK,
       "space": win32con.VK_SPACE, "f2": win32con.VK_F2, "f5": win32con.VK_F5,
       "ctrl": win32con.VK_CONTROL, "shift": win32con.VK_SHIFT,
       "alt": win32con.VK_MENU, "home": win32con.VK_HOME, "end": win32con.VK_END,
       "pageup": win32con.VK_PRIOR, "pagedown": win32con.VK_NEXT,
       "apps": win32con.VK_APPS,
       **{f"f{n}": win32con.VK_F1 + n - 1 for n in range(1, 13)}}


def keys(combo: str, title_hint: str = TITLE_HINT) -> dict:
    """Press a key or chord, e.g. "enter", "ctrl+a", "esc", "alt+f4" is refused."""
    if combo.lower().replace(" ", "") in ("alt+f4", "ctrl+w"):
        raise ValueError("closing SOLIDWORKS from a key chord is not allowed")
    _guard(title_hint)
    parts = [p.strip().lower() for p in combo.split("+")]
    codes = []
    for p in parts:
        if p in _VK:
            codes.append(_VK[p])
        elif len(p) == 1:
            codes.append(win32api.VkKeyScan(p) & 0xFF)
        else:
            raise ValueError(f"unknown key {p!r}")
    for c in codes:
        win32api.keybd_event(c, 0, 0, 0)
    for c in reversed(codes):
        win32api.keybd_event(c, 0, win32con.KEYEVENTF_KEYUP, 0)
    return {"ok": True, "keys": combo}


def type_text(text: str, title_hint: str = TITLE_HINT) -> dict:
    """Type text into the focused SOLIDWORKS control (Unicode-safe)."""
    _guard(title_hint)
    # Virtual-key presses, not SendInput/KEYEVENTF_UNICODE: the Unicode route
    # reached nothing in SOLIDWORKS dialogs (2026-10-16, Associate cable
    # cores filter box stayed empty).
    for ch in text:
        vk = win32api.VkKeyScan(ch)
        if vk == -1:
            raise ValueError(f"cannot type {ch!r} on this keyboard layout")
        code, mods = vk & 0xFF, (vk >> 8) & 0x07
        # modifier bits: 1 shift, 2 ctrl, 4 alt (ctrl+alt = AltGr, needed for
        # characters like backslash on a Nordic layout)
        held = [k for bit, k in ((1, win32con.VK_SHIFT), (2, win32con.VK_CONTROL),
                                 (4, win32con.VK_MENU)) if mods & bit]
        for k in held:
            win32api.keybd_event(k, 0, 0, 0)
        win32api.keybd_event(code, 0, 0, 0)
        win32api.keybd_event(code, 0, win32con.KEYEVENTF_KEYUP, 0)
        for k in reversed(held):
            win32api.keybd_event(k, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.02)
    return {"ok": True, "typed": len(text)}


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _U)]


def _send_unicode(code: int, flag: int) -> None:
    for up in (0, 0x0002):
        inp = _INPUT(type=1)
        inp.ki = _KEYBDINPUT(0, code, flag | up, 0, None)
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
