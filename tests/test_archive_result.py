"""Regression test: archive_project never takes rc 0 as proof of a file.

Observed 2026-10-20 on 2025 SP5: IEwProjectManagerX.archive answered
EW_NO_ERROR four times in a row and wrote nothing (into an existing folder
with overwrite, into a fresh path, without dependencies, and once more into
a temp folder), with one drawing open and unsaved in the editor. The day
before the same call wrote 47 MB. The API help names no precondition. So the
result has to say, in words, that no file was written and what was observed,
and offer the one lead there is: closing the open drawings first.

Cases:
  A. rc 0 and no file -> ok False, written False, reason says "no file was
     written" and lists the open folios
  B. close_open_folios closes the open folios before the archive and reopens
     them after, even when the archive raises
  C. a file that does appear -> ok True, written True, no reason
  D. a non-zero rc gets its own reason, with the code name
  E. no folio open and still no file -> the reason says the open-drawing
     lead is ruled out this time

Run directly:
    .venv/Scripts/python.exe tests/test_archive_result.py
"""
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from solidworks_electrical_mcp import projects as pj

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)
        print("FAIL:", msg)


def ok(msg: str, mark: int) -> None:
    if len(failures) == mark:
        print(msg)


# The real code imports pythoncom / win32com inside archive_project; stub
# them so the test runs where pywin32 is absent too.
import types

if "pythoncom" not in sys.modules:
    sys.modules["pythoncom"] = types.SimpleNamespace(VT_ARRAY=0x2000,
                                                      VT_I4=3)
if "win32com.client" not in sys.modules:
    w32 = types.ModuleType("win32com")
    cl = types.ModuleType("win32com.client")
    cl.VARIANT = lambda vt, val: ("VARIANT", vt, val)
    w32.client = cl
    sys.modules["win32com"] = w32
    sys.modules["win32com.client"] = cl


class FakeFolio:
    def __init__(self, fid, tag, is_open):
        self.fid, self.tag, self._open = fid, tag, is_open
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
        return (self.fid, 0)

    def getEwProjectBookID(self):
        return (1, 0)

    def getEwProjectFolderID(self):
        return (-1, 0)

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
        self.folios = folios

    def getEwProjectFileArray(self):
        return (list(self.folios), 0)

    def findEwProjectFileByID(self, fid):
        return next(((f, 0) for f in self.folios if f.fid == int(fid)),
                    (None, 22))


class FakeProject:
    def __init__(self, pid, name, folios):
        self.pid, self.name, self.folios = pid, name, folios

    def getID(self):
        return self.pid

    def getName(self):
        return (self.name, 0)

    def getProjectType(self):
        return (0, 0)

    def isOpenByMe(self):
        return (False, 0)

    def isOpenByAnother(self):
        return (False, 0)

    def getEwProjectFileManager(self):
        return (FakeFileMgr(self.folios), 0)

    def __getattr__(self, name):
        if name.startswith("get"):
            return lambda *a: ("", 0)
        raise AttributeError(name)


class FakeProjectMgr:
    """``archive`` writes a file, or not, or raises - per ``behaviour``."""

    def __init__(self, proj, behaviour, log):
        self.proj, self.behaviour, self.log = proj, behaviour, log

    def findEwProjectByID(self, pid):
        return (self.proj, 0) if int(pid) == self.proj.pid else (None, 22)

    def archive(self, ids, path, deps):
        self.log.append(("archive", path, deps,
                         [f._open for f in self.proj.folios]))
        if self.behaviour == "write":
            Path(path).write_bytes(b"x" * 100)
            return 0
        if self.behaviour == "raise":
            raise RuntimeError("COM blew up")
        if self.behaviour == "refuse":
            return 2
        return 0                                   # "silent": rc 0, no file


class FakeEnv:
    def __init__(self, mgr):
        self.mgr = mgr

    def getEwProjectManager(self):
        return (self.mgr, 0)


class FakeApp:
    def __init__(self, proj, behaviour, log):
        self.proj = proj
        self.env = FakeEnv(FakeProjectMgr(proj, behaviour, log))

    def getEwEnvironment(self):
        return (self.env, 0)

    def getEwProjectCurrent(self):
        return (self.proj, 0)


class FakeClient:
    @staticmethod
    def Dispatch(x):
        return x


CLIENT = FakeClient()


def world(behaviour, open_pages=("09",)):
    folios = [FakeFolio(21745, "08", False),
              FakeFolio(21746, "09", "09" in open_pages),
              FakeFolio(22026, "10", "10" in open_pages)]
    log: list = []
    proj = FakeProject(23, "Demo project", folios)
    return FakeApp(proj, behaviour, log), proj, log


