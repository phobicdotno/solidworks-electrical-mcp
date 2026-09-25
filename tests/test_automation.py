"""Regression test: the project-wide operations, without SOLIDWORKS.

Every function here is a bulk edit of a whole project with no undo, and the
difference between a safe one and a destructive one is a single enum value.
``number_wires`` passes 0 to number the wires that have none and 2 to throw
every wire number away and start again; on a project somebody has already
built to, the second changes labels printed on real wires in a real cabinet.
A live test cannot check that mapping without doing the damage, so the enum
each action really reaches ``process`` with is checked here.

The same goes for the selection. A run meant for one page that silently
widens to the whole project is the failure mode worth designing against, so
the id array handed to setSelection is asserted, not assumed.

Cases:
  A. an unknown action is refused, and nothing is processed
  B. each wire action reaches process with the right enum
  C. renumber_manual is off unless asked for, and is forwarded
  D. a book selection without book_id is refused before anything runs
  E. a folio selection resolves pages to ids and sets exactly those
  F. number_marks maps its object types and applies start/step to each
  G. an unknown object type is refused with the valid names
  H. the arrow actions map, including remove
  I. export_dwg validates save_type and version, and reports files written
  J. export_dwg with no selection at all is refused
  K. export_reports picks its writer from the format
  L. list_wires matches a component at either end of the wire
  M. update_wire writes only the fields it was given
  N. a file-per-page DWG export always carries a naming formula
  O. an exporter that reports success and writes nothing is not a success
  P. naming pages scopes the run to those pages without being told twice
  Q. a scope that contradicts the pages given is refused
  R. a repeat export to the same folder is not mistaken for a failure, and
     an export left in place is told apart from one that did nothing
  S. a pass whose process() refuses is reported as a failure, with the code

Run directly:
    .venv/Scripts/python.exe tests/test_automation.py
"""
import sys
import tempfile
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


# --------------------------------------------------------------------------
# Fakes. The VARIANT the real code builds for setSelection is opaque, so the
# id array is captured before it is wrapped.


class Recorder:
    """A generic operation object: every call lands in the shared log.

    ``fail`` makes one named member return a non-zero EwErrorCode, because
    every member returning 0 means no test ever sees what happens when
    SOLIDWORKS refuses.
    """

    def __init__(self, log, name, fail=None):
        self.log, self.name = log, name
        self.fail = fail or {}

    def __getattr__(self, member):
        def _call(*args):
            self.log.append((member, args))
            return self.fail.get(member, 0)
        return _call


class ExportRecorder(Recorder):
    """A recorder that also drops files where the export was pointed.

    The exporters are judged by what turned up on disk, not by their return
    code - a rc of 0 with an empty folder is the failure worth catching - so
    the fake has to actually write something. __getattr__ is looked up on the
    type, never the instance, so this has to be its own class.
    """

    WRITERS = ("exportDwg", "doExcelExport", "doTxtExport", "doXMLExport")
    DIR_SETTERS = ("setExportDirectory", "setTargetFolder")

    def __init__(self, log, name, files):
        super().__init__(log, name)
        self.files, self.target_dir = files, None

    def __getattr__(self, member):
        def _call(*args):
            self.log.append((member, args))
            if member in self.DIR_SETTERS:
                self.target_dir = args[0]
            elif member in self.WRITERS and self.target_dir:
                for fn in self.files:
                    Path(self.target_dir, fn).write_text("x")
            return 0
        return _call


class FakeFolio:
    def __init__(self, fid, tag):
        self.fid, self.tag = fid, tag

    def getID(self):
        return self.fid

    def getTag(self):
        return (self.tag, 0)

    def __getattr__(self, name):
        if name == "isOpen":
            return lambda: (False, 0)
        if name.startswith("get") or name.startswith("is"):
            return lambda *a: (0, 0)
        raise AttributeError(name)


