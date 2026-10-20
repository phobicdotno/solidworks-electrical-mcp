"""Regression test: page marks in a multi-book project, and a component that
goes with its last symbol. No SOLIDWORKS needed.

Observed 2026-10-20 on a three-book project (117 folios): page "14" exists
in two books, so delete_component(tag, pages=["14"]) and find_folio("14")
were refused as ambiguous, delete_component had no way to name a folio id,
and pages=[30604] was read as a page mark. The same session saw
remove_symbols take a device's last footprint and SOLIDWORKS Electrical
delete the device with it, so the delete_component that followed answered
"no component with id".

Cases:
  A. an ambiguous page mark is refused, and the refusal names folio AND
     book ids
  B. book_id resolves the mark to that book's page
  C. an int page that matches no mark is tried as a folio id
  D. a mark nobody has is still refused (the id fallback is not a guess)
  E. delete_component(file_ids=[...]) sweeps exactly those folios
  F. delete_component(pages, book_id) resolves per book; without book_id the
     old ambiguity error still comes through
  G. the sweep removes the last symbol, SOLIDWORKS deletes the component,
     the retried remove fails: reported as success with a note
  H. remove_symbols lists the components that went with their last symbol,
     with the tag read before they went
  I. remove_symbol (singular) reports the same
  J. delete_component on a component that is already gone says why

Run directly:
    .venv/Scripts/python.exe tests/test_multibook_delete.py
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


# --------------------------------------------------------------------------
# Fakes. Late-bound COM returns (value, rc) tuples; the fakes do the same so
# the unwrapping in the workflows is exercised.


class FakeFolio:
    def __init__(self, fid, tag, book, folder=-1, position=0):
        self.fid, self.tag, self.book = fid, tag, book
        self.folder, self.position = folder, position
        self._open = False
        self.log = []

    def getID(self):
        return self.fid

    def getTag(self):
        return (self.tag, 0)

    def getTagNumber(self):
        return (int(self.tag), 0)

    def getDescription(self, lang):
        return (f"page {self.tag}", 0)

    def getFileType(self):
        return (12, 0)

    def getPosition(self):
        return (self.position, 0)

    def getEwProjectBookID(self):
        return (self.book, 0)

    def getEwProjectFolderID(self):
        return (self.folder, 0)

    def getLocationID(self):
        return (3, 0)

    def isOpen(self):
        return (self._open, 0)

    def close(self):
        self.log.append("close")
        self._open = False
        return 0

    def open(self):
        self.log.append("open")
        self._open = True
        return 0


class FakeFileMgr:
    def __init__(self, folios):
        self.folios = list(folios)

    def getEwProjectFileArray(self):
        return (list(self.folios), 0)

    def findEwProjectFileByID(self, fid):
        for f in self.folios:
            if f.fid == int(fid):
                return (f, 0)
        return (None, 22)


class FakeComponent:
    def __init__(self, cid, tag, project):
        self.cid, self.tag, self.project = cid, tag, project
        self.removed = False

    def getID(self):
        return self.cid

    def getTag(self):
        return (self.tag, 0)

    def getTagPath(self):
        return (f"=F1+L1-{self.tag}", 0)

    def getParentID(self):
        return (0, 0)

    def getLocationID(self):
        return (3, 0)

    def getType(self):
        return (0, 0)

    def getChildrenCount(self):
        return (0, 0)

    raises_when_gone = False

    def remove(self):
        if self.removed and self.raises_when_gone:
            raise OSError("com_error: object is gone")
        if self.removed:
            return 22                       # EW_OBJECT_NOT_FOUND
        if self.project.symbols_of(self.cid):
            return 35                       # EW_CANNOT_REMOVE
        self.removed = True
        return 0


class FakeSymbol:
    def __init__(self, sid, folio, component, stype=20, project=None):
        self.sid, self.folio, self.component = sid, folio, component
        self.stype, self.project = stype, project
        self.removed = False
        self.x = self.y = 10.0

    def getID(self):
        return self.sid

    def getFileID(self):
        return (self.folio.fid, 0)

    def getObjectID(self):
        return (self.component.cid, 0)

    def getEwSymbolType(self):
        return (self.stype, 0)

    def getEwSymbolName(self):
        return ("SYM", 0)

    def getXPosition(self):
        return (self.x, 0)

    def getYPosition(self):
        return (self.y, 0)

    def remove(self):
        self.removed = True
        self.folio.log.append(("remove", self.sid))
        # What SOLIDWORKS Electrical does: a component with no symbol left
        # is deleted together with the last one.
        if self.stype not in wf.NON_COMPONENT_SYMBOL_TYPES \
                and not self.project.symbols_of(self.component.cid):
            self.component.removed = True
        return 0


class FakeSymMgr:
    def __init__(self, project):
        self.project = project

    def getProjectSymbolsFromFileID(self, fid):
        return ([s for s in self.project.symbols
                 if not s.removed and s.folio.fid == fid], 0)

    def getProjectSymbolByID(self, sid):
        for s in self.project.symbols:
            if s.sid == int(sid) and not s.removed:
                return (s, 0)
        return (None, 22)


class FakeCompMgr:
    def __init__(self, project):
        self.project = project

    def findEwProjectComponentByID(self, cid):
        for c in self.project.components:
            if c.cid == int(cid) and not c.removed:
                return (c, 0)
        return (None, 22)

    def getEwProjectComponentArray(self):
        return ([c for c in self.project.components if not c.removed], 0)


class FakeProject:
    def __init__(self, folios):
        self.folios = folios
        self.components: list[FakeComponent] = []
        self.symbols: list[FakeSymbol] = []

    def component(self, cid, tag):
        c = FakeComponent(cid, tag, self)
        self.components.append(c)
        return c

    def symbol(self, sid, folio, comp, stype=20):
        s = FakeSymbol(sid, folio, comp, stype, self)
        self.symbols.append(s)
        return s

    def symbols_of(self, cid):
        return [s for s in self.symbols
                if not s.removed and s.component.cid == cid
                and s.stype not in wf.NON_COMPONENT_SYMBOL_TYPES]

    def getEwProjectFileManager(self):
        return (FakeFileMgr(self.folios), 0)

    def getEwProjectSymbolManager(self):
        return (FakeSymMgr(self), 0)

    def getEwProjectComponentManager(self):
        return (FakeCompMgr(self), 0)


class FakeApp:
    def __init__(self, proj):
        self.proj = proj

    def getEwProjectCurrent(self):
        return (self.proj, 0)


class FakeClient:
    @staticmethod
    def Dispatch(x):
        return x


CLIENT = FakeClient()


def world():
    """Two books, each with a page "14"; a device drawn once per book."""
    f14a = FakeFolio(30604, "14", book=1, folder=22086, position=17)
    f14b = FakeFolio(44307, "14", book=44914, folder=43356, position=42)
    f18 = FakeFolio(22089, "18", book=1, folder=22086, position=21)
    proj = FakeProject([f14a, f14b, f18])
    return FakeApp(proj), proj, f14a, f14b, f18


ORIG_LINES = wf._folio_lines
ORIG_POINTS = wf._symbol_points
wf._folio_lines = lambda app, client, fid: []
wf._symbol_points = lambda s: [{"i": 0, "mesh": 1, "x": s.x, "y": s.y}]

try:
    mark = len(failures)
    # --- A: ambiguity names both the folio ids and the books -------------
    app, proj, f14a, f14b, _ = world()
    try:
        wf.find_folio(app, CLIENT, page="14")
        check(False, "A: page '14' in two books must be refused")
    except LookupError as e:
        msg = str(e)
        check("30604" in msg and "44307" in msg,
              f"A: the refusal must list the folio ids, got {msg!r}")
        check("44914" in msg and "book_id" in msg,
              f"A: the refusal must name the books and the way out, got "
              f"{msg!r}")
    ok("A ok: an ambiguous mark is refused with folio and book ids", mark)

    mark = len(failures)
    # --- B: book_id picks the book ---------------------------------------
    f = wf.find_folio(app, CLIENT, page="14", book_id=44914)
    check(f is f14b, f"B: book 44914's page 14 is 44307, got {f.fid}")
    f = wf.find_folio(app, CLIENT, page=14, book_id=1)
    check(f is f14a, f"B: book 1's page 14 is 30604, got {f.fid}")
    try:
        wf.find_folio(app, CLIENT, page="18", book_id=44914)
        check(False, "B: page 18 does not exist in book 44914")
    except LookupError as e:
        check("book 44914" in str(e),
              f"B: the refusal must say which book was searched, got {e!r}")
    ok("B ok: book_id narrows a page mark to one book", mark)

    mark = len(failures)
    # --- C: an int that is no page mark is tried as a folio id -----------
    f = wf.find_folio(app, CLIENT, page=30604)
    check(f is f14a, f"C: 30604 matches no mark and is folio 30604, got {f}")
    try:
        wf.find_folio(app, CLIENT, page="30604")
        check(False, "C: a string is a page mark, never an id")
    except LookupError:
        pass
    ok("C ok: pages=[30604] reaches folio 30604; '30604' stays a mark", mark)

    mark = len(failures)
    # --- D: no mark, no id -> refused -------------------------------------
    try:
        wf.find_folio(app, CLIENT, page=999)
        check(False, "D: 999 is neither a mark nor an id")
    except LookupError as e:
        check("999" in str(e), f"D: got {e!r}")
    ok("D ok: the id fallback never guesses", mark)

    mark = len(failures)
    # --- E: file_ids sweep exactly those folios --------------------------
    app, proj, f14a, f14b, f18 = world()
    k1 = proj.component(22354, "K1")
    proj.symbol(1, f14a, k1)
    proj.symbol(2, f14b, k1)
    r = wf.delete_component(app, CLIENT, tag="K1", file_ids=[30604])
    check(not r["ok"] and r["rc"] == 35,
          f"E: with a symbol left in the other book the remove must still "
          f"be blocked, got {r}")
    check([s["symbol_id"] for s in r["symbols_removed"]] == [1],
          f"E: only folio 30604's symbol goes, got {r['symbols_removed']}")
    check(not r["swept_all_folios"], "E: file_ids must not mean a full sweep")
    ok("E ok: file_ids names the folios to sweep", mark)

    mark = len(failures)
    # --- F: pages + book_id --------------------------------------------
    app, proj, f14a, f14b, f18 = world()
    k2 = proj.component(100, "K2")
    proj.symbol(3, f14a, k2)
    proj.symbol(4, f18, k2)
    try:
        wf.delete_component(app, CLIENT, tag="K2", pages=["14"])
        check(False, "F: pages=['14'] without book_id is still ambiguous")
    except LookupError as e:
        check("ambiguous" in str(e), f"F: got {e!r}")
    r = wf.delete_component(app, CLIENT, tag="K2", pages=["14", "18"],
                            book_id=1)
    check(r["ok"], f"F: both symbols are in book 1, got {r}")
    check(sorted(s["symbol_id"] for s in r["symbols_removed"]) == [3, 4],
          f"F: got {r['symbols_removed']}")
    ok("F ok: pages resolve inside book_id", mark)

    mark = len(failures)
    # --- G: the last symbol takes the component with it ------------------
    app, proj, f14a, f14b, f18 = world()
    k3 = proj.component(200, "K3")
    proj.symbol(5, f14a, k3)
    r = wf.delete_component(app, CLIENT, component_id=200, file_ids=[30604])
    check(k3.removed, "G: the fake must have deleted the component")
    check(r["ok"], f"G: a component that went with its last symbol is the "
                   f"job done, got {r}")
    check(r["gone_with_last_symbol"] and r["note"]
          and "last symbol" in r["note"],
          f"G: the result must say why the retry failed, got {r}")
    check(r["rc"] == 22, f"G: the raw rc stays visible, got {r['rc']}")
    # The COM object may raise instead of answering a code once its row
    # is gone; the outcome must be the same.
    app, proj, f14a, f14b, f18 = world()
    k3b = proj.component(201, "K3B")
    k3b.raises_when_gone = True
    proj.symbol(5, f14a, k3b)
    r = wf.delete_component(app, CLIENT, component_id=201, file_ids=[30604])
    check(r["ok"] and r["gone_with_last_symbol"] and "OSError" in r["note"],
          f"G: a raising remove on a gone component is still success, "
          f"got {r}")
    ok("G ok: component gone with its last symbol counts as removed", mark)

    mark = len(failures)
    # --- H: remove_symbols reports the components that went --------------
    app, proj, f14a, f14b, f18 = world()
    k4 = proj.component(300, "K4")
    k5 = proj.component(301, "K5")
    proj.symbol(6, f14a, k4)
    proj.symbol(7, f14a, k5)
    proj.symbol(8, f18, k5)
    proj.symbol(9, f14a, k4, stype=130)          # a location label
    r = wf.remove_symbols(app, CLIENT, symbol_ids=[6, 7, 9])
    check(r["ok"] and r["removed"] == 3, f"H: got {r}")
    check(r["components_removed"] == [{"component_id": 300, "tag": "K4"}],
          f"H: K4 lost its last symbol, K5 still has one on page 18, got "
          f"{r['components_removed']}")
    check("K4" in r["note"] and "delete_component" in r["note"],
          f"H: the note must name the device and warn off delete_component, "
          f"got {r['note']!r}")
    ok("H ok: remove_symbols lists the components it took with it", mark)

    mark = len(failures)
    # --- I: the singular form reports the same ---------------------------
    r = wf.remove_symbol(app, CLIENT, symbol_id=8)
    check(r["ok"] and r["components_removed"] == [
        {"component_id": 301, "tag": "K5"}], f"I: got {r}")
    check("K5" in (r["note"] or ""), f"I: got {r['note']!r}")
    ok("I ok: remove_symbol reports the component it took with it", mark)

    mark = len(failures)
    # --- J: deleting what is already gone says why -----------------------
    try:
        wf.delete_component(app, CLIENT, component_id=300)
        check(False, "J: K4 is gone")
    except LookupError as e:
        check("last one" in str(e) and "find_component" in str(e),
              f"J: the error must point at the symbol removal, got {e!r}")
    ok("J ok: a missing component points at the symbol removal", mark)
finally:
    wf._folio_lines = ORIG_LINES
    wf._symbol_points = ORIG_POINTS

print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("all multi-book / last-symbol checks passed")