with tempfile.TemporaryDirectory() as tmp:
    mark = len(failures)
    # --- A: rc 0, nothing on disk --------------------------------------
    app, proj, log = world("silent")
    out = os.path.join(tmp, "a", "demo.tewzip")
    r = pj.archive_project(app, CLIENT, out)
    check(not r["ok"] and r["written"] is False and r["exists"] is False,
          f"A: no file means not ok, got {r}")
    check(r["rc"] == 0 and r["rc_name"] == "EW_NO_ERROR",
          f"A: the raw rc stays visible, got {r.get('rc')}")
    check("no file was written" in r.get("reason", ""),
          f"A: the reason must say it in words, got {r.get('reason')!r}")
    check("21746" in r["reason"] and "page 09" in r["reason"],
          f"A: the open folio must be named, got {r['reason']!r}")
    check(r["open_folios"] == [{"id": 21746, "page": "09", "book_id": 1,
                                "description": "page 09"}],
          f"A: open_folios must list the editor's open page, got "
          f"{r['open_folios']}")
    check("close_open_folios" in r["reason"],
          f"A: the reason must offer the way to test the lead, got "
          f"{r['reason']!r}")
    check(proj.folios[1].log == [],
          "A: without close_open_folios nothing is closed")
    ok("A ok: rc 0 with no file is reported as no file, with the open page",
       mark)

    mark = len(failures)
    # --- B: close_open_folios closes first, reopens after ---------------
    app, proj, log = world("write")
    out = os.path.join(tmp, "b", "demo.tewzip")
    r = pj.archive_project(app, CLIENT, out, close_open_folios=True)
    check(r["ok"] and r["written"],
          f"B: the archive must still be judged by the file, got {r}")
    check(log[0][3] == [False, False, False],
          f"B: every folio must be closed when archive runs, got {log}")
    check(proj.folios[1].log == ["close", "open"],
          f"B: the open folio is closed once and reopened once, got "
          f"{proj.folios[1].log}")
    check(proj.folios[0].log == [], "B: a closed folio is left alone")
    check(r["closed_folios"] and r["closed_folios"][0]["id"] == 21746
          and r["closed_folios"][0]["open_rc"] == 0,
          f"B: closed_folios reports the cycle, got {r['closed_folios']}")
    app, proj, log = world("raise")
    try:
        pj.archive_project(app, CLIENT, os.path.join(tmp, "b2", "x.tewzip"),
                           close_open_folios=True)
        check(False, "B: the COM error must propagate")
    except RuntimeError:
        pass
    check(proj.folios[1].log == ["close", "open"],
          f"B: the folio is reopened even when archive raises, got "
          f"{proj.folios[1].log}")
    ok("B ok: close_open_folios cycles the open folios around the archive",
       mark)

    mark = len(failures)
    # --- C: a real file -------------------------------------------------
    app, proj, log = world("write")
    out = os.path.join(tmp, "c", "demo.tewzip")
    r = pj.archive_project(app, CLIENT, out)
    check(r["ok"] and r["written"] and r["exists"] and r["size_bytes"] == 100,
          f"C: got {r}")
    check("reason" not in r, f"C: a written archive needs no reason, got {r}")
    r2 = pj.archive_project(app, CLIENT, out)
    check(not r2["ok"] and r2["written"] is False and "overwrite" in r2["error"],
          f"C: an existing file is still refused without overwrite, got {r2}")
    r3 = pj.archive_project(app, CLIENT, out, overwrite=True)
    check(r3["ok"] and r3["replaced"], f"C: overwrite replaces, got {r3}")
    ok("C ok: a written archive is ok, written, and carries no reason", mark)

    mark = len(failures)
    # --- D: a refusal keeps its code ------------------------------------
    app, proj, log = world("refuse")
    r = pj.archive_project(app, CLIENT, os.path.join(tmp, "d", "x.tewzip"))
    check(not r["ok"] and r["written"] is False
          and "EW_BAD_INPUTS" in r["reason"] and "no file was written" in r["reason"],
          f"D: got {r}")
    ok("D ok: a non-zero rc is named in the reason", mark)

    mark = len(failures)
    # --- E: nothing open, still nothing written -------------------------
    app, proj, log = world("silent", open_pages=())
    r = pj.archive_project(app, CLIENT, os.path.join(tmp, "e", "x.tewzip"))
    check(not r["ok"] and r["open_folios"] == []
          and "No folio was open" in r["reason"]
          and "no file was written" in r["reason"],
          f"E: got {r}")
    app, proj, log = world("silent")
    r = pj.archive_project(app, CLIENT, os.path.join(tmp, "e2", "x.tewzip"),
                           close_open_folios=True)
    check("were closed first" in r["reason"] and "not the cause" in r["reason"],
          f"E: closing them and still getting nothing must say so, got "
          f"{r['reason']!r}")
    ok("E ok: the reason tells an open-drawing case from the unknown one",
       mark)

print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("all archive result checks passed")
