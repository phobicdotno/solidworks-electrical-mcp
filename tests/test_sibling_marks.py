"""Regression test: a mark only has to be unique among its siblings.

SOLIDWORKS Electrical builds a component's tag path from its function, its
location, its parent chain and its own mark. Two PLC racks each have a first
module tagged ``N1`` (``-N1-N1`` and ``-N3-N1``), and that is legal.

rename_component, renumber_components, clone_component and add_component used
to test the new mark against every component in the project. Renaming a rack
coupler from ``N3`` to ``N1`` was then refused as "already exists more than
once", because the first module of every rack is also called ``N1``.

Cases:
  A. renaming a coupler onto a mark that only children of other parents use
     succeeds
  B. renaming onto a mark a real sibling holds (same parent, location,
     function) is still refused
  C. a renumber may give a module a mark that a module of another rack holds
  D. a renumber onto a sibling's mark is still refused
  E. the PDF export finds the file SOLIDWORKS actually writes ("1-out.pdf"
     for "out.pdf") and hands it back under the requested name

Run directly:
    .venv/Scripts/python.exe tests/test_sibling_marks.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.environ.get("SWE_SRC") or str(REPO / "src"))

from solidworks_electrical_mcp import workflows as wf

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)
        print("FAIL:", msg)


class Comp:
    def __init__(self, cid, tag, parent=-1, location=10, function=1):
        self.cid, self.tag = cid, tag
        self.parent, self.location, self.function = parent, location, function
        self.root, self.number = wf._split_mark(tag)[:2]

    def getID(self): return self.cid
    def getTag(self): return self.tag
    def getTagRoot(self): return self.root
    def getTagNumber(self): return self.number
    def getParentID(self): return self.parent
    def getLocationID(self): return self.location
    def getFunctionID(self): return self.function
    def getDescription(self, lang="en"): return ""
    def getType(self): return 0
    def getChildrenCount(self): return 0

    def getTagPath(self):
        return f"=F1+L1-{self.tag}" if self.parent == -1 else \
            f"=F1+L1-P{self.parent}-{self.tag}"

    def setTag(self, v): self.tag = v; return 0
    def setTagRoot(self, v): self.root = v; return 0
    def setTagNumber(self, v): self.number = v; return 0
    def update(self): return 0


class Mgr:
    def __init__(self, comps): self.comps = comps
    def getEwProjectComponentArray(self): return self.comps


class Proj:
    def __init__(self, comps): self.comps = comps
    def getEwProjectComponentManager(self): return Mgr(self.comps)


def two_racks():
    """Rack coupler N1 (id 1) and N3 (id 2), each with modules N1..N3."""
    comps = [Comp(1, "N1"), Comp(2, "N3")]
    for rack in (1, 2):
        for i in range(1, 4):
            comps.append(Comp(rack * 10 + i, f"N{i}", parent=rack))
    return comps


def with_fakes(comps, fn):
    orig = {n: getattr(wf, n) for n in
            ("_project", "_each", "_find_one_component", "_symbols_bound_to",
             "_text_references", "_text_references_multi", "_component_row",
             "find_folio")}
    by_id = {c.cid: c for c in comps}
    wf._project = lambda app: Proj(comps)
    wf._each = lambda client, arr: list(arr or ())

    def find_one(app, client, tag):
        # Ids for the device under test; a mark behaves like the real lookup
        # (no hit or several hits raise LookupError).
        if str(tag).isdigit():
            return by_id[int(tag)]
        hits = [c for c in comps if c.tag == str(tag).lstrip("-")]
        if not hits:
            raise LookupError(f"no component with tag {tag!r}")
        if len(hits) > 1:
            raise LookupError(f"tag {tag!r} is ambiguous")
        return hits[0]
    wf._find_one_component = find_one
    wf._symbols_bound_to = lambda app, client, cid: []
    wf._text_references = lambda app, client, t: []
    wf._text_references_multi = lambda app, client, t: []
    wf._component_row = lambda c, parts: {"id": c.cid, "tag": c.tag}
    wf.find_folio = lambda app, client, **k: None
    try:
        return fn()
    finally:
        for n, v in orig.items():
            setattr(wf, n, v)


# --- A ----------------------------------------------------------------------
comps = two_racks()
try:
    r = with_fakes(comps, lambda: wf.rename_component(
        None, None, tag="2", new_tag="N2", scan_text=False,
        refresh_folios=False, dry_run=False))
    check(r["ok"] and comps[1].tag == "N2",
          f"A: coupler N3 -> N2 should succeed, got {r}")
except Exception as e:  # noqa: BLE001
    check(False, f"A: coupler rename onto a child-only mark refused: {e}")
else:
    print("A ok: a coupler may take a mark only children of other racks use")

# --- B ----------------------------------------------------------------------
comps = two_racks()
try:
    with_fakes(comps, lambda: wf.rename_component(
        None, None, tag="2", new_tag="N1", scan_text=False,
        refresh_folios=False, dry_run=True))
    check(False, "B: renaming onto a sibling's mark must be refused")
except ValueError as e:
    check("already exists" in str(e), f"B: unexpected message: {e}")
    print("B ok: a real sibling clash is still refused")

# --- C ----------------------------------------------------------------------
comps = two_racks()
# module N3 of rack 2 (id 23) -> N7, while rack 1 gets a module N7 first
comps.append(Comp(17, "N7", parent=1))
try:
    r = with_fakes(comps, lambda: wf.renumber_components(
        None, None, renames=[["23", "N7"]], scan_text=False,
        refresh_folios=False, dry_run=False))
    check(r["ok"] and next(c for c in comps if c.cid == 23).tag == "N7",
          f"C: renumber across racks should succeed, got {r}")
except Exception as e:  # noqa: BLE001
    check(False, f"C: renumber onto another rack's module mark refused: {e}")
else:
    print("C ok: a module may take a mark a module of another rack holds")

# --- D ----------------------------------------------------------------------
comps = two_racks()
try:
    with_fakes(comps, lambda: wf.renumber_components(
        None, None, renames=[["23", "N1"]], scan_text=False,
        refresh_folios=False, dry_run=True))
    check(False, "D: renumber onto a sibling's mark must be refused")
except ValueError as e:
    check("already belong" in str(e), f"D: unexpected message: {e}")
    print("D ok: a renumber onto a sibling's mark is still refused")

# --- E ----------------------------------------------------------------------
if hasattr(wf, "_find_pdf_written"):
    with tempfile.TemporaryDirectory() as d:
        want = os.path.join(d, "out.pdf")
        t0 = time.time()
        Path(d, "1-out.pdf").write_bytes(b"%PDF-1.4 fake")
        got = wf._find_pdf_written(want, t0)
        check(got == os.path.join(d, "1-out.pdf"),
              f"E: the book-prefixed file must be found, got {got}")
        check(wf._book_prefixed_pdfs(want, t0) == [os.path.join(d, "1-out.pdf")],
              "E: exactly one book file expected")
        Path(d, "old-1-out.pdf").write_bytes(b"x")
        check(len(wf._book_prefixed_pdfs(want, t0)) == 1,
              "E: only <number>-<name>.pdf counts as a book file")
    print("E ok: the export finds the file SOLIDWORKS really writes")
else:
    check(False, "E: the export has no way to find the book-prefixed file")

# --- F: rebind_symbol ------------------------------------------------------
class Sym:
    def __init__(self): self.obj, self.updates = 2, 0
    def getObjectID(self): return self.obj
    def setObjectID(self, v): self.obj = v; return 0
    def update(self): self.updates += 1; return 0
    def getFileID(self): return 7
    def getEwSymbolName(self): return "PLC block"


class Folio:
    def __init__(self, is_open): self.is_open, self.log = is_open, []
    def isOpen(self): return self.is_open
    def close(self): self.log.append("close"); self.is_open = False; return 0
    def open(self): self.log.append("open"); self.is_open = True; return 0


if hasattr(wf, "rebind_symbol"):
    comps = two_racks()
    by_id = {c.cid: c for c in comps}
    sym, folio = Sym(), Folio(is_open=True)

    class SymMgr:
        def getProjectSymbolByID(self, i): return sym if i == 99 else None

    class CMgr(Mgr):
        def findEwProjectComponentByID(self, i): return by_id.get(i)

    class P2(Proj):
        def getEwProjectSymbolManager(self): return SymMgr()
        def getEwProjectComponentManager(self): return CMgr(self.comps)

    orig = (wf._project, wf.find_folio, wf._folio_row)
    wf._project = lambda app: P2(comps)
    wf.find_folio = lambda app, client, **k: folio
    wf._folio_row = lambda f: {"id": 7}
    try:
        plan = wf.rebind_symbol(None, None, symbol_id=99, component_id=1)
        check(plan["dry_run"] and sym.obj == 2,
              "F: a dry run must not rebind")
        r = wf.rebind_symbol(None, None, symbol_id=99, component_id=1,
                             dry_run=False)
        check(r["ok"] and sym.obj == 1, f"F: rebind should land, got {r}")
        check(folio.log == ["close", "open"] and folio.is_open,
              f"F: an open folio must be closed for the write and reopened, "
              f"got {folio.log}")
        check("component_id=2" in r["undo"], "F: undo must name the old id")
    finally:
        wf._project, wf.find_folio, wf._folio_row = orig
    print("F ok: rebind_symbol rebinds, keeps the folio open state, gives undo")
else:
    check(False, "F: no rebind_symbol")

if failures:
    print(f"{len(failures)} failure(s)")
    sys.exit(1)
print("all sibling-mark checks passed")