class FakeWire:
    def __init__(self, wid, mark, equi, frm, to, frm_id, to_id):
        self.f = {"setTag": mark, "setSignal": "", "setColorCode": "",
                  "setSectionOrGauge": ""}
        self.wid, self.equi = wid, equi
        self.frm, self.to, self.frm_id, self.to_id = frm, to, frm_id, to_id
        self.calls: list = []

    def getID(self):
        return self.wid

    def getTag(self):
        return (self.f["setTag"], 0)

    def getEquipotential(self):
        return (self.equi, 0)

    def getOrigin(self):
        return (self.frm, 0)

    def getDestination(self):
        return (self.to, 0)

    def getOriginComponentID(self):
        return (self.frm_id, 0)

    def getDestinationComponentID(self):
        return (self.to_id, 0)

    def __getattr__(self, name):
        if name.startswith("get"):
            return lambda *a: (self.f.get("set" + name[3:], ""), 0)
        if name.startswith("set"):
            def _set(*a):
                self.calls.append((name, a))
                self.f[name] = a[-1]
                return 0
            return _set
        if name == "update":
            def _update():
                self.calls.append(("update", ()))
                return 0
            return _update
        raise AttributeError(name)


class FakeReport:
    def __init__(self, rid, filename, filt, order):
        self.rid, self.filename, self.filt, self.order = (rid, filename,
                                                          filt, order)

    def getID(self):
        return self.rid

    def getReportFileName(self):
        return (self.filename, 0)

    def getFilter(self):
        return (self.filt, 0)

    def getOrderNo(self):
        return (self.order, 0)

    def getEwProjectDataExportType(self):
        return (0, 0)


class FakeProject:
    def __init__(self, log, folios, wires, reports, writes_files=None,
                 fail=None):
        self.log, self.folios, self.wires = log, folios, wires
        self.reports = reports
        self.writes_files = writes_files or []
        self.fail = fail or {}

    # Each factory hands back a recorder tagged with which operation it is,
    # so one log tells the whole story in order.
    def _new(self, name):
        self.log.append((f"new {name}", ()))
        if name in ("ExportDWGFiles", "ExportReport"):
            return (ExportRecorder(self.log, name, self.writes_files), 0)
        return (Recorder(self.log, name, self.fail), 0)

    def newEwProjectNumberWires(self):
        return self._new("NumberWires")

    def newEwProjectNumberMarks(self):
        return self._new("NumberMarks")

    def newEwProjectAutomaticArrows(self):
        return self._new("AutomaticArrows")

    def newEwProjectOptimizeWireOrder(self):
        return self._new("OptimizeWireOrder")

    def newEwProjectGenerateTSDrawing(self):
        return self._new("GenerateTSDrawing")

    def newEwProjectExportDWGFiles(self):
        return self._new("ExportDWGFiles")

    def newEwProjectExportReport(self):
        return self._new("ExportReport")

    def getEwProjectWireManager(self):
        return (FakeWireManager(self.wires), 0)

    def getEwProjectReportManager(self):
        return (FakeReportManager(self.reports), 0)


class FakeWireManager:
    def __init__(self, wires):
        self.wires = wires

    def getEwProjectWireArray(self):
        return (self.wires, 0)

    def findEwProjectWireByID(self, wid):
        for w in self.wires:
            if w.wid == wid:
                return (w, 0)
        return (None, 8)


class FakeReportManager:
    def __init__(self, reports):
        self.reports = reports

    def getCount(self):
        return (len(self.reports), 0)

    def at(self, i):
        return (self.reports[i], 0)


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


def world(writes_files=None, fail=None):
    log: list = []
    folios = [FakeFolio(101, "101"), FakeFolio(102, "102"),
              FakeFolio(107, "07")]
    wires = [
        FakeWire(1, "W1", "L1", "-CC_N1:X5:3", "-CC_K1:A1", 500, 600),
        FakeWire(2, "W2", "L2", "-CC_K1:A2", "-CC_T1:2", 600, 700),
        FakeWire(3, "", "PE", "-CC_A1:PE", "-CC_T1:8", 900, 700),
    ]
    reports = [FakeReport(11, "Bill of materials", "cmp", 2),
               FakeReport(12, "Wire list", "wire", 1)]
    app = FakeApp(FakeProject(log, folios, wires, reports, writes_files,
                              fail))
    return app, log


