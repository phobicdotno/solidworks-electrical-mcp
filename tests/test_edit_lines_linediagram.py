"""Regression test: edit_lines on a line-diagram folio (file type 1).

Observed 2026-10-20 on 2025 SP5, a 3-book project:

  * "add" with like_line_id copied the other line's wire_style_id (838) into
    setWireStyleID on a new schematic line and newEwProjectLine returned
    NULL; the same add with synoptic_style_id=838 drew the line (new id
    45177). On a line diagram a line's style id IS its synoptic style.
  * "set" on line 30068 (117.5/290 - 95/290, asked to become 192.5/260 -
    95/290) answered update EW_NO_ERROR and moved nothing: lines_after and
    a later list_lines still showed the old coordinates.

Cases:
  A. line diagram, add with like_line_id: the plan carries the copied id as
     synoptic_style_id, the apply draws a synoptic line with it
  B. line diagram, add with a bare wire_style_id: refused before anything
     is drawn, and the message names synoptic_style_id
  C. schematic folio, add with like_line_id: unchanged, the id goes to
     wire_style_id and the line is a schematic one
  D. line diagram, set that does not take: update answers EW_NO_ERROR, the
     read-back keeps the old start point, the result says applied: false,
     lists the end in not_applied, and the note says remove and add
  E. schematic folio, set that takes: applied: true, no not_applied
  F. a set whose line is gone after the edit (merged, or re-formed with a
     new id) is reported as unknown: listed in not_applied, neither counted
     as applied nor as a failure (a merge is normal on a schematic)
  G. a dry run on a line diagram warns about the set before anything runs

Run directly:
    .venv/Scripts/python.exe tests/test_edit_lines_linediagram.py
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


class FakeLine:
    def __init__(self, lid, fid, x1, y1, x2, y2, style, line_type, frozen):
        self.lid, self.fid = lid, fid
        self.pts = [x1, y1, x2, y2]
        self.style, self.line_type = style, line_type
        self.frozen = frozen           # a line diagram keeps its coordinates
        self.pending = list(self.pts)
        self.syn = None
        self.calls = []
        self.vanish_on_update = False   # merged away by SOLIDWORKS
        self.vanished = False

    def getID(self):
        return (self.lid, 0)

    def getFileID(self):
        return (self.fid, 0)

    def getStartPointXPosition(self):
        return (self.pts[0], 0)

    def getStartPointYPosition(self):
        return (self.pts[1], 0)

    def getEndPointXPosition(self):
        return (self.pts[2], 0)

    def getEndPointYPosition(self):
        return (self.pts[3], 0)

    def setStartPointXPosition(self, v):
        self.pending[0] = float(v)
        self.calls.append("setStartPointXPosition")
        return 0

    def setStartPointYPosition(self, v):
        self.pending[1] = float(v)
        self.calls.append("setStartPointYPosition")
        return 0

    def setEndPointXPosition(self, v):
        self.pending[2] = float(v)
        self.calls.append("setEndPointXPosition")
        return 0

    def setEndPointYPosition(self, v):
        self.pending[3] = float(v)
        self.calls.append("setEndPointYPosition")
        return 0

    def update(self):
        self.calls.append("update")
        if not self.frozen:
            self.pts = list(self.pending)
        if self.vanish_on_update:
            self.vanished = True
        return 0

    def getMeshID(self):
        return (7, 0)

    def getEquipotentialID(self):
        return (0, 0)

    def getWireStyleID(self):
        return (self.style, 0)

    def getEwLineType(self):
        return (self.line_type, 0)

    def getAssociatedType(self):
        return (0, 0)

    def getWireMarkText(self):
        return ("", 0)

    def getCableCoreText(self):
        return ("", 0)

    def remove(self):
        self.calls.append("remove")
        return 0

    # a line built by newEwProjectLine
    def setWireStyleSynopticID(self, v):
        self.calls.append(("setWireStyleSynopticID", int(v)))
        self.syn = int(v)
        return 0

    def setWireStyleID(self, v):
        self.calls.append(("setWireStyleID", int(v)))
        self.style = int(v)
        return 0

    def insert(self):
        self.calls.append("insert")
        self.pts = list(self.pending)
        return 0


class FakeFolio:
    def __init__(self, fid, ftype, proj):
        self.fid, self.ftype, self.proj = fid, ftype, proj
        self.open_now = False
        self.new_lines = []
        self.null_on = None      # (line_type, style) that answers NULL

    def getID(self):
        return self.fid

    def getTag(self):
        return ("12", 0)

    def getTagNumber(self):
        return (12, 0)

    def getDescription(self, lang):
        return ("overview", 0)

    def getFileType(self):
        return (self.ftype, 0)

    def getPosition(self):
        return (5, 0)

    def getEwProjectBookID(self):
        return (1, 0)

    def getEwProjectFolderID(self):
        return (-1, 0)

    def getLocationID(self):
        return (3, 0)

    def isOpen(self):
        return (self.open_now, 0)

    def close(self):
        return 0

    def open(self):
        return 0

    def newEwProjectLine(self, line_type):
        self.proj.next_id += 1
        ln = FakeLine(self.proj.next_id, self.fid, 0, 0, 0, 0, None,
                      line_type, frozen=False)
        ln.created_as = line_type
        self.new_lines.append(ln)
        self.proj.lines.append(ln)
        return (ln, 0)


class FakeLineMgr:
    def __init__(self, proj):
        self.proj = proj

    def getEwProjectLineArrayFromFileID(self, fid):
        rows = [ln for ln in self.proj.lines
                if ln.fid == int(fid) and not ln.vanished]
        # a new line only exists on the page once inserted
        rows = [ln for ln in rows if "insert" in ln.calls
                or not hasattr(ln, "created_as")]
        return (rows, 0)

    def findEwProjectLineByID(self, lid):
        return next(((ln, 0) for ln in self.proj.lines if ln.lid == int(lid)),
                    (None, 22))


class FakeFileMgr:
    def __init__(self, proj):
        self.proj = proj

    def findEwProjectFileByID(self, fid):
        f = self.proj.folios.get(int(fid))
        return (f, 0) if f else (None, 22)


class FakeProject:
    def __init__(self):
        self.folios = {}
        self.lines = []
        self.next_id = 45170

    def getEwProjectFileManager(self):
        return (FakeFileMgr(self), 0)

    def getEwProjectLineManager(self):
        return (FakeLineMgr(self), 0)


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
BOX = {"y_max": 300.0}      # the observed lines sit at y=290


def world():
    """Folio 300 is a line diagram with line 30068 (117.5/290 - 95/290,
    style 838); folio 400 is a schematic with line 30100 in style 12."""
    proj = FakeProject()
    proj.folios[300] = FakeFolio(300, 1, proj)
    proj.folios[400] = FakeFolio(400, 0, proj)
    proj.lines.append(FakeLine(30068, 300, 117.5, 290.0, 95.0, 290.0, 838, 1,
                               frozen=True))
    proj.lines.append(FakeLine(30100, 400, 100.0, 200.0, 150.0, 200.0, 12, 0,
                               frozen=False))
    return FakeApp(proj), proj


mark = len(failures)
# --- A: line diagram, add like another line --------------------------------
app, proj = world()
r = wf.edit_lines(app, CLIENT, file_id=300, box=BOX, dry_run=True,
                  ops=[{"op": "add", "x1": 95, "y1": 260, "x2": 192.5,
                        "y2": 260, "like_line_id": 30068}])
check(r["ok"], f"A: the dry run must plan the add, got {r}")
p = r["plan"][0]
check(p["synoptic_style_id"] == 838 and p["wire_style_id"] is None,
      f"A: like_line_id on a line diagram goes to synoptic_style_id, got {p}")
r = wf.edit_lines(app, CLIENT, file_id=300, box=BOX, dry_run=False,
                  ops=[{"op": "add", "x1": 95, "y1": 260, "x2": 192.5,
                        "y2": 260, "like_line_id": 30068}])
check(r["ok"], f"A: the apply must succeed, got {r}")
new = proj.folios[300].new_lines
check(len(new) == 1 and new[0].created_as == 1
      and ("setWireStyleSynopticID", 838) in new[0].calls
      and not any(isinstance(c, tuple) and c[0] == "setWireStyleID"
                  for c in new[0].calls),
      f"A: newEwProjectLine(1) + setWireStyleSynopticID(838), got "
      f"{[ln.calls for ln in new]}")
check(r["applied"][0].get("setWireStyleSynopticID") == "EW_NO_ERROR"
      and r["applied"][0]["line_id"] == new[0].lid,
      f"A: the row reports the synoptic step and the new id, got {r['applied']}")
check(any(ln["line_id"] == new[0].lid for ln in r["lines_after"]),
      "A: the new line is in lines_after")
ok("A ok: like_line_id on a line diagram draws a synoptic line", mark)

mark = len(failures)
# --- B: line diagram, bare wire_style_id is refused ------------------------
app, proj = world()
r = wf.edit_lines(app, CLIENT, file_id=300, box=BOX, dry_run=False,
                  ops=[{"op": "add", "x1": 95, "y1": 260, "x2": 192.5,
                        "y2": 260, "wire_style_id": 838}])
check(not r["ok"] and r.get("problems"), f"B: must be refused, got {r}")
msg = r["problems"][0]["error"] if r.get("problems") else ""
check("synoptic_style_id" in msg and "line-diagram" in msg,
      f"B: the refusal names synoptic_style_id and the folio kind, got {msg!r}")
check(proj.folios[300].new_lines == [],
      "B: nothing is drawn on a refused plan")
ok("B ok: wire_style_id alone on a line diagram is refused with the fix", mark)

mark = len(failures)
# --- C: schematic, add like another line is unchanged ----------------------
app, proj = world()
r = wf.edit_lines(app, CLIENT, file_id=400, dry_run=False,
                  ops=[{"op": "add", "x1": 100, "y1": 150, "x2": 150,
                        "y2": 150, "like_line_id": 30100}])
check(r["ok"], f"C: got {r}")
p = r["plan"][0]
check(p["wire_style_id"] == 12 and p["synoptic_style_id"] is None,
      f"C: a schematic like_line_id still copies wire_style_id, got {p}")
new = proj.folios[400].new_lines
check(len(new) == 1 and new[0].created_as == 0
      and ("setWireStyleID", 12) in new[0].calls,
      f"C: newEwProjectLine(0) + setWireStyleID(12), got {[ln.calls for ln in new]}")
ok("C ok: schematic adds are unchanged", mark)

mark = len(failures)
# --- D: line diagram, set does not take ------------------------------------
app, proj = world()
r = wf.edit_lines(app, CLIENT, file_id=300, box=BOX, dry_run=False,
                  ops=[{"op": "set", "line_id": 30068, "x1": 192.5,
                        "y1": 260}])
row = r["applied"][0]
check(row["update"] == "EW_NO_ERROR",
      f"D: the fake answers EW_NO_ERROR like SOLIDWORKS did, got {row}")
check(not r["ok"] and row["ok"] is False and row.get("applied") is False,
      f"D: an end that did not move is not success, got {r}")
na = r.get("not_applied") or []
check(len(na) == 1 and na[0]["end"] == "start"
      and na[0]["requested"] == [192.5, 260.0]
      and na[0]["read_back"] == [117.5, 290.0],
      f"D: not_applied names the start end with both coordinates, got {na}")
check("remove" in r["note"] and "line-diagram" in r["note"],
      f"D: the note says remove and add instead, got {r['note']!r}")
after = next(ln for ln in r["lines_after"] if ln["line_id"] == 30068)
check(after["x1"] == 117.5 and after["y1"] == 290.0,
      "D: lines_after carries the read-back, not the request")
ok("D ok: an unmoved line-diagram end is reported as not applied", mark)

mark = len(failures)
# --- E: schematic, set takes -----------------------------------------------
app, proj = world()
r = wf.edit_lines(app, CLIENT, file_id=400, dry_run=False,
                  ops=[{"op": "set", "line_id": 30100, "x2": 170}])
check(r["ok"] and r["applied"][0].get("applied") is True
      and "not_applied" not in r,
      f"E: a moved end is applied, got {r}")
check("merges collinear" in r["note"], "E: the usual note stays")
ok("E ok: a schematic set that takes is reported applied", mark)

mark = len(failures)
# --- F: the line is gone after the edit ------------------------------------
app, proj = world()
next(ln for ln in proj.lines if ln.lid == 30100).vanish_on_update = True
r = wf.edit_lines(app, CLIENT, file_id=400, dry_run=False,
                  ops=[{"op": "set", "line_id": 30100, "x2": 170}])
na = r.get("not_applied") or []
check(len(na) == 1 and na[0]["end"] is None
      and "not on the folio" in na[0]["reason"],
      f"F: a vanished line is reported, got {r}")
row = r["applied"][0]
check(r["ok"] and row["ok"] and row.get("applied") == "unknown"
      and "lines_after" in row.get("error", ""),
      f"F: a merged line is unknown, not a failure, got {row}")
check("merges collinear" in r["note"],
      "F: on a schematic the note stays the merge note")
ok("F ok: a line gone after the edit is unknown, not applied and not "
   "failed", mark)

mark = len(failures)
# --- G: dry run warns on a line diagram ------------------------------------
app, proj = world()
r = wf.edit_lines(app, CLIENT, file_id=300, box=BOX, dry_run=True,
                  ops=[{"op": "set", "line_id": 30068, "x1": 192.5,
                        "y1": 260}])
check(r["ok"] and r["dry_run"] and r.get("warnings")
      and "remove" in r["warnings"][0]["warning"],
      f"G: the dry run carries the warning, got {r}")
r = wf.edit_lines(app, CLIENT, file_id=400, dry_run=True,
                  ops=[{"op": "set", "line_id": 30100, "x2": 170}])
check("warnings" not in r, "G: no warning on a schematic")
ok("G ok: the dry run warns before a line-diagram set", mark)

print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("all edit_lines line-diagram checks passed")
