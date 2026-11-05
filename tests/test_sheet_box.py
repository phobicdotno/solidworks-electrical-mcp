"""Regression test: the drawable box follows the folio's paper size.

Every box-checking tool used GO_BOX (an A3 rule, x 50..370, y 80..240) unless
the caller passed a box. On an A2 folio (Mariner Standard page 68, drawn out
to x 580) every place, move, text and edit_lines call needed an explicit box,
and check_page_ink scaled the PDF as if the sheet were the export's paper:
the export fits every sheet to the print configuration's paper, so an A2
folio came out 842 pt wide and was measured as a 297 mm sheet.

The sheet size is not reachable through the API (no field on the page row,
getEwTitleBlockX NULL with rc 53), so it is judged from the folio's geometry.

Cases:
  A. geometry inside A3 -> GO_BOX
  B. a line end past 420 mm -> the A2 box, and the verdict is cached
  C. a full explicit box is used as given; a partial one overrides the A2 box
  D. an empty folio -> GO_BOX and NOT cached (it may become A2 later)
  E. a symbol origin past 420 mm with short lines -> A2
  F. check_page_ink on an A2 folio exported to 842 pt measures A2 millimetres
  G. a failing sweep raises (no silent A3); a full box needs no sweep
  H. the cache is keyed per project, so folio ids never share a verdict

Run directly:
    .venv/Scripts/python.exe tests/test_sheet_box.py
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


def line(x1, y1, x2, y2):
    return {"id": 1, "x1": x1, "y1": y1, "x2": x2, "y2": y2}


class Sym:
    def __init__(self, x, y):
        self.x, self.y = x, y

    def getXPosition(self):
        return self.x

    def getYPosition(self):
        return self.y


class Proj:
    def __init__(self, syms):
        self.syms = syms

    def getEwProjectSymbolManager(self):
        p = self

        class M:
            def getProjectSymbolsFromFileID(self, fid):
                return p.syms
        return M()


def with_folio(lines, syms, fn):
    o_lines, o_proj, o_each = wf._folio_lines, wf._project, wf._each
    wf._folio_lines = lambda app, client, fid: lines
    wf._project = lambda app: Proj(syms)
    wf._each = lambda client, arr: list(arr or [])
    try:
        return fn()
    finally:
        wf._folio_lines, wf._project, wf._each = o_lines, o_proj, o_each


A2 = wf._BOX_BY_PAPER["A2"]

# --- A ---------------------------------------------------------------------
wf._paper_cache.clear()
bx = with_folio([line(50, 100, 365, 234)], [], lambda: wf._resolve_box(None, None, 1, None))
check(bx == wf.GO_BOX, f"A: A3 geometry should give GO_BOX, got {bx}")

# --- B ---------------------------------------------------------------------
wf._paper_cache.clear()
bx = with_folio([line(20, 30, 575, 390)], [], lambda: wf._resolve_box(None, None, 2, None))
check(bx == A2, f"B: a line to x 575 should give the A2 box, got {bx}")
check((None, 2) in wf._paper_cache, "B: an A2 verdict should be cached")
bx = with_folio([], [], lambda: wf._resolve_box(None, None, 2, None))
check(bx == A2, f"B: cached A2 must survive an empty re-read, got {bx}")

# --- C ---------------------------------------------------------------------
full = {"x_min": 1, "x_max": 2, "y_min": 3, "y_max": 4}
bx = with_folio([line(20, 30, 575, 390)], [], lambda: wf._resolve_box(None, None, 2, full))
check(bx == {k: float(v) for k, v in full.items()}, f"C: full box must be used as given, got {bx}")
bx = with_folio([], [], lambda: wf._resolve_box(None, None, 2, {"x_max": 500}))
check(bx["x_max"] == 500 and bx["x_min"] == A2["x_min"] and bx["y_max"] == A2["y_max"],
      f"C: partial box should override only x_max of the A2 box, got {bx}")

# --- D ---------------------------------------------------------------------
wf._paper_cache.clear()
bx = with_folio([], [], lambda: wf._resolve_box(None, None, 3, None))
check(bx == wf.GO_BOX, f"D: empty folio should give GO_BOX, got {bx}")
check((None, 3) not in wf._paper_cache, "D: an A3 verdict must not be cached")

# --- E ---------------------------------------------------------------------
wf._paper_cache.clear()
bx = with_folio([line(50, 100, 100, 100)], [Sym(480, 200)],
                lambda: wf._resolve_box(None, None, 4, None))
check(bx == A2, f"E: a symbol at x 480 should give the A2 box, got {bx}")

# --- G: a failing sweep raises instead of quietly giving A3 ----------------
wf._paper_cache.clear()


def broken_lines(app, client, fid):
    raise RuntimeError("COM gone")


o_lines, o_proj = wf._folio_lines, wf._project
wf._folio_lines, wf._project = broken_lines, (lambda app: Proj([]))
try:
    wf._resolve_box(None, None, 6, None)
    check(False, "G: a failing line sweep must raise, not fall back to A3")
except RuntimeError:
    pass
finally:
    wf._folio_lines, wf._project = o_lines, o_proj
bx = wf._resolve_box(None, None, 6, full)
check(bx == {k: float(v) for k, v in full.items()},
      "G: a full box needs no sweep, so it works even when the sweep would fail")

# --- H: the cache is per project -------------------------------------------
wf._paper_cache.clear()


class IdProj(Proj):
    def __init__(self, pid, syms):
        super().__init__(syms)
        self.pid = pid

    def getID(self):
        return self.pid


o_lines, o_proj, o_each = wf._folio_lines, wf._project, wf._each
wf._each = lambda client, arr: list(arr or [])
try:
    wf._folio_lines = lambda app, client, fid: [line(20, 30, 575, 390)]
    wf._project = lambda app: IdProj(23, [])
    check(wf._resolve_box(None, None, 7, None) == A2, "H: project 23 folio 7 is A2")
    wf._folio_lines = lambda app, client, fid: [line(50, 100, 300, 200)]
    wf._project = lambda app: IdProj(24, [])
    check(wf._resolve_box(None, None, 7, None) == wf.GO_BOX,
          "H: folio 7 of project 24 must not inherit project 23's A2 verdict")
finally:
    wf._folio_lines, wf._project, wf._each = o_lines, o_proj, o_each

# --- F ---------------------------------------------------------------------
try:
    import fitz
except ImportError:
    fitz = None
    print("SKIP F: PyMuPDF is not installed")
if fitz is not None:
    W_PT, H_PT = 842.0, 595.0          # what the export gives for an A2 folio
    S = W_PT / 594.0

    def fake_export(app, client, output_path, **kw):
        doc = fitz.open()
        pg = doc.new_page(width=W_PT, height=H_PT)
        x0, y0, x1, y1 = 500.0, 300.0, 560.0, 380.0      # A2 millimetres
        pg.draw_rect(fitz.Rect(x0 * S, H_PT - y1 * S, x1 * S, H_PT - y0 * S), width=0.5)
        doc.save(output_path)
        doc.close()
        return {"ok": True, "size_bytes": 1}

    o_exp, o_find, o_row = wf.export_folio_pdf, wf.find_folio, wf._folio_row
    wf.export_folio_pdf = fake_export
    wf.find_folio = lambda app, client, **kw: object()
    wf._folio_row = lambda f: {"id": 5, "tag": "68", "is_open": False}
    wf._paper_cache.clear()
    try:
        r = with_folio([line(20, 30, 575, 390)], [],
                       lambda: wf.check_page_ink(None, None, page="68"))
    finally:
        wf.export_folio_pdf, wf.find_folio, wf._folio_row = o_exp, o_find, o_row
    e = r.get("extent") or {}
    check(r.get("ok") and r.get("inside"), f"F: A2 device inside the A2 box, got {r}")
    check(abs(e.get("x_min", 0) - 500) < 1 and abs(e.get("x_max", 0) - 560) < 1,
          f"F: extent should be measured in A2 mm (500..560), got {e}")

if failures:
    print(f"{len(failures)} FAILED")
    raise SystemExit(1)
print("test_sheet_box: all cases pass")