# find_folio is a workflows function that walks the real project; the fakes
# here carry only what these operations need, so it is pointed at the folio
# list directly.
def _fake_find_folio(app, client, page=None, file_id=None):
    for f in app.proj.folios:
        if file_id is not None and f.fid == file_id:
            return f
        if page is not None and str(f.tag) == str(page):
            return f
    raise LookupError(f"no folio {page or file_id}")


auto.find_folio = _fake_find_folio
auto._folio_row = lambda f: {"id": f.fid, "page": f.tag}
# The real _id_array builds a pywin32 VARIANT; keep the ids visible instead.
auto._id_array = lambda ids: ("IDS", tuple(int(i) for i in ids))


def find(log, member):
    return [a for m, a in log if m == member]


mark = len(failures)
# --- A: an unknown action is refused before anything runs -----------------
app, log = world()
for fn, kw in ((auto.number_wires, {"action": "reset"}),
               (auto.number_marks, {"action": "wipe"}),
               (auto.generate_arrows, {"action": "connect"})):
    try:
        fn(app, CLIENT, **kw)
        check(False, f"A: {fn.__name__} should refuse {kw}")
    except ValueError as e:
        check("have" in str(e),
              f"A: the error should list the valid actions: {e}")
check(not log, f"A: nothing may be created or processed, log={log}")
ok("A ok: an unknown action is refused and nothing runs", mark)

mark = len(failures)
# --- B: each wire action reaches process with the right enum --------------
# This is the whole test. "new" is 0 and "renumber" is 2; swapping them
# rewrites every wire label in the project.
for action, want in (("new", 0), ("new_and_recalculate", 1),
                     ("renumber", 2), ("remove", 3)):
    app, log = world()
    r = auto.number_wires(app, CLIENT, action=action)
    got = find(log, "process")
    check(r["ok"] and got == [(want,)],
          f"B: {action} should process({want}), got {got}")
ok("B ok: every wire action maps to its own enum", mark)

mark = len(failures)
# --- C: a hand-typed number is left alone unless asked -------------------
app, log = world()
auto.number_wires(app, CLIENT)
check(find(log, "setActionOnManualNumber") == [(False,)],
      f"C: manual numbers are off by default, "
      f"got {find(log, 'setActionOnManualNumber')}")
app, log = world()
auto.number_wires(app, CLIENT, renumber_manual=True)
check(find(log, "setActionOnManualNumber") == [(True,)],
      f"C: renumber_manual must reach the operation, "
      f"got {find(log, 'setActionOnManualNumber')}")
ok("C ok: manual numbers are left alone unless asked for", mark)

mark = len(failures)
# --- D: a book selection with no book is refused, not widened ------------
app, log = world()
try:
    auto.number_wires(app, CLIENT, selection="book")
    check(False, "D: selection='book' with no book_id must raise")
except ValueError as e:
    check("book_id" in str(e), f"D: the error should name book_id: {e}")
check(not find(log, "process"),
      "D: it must not fall through and process everything")
try:
    auto.number_wires(app, CLIENT, selection="everything")
    check(False, "D: an unknown selection must raise")
except ValueError:
    pass
ok("D ok: an incomplete selection is refused, never widened", mark)

mark = len(failures)
# --- E: a folio selection resolves pages to exactly those ids ------------
app, log = world()
r = auto.number_wires(app, CLIENT, action="renumber", selection="folios",
                      pages=["102", "07"])
check(find(log, "setSelectionType") == [(3,)],
      f"E: the selection type should be folios (3), "
      f"got {find(log, 'setSelectionType')}")
