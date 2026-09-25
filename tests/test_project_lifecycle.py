"""Regression test: the project-lifecycle tools, without SOLIDWORKS.

Every other workflow starts from ``getEwProjectCurrent`` and so could only
ever touch the project a human had already opened. These functions work one
level up, on the environment's project manager, which means they are the only
ones that can pick the wrong project entirely - or delete it.

The interesting behaviour is all in the guards, and guards are exactly what a
live test cannot exercise safely: you cannot check that delete_project
refuses a mismatched name by pointing it at a real project. So this drives a
fake project manager that records what was called on it.

Cases:
  A. list_projects works with nothing open, filters, and flags the current one
  B. open_project resolves an exact name
  C. open_project resolves a unique substring ("65021")
  D. an ambiguous substring is refused rather than guessed
  E. a project another user holds open is refused, and never opened
  F. close_project with no argument closes the current project
  G. close_project with nothing open succeeds and does nothing
  H. create_project refuses a name that already exists
  I. create_project refuses a template that is not installed
  J. create_project re-applies the name AFTER the template insert
  K. delete_project refuses a mismatched confirm_name and removes nothing
  L. delete_project closes the project first when it is the current one
  M. project_properties writes only what it was given, then updates once
  N. project_properties reads the addresses back for the caller

Run directly:
    .venv/Scripts/python.exe tests/test_project_lifecycle.py
"""
import sys
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


# --------------------------------------------------------------------------
# A fake project manager. Every setter records into ``calls`` so a test can
# assert on the ORDER of operations, which is where create_project gets it
# wrong if the name is applied before the template insert.


class FakeProject:
    def __init__(self, pid, name, desc="", ptype=0, by_another=False,
                 log=None):
        self._id, self._name, self._desc = pid, name, desc
        self._ptype, self._by_another = ptype, by_another
        self.calls = log if log is not None else []
        self.removed = False
        self.inserted = False
        self.fields = {}
        self.app = None                       # set once the FakeApp exists
        self._committed = (name, desc)
        self._committed_fields = {}

    # --- identity -------------------------------------------------------
    def getID(self):
        return self._id

    def getName(self):
        return (self._name, 0)

    def getDescription(self, lang):
        return (self._desc, 0)

    def getProjectType(self):
        return (self._ptype, 0)

    def isOpenByMe(self):
        return (False, 0)

    def isOpenByAnother(self):
        return (self._by_another, 0)

    def __getattr__(self, name):
        # Every other getter reports an empty string; every other setter
        # records itself and succeeds. Keeps the fake to the parts that
        # matter without a wall of stubs.
        if name.startswith("get"):
            # Read back whatever the matching setter recorded, so a test can
            # tell a committed write from one update() threw away.
            return lambda *a: (self.fields.get("set" + name[3:], ""), 0)
        if name.startswith("set"):
            def _set(*a):
                self.calls.append((name, a))
                self.fields[name] = a[-1]
                return 0
            return _set
        raise AttributeError(name)

    # --- lifecycle ------------------------------------------------------
    def setName(self, v):
        self.calls.append(("setName", (v,)))
        self._name = v
        return 0

    def setDescription(self, lang, v):
        self.calls.append(("setDescription", (lang, v)))
        self._desc = v
        return 0

    def insert(self):
        self.calls.append(("insert", ()))
        return 0

    def insertFromTemplate(self, template):
        self.calls.append(("insertFromTemplate", (template,)))
        self._desc = f"{template} Template project"
        self.inserted = True
        return 0

    def update(self):
        # The rule a live run exposed: update() commits nothing on a closed
        # project. It returns EW_PROJECT_NOTOPENED (20) and drops the edits
        # silently, so a caller that ignores the rc reports a write that
        # never happened. The fake enforces it - the pending edits are
        # discarded unless this project is the open one.
        self.calls.append(("update", ()))
        if self.app is None or self.app.current is not self:
            self._name, self._desc = self._committed
            self.fields = dict(self._committed_fields)
            return 20
        self._committed = (self._name, self._desc)
        self._committed_fields = dict(self.fields)
        return 0

    def remove(self):
        self.calls.append(("remove", ()))
        self.removed = True
        return 0


