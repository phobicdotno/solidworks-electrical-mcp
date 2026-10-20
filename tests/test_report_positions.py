"""Regression test: regenerated report folios go back where the old ones sat.

Observed 2026-10-20 on 2025 SP5: generate_report_drawings(report_ids=[42])
deleted the fifteen BOM folios (pages 72-86) and drew fifteen new ones with
new ids at the END of their folder, after the cable list (87-90), while the
page marks stayed 72-86. The tree and a full PDF export then ran cable list
before BOM. ``keep_position`` records the old tree positions before each
run and writes them onto the new folios (``fil_tagposition``, the same field
move_folio uses).

Cases:
  A. keep_position=False: the folios land at the end and nothing is moved;
     the result carries the move_folio note
  B. keep_position=True: the new folios take the old positions in order, the
     cable list keeps its place, and the result lists the moves
  C. a report that grew by a page: the new folios take the old slot and
     everything after it in the book moves up by one
  D. a report that shrank: the old slots are reused, nothing else moves
  E. two reports in one call: positions are restored per run, so the second
     report's old slot is read after the first was put back
  F. a run that fails does not touch positions

Run directly:
    .venv/Scripts/python.exe tests/test_report_positions.py
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from solidworks_electrical_mcp import automation as auto

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)
        print("FAIL:", msg)


def ok(msg: str, mark: int) -> None:
    if len(failures) == mark:
        print(msg)


class FakeFolio:
    def __init__(self, fid, tag, position, folder=41139, book=1, report=None):
        self.fid, self.tag, self.position = fid, tag, position
        self.folder, self.book, self.report = folder, book, report
        self.writes = []

    def getID(self):
        return self.fid

    def getTag(self):
        return (self.tag, 0)

    def getTagNumber(self):
        return (int(self.tag), 0)

    def getDescription(self, lang):
        return (f"report {self.report}" if self.report else "page", 0)

    def getFileType(self):
        return (2 if self.report else 12, 0)

    def getPosition(self):
        return (self.position, 0)

    def getEwProjectBookID(self):
        return (self.book, 0)

    def getEwProjectFolderID(self):
        return (self.folder, 0)

    def getLocationID(self):
        return (3, 0)

    def isOpen(self):
        return (False, 0)

    # raw database fields, as rawfields.set_fields drives them
    def getProperty(self, name):
        if name == "fil_tagposition":
            return (self.position, 0)
        return (None, 2)

    def setProperty(self, name, value):
        if name != "fil_tagposition":
            return 2
        self.writes.append(value)
        self.position = int(value)
        return 0

    def update(self):
        return 0


class FakeFileMgr:
    def __init__(self, proj):
        self.proj = proj

    def getEwProjectFileArray(self):
        return (list(self.proj.folios), 0)

    def findEwProjectFileByID(self, fid):
        return next(((f, 0) for f in self.proj.folios if f.fid == int(fid)),
                    (None, 22))


class FakeExport:
    """doGenerateDrawings behaves like SOLIDWORKS: delete the report's folios
    and draw ``pages`` new ones with fresh ids at the end of the folder."""

    def __init__(self, proj, log):
        self.proj, self.log, self.report = proj, log, None

    def setExportAllReports(self, v):
        return 0

    def setEwProjectReportIDArray(self, arr):
        self.report = arr[0]
        return 0

    def setBookOrFolderId(self, v):
        return 0

    def doGenerateDrawings(self):
        self.log.append(("generate", self.report))
        if self.report in self.proj.fail:
            return 45
        old = [f for f in self.proj.folios if f.report == self.report]
        folder = old[0].folder if old else 41139
        n = self.proj.pages.get(self.report, len(old))
        tags = [f.tag for f in old] + [str(90 + i) for i in range(n)]
        for f in old:
            self.proj.folios.remove(f)
        top = max(f.position for f in self.proj.folios) + 1
        for i in range(n):
            self.proj.next_id += 1
            self.proj.folios.append(FakeFolio(self.proj.next_id, tags[i],
                                              top + i, folder, 1,
                                              self.report))
        return 0


class FakeProject:
    def __init__(self, folios, pages=None, fail=()):
        self.folios, self.pages, self.fail = folios, pages or {}, set(fail)
        self.next_id = 50000
        self.log = []

    def getEwProjectFileManager(self):
        return (FakeFileMgr(self), 0)

    def newEwProjectExportReport(self):
        return (FakeExport(self, self.log), 0)


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


def world(pages=None, fail=()):
    """Book 1: a schematic, then a reports folder with BOM (report 42, three
    pages, positions 120-122) and cable list (report 43, two pages, 123-124),
    then one more schematic page after the folder."""
    folios = [FakeFolio(37734, "70", 77, folder=27634),
              FakeFolio(45012, "72", 120, report=42),
              FakeFolio(45013, "73", 121, report=42),
              FakeFolio(45014, "74", 122, report=42),
              FakeFolio(45027, "87", 123, report=43),
              FakeFolio(45028, "88", 124, report=43),
              FakeFolio(46000, "91", 125, folder=27640)]
    proj = FakeProject(folios, pages, fail)
    return FakeApp(proj), proj


def order(proj):
    return [f.tag for f in sorted(proj.folios, key=lambda f: f.position)]


def _id_array(ids):
    return [int(i) for i in ids]


auto._id_array = _id_array
# The fake draws synchronously, so the settle wait sees the same tree twice
# and must not spend wall-clock time on it (tests/test_report_settle.py
# covers the wait itself).
auto._SLEEP = lambda s: None

mark = len(failures)
# --- A: default: end of the folder, with the note ------------------------
app, proj = world()
r = auto.generate_report_drawings(app, CLIENT, report_ids=[42])
check(r["ok"] and "move_folio" in r["note"] and "keep_position" in r["note"],
      f"A: the default result must say how to put the pages back, got {r}")
check(order(proj) == ["70", "87", "88", "91", "72", "73", "74"],
      f"A: the fake must reproduce the observed order, got {order(proj)}")
check("positions" not in r, "A: nothing is restored unless asked")
ok("A ok: regenerated folios land at the end, the note says what to do", mark)

mark = len(failures)
# --- B: keep_position puts them back ------------------------------------
app, proj = world()
r = auto.generate_report_drawings(app, CLIENT, report_ids=[42],
                                  keep_position=True)
check(r["ok"], f"B: got {r}")
check(order(proj) == ["70", "72", "73", "74", "87", "88", "91"],
      f"B: the BOM must be back before the cable list, got {order(proj)}")
pos = r["positions"][0]
check(pos["folios_replaced"] == [45012, 45013, 45014]
      and len(pos["folios_new"]) == 3,
      f"B: the result names old and new ids, got {pos}")
check([m["to"] for m in pos["repositioned"]] == [120, 121, 122]
      and all(m["ok"] for m in pos["repositioned"]),
      f"B: the new folios take 120-122 in page order, got "
      f"{pos['repositioned']}")
cables = [f for f in proj.folios if f.report == 43]
check(all(f.writes == [] for f in cables),
      "B: the cable list is not touched when the BOM fits its old slot")
ok("B ok: the new folios take the old positions", mark)

mark = len(failures)
# --- C: a report that grew ---------------------------------------------
app, proj = world(pages={42: 4})
r = auto.generate_report_drawings(app, CLIENT, report_ids=[42],
                                  keep_position=True)
check(r["ok"], f"C: got {r}")
check(order(proj) == ["70", "72", "73", "74", "90", "87", "88", "91"],
      f"C: four BOM pages, then the cable list, then the page after the "
      f"folder, got {order(proj)}")
by_tag = {f.tag: f.position for f in proj.folios}
check(by_tag["87"] == 124 and by_tag["88"] == 125 and by_tag["91"] == 126,
      f"C: everything after the slot moves up by one, got {by_tag}")
check(by_tag["70"] == 77, "C: pages before the slot stay put")
ok("C ok: a grown report pushes what follows it up by the difference", mark)

mark = len(failures)
# --- D: a report that shrank -------------------------------------------
app, proj = world(pages={42: 2})
r = auto.generate_report_drawings(app, CLIENT, report_ids=[42],
                                  keep_position=True)
check(r["ok"] and order(proj) == ["70", "72", "73", "87", "88", "91"],
      f"D: got {order(proj)}")
check({f.tag: f.position for f in proj.folios}["87"] == 123,
      "D: nothing after a shrunk report moves")
ok("D ok: a shrunk report reuses its first slots", mark)

mark = len(failures)
# --- E: two reports, restored per run ----------------------------------
app, proj = world()
r = auto.generate_report_drawings(app, CLIENT, report_ids=[42, 43],
                                  keep_position=True)
check(r["ok"] and len(r["positions"]) == 2, f"E: got {r}")
check(order(proj) == ["70", "72", "73", "74", "87", "88", "91"],
      f"E: both reports back in place, got {order(proj)}")
check([m["to"] for m in r["positions"][1]["repositioned"]] == [123, 124],
      f"E: the cable list's slot is read after the BOM was put back, got "
      f"{r['positions'][1]['repositioned']}")
ok("E ok: positions are restored after each run", mark)

mark = len(failures)
# --- F: a failed run moves nothing --------------------------------------
app, proj = world(fail=(42,))
r = auto.generate_report_drawings(app, CLIENT, report_ids=[42],
                                  keep_position=True)
check(not r["ok"] and r["positions"] == [],
      f"F: a refused generate must not reposition anything, got {r}")
check(order(proj) == ["70", "72", "73", "74", "87", "88", "91"],
      f"F: got {order(proj)}")
ok("F ok: a failed run leaves the tree alone", mark)

print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("all report position checks passed")