check(find(log, "setSelection") == [(("IDS", (102, 107)),)],
      f"E: exactly the two folios should be selected, "
      f"got {find(log, 'setSelection')}")
check([f["page"] for f in r["folios"]] == ["102", "07"],
      f"E: the result should name what it acted on, got {r['folios']}")
ok("E ok: a folio selection resolves to exactly those pages", mark)

mark = len(failures)
# --- F: mark numbering maps its object types -----------------------------
app, log = world()
r = auto.number_marks(app, CLIENT, action="renumber",
                      object_types=["component", "cable"],
                      start_number=10, step_increment=5)
check(find(log, "process") == [(1,)],
      f"F: renumber should process(1), got {find(log, 'process')}")
check(sorted(a[0] for a in find(log, "addNumberObjectType")) == [4, 32],
      f"F: component is 4 and cable is 32, "
      f"got {find(log, 'addNumberObjectType')}")
check(sorted(find(log, "setStartNumber")) == [(4, 10), (32, 10)],
      f"F: the start number applies per object type, "
      f"got {find(log, 'setStartNumber')}")
check(sorted(find(log, "setStepIncrement")) == [(4, 5), (32, 5)],
      f"F: so does the step, got {find(log, 'setStepIncrement')}")
ok("F ok: mark numbering maps its object types and per-type settings", mark)

mark = len(failures)
# --- G: an unknown object type is refused --------------------------------
app, log = world()
try:
    auto.number_marks(app, CLIENT, object_types=["component", "widget"])
    check(False, "G: an unknown object type must raise")
except ValueError as e:
    check("widget" in str(e) and "have" in str(e),
          f"G: the error should name it and list the valid ones: {e}")
check(not log, "G: nothing may run")
ok("G ok: an unknown object type is refused", mark)

mark = len(failures)
# --- H: the arrow actions map, including the destructive one -------------
for action, want in (("auto_connect", 0), ("reconnect", 1), ("remove", 2)):
    app, log = world()
    auto.generate_arrows(app, CLIENT, action=action)
    check(find(log, "process") == [(want,)],
          f"H: {action} should process({want}), got {find(log, 'process')}")
ok("H ok: every arrow action maps to its own enum", mark)

mark = len(failures)
# --- I: DWG export validates its format and reports what appeared --------
tmp = tempfile.mkdtemp()
app, log = world(writes_files=["101.dwg", "102.dwg"])
r = auto.export_dwg(app, CLIENT, output_dir=tmp, pages=["101", "102"],
                    save_type="dxf", dwg_version="2013")
check(find(log, "setDwgSaveType") == [(1,)],
      f"I: dxf is save type 1, got {find(log, 'setDwgSaveType')}")
check(find(log, "setDwgVersion") == [(31,)],
      f"I: 2013 is version 31, got {find(log, 'setDwgVersion')}")
check(r["ok"] and r["files_written"] == ["101.dwg", "102.dwg"],
      f"I: it should report the files that appeared, got {r}")
for kw in ({"save_type": "pdf"}, {"dwg_version": "2026"}):
    try:
        auto.export_dwg(app, CLIENT, output_dir=tmp, all_pages=True, **kw)
        check(False, f"I: export_dwg should refuse {kw}")
    except ValueError as e:
        check("have" in str(e), f"I: list the valid values: {e}")
ok("I ok: DWG export validates its format and reports what it wrote", mark)

mark = len(failures)
# --- J: an export with no selection at all is refused --------------------
# Defaulting to the whole project here would quietly write 181 files.
app, log = world()
try:
    auto.export_dwg(app, CLIENT, output_dir=tmp)
    check(False, "J: export_dwg with no selection must raise")
except ValueError as e:
    check("all_pages" in str(e), f"J: the error should say how: {e}")
check(not find(log, "exportDwg"), "J: nothing may be exported")
ok("J ok: an export with no selection is refused, not widened", mark)