class FakeManager:
    def __init__(self, projects, log):
        self.projects = projects
        self.calls = log
        self.next_id = max([p._id for p in projects] + [0]) + 1

    def getEwProjectArray(self):
        return ([p for p in self.projects if not p.removed], 0)

    def findEwProjectByID(self, pid):
        for p in self.projects:
            if p._id == pid and not p.removed:
                return (p, 0)
        return (None, 8)

    def findEwProjectByName(self, name):
        for p in self.projects:
            if p._name == name and not p.removed:
                return (p, 0)
        return (None, 8)

    def newEwProject(self):
        p = FakeProject(self.next_id, "", log=self.calls)
        p.app = getattr(self, "app", None)
        self.next_id += 1
        self.projects.append(p)
        return (p, 0)


class FakeEnv:
    def __init__(self, mgr, template_folder):
        self.mgr, self.template_folder = mgr, template_folder

    def getEwProjectManager(self):
        return (self.mgr, 0)

    def getFolderPath(self, which):
        return (self.template_folder, 0)


class FakeApp:
    def __init__(self, projects, current=None, template_folder="", log=None):
        # One shared call log across the application, the manager and every
        # project, so a test can assert on the order of operations that span
        # them - closing before removing, for instance.
        self.calls: list = [] if log is None else log
        self.mgr = FakeManager(projects, self.calls)
        self.env = FakeEnv(self.mgr, template_folder)
        self.current = current
        self.mgr.app = self
        for p in projects:
            p.app = self

    def getEwEnvironment(self):
        return (self.env, 0)

    def getEwProjectCurrent(self):
        return (self.current, 0)

    def openEwProjectID(self, pid):
        self.calls.append(("openEwProjectID", (pid,)))
        hit = self.mgr.findEwProjectByID(pid)[0]
        if hit is None:
            return 8
        self.current = hit
        return 0

    def closeEwProjectID(self, pid):
        self.calls.append(("closeEwProjectID", (pid,)))
        if self.current is not None and self.current._id == pid:
            self.current = None
        return 0


class FakeClient:
    @staticmethod
    def Dispatch(x):
        return x


CLIENT = FakeClient()


def world(current_index=None, template_folder=""):
    """A small environment: four projects, one held elsewhere, plus a macro."""
    log: list = []
    projects = [
        FakeProject(1, "Unarchive Project Information", "ANSI Template",
                    log=log),
        FakeProject(9, "MR3ELE.D001.65021 USV", "BZM - SeaLeopard", log=log),
        FakeProject(4, "Main Control Panel - 2026", "Standard Industrial",
                    log=log),
        FakeProject(11, "Held Elsewhere", "locked", by_another=True, log=log),
        FakeProject(12, "Some Macro", "", ptype=1, log=log),
    ]
    cur = None if current_index is None else projects[current_index]
    return FakeApp(projects, current=cur, template_folder=template_folder,
                   log=log)


mark = len(failures)
# --- A: the listing works with nothing open -------------------------------
app = world()
r = pj.list_projects(app, CLIENT)
names = [p["name"] for p in r["projects"]]
check(r["current_project_id"] is None,
      f"A: nothing is open, so there is no current project: {r}")
check("Some Macro" not in names,
      f"A: the default listing is projects, not macros: {names}")
check(len(names) == 4, f"A: expected the four projects, got {names}")

app = world(current_index=1)
r = pj.list_projects(app, CLIENT, name_contains="65021")
check(r["count"] == 1 and r["projects"][0]["id"] == 9,
      f"A: name_contains should find the one project, got {r}")
check(r["projects"][0]["is_current"],
      f"A: the open project must be flagged current, got {r['projects'][0]}")
ok("A ok: listing works unopened, filters, and flags the current project",
   mark)

mark = len(failures)
# --- B: open by exact name ------------------------------------------------
app = world()
r = pj.open_project(app, CLIENT, name="MR3ELE.D001.65021 USV")
check(r["ok"] and r["current_project_id"] == 9, f"B: expected to open 9, {r}")
check(("openEwProjectID", (9,)) in app.calls,
      f"B: the open must go through openEwProjectID, calls={app.calls}")
ok("B ok: an exact name opens the project", mark)

mark = len(failures)
# --- C: open by unique substring -----------------------------------------
app = world()
r = pj.open_project(app, CLIENT, name="65021")
check(r["ok"] and r["current_project_id"] == 9,
      f"C: a unique substring should resolve, got {r}")
