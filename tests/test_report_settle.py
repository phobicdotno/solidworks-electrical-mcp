"""Regression test: generate_report_drawings waits for SOLIDWORKS to settle.

Observed 2026-10-20 on 2025 SP5, a 3-book project: doGenerateDrawings
returned at once, and list_folios(file_type="bom") then showed 48 new blank
folios "Bill Of Materials grouped by manufacturer" (no filter suffix, an
empty page) at the end of book 1's top level while the report's own 15
folios still had their ids and folder. A minute later the blanks were gone
except the last three, and the report's folios had been MOVED to the book's
top level (folder -1, positions after the cable list). Deleting the blanks
inside that window worked and was wrong.

Cases:
  H. wait_for_settle (default): polls until two listings agree, reports the
     wait, and keep_position restores the report's folios to their old
     FOLDER and positions; the cable list is untouched; the blanks that
     outlive the settle are listed as unmatched, not moved
  I. wait_for_settle=False returns at once with a warning, on the unsettled
     tree
  J. a tree that never settles: timeout reported, ok false, error text
  K. new ids that land at the book's top level (replaced AND moved) are
     matched to the gone group by description and get the old folder back;
     the blanks are not mistaken for report pages
  L. two reports in one call: one settle per run

Run directly:
    .venv/Scripts/python.exe tests/test_report_settle.py
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


BOM = "Bill Of Materials grouped by manufacturer"
BOM_FILTERED = BOM + " - by location"
REPORTS = 41139          # the reports folder
TOP = -1                 # book top level


class FakeFolio:
    def __init__(self, fid, tag, position, folder=REPORTS, book=1,
                 report=None, desc=None):
        self.fid, self.tag, self.position = fid, tag, position
        self.folder, self.book, self.report = folder, book, report
        self.desc = desc or ("page" if report is None else BOM_FILTERED)
        self.writes = []

    def getID(self):
        return self.fid

    def getTag(self):
        return (self.tag, 0)

    def getTagNumber(self):
        return (int(self.tag), 0)

    def getDescription(self, lang):
        return (self.desc, 0)

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

    FIELDS = {"fil_tagposition": "position", "fil_fol_id": "folder",
              "fil_bun_id": "book"}

    def getProperty(self, name):
        if name in self.FIELDS:
            return (getattr(self, self.FIELDS[name]), 0)
        return (None, 2)

    def setProperty(self, name, value):
        if name not in self.FIELDS:
            return 2
        self.writes.append((name, int(value)))
        setattr(self, self.FIELDS[name], int(value))
        return 0

    def update(self):
        return 0


class FakeFileMgr:
    def __init__(self, proj):
        self.proj = proj

    def getEwProjectFileArray(self):
        # Every listing is a poll: SOLIDWORKS keeps working between them.
        self.proj.tick()
        return (list(self.proj.folios), 0)

    def findEwProjectFileByID(self, fid):
        return next(((f, 0) for f in self.proj.folios if f.fid == int(fid)),
                    (None, 22))


class FakeExport:
    def __init__(self, proj):
        self.proj, self.report = proj, None

    def setExportAllReports(self, v):
        return 0

    def setEwProjectReportIDArray(self, arr):
        self.report = arr[0]
        return 0

    def setBookOrFolderId(self, v):
        return 0

    def doGenerateDrawings(self):
        self.proj.generate(self.report)
        return 0


class FakeProject:
    """``generate`` puts the blank folios on the tree at once and queues
    the later steps; each listing applies one queued step (``tick``)."""

    def __init__(self, folios, replace_ids=False, never_settle=False):
        self.folios = folios
        self.replace_ids, self.never_settle = replace_ids, never_settle
        self.queue = []
        self.next_id = 50000
        self.listings = 0

    def getEwProjectFileManager(self):
        return (FakeFileMgr(self), 0)

    def newEwProjectExportReport(self):
        return (FakeExport(self), 0)

    def _blank(self, tag, position):
        self.next_id += 1
        return FakeFolio(self.next_id, tag, position, TOP, 1, 99, BOM)

    def generate(self, report):
        top = max(f.position for f in self.folios)
        self.blanks = [self._blank(str(200 + i), top + 1 + i)
                       for i in range(5)]
        self.folios += self.blanks

        def later():
            # all but the last three blanks go
            for b in self.blanks[:-3]:
                self.folios.remove(b)
            olds = [f for f in self.folios if f.report == report]
            top = max(f.position for f in self.folios
                      if f not in olds) + 1
            if self.replace_ids:
                for f in olds:
                    self.folios.remove(f)
                for i, f in enumerate(olds):
                    self.next_id += 1
                    self.folios.append(FakeFolio(self.next_id, f.tag, top + i,
                                                 TOP, 1, report))
            else:
                for i, f in enumerate(olds):
                    f.folder, f.position = TOP, top + i
        # the first listing after the call still shows the blanks, as the
        # live list_folios did; the one after that shows the settled tree
        self.queue.append(lambda: None)
        self.queue.append(later)

    def tick(self):
        self.listings += 1
        if self.never_settle:
            top = max(f.position for f in self.folios)
            self.folios.append(self._blank(str(300 + self.listings), top + 1))
        elif self.queue:
            self.queue.pop(0)()


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


class FakeTime:
    """A clock that advances only when the tool sleeps."""

    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s

    def clock(self):
        return self.now


def world(**kw):
    """Book 1: a schematic, then the reports folder with BOM (report 42,
    three pages at 120-122) and cable list (report 43, two pages at
    123-124), then one more page after the folder."""
    folios = [FakeFolio(37734, "70", 77, folder=27634),
              FakeFolio(45012, "72", 120, report=42),
              FakeFolio(45013, "73", 121, report=42),
              FakeFolio(45014, "74", 122, report=42),
              FakeFolio(45027, "87", 123, report=43, desc="Cable list"),
              FakeFolio(45028, "88", 124, report=43, desc="Cable list"),
              FakeFolio(46000, "91", 125, folder=27640)]
    proj = FakeProject(folios, **kw)
    t = FakeTime()
    auto._SLEEP, auto._CLOCK = t.sleep, t.clock
    return FakeApp(proj), proj, t


def order(proj):
    return [f.tag for f in sorted(proj.folios, key=lambda f: f.position)]


auto._id_array = lambda ids: [int(i) for i in ids]

mark = len(failures)
# --- H: settle, then restore folder and position --------------------------
app, proj, t = world()
r = auto.generate_report_drawings(app, CLIENT, report_ids=[42],
                                  keep_position=True)
check(r["ok"], f"H: got {r}")
s = r["settle"][0]
check(s["settled"] and s["polls"] == 3 and s["seconds"] == 6.0
      and t.sleeps == [3.0, 3.0],
      f"H: three listings, two sleeps of the interval, got {s} {t.sleeps}")
check(len(s["transient_ids"]) == 2,
      f"H: the two blanks that came and went are reported, got {s}")
bom = {f.fid: f for f in proj.folios if f.report == 42}
check(set(bom) == {45012, 45013, 45014},
      f"H: the report kept its ids in this scenario, got {sorted(bom)}")
check(all(f.folder == REPORTS for f in bom.values())
      and [bom[i].position for i in (45012, 45013, 45014)] == [120, 121, 122],
      f"H: the BOM is back in its folder at 120-122, got "
      f"{[(f.fid, f.folder, f.position) for f in bom.values()]}")
check(all(("fil_fol_id", REPORTS) in f.writes for f in bom.values()),
      "H: the folder was written back, not only the position")
pos = r["positions"][0]
check(pos["folios_replaced"] == [] and sorted(pos["folios_new"]) == sorted(pos["unmatched_new"])
      and len(pos["unmatched_new"]) == 3 and "blank" in pos["note"],
      f"H: the three blanks that stayed are unmatched and explained, got {pos}")
check(all(m["ok"] and m["folder_from"] == TOP and m["folder_to"] == REPORTS
          for m in pos["repositioned"]),
      f"H: every move names the folder change, got {pos['repositioned']}")
cables = [f for f in proj.folios if f.report == 43]
check(all(f.writes == [] for f in cables), "H: the cable list is not touched")
blanks = [f for f in proj.folios if f.report == 99]
check(all(f.writes == [] for f in blanks), "H: the blanks are not moved")
check(order(proj)[:6] == ["70", "72", "73", "74", "87", "88"],
      f"H: the tree runs BOM before cable list again, got {order(proj)}")
ok("H ok: the tool waits for the tree to settle and restores folder and "
   "position", mark)

mark = len(failures)
# --- I: no wait ----------------------------------------------------------
app, proj, t = world()
r = auto.generate_report_drawings(app, CLIENT, report_ids=[42],
                                  keep_position=False, wait_for_settle=False)
check(r["ok"] and "settle" not in r and "warning" in r
      and "list_folios" in r["warning"],
      f"I: returns at once with a warning, got {r}")
check(t.sleeps == [] and proj.queue, "I: nothing slept, the tree is unsettled")
check("folder_id" in r["note"], "I: the note names move_folio's folder_id")
ok("I ok: wait_for_settle=False returns at once and says so", mark)

mark = len(failures)
# --- J: never settles ----------------------------------------------------
app, proj, t = world(never_settle=True)
r = auto.generate_report_drawings(app, CLIENT, report_ids=[42],
                                  keep_position=True, settle_timeout=30)
s = r["settle"][0]
check(not r["ok"] and not s["settled"] and s["seconds"] == 30.0
      and s["polls"] == 11,
      f"J: the wait gives up at the timeout, got {s}")
check("still changing" in r.get("error", "") and "list_folios" in r["error"],
      f"J: the result says why, got {r.get('error')!r}")
ok("J ok: a tree that keeps changing is reported, not waited on forever",
   mark)

mark = len(failures)
# --- K: new ids at the top level -----------------------------------------
app, proj, t = world(replace_ids=True)
r = auto.generate_report_drawings(app, CLIENT, report_ids=[42],
                                  keep_position=True)
check(r["ok"], f"K: got {r}")
pos = r["positions"][0]
check(pos["folios_replaced"] == [45012, 45013, 45014]
      and len(pos["folios_new"]) == 6 and len(pos["unmatched_new"]) == 3,
      f"K: three new BOM pages matched, three blanks not, got {pos}")
bom = sorted((f for f in proj.folios if f.report == 42),
             key=lambda f: f.position)
check([f.tag for f in bom] == ["72", "73", "74"]
      and all(f.folder == REPORTS for f in bom)
      and [f.position for f in bom] == [120, 121, 122],
      f"K: the new ids sit in the old folder at 120-122, got "
      f"{[(f.fid, f.folder, f.position) for f in bom]}")
blanks = [f for f in proj.folios if f.report == 99]
check(all(f.folder == TOP and f.writes == [] for f in blanks),
      "K: the blanks (no filter suffix) stay at the top level, untouched")
check(order(proj)[:7] == ["70", "72", "73", "74", "87", "88", "91"],
      f"K: got {order(proj)}")
ok("K ok: replaced folios are matched by description and get the folder "
   "back", mark)

mark = len(failures)
# --- L: one settle per run -----------------------------------------------
app, proj, t = world()
r = auto.generate_report_drawings(app, CLIENT, report_ids=[42, 43],
                                  keep_position=True)
check(r["ok"] and len(r["settle"]) == 2 and len(r["positions"]) == 2
      and all(s["settled"] for s in r["settle"]),
      f"L: got {r}")
check(order(proj)[:7] == ["70", "72", "73", "74", "87", "88", "91"],
      f"L: both reports back in place, got {order(proj)}")
ok("L ok: every run waits for its own settle", mark)

print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("all report settle checks passed")