mark = len(failures)
# --- K: the report format picks the writer -------------------------------
for fmt, writer, ext in (("xlsx", "doExcelExport", 3),
                         ("csv", "doTxtExport", 1),
                         ("xml", "doXMLExport", 4)):
    app, log = world(writes_files=[f"report.{fmt}"])
    d = tempfile.mkdtemp()
    r = auto.export_reports(app, CLIENT, output_dir=d, all_reports=True,
                            file_format=fmt)
    check(r["ok"] and find(log, writer) == [()],
          f"K: {fmt} should go through {writer}, log={[m for m, _ in log]}")
    check(find(log, "setEwFileExtension") == [(ext,)],
          f"K: {fmt} is extension {ext}, "
          f"got {find(log, 'setEwFileExtension')}")
app, log = world()
r = auto.list_reports(app, CLIENT)
check([x["id"] for x in r["reports"]] == [12, 11],
      f"K: reports should come back in their configured order, got {r}")
ok("K ok: the format picks the writer, and reports keep their order", mark)

mark = len(failures)
# --- L: a wire is matched at either end ----------------------------------
app, log = world()
r = auto.list_wires(app, CLIENT, component_id=600)
check(r["count"] == 2,
      f"L: the relay is at one end of two wires, got {r['count']}")
check({w["id"] for w in r["wires"]} == {1, 2},
      f"L: both of them, whichever end it sits on, got {r['wires']}")
r = auto.list_wires(app, CLIENT, equipotential_contains="pe")
check(r["count"] == 1 and r["wires"][0]["id"] == 3,
      f"L: the equipotential filter should find the earth wire, got {r}")
r = auto.list_wires(app, CLIENT, limit=2)
check(r["count"] == 2 and r["scanned"] == 2,
      f"L: the limit should stop the scan, not just the output, got {r}")
ok("L ok: wires match at either end, and the limit stops the scan", mark)

mark = len(failures)
# --- M: update_wire writes only what it was given ------------------------
app, log = world()
r = auto.update_wire(app, CLIENT, wire_id=1, colour="BK",
                     section_or_gauge="0.75")
w = app.proj.wires[0]
written = [c[0] for c in w.calls]
check(r["ok"] and sorted(r["changed"]) == ["colour", "section_or_gauge"],
      f"M: exactly the two given fields should change, got {r}")
check("setTag" not in written and "setSignal" not in written,
      f"M: an untouched field must not be written, calls={written}")
check(written.count("update") == 1,
      f"M: one commit at the end, calls={written}")
r = auto.update_wire(app, CLIENT, wire_id=1)
check(r["ok"] and r["changed"] == [],
      f"M: a call with no fields is a quiet no-op, got {r}")
r = auto.update_wire(app, CLIENT, wire_id=999)
check(not r["ok"] and "no wire" in r["error"],
      f"M: a missing wire should report, not raise, got {r}")
ok("M ok: update_wire writes only what it was given", mark)

mark = len(failures)
# --- N: a file-per-page export always carries a naming formula ------------
# Measured live: exportDwg(kExportToMultipleFile) returns EW_BAD_INPUTS with
# no formula set, and again with %FILE_TAG%. The bare name FILE_TAG is what
# works, so one is supplied rather than left to the caller to discover.
app, log = world(writes_files=["p/101.dwg"])
d = tempfile.mkdtemp()
Path(d, "p").mkdir()
r = auto.export_dwg(app, CLIENT, output_dir=d, pages=["101"])
check(find(log, "setStrFileNameFormula") == [("FILE_TAG",)],
      f"N: a formula must be supplied by default, "
      f"got {find(log, 'setStrFileNameFormula')}")
check(find(log, "exportDwg") == [(0,)],
      f"N: and this is the file-per-page mode, got {find(log, 'exportDwg')}")
check(r["files_written"] == [str(Path("p", "101.dwg"))],
      f"N: the listing must walk subfolders, got {r['files_written']}")