ok("C ok: a unique substring resolves", mark)

mark = len(failures)
# --- D: an ambiguous substring is refused, not guessed --------------------
# "Project" appears in two names. Opening the wrong project is silent and
# expensive, so this must raise rather than pick one.
app = world()
try:
    pj.open_project(app, CLIENT, name="a")
    check(False, "D: an ambiguous substring must not resolve to a guess")
except LookupError as e:
    check("matches" in str(e), f"D: unhelpful error for an ambiguity: {e}")
check(not any(c[0] == "openEwProjectID" for c in app.calls),
      f"D: nothing should have been opened, calls={app.calls}")
ok("D ok: an ambiguous name is refused without opening anything", mark)

mark = len(failures)
# --- E: a project held by another user is refused -------------------------
app = world()
r = pj.open_project(app, CLIENT, name="Held Elsewhere")
check(not r["ok"] and "another user" in r["error"],
      f"E: a project open elsewhere must be refused, got {r}")
check(not any(c[0] == "openEwProjectID" for c in app.calls),
      f"E: it must not be opened anyway, calls={app.calls}")
ok("E ok: a project held by another user is refused", mark)

mark = len(failures)
# --- F: close with no argument closes the current project -----------------
app = world(current_index=1)
r = pj.close_project(app, CLIENT)
check(r["ok"] and r["closed"]["id"] == 9,
      f"F: the open project should be the one closed, got {r}")
check(r["current_project_id"] is None,
      f"F: nothing should be current afterwards, got {r}")
ok("F ok: close with no argument closes the open project", mark)

mark = len(failures)
# --- G: closing with nothing open is a successful no-op -------------------
app = world()
r = pj.close_project(app, CLIENT)
check(r["ok"] and r["closed"] is None,
      f"G: closing nothing should succeed quietly, got {r}")
check(not app.calls, f"G: it should call nothing, calls={app.calls}")
ok("G ok: closing with nothing open is a quiet success", mark)

mark = len(failures)
# --- H: a duplicate name is refused ---------------------------------------
app = world()
before = len(app.mgr.projects)
r = pj.create_project(app, CLIENT, name="Main Control Panel - 2026")
check(not r["ok"] and "already exists" in r["error"],
      f"H: a duplicate name must be refused, got {r}")
check(len(app.mgr.projects) == before,
      "H: nothing should have been created")
ok("H ok: a duplicate project name is refused", mark)

mark = len(failures)
# --- I: an uninstalled template is refused --------------------------------
import tempfile

tmp = tempfile.mkdtemp()
for t in ("ANSI", "IEC", "JIS"):
    Path(tmp, f"{t}{pj.TEMPLATE_SUFFIX}").write_text("")
app = world(template_folder=tmp)
r = pj.list_project_templates(app, CLIENT)
check(r["templates"] == ["ANSI", "IEC", "JIS"],
      f"I: the template names come off the folder, got {r}")
r = pj.create_project(app, CLIENT, name="New", template="DIN")
check(not r["ok"] and "unknown template" in r["error"],
      f"I: an uninstalled template must be refused, got {r}")
ok("I ok: templates are listed, and an unknown one is refused", mark)

mark = len(failures)
# --- J: the fields are written with the new project OPEN ------------------
# update() returns EW_PROJECT_NOTOPENED on a closed project and throws the
# edits away, so the description and customer have to be applied in a second
# pass between an open and a close. Without that, create_project reported
# success and the new project came back carrying the template's description.
app = world(current_index=1, template_folder=tmp)
r = pj.create_project(app, CLIENT, name="MR3ELE.D002 Test",
                      template="IEC", description="Bench rig",
                      customer="Maritime Robotics")
order = [c[0] for c in app.calls]
check(r["ok"], f"J: creation should succeed, got {r}")
check(r["project"]["description"] == "Bench rig",
      f"J: the description must survive the template, got {r['project']}")
check(r["project"]["customer"] == "Maritime Robotics",
      f"J: the customer must be committed, got {r['project']}")
check(order.index("openEwProjectID") < order.index("update")
      < order.index("closeEwProjectID"),
      f"J: open, then write, then close, order={order}")
check(order.index("insertFromTemplate") < order.index("openEwProjectID"),
      f"J: the project must exist before it is opened, order={order}")
ok("J ok: the fields are committed against the open project", mark)

