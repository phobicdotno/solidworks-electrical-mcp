"""Regression test: batched symbol ops close the folio ONCE, not once per unit.

Observed 2026-09-24: laying out a twelve-device cabinet page took SOLIDWORKS
Electrical down. place_symbol and remove_symbol each close and reopen the
folio around every single insert or delete (a symbol written into a folio the
GUI has open is discarded when the editor saves its copy back), so one page
meant twenty-four editor close/open cycles. place_symbols and remove_symbols
do the whole page in one cycle.

The point of this test is the cycle COUNT, which no return value reveals, plus
the per-item behaviour that must survive batching: a bad placement is rejected
and taken back out while its neighbours still go in.

Cases:
  A. twelve placements -> exactly one close and one open, twelve inserts
  B. an origin outside the box -> whole batch refused BEFORE the folio is
     touched, so nothing is half-applied
  C. a symbol whose connection point lands outside the box -> that one is
     removed, the others stay, ok is False, and the folio still reopens once
  D. a closed folio is never reopened (was_open drives both ends)
  E. remove_symbols(all_on_page) -> one close and one open for the whole page
  F. remove_symbols spanning two folios -> the foreign id is reported, not
     crashed on, and the rest still go

Run directly:
    .venv/Scripts/python.exe tests/test_batch_symbol_ops.py
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from solidworks_electrical_mcp import workflows as wf  # noqa: E402

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)
        print("FAIL:", msg)


def ok(msg: str, mark: int) -> None:
    """Report a case as passed only if it added no failures."""
    if len(failures) == mark:
        print(msg)


class FakeSymbol:
    def __init__(self, sid, folio):
        self.sid, self.folio = sid, folio
        self.x = self.y = 0.0
        self.removed = False

    def setObjectID(self, v):
        return 0

    def setEwSymbolName(self, v):
        return 0

    def setXPosition(self, v):
        self.x = v
        return 0

    def setYPosition(self, v):
        self.y = v
        return 0

    def setRotationAngle(self, v):
        return 0

    def setXScale(self, v):
        return 0

    def setYScale(self, v):
        return 0

    def insert(self):
        return 0

    def getID(self):
        return self.sid

    def getFileID(self):
        return self.folio.fid

    def remove(self):
        self.removed = True
        self.folio.log.append(("remove", self.sid))
        return 0


class FakeFolio:
    """Records every close/open so the test can count editor cycles."""

    def __init__(self, fid=7, is_open=True):
        self.fid, self._open, self.log = fid, is_open, []
        self.made, self._next = [], 100

    def getID(self):
        return self.fid

    def isOpen(self):
        return self._open

    def close(self):
        self.log.append("close")
        self._open = False
        return 0

    def open(self):
        self.log.append("open")
        self._open = True
        return 0

    def newEwProjectSymbolFromSymbolType(self, t):
        self._next += 1
        s = FakeSymbol(self._next, self)
        self.made.append(s)
        self.log.append(("insert", self._next))
        return s

    @property
    def closes(self):
        return self.log.count("close")

    @property
    def opens(self):
        return self.log.count("open")


class FakeComponent:
    def __init__(self, tag):
        self.tag = tag

    def getID(self):
        return 1

    def getTag(self):
        return self.tag


class FakeSymMgr:
    def __init__(self, syms):
        self.syms = {s.sid: s for s in syms}

    def getProjectSymbolsFromFileID(self, fid):
        return [s for s in self.syms.values() if s.folio.fid == fid]

    def getProjectSymbolByID(self, sid):
        return self.syms.get(int(sid))


class FakeProject:
    def __init__(self, mgr):
        self.mgr = mgr

    def getEwProjectSymbolManager(self):
        return self.mgr


ORIGINALS = {name: getattr(wf, name) for name in
             ("find_folio", "_folio_row", "_find_one_component",
              "_symbol_points", "_folio_lines", "_project")}


def patch(folio):
    """Swap the COM-touching helpers for fakes."""
    wf.find_folio = lambda app, client, **kw: folio
    wf._folio_row = lambda f: {"id": f.fid, "is_open": f.isOpen(),
                               "tag": str(f.fid)}
    wf._find_one_component = lambda app, client, tag: FakeComponent(tag)
    wf._symbol_points = lambda s: [{"i": 0, "mesh": 1, "x": s.x, "y": s.y}]
    wf._folio_lines = lambda app, client, fid: []
    wf._project = lambda app: app


def unpatch():
    for k, v in ORIGINALS.items():
        setattr(wf, k, v)


def rows(n, y=160.0):
    return [{"tag": "K%d" % i, "symbol_name": "859-304", "x": 100.0 + i * 6,
             "y": y, "symbol_type": 105, "rotation": -90.0}
            for i in range(n)]


try:
    mark = len(failures)
    # --- A: the whole page in one editor cycle ---------------------------
    folio = FakeFolio()
    patch(folio)
    r = wf.place_symbols(None, None, placements=rows(12), page="102",
                         dry_run=False)
    check(r["ok"], "A: batch should succeed, got %s" % r.get("error"))
    check(r["placed"] == 12, "A: expected 12 placed, got %s" % r.get("placed"))
    check(folio.closes == 1,
          "A: folio must close ONCE for the page, closed %dx" % folio.closes)
    check(folio.opens == 1,
          "A: folio must reopen ONCE, opened %dx" % folio.opens)
    ok("A ok: 12 devices, %d close / %d open"
       % (folio.closes, folio.opens), mark)

    mark = len(failures)
    # --- B: validation happens before anything is touched ----------------
    folio2 = FakeFolio()
    patch(folio2)
    bad = rows(3) + [{"tag": "KX", "symbol_name": "859-304", "x": 999.0,
                      "y": 160.0, "symbol_type": 105}]
    r = wf.place_symbols(None, None, placements=bad, page="102",
                         dry_run=False)
    check(not r["ok"], "B: a placement outside the box must fail the batch")
    check(folio2.closes == 0 and not folio2.made,
          "B: nothing may be inserted when validation fails; closes=%d made=%d"
          % (folio2.closes, len(folio2.made)))
    ok("B ok: out-of-box origin refused before the folio was touched", mark)

    mark = len(failures)
    # --- C: one stray symbol is removed, its neighbours survive ----------
    folio3 = FakeFolio()
    patch(folio3)

    def stray_points(sym):
        # the third symbol inserted drops a connection point off the sheet
        if sym.sid == 103:
            return [{"i": 0, "mesh": 1, "x": 5.0, "y": 160.0}]
        return [{"i": 0, "mesh": 1, "x": sym.x, "y": sym.y}]

    wf._symbol_points = stray_points
    r = wf.place_symbols(None, None, placements=rows(5), page="102",
                         dry_run=False)
    check(not r["ok"], "C: batch with a stray point must report ok False")
    check(r["placed"] == 4 and r["failed"] == 1,
          "C: expected 4 placed / 1 failed, got %s/%s"
          % (r["placed"], r["failed"]))
    check(("remove", 103) in folio3.log,
          "C: the offending symbol must be taken back out")
    check(folio3.closes == 1 and folio3.opens == 1,
          "C: still one cycle, got %d/%d" % (folio3.closes, folio3.opens))
    ok("C ok: 1 of 5 rejected and removed, neighbours kept, one cycle", mark)

    mark = len(failures)
    # --- D: a folio the GUI does not have open stays untouched -----------
    folio4 = FakeFolio(is_open=False)
    patch(folio4)
    r = wf.place_symbols(None, None, placements=rows(4), page="102",
                         dry_run=False)
    check(r["ok"], "D: closed-folio batch should succeed")
    check(folio4.closes == 0 and folio4.opens == 0,
          "D: a closed folio must stay untouched, got %d close / %d open"
          % (folio4.closes, folio4.opens))
    ok("D ok: closed folio neither closed nor reopened", mark)

    mark = len(failures)
    # --- E: clearing a page is one cycle ---------------------------------
    folio5 = FakeFolio()
    syms = [FakeSymbol(200 + i, folio5) for i in range(12)]
    other = FakeFolio(fid=9)
    foreign = FakeSymbol(999, other)
    patch(folio5)
    wf._project = lambda app: FakeProject(FakeSymMgr(syms + [foreign]))
    r = wf.remove_symbols(None, None, page="102", all_on_page=True)
    check(r["ok"], "E: clear-page should succeed, got %s" % r.get("error"))
    check(r["removed"] == 12,
          "E: expected 12 removed, got %s" % r.get("removed"))
    check(folio5.closes == 1 and folio5.opens == 1,
          "E: clearing a page must be ONE cycle, got %d close / %d open"
          % (folio5.closes, folio5.opens))
    ok("E ok: cleared 12, %d close / %d open"
       % (folio5.closes, folio5.opens), mark)

    mark = len(failures)
    # --- F: a symbol on another folio is reported, not crashed on --------
    folio6 = FakeFolio()
    keep = [FakeSymbol(300 + i, folio6) for i in range(3)]
    patch(folio6)
    wf._project = lambda app: FakeProject(FakeSymMgr(keep + [foreign]))
    r = wf.remove_symbols(None, None, symbol_ids=[300, 999, 301])
    check(not r["ok"], "F: a foreign symbol id must fail the batch")
    check(r["removed"] == 2,
          "F: the two same-folio symbols should still go, got %s"
          % r["removed"])
    check(any("another folio" in str(x.get("error", ""))
              for x in r["results"]),
          "F: the foreign id must be named, not crashed on")
    check(folio6.closes == 1 and folio6.opens == 1, "F: still one cycle")
    ok("F ok: foreign id reported, the other two removed, one cycle", mark)
finally:
    unpatch()

print()
if failures:
    print("%d FAILURE(S)" % len(failures))
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("all batch symbol-op checks passed")