# A single-file export does not need one, and an explicit formula wins.
app, log = world(writes_files=["all.dwg"])
d = tempfile.mkdtemp()
auto.export_dwg(app, CLIENT, output_dir=d, pages=["101"], single_file=True)
check(not find(log, "setStrFileNameFormula"),
      f"N: a single-file export needs no formula, "
      f"got {find(log, 'setStrFileNameFormula')}")
app, log = world(writes_files=["x.dwg"])
d = tempfile.mkdtemp()
auto.export_dwg(app, CLIENT, output_dir=d, pages=["101"],
                file_name_formula="PROJECT_TAG")
check(find(log, "setStrFileNameFormula") == [("PROJECT_TAG",)],
      f"N: an explicit formula must win, "
      f"got {find(log, 'setStrFileNameFormula')}")
ok("N ok: a file-per-page export always carries a naming formula", mark)

mark = len(failures)
# --- O: success is what landed on disk, not the return code ---------------
# The report writers return EW_NO_ERROR and produce no file on 2025 SP5.
# Trusting the rc would report a bill of materials that does not exist.
app, log = world(writes_files=[])
d = tempfile.mkdtemp()
r = auto.export_reports(app, CLIENT, output_dir=d, all_reports=True)
check(find(log, "doExcelExport") == [()],
      "O: the writer should still have been called")
check(not r["ok"] and "wrote nothing" in r["error"],
      f"O: a silent empty export must not report success, got {r}")
d = tempfile.mkdtemp()
r = auto.export_dwg(app, CLIENT, output_dir=d, pages=["101"])
check(not r["ok"] and "wrote nothing" in r["error"],
      f"O: the same goes for the DWG export, got {r}")
ok("O ok: an empty export is a failure however clean the return code", mark)

mark = len(failures)
# --- P: naming pages is enough to scope the run --------------------------
# The defect this guards: number_wires(pages=["102"], action="renumber") set
# selection type 0 (the whole project), never called setSelection, renumbered
# all 181 folios, and returned {"selection": "all", "folios": [page 102]} -
# a project-wide rewrite that read back as one page.
for fn, kw in ((auto.number_wires, {"action": "renumber"}),
               (auto.generate_arrows, {"action": "remove"}),
               (auto.optimize_wire_order, {})):
    app, log = world()
    r = fn(app, CLIENT, pages=["102"], **kw)
    check(find(log, "setSelectionType") == [(3,)],
          f"P: {fn.__name__} with pages must scope to folios (3), "
          f"got {find(log, 'setSelectionType')}")
    check(find(log, "setSelection") == [(("IDS", (102,)),)],
          f"P: {fn.__name__} must select exactly that folio, "
          f"got {find(log, 'setSelection')}")
    check(r["selection"] == "folios",
          f"P: {fn.__name__} must report the scope it used, got {r}")

# A book id alone scopes to the book, and nothing at all still means all.
app, log = world()
auto.number_wires(app, CLIENT, book_id=7)
check(find(log, "setSelectionType") == [(1,)]
      and find(log, "setSelection") == [(("IDS", (7,)),)],
      f"P: a book id alone should scope to that book, "
      f"got {find(log, 'setSelectionType')} {find(log, 'setSelection')}")
app, log = world()
r = auto.number_wires(app, CLIENT)
check(find(log, "setSelectionType") == [(0,)] and r["selection"] == "all",
      f"P: nothing asked for still means the whole project, got {r}")
ok("P ok: naming pages scopes the run, and is reported as such", mark)

mark = len(failures)
# --- Q: a contradictory scope is refused ---------------------------------
# Either reading might be what was meant, and one of them runs over the
# whole project, so guessing is not acceptable here.
app, log = world()
try:
    auto.number_wires(app, CLIENT, selection="all", pages=["102"])
    check(False, "Q: selection='all' with pages must raise")
except ValueError as e:
    check("ambiguous" in str(e), f"Q: unhelpful error: {e}")