mark = len(failures)
# --- J2: creating from inside another project puts it back ----------------
# Opening the new project displaces whatever was current. Leaving the session
# pointed at a blank new project after a create would be a nasty surprise.
check(app.current is not None and app.current._id == 9,
      f"J2: the previously open project should be current again, "
      f"got {None if app.current is None else app.current._id}")
check(r["current_project_id"] == 9,
      f"J2: the result should say so too, got {r['current_project_id']}")
ok("J2 ok: the project that was open is restored afterwards", mark)

mark = len(failures)
# --- J3: open_after leaves the new project open ---------------------------
app = world(current_index=1, template_folder=tmp)
r = pj.create_project(app, CLIENT, name="Left Open", template="ANSI",
                      description="stays", open_after=True)
check(r["ok"] and app.current is not None
      and app.current._name == "Left Open",
      f"J3: open_after should leave the new project current, got {r}")
check("closeEwProjectID" not in [c[0] for c in app.calls],
      f"J3: it must not be closed again, calls={[c[0] for c in app.calls]}")
ok("J3 ok: open_after leaves the new project open", mark)

mark = len(failures)
# --- K: a mismatched confirm_name deletes nothing -------------------------
app = world()
r = pj.delete_project(app, CLIENT, project_id=9, confirm_name="wrong")
check(not r["ok"] and "does not match" in r["error"],
      f"K: a mismatched name must refuse, got {r}")
check(not any(c[0] == "remove" for c in app.calls),
      f"K: nothing may be removed, calls={app.calls}")
ok("K ok: a mismatched confirmation deletes nothing", mark)

mark = len(failures)
# --- L: deleting the open project closes it first -------------------------
# remove() on a project the application still holds open is how a database
# gets a half-deleted row, so the close has to come first.
app = world(current_index=1)
r = pj.delete_project(app, CLIENT, project_id=9,
                      confirm_name="MR3ELE.D001.65021 USV")
order = [c[0] for c in app.calls]
check(r["ok"] and r["confirmed_gone"], f"L: the delete should succeed, {r}")
check("closeEwProjectID" in order
      and order.index("closeEwProjectID") < order.index("remove"),
      f"L: it must be closed before removal, order={order}")
ok("L ok: the open project is closed before it is removed", mark)

mark = len(failures)
# --- M: only the given fields are written, and update runs once -----------
app = world(current_index=1)
r = pj.project_properties(app, CLIENT, customer="Maritime Robotics",
                          contract_number="65021")
setters = [c[0] for c in app.calls]
check(r["ok"], f"M: the write should succeed, got {r}")
check(sorted(r["changed"]) == ["contract_number", "customer"],
      f"M: exactly the two given fields should change, got {r['changed']}")
check("setDrawingOfficeName" not in setters,
      f"M: an untouched field must not be written, calls={setters}")
check(setters.count("update") == 1,
      f"M: update belongs at the end, once, calls={setters}")
ok("M ok: only the given fields are written, committed once", mark)

mark = len(failures)
# --- N: a read-only call writes nothing and reports the addresses ---------
app = world(current_index=1)
r = pj.project_properties(app, CLIENT)
check(r["changed"] == [] and not app.calls,
      f"N: reading must not write, changed={r['changed']} calls={app.calls}")
check(len(r["project"]["customer_address"]) == 3
      and len(r["project"]["drawing_office_address"]) == 3,
      f"N: both address blocks should be read back, got {r['project']}")
ok("N ok: reading the properties writes nothing", mark)

mark = len(failures)
# --- O: writing to a project that is not open is refused ------------------
# Not a nicety: SOLIDWORKS accepts every setter, fails the update with
# EW_PROJECT_NOTOPENED, and the caller is told nothing. Better to refuse.
app = world(current_index=1)
r = pj.project_properties(app, CLIENT, project_id=4, customer="Someone")
check(not r["ok"] and "not the open project" in r["error"],
      f"O: writing to a closed project must be refused, got {r}")
check(not app.calls, f"O: nothing may be written, calls={app.calls}")
r = pj.project_properties(app, CLIENT, project_id=4)
check(r["project"]["id"] == 4 and r["changed"] == [],
      f"O: reading a closed project is still fine, got {r}")
ok("O ok: a write to a closed project is refused, a read is not", mark)

print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("all project-lifecycle checks passed")
