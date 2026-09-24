"""Regression test: check_page_ink measures what a sheet really draws.

check_drawing_rules only inspects connection points and line ends. A 2D
footprint imported from a DWG reports getWidth 0.0 and carries no connection
points, so a footprint whose body hangs off the sheet passes that check with
outside=0 while the exported PDF plainly shows it hanging out - which is
exactly what happened to the CC100 on page 102 (placed at x=90, its body ran
out to x=-18 because the symbol origin sits 94.5 mm right of its geometry).

check_page_ink exports the folio and measures the vector ink instead. This
test feeds it synthetic A3 sheets with geometry at known millimetre positions,
so the arithmetic and the frame/title-block filtering are checked without
SOLIDWORKS.

Cases:
  A. geometry wholly inside the box -> inside True, no overflow
  B. geometry hanging off the left -> the overflow millimetres are right
  C. overflow on several sides at once is reported per side
  D. the sheet frame (long thin runs) is not mistaken for device ink, while
     a large device that is NOT thin is still measured
  E. title-block geometry below the box is ignored
  F. an empty sheet reports no extent rather than crashing
  G. a 260 mm enclosure is measured as a device, not filtered as a frame
  H. title-block furniture around the box is ignored however short its rules
  I. a device CROSSING the boundary is still reported, so the exclusion does
     not swallow what the tool exists to find

Run directly:
    .venv/Scripts/python.exe tests/test_page_ink.py
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

try:
    import fitz
except ImportError:
    print("SKIP: PyMuPDF is not installed")
    raise SystemExit(0)

from solidworks_electrical_mcp import workflows as wf

failures: list[str] = []

W_PT, H_PT = 1191.0, 842.0          # A3 landscape
S = W_PT / 420.0                    # points per millimetre at 1:1
BOX = wf.GO_BOX                     # x 50..370, y 80..240


def check(cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)
        print("FAIL:", msg)


def ok(msg: str, mark: int) -> None:
    if len(failures) == mark:
        print(msg)


def rect_mm(x0, y0, x1, y1):
    """A rectangle given in page millimetres, as a PDF rect (y flips)."""
    return fitz.Rect(x0 * S, H_PT - y1 * S, x1 * S, H_PT - y0 * S)


def make_pdf(path, boxes):
    doc = fitz.open()
    pg = doc.new_page(width=W_PT, height=H_PT)
    for b in boxes:
        pg.draw_rect(rect_mm(*b), width=0.5)
    doc.save(path)
    doc.close()


def run(boxes):
    """Point check_page_ink at a synthetic sheet and return its verdict."""
    def fake_export(app, client, output_path, **kw):
        make_pdf(output_path, boxes)
        return {"ok": True, "size_bytes": 1}

    orig_export = wf.export_folio_pdf
    orig_find = wf.find_folio
    orig_row = wf._folio_row
    wf.export_folio_pdf = fake_export
    wf.find_folio = lambda app, client, **kw: object()
    wf._folio_row = lambda f: {"id": 42, "tag": "102", "is_open": False}
    try:
        return wf.check_page_ink(None, None, page="102")
    finally:
        wf.export_folio_pdf = orig_export
        wf.find_folio = orig_find
        wf._folio_row = orig_row


def near(a, b, tol=1.0):
    return a is not None and abs(a - b) <= tol


mark = len(failures)
# --- A: a device sitting well inside the drawable box ---------------------
r = run([(60.0, 100.0, 160.0, 200.0)])
check(r["ok"], f"A: should succeed, got {r.get('error')}")
check(r["inside"], f"A: geometry inside the box must report inside, {r}")
check(not r["overflow"], f"A: no overflow expected, got {r.get('overflow')}")
e = r["extent"]
check(near(e["x_min"], 60.0) and near(e["x_max"], 160.0)
      and near(e["y_min"], 100.0) and near(e["y_max"], 200.0),
      f"A: extent should be 60..160 x 100..200, got {e}")
ok(f"A ok: inside, extent {e['x_min']}..{e['x_max']} x "
   f"{e['y_min']}..{e['y_max']}", mark)

mark = len(failures)
# --- B: the CC100 case - body hanging off the left of the box ------------
r = run([(20.0, 100.0, 120.0, 200.0)])
check(not r["inside"], "B: geometry left of the box must not report inside")
check(near(r["overflow"].get("left"), 30.0),
      f"B: expected ~30 mm of left overflow, got {r.get('overflow')}")
ok(f"B ok: overflow {r['overflow']}", mark)

mark = len(failures)
# --- C: several sides at once --------------------------------------------
# Two modest devices in opposite corners, each hanging out on two sides.
# They must be device-sized: a single object overflowing both left and right
# would necessarily be over 320 mm wide and look like a frame rule.
r = run([(30.0, 60.0, 120.0, 150.0),      # off the left and the bottom
         (300.0, 200.0, 390.0, 260.0)])   # off the right and the top
o = r["overflow"]
check(near(o.get("left"), 20.0) and near(o.get("right"), 20.0)
      and near(o.get("bottom"), 20.0) and near(o.get("top"), 20.0),
      f"C: expected ~20 mm on each of left/right/bottom/top, got {o}")
ok(f"C ok: per-side overflow {o}", mark)

mark = len(failures)
# --- D: the sheet frame must not be read as device ink -------------------
# A full-width border plus a small device well inside it. Without the frame
# filter the extent would span the whole sheet and report a false overflow.
r = run([(10.0, 20.0, 410.0, 20.6),       # frame rule: long AND thin
         (10.0, 20.0, 10.6, 280.0),       # frame rule: tall AND thin
         (100.0, 120.0, 140.0, 160.0)])   # the actual device
e = r["extent"]
check(r["inside"], f"D: the frame must not create an overflow, got {r}")
check(near(e["x_min"], 100.0) and near(e["x_max"], 140.0),
      f"D: extent should be the device, not the frame, got {e}")
ok(f"D ok: frame ignored, extent {e['x_min']}..{e['x_max']}", mark)

mark = len(failures)
# --- E: title-block geometry below the box is ignored ---------------------
r = run([(100.0, 120.0, 140.0, 160.0),    # device
         (60.0, 20.0, 200.0, 60.0)])      # title block, wholly below y_min
e = r["extent"]
check(r["inside"], f"E: the title block must not create an overflow, got {r}")
check(near(e["y_min"], 120.0),
      f"E: extent should start at the device, not the title block, got {e}")
ok(f"E ok: title block ignored, y_min {e['y_min']}", mark)

mark = len(failures)
# --- F: an empty sheet ----------------------------------------------------
r = run([])
check(r["ok"] and r["inside"] and r["extent"] is None and r["paths"] == 0,
      f"F: an empty sheet should report no extent, got {r}")
ok("F ok: empty sheet reports no extent", mark)

mark = len(failures)
# --- G: a 260 mm enclosure is a device, not a frame -----------------------
# AN-2823-AB is 260.1 x 160.0 mm. A length-only frame filter discarded it.
r = run([(55.0, 80.0, 315.1, 240.0)])
e = r["extent"]
check(e is not None and near(e["x_max"] - e["x_min"], 260.1),
      f"G: the 260 mm enclosure must be measured, not filtered, got {e}")
check(r["inside"], f"G: the enclosure sits inside the box, got {r}")
ok(f"G ok: enclosure measured, {e['x_max'] - e['x_min']:.1f} mm wide", mark)

mark = len(failures)
# --- H: title-block furniture anywhere around the box is ignored ----------
# Shape alone does not catch this. A real sheet's title block is full of
# SHORT thin rules, well under max_frame_mm, sitting to the right of the box
# and above it. On the real page 102 export they dragged the measured extent
# out to the paper edge (x 410.44, y 281.41) and reported an overflow on
# every side of a page whose devices were nowhere near those edges.
device = (100.0, 120.0, 140.0, 160.0)
furniture = [
    (276.0, 22.0, 391.0, 25.5),     # title block rules, below the box
    (393.0, 25.0, 407.5, 35.0),     # revision cells, below and right
    (405.0, 250.0, 410.5, 281.5),   # corner marks, above and right
    (26.5, 250.0, 27.1, 281.5),     # corner marks, above and left
]
r = run([device, *furniture])
e = r["extent"]
check(r["inside"], f"H: furniture outside the box must not overflow, got {r}")
check(near(e["x_min"], 100.0) and near(e["x_max"], 140.0)
      and near(e["y_min"], 120.0) and near(e["y_max"], 160.0),
      f"H: the extent should be the device alone, got {e}")
check(r["frame_paths_ignored"] == len(furniture),
      f"H: expected {len(furniture)} frame paths ignored, got "
      f"{r['frame_paths_ignored']}")
ok(f"H ok: {r['frame_paths_ignored']} pieces of furniture ignored, "
   f"extent is the device", mark)

mark = len(failures)
# --- I: a device that CROSSES the boundary is still caught ----------------
# The exclusion must not swallow the thing the tool exists to find.
r = run([(20.0, 100.0, 120.0, 200.0), *furniture])
check(not r["inside"], "I: a device crossing the boundary must be reported")
check(near(r["overflow"].get("left"), 30.0),
      f"I: expected 30 mm of left overflow, got {r.get('overflow')}")
ok(f"I ok: crossing device still reported, overflow {r['overflow']}", mark)

print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("all page-ink checks passed")