check(not find(log, "process"), "Q: nothing may run")
try:
    auto.number_wires(app, CLIENT, selection="folios", book_id=7)
    check(False, "Q: selection='folios' with book_id must raise")
except ValueError as e:
    check("ambiguous" in str(e), f"Q: unhelpful error: {e}")
# The explicit form still works when it agrees with the arguments.
app, log = world()
r = auto.number_wires(app, CLIENT, selection="folios", pages=["101", "07"])
check(r["ok"] and find(log, "setSelection") == [(("IDS", (101, 107)),)],
      f"Q: an explicit scope that agrees must still work, got {r}")
ok("Q ok: a scope that contradicts the pages given is refused", mark)

mark = len(failures)
# --- R: the three export outcomes are told apart -------------------------
# Measured live: SOLIDWORKS does not rewrite a DWG that is already there, so
# a second export to the same folder leaves the file untouched. "Nothing
# changed" therefore means two quite different things, and calling both a
# failure is wrong - a fixed per-project export folder is normal usage.
import os

# 1. files appear -> it worked.
d = tempfile.mkdtemp()
app, log = world(writes_files=["101.dwg"])
r1 = auto.export_dwg(app, CLIENT, output_dir=d, pages=["101"])
check(r1["ok"] and r1["files_written"] == ["101.dwg"],
      f"R: the first export should report the file, got {r1}")
check("note" not in r1 and "error" not in r1,
      f"R: and needs no explanation, got {r1}")

# 2. nothing changes but the folder holds the export -> left in place.
app, log = world(writes_files=[])        # the exporter writes nothing new
r2 = auto.export_dwg(app, CLIENT, output_dir=d, pages=["101"])
check(r2["ok"], f"R: an export already in place is not a failure, got {r2}")
check("already in" in r2.get("note", ""),
      f"R: and it should say why nothing changed, got {r2}")
check(r2["files_present"] == ["101.dwg"],
      f"R: reporting what is there, got {r2}")

# 3. nothing changes and the folder is empty -> it really did nothing.
d = tempfile.mkdtemp()
app, log = world(writes_files=[])
r3 = auto.export_dwg(app, CLIENT, output_dir=d, pages=["101"])
check(not r3["ok"] and "wrote nothing" in r3["error"],
      f"R: a genuinely empty export is still a failure, got {r3}")

# The report writer takes the same three readings.
d = tempfile.mkdtemp()
app, log = world(writes_files=[])
r4 = auto.export_reports(app, CLIENT, output_dir=d, all_reports=True)
check(not r4["ok"] and "generate_report_drawings" in r4["error"],
      f"R: and points at the route that does work, got {r4}")
ok("R ok: written, left in place and did nothing are three answers", mark)

mark = len(failures)
# --- S: a refusal from SOLIDWORKS is a failure, and says which code ------
# Every member of the fake returned 0, so no test had ever seen a pass that
# SOLIDWORKS declined. A numbering run that quietly reports ok on a refusal
# is worse than one that fails: the caller believes the project was changed.
for fn, kw in ((auto.number_wires, {"action": "new"}),
               (auto.number_marks, {"action": "update"}),
               (auto.generate_arrows, {"action": "auto_connect"}),
               (auto.optimize_wire_order, {}),
               (auto.generate_terminal_strip_drawings, {})):
    member = "generate" if fn is auto.generate_terminal_strip_drawings         else "process"
    app, log = world(fail={member: 45})
    r = fn(app, CLIENT, **kw)
    check(not r["ok"],
          f"S: {fn.__name__} must not report ok when {member} refuses, "
          f"got {r}")
    named = [st for st in r["steps"]
             if st["step"] == member and st["rc_name"] == "EW_PROJECT_OPENED"]
    check(named,
          f"S: and the steps must carry the named code, got {r['steps']}")
ok("S ok: a refused pass is reported as one, with the error code", mark)

print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("all automation checks passed")
