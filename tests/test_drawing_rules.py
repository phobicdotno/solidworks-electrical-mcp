"""Regression test: the drawing rules Karstein set, checked without the app.

Three rules, all from the SeaLeopard project (2026-09-22):

  "dot to dot is minimum distance" - the sheet grid is 10 mm, so two
  connection points of different symbols may sit on adjacent grid dots but no
  closer. Corroborated on the project: 20 mm is by far the most common gap
  between points of different symbols.

  "the text is actually 3x gridpitch wide" - a device's LABEL is wider than
  its connections, so two symbols sharing a row need their ORIGINS 30 mm
  apart or the labels collide even though the points are legally spaced.
  Every relay row on the project is drawn at 30 mm origin spacing. This was
  documented but never actually implemented: MIN_TEXT_SYMBOL_ORIGIN_SPACING
  was defined and then used nowhere.

  "keep all lines and end points within these lines" - the drawable box is
  x 50..370, y 80..240.

The label rule must NOT fire on a 2D cabinet layout, where devices are drawn
at their real width and physically abut: the ten relays on the CC100 rail sit
6 mm apart and are correct.

Cases:
  A. points 20 mm apart on a row are fine; 10 mm is the floor, not an error
  B. points closer than a grid step are reported, and coincident points too
  C. points on different rows and columns never crowd, however close
  D. symbols 30 mm apart on a row are fine; 20 mm collides labels
  E. the label rule is NOT applied to a 2D cabinet layout
  F. a label-type symbol is not treated as a device for the label rule, and
     only ADJACENT symbols are compared so one bad row is not N-squared
     reports of the same thing
  G. points and line ends outside the drawable box are reported

Run directly:
    .venv/Scripts/python.exe tests/test_drawing_rules.py
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from solidworks_electrical_mcp import workflows as wf

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)
        print("FAIL:", msg)


def ok(msg: str, mark: int) -> None:
    if len(failures) == mark:
        print(msg)


class FakeSymbol:
    def __init__(self, sid, tag, x, y, points, stype=20):
        self.sid, self.tag, self.x, self.y = sid, tag, x, y
        self.points, self.stype = points, stype

    def getID(self):
        return self.sid

    def getObjectID(self):
        return self.sid

    def getEwSymbolName(self):
        return "sym"

    def getEwSymbolType(self):
        return self.stype

    def getXPosition(self):
        return self.x

    def getYPosition(self):
        return self.y


class FakeComponent:
    def __init__(self, tag):
        self.tag = tag

    def getTag(self):
        return self.tag


class FakeCompMgr:
    def __init__(self, syms):
        self.syms = {s.sid: s for s in syms}

    def findEwProjectComponentByID(self, oid):
        s = self.syms.get(oid)
        return FakeComponent(s.tag) if s else None


class FakeSymMgr:
    def __init__(self, syms):
        self.syms = syms

    def getProjectSymbolsFromFileID(self, fid):
        return self.syms


class FakeProject:
    def __init__(self, syms):
        self.syms = syms

    def getEwProjectSymbolManager(self):
        return FakeSymMgr(self.syms)

    def getEwProjectComponentManager(self):
        return FakeCompMgr(self.syms)


def run(syms, lines=(), file_type=12, **kw):
    """file_type 12 is a schematic; 9 is a 2D cabinet layout."""
    orig = {n: getattr(wf, n) for n in
            ("find_folio", "_folio_row", "_project", "_each",
             "_symbol_points", "_folio_lines")}
    wf.find_folio = lambda app, client, **k: object()
    wf._folio_row = lambda f: {"id": 1, "page": "59", "is_open": False,
                               "file_type_code": file_type}
    wf._project = lambda app: FakeProject(syms)
    wf._each = lambda client, arr: list(arr or ())
    wf._symbol_points = lambda s: [
        {"i": i, "mesh": i, "x": px, "y": py}
        for i, (px, py) in enumerate(s.points)]
    wf._folio_lines = lambda app, client, fid: list(lines)
    try:
        return wf.check_drawing_rules(None, None, page="59", **kw)
    finally:
        for n, v in orig.items():
            setattr(wf, n, v)


mark = len(failures)
# --- A: the legal spacings ------------------------------------------------
r = run([FakeSymbol(1, "K1", 100.0, 160.0, [(100.0, 160.0)]),
         FakeSymbol(2, "K2", 130.0, 160.0, [(120.0, 160.0)])])
check(r["crowded_count"] == 0,
      f"A: 20 mm between points is legal, got {r['crowded']}")
r2 = run([FakeSymbol(1, "K1", 100.0, 160.0, [(100.0, 160.0)]),
          FakeSymbol(2, "K2", 140.0, 160.0, [(110.0, 160.0)])])
check(r2["crowded_count"] == 0,
      f"A: 10 mm (dot to dot) is the floor, not an error, got {r2['crowded']}")
ok("A ok: 20 mm clear, 10 mm dot-to-dot accepted", mark)

mark = len(failures)
# --- B: too close, and exactly on top -------------------------------------
r = run([FakeSymbol(1, "K1", 100.0, 160.0, [(100.0, 160.0)]),
         FakeSymbol(2, "K2", 140.0, 160.0, [(105.0, 160.0)])])
check(r["crowded_count"] == 1 and r["crowded"][0]["gap"] == 5.0,
      f"B: 5 mm apart must be reported, got {r['crowded']}")
r = run([FakeSymbol(1, "K1", 100.0, 160.0, [(100.0, 160.0)]),
         FakeSymbol(2, "K2", 140.0, 160.0, [(100.0, 160.0)])])
check(r["crowded_count"] == 1
      and r["crowded"][0]["axis"] == "coincident",
      f"B: coincident points must be called out, got {r['crowded']}")
ok("B ok: 5 mm reported, coincident points flagged", mark)

mark = len(failures)
# --- C: off-row and off-column points never crowd -------------------------
r = run([FakeSymbol(1, "K1", 100.0, 160.0, [(100.0, 160.0)]),
         FakeSymbol(2, "K2", 140.0, 200.0, [(102.0, 162.0)])])
check(r["crowded_count"] == 0,
      f"C: points sharing neither row nor column cannot crowd, got "
      f"{r['crowded']}")
ok("C ok: diagonal neighbours are not crowding", mark)

mark = len(failures)
# --- D: the label-width rule ----------------------------------------------
# Points are a legal 20 mm apart in both, so only the origin spacing differs.
r = run([FakeSymbol(1, "K1", 100.0, 160.0, [(100.0, 160.0)]),
         FakeSymbol(2, "K2", 130.0, 160.0, [(120.0, 160.0)])])
check(r["cramped_count"] == 0,
      f"D: 30 mm between origins is the project spacing, got "
      f"{r['cramped_labels']}")
r = run([FakeSymbol(1, "K1", 100.0, 160.0, [(100.0, 160.0)]),
         FakeSymbol(2, "K2", 120.0, 160.0, [(140.0, 160.0)])])
check(r["cramped_count"] == 1 and r["cramped_labels"][0]["gap"] == 20.0,
      f"D: 20 mm between origins collides the labels, got "
      f"{r['cramped_labels']}")
check(not r["ok"], "D: a colliding label must make the page not ok")
ok("D ok: 30 mm origins clear, 20 mm reported as colliding labels", mark)

mark = len(failures)
# --- E: a cabinet layout is exempt ----------------------------------------
# The ten CC100 relays sit 6 mm apart at their real width and are correct.
rail = [FakeSymbol(i, f"CC_K{i}", 188.4 + i * 6.0, 160.0, [], stype=105)
        for i in range(10)]
r = run(rail, file_type=9)
check(r["cramped_count"] == 0,
      f"E: the label rule must not fire on a cabinet layout, got "
      f"{r['cramped_count']} reports")
check(r["ok"], f"E: the relay rail should pass, got {r}")
# the same rail on a schematic WOULD be reported
r2 = run(rail, file_type=12)
# Adjacent pairs only: nine gaps along a row of ten, not all 45 pairings.
check(r2["cramped_count"] == 9,
      f"E: on a schematic the row must give 9 adjacent reports, got "
      f"{r2['cramped_count']}")
ok("E ok: 6 mm rail fine on a layout, flagged on a schematic", mark)

mark = len(failures)
# --- F: label symbols are not devices -------------------------------------
# Types 110-135 are cable/wire/location labels, not devices with their own
# mark, so they must not trip the device-label rule.
r = run([FakeSymbol(1, "K1", 100.0, 160.0, []),
         FakeSymbol(2, None, 105.0, 160.0, [], stype=120)])
check(r["cramped_count"] == 0,
      f"F: a wire label beside a device is not a label collision, got "
      f"{r['cramped_labels']}")
ok("F ok: label-type symbols excluded from the device rule", mark)

mark = len(failures)
# --- G: everything must stay inside the drawable box ----------------------
r = run([FakeSymbol(1, "K1", 60.0, 160.0, [(60.0, 78.0)])],
        lines=[{"id": 7, "x1": 60.0, "y1": 100.0, "x2": 400.0, "y2": 100.0}])
kinds = {o["kind"] for o in r["outside_box"]}
check(r["outside_count"] == 2 and kinds == {"connection point", "line end"},
      f"G: the low point and the long line end must both be reported, got "
      f"{r['outside_box']}")
ok("G ok: point below the box and line end past it both reported", mark)

print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("all drawing-rule checks passed")
