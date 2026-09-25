"""Regression test: snapshots, PLC I/O, functions and cable writes.

Snapshots are the reason this file exists. ``restore_snapshot`` throws away
everything done since a restore point, and a project-wide numbering pass is
the kind of thing people reach for it after, which means it will be called
in a hurry with an id read off a listing. The guard that stops a stale id
rolling a project back by weeks cannot be checked against a real project
without rolling one back, so it is checked here.

Cases:
  A. snapshots come back newest first
  B. creating one closes the project, names it, takes it, and reopens
  C. restore refuses a confirmation that does not match the snapshot
  D. restore goes through when it does match
  E. delete carries the same guard
  F. a missing snapshot id reports rather than raising
  G. I/O rows carry the channel address, and the filters work
  H. update_io writes only what it was given, and never the address
  I. add_function refuses a tag that already exists
  J. delete_function needs the tag back
  K. update_cable coerces each field to the type its setter wants
  L. delete_cable needs the tag back, and confirms the cable is gone
  M. a failure inside the closed block still leaves the project open
  N. the snapshot manager is taken while the project is still open
  O. delete_snapshot retries with the project closed when told to, and does
     not pay for a close when it is not

Run directly:
    .venv/Scripts/python.exe tests/test_project_data.py
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from solidworks_electrical_mcp import projectdata as pd

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)
        print("FAIL:", msg)


def ok(msg: str, mark: int) -> None:
    if len(failures) == mark:
        print(msg)


# --------------------------------------------------------------------------
# Fakes


class Base:
    """Getter/setter plumbing shared by the fake project objects."""

    def __init__(self, log):
        self.f: dict = {}
        self.calls = log
        self.removed = False

    def __getattr__(self, name):
        if name.startswith("get") or name.startswith("is"):
            key = "set" + (name[3:] if name.startswith("get") else name[2:])
            return lambda *a: (self.f.get(key, ""), 0)
        if name.startswith("set"):
            def _set(*a):
                self.calls.append((name, a))
                self.f[name] = a[-1]
                return 0
            return _set
        raise AttributeError(name)

    def getID(self):
        return self.f.get("id")

    def update(self):
        self.calls.append(("update", ()))
        return 0

    def remove(self):
        self.calls.append(("remove", ()))
        self.removed = True
        return 0


class FakeSnapshot(Base):
    def __init__(self, log, sid, name, created, app=None):
        super().__init__(log)
        self.f.update({"id": sid, "setName": name,
                       "setCreationDate": created})
        self.app, self.taken = app, False

    def getCreationDate(self):
        return (self.f["setCreationDate"], 0)

    def getEventType(self):
        return (0, 0)

    def getFileSize(self):
        return (1024, 0)

    def create(self):
        # The rule a live run exposed: create() returns EW_PROJECT_OPENED
        # (45) and takes no snapshot while the project is open. Modelled
        # here, because a create that silently does nothing is exactly what
        # shipped before.
        self.calls.append(("create", ()))
        if self.app is not None and self.app.current_id is not None:
            return 45
        self.taken = True
        return 0

    def restore(self):
        self.calls.append(("restore", ()))
        if self.app is not None and self.app.current_id is not None:
            return 45
        return 0


class FakeIO(Base):
    def __init__(self, log, iid, mnemonic, address, description):
        super().__init__(log)
        self.f.update({"id": iid, "setMnemonic": mnemonic,
                       "address": address, "setDescription": description})

    def getChannelAddress(self):
        return (self.f["address"], 0)

    def getDescription(self, lang):
        return (self.f["setDescription"], 0)

    def setDescription(self, lang, v):
        self.calls.append(("setDescription", (lang, v)))
        self.f["setDescription"] = v
        return 0

    def getFunctionID(self):
        return (self.f.get("setFunctionID", 0), 0)

    def getProjectComponentCircuitID(self):
        return (77, 0)


class FakeFunction(Base):
    def __init__(self, log, fid, tag, description=""):
        super().__init__(log)
        self.f.update({"id": fid, "setTag": tag, "setTagRoot": tag,
                       "setDescription": description})

    def getTagNumber(self):
        return (self.f.get("setTagNumber", 0), 0)

    def getDescription(self, lang):
        return (self.f["setDescription"], 0)

    def setDescription(self, lang, v):
        self.calls.append(("setDescription", (lang, v)))
        self.f["setDescription"] = v
        return 0

    def insert(self):
        self.calls.append(("insert", ()))
        return 0


class FakeCable(Base):
    def __init__(self, log, cid, tag):
        super().__init__(log)
        self.f.update({"id": cid, "setTag": tag})

    def getTagNumber(self):
        return (self.f.get("setTagNumber", 0), 0)

    def getCoreCount(self):
        return (4, 0)

    def getLength(self):
        return (self.f.get("setLength", 0.0), 0)

    def getDiameter(self):
        return (self.f.get("setDiameter", 0.0), 0)

    def getIsFixedLength(self):
        return (bool(self.f.get("setIsFixedLength", False)), 0)

    def getUpStreamLocationID(self):
        return (self.f.get("setUpStreamLocationID", 0), 0)

    def getDownStreamLocationID(self):
        return (self.f.get("setDownStreamLocationID", 0), 0)

    def getFunctionID(self):
        return (self.f.get("setFunctionID", 0), 0)

    def getDescription(self, lang):
        return (self.f.get("setDescription", ""), 0)

    def setDescription(self, lang, v):
        self.calls.append(("setDescription", (lang, v)))
        self.f["setDescription"] = v
        return 0


class SnapshotManager:
    def __init__(self, log, snaps, app=None):
        self.log, self.snaps, self.app = log, snaps, app

    def getCount(self):
        return (len([s for s in self.snaps if not s.removed]), 0)

    def at(self, i):
        return ([s for s in self.snaps if not s.removed][i], 0)

    def findProjectSnapshotByID(self, sid):
        for s in self.snaps:
            if s.getID() == sid and not s.removed:
                return (s, 0)
        return (None, 8)

    def newEwProjectSnapshot(self):
        s = FakeSnapshot(self.log, 99, "", "2026-09-25T00:00:00+00:00",
                         app=self.app)
        self.snaps.append(s)
        return (s, 0)


class ArrayManager:
    """A manager over one list, with the find/new members its kind uses."""

    def __init__(self, log, items, factory=None):
        self.log, self.items, self.factory = log, items, factory

    def _live(self):
        return [x for x in self.items if not x.removed]

    def _find(self, oid):
        for x in self.items:
            if x.getID() == oid and not x.removed:
                return (x, 0)
        return (None, 8)

    def getEwProjectInputOutputArray(self):
        return (self._live(), 0)

    findEwProjectInputOutputByID = _find

    def getEwProjectFunctionArray(self):
        return (self._live(), 0)

    findEwProjectFunctionByID = _find
    findEwProjectCableByID = _find

    def newEwProjectFunction(self):
        f = self.factory()
        self.items.append(f)
        return (f, 0)


class FakeProject:
    def __init__(self, log, snaps, ios, funcs, cables):
        self.log = log
        self.snaps = SnapshotManager(log, snaps)
        self.app = None
        self.ios = ArrayManager(log, ios)
        self.funcs = ArrayManager(log, funcs,
                                  lambda: FakeFunction(log, 50, ""))
        self.cables = ArrayManager(log, cables)

    def getID(self):
        return 9

    def getEwProjectSnapshotManager(self):
        # The other half of the rule the live run found: this returns NULL
        # while the project is closed, so the manager cannot be fetched
        # after the close. Together with create() refusing on an open
        # project, that leaves exactly one workable order.
        if self.app is not None and self.app.current_id is None:
            return (None, 0)
        return (self.snaps, 0)

    def getEwProjectInputOutputManager(self):
        return (self.ios, 0)

    def getEwProjectFunctionManager(self):
        return (self.funcs, 0)

    def getEwProjectCableManager(self):
        return (self.cables, 0)


class FakeApp:
    """The application, which knows whether the project is open.

    Closing one has to really close it here, or the rule the snapshot
    operations exist to work around cannot be tested: a fake that always
    says "open" would fail them, and one that always says "closed" would
    pass them whether or not they close anything.
    """

    def __init__(self, proj, project_id=9):
        self.proj, self.project_id = proj, project_id
        self.current_id = project_id

    def getEwProjectCurrent(self):
        return ((self.proj, 0) if self.current_id is not None
                else (None, 19))

    def closeEwProjectID(self, pid):
        self.proj.log.append(("closeEwProjectID", (pid,)))
        if pid == self.project_id:
            self.current_id = None
        return 0

    def openEwProjectID(self, pid):
        self.proj.log.append(("openEwProjectID", (pid,)))
        if pid == self.project_id:
            self.current_id = pid
        return 0


class FakeProject:
    def __init__(self, log, snaps, ios, funcs, cables):
        self.log = log
        self.snaps = SnapshotManager(log, snaps)
        self.app = None
        self.ios = ArrayManager(log, ios)
        self.funcs = ArrayManager(log, funcs,
                                  lambda: FakeFunction(log, 50, ""))
        self.cables = ArrayManager(log, cables)

    def getID(self):
        return 9

    def getEwProjectSnapshotManager(self):
        # The other half of the rule the live run found: this returns NULL
        # while the project is closed, so the manager cannot be fetched
        # after the close. Together with create() refusing on an open
        # project, that leaves exactly one workable order.
        if self.app is not None and self.app.current_id is None:
            return (None, 0)
        return (self.snaps, 0)

    def getEwProjectInputOutputManager(self):
        return (self.ios, 0)

    def getEwProjectFunctionManager(self):
        return (self.funcs, 0)

    def getEwProjectCableManager(self):
        return (self.cables, 0)


class FakeApp:
    """The application, which knows whether the project is open.

    Closing one has to really close it here, or the rule the snapshot
    operations exist to work around cannot be tested: a fake that always
    says "open" would fail them, and one that always says "closed" would
    pass them whether or not they close anything.
    """

    def __init__(self, proj, project_id=9):
        self.proj, self.project_id = proj, project_id
        self.current_id = project_id

    def getEwProjectCurrent(self):
        return ((self.proj, 0) if self.current_id is not None
                else (None, 19))

    def closeEwProjectID(self, pid):
        self.proj.log.append(("closeEwProjectID", (pid,)))
        if pid == self.project_id:
            self.current_id = None
        return 0

    def openEwProjectID(self, pid):
        self.proj.log.append(("openEwProjectID", (pid,)))
        if pid == self.project_id:
            self.current_id = pid
        return 0


class FakeEnv:
    """The environment's project manager, which still hands out a closed
    project - that is how the snapshot manager is reached once the project
    the caller started from has been closed."""

    def __init__(self, app):
        self.app = app

    def getEwProjectManager(self):
        return (self, 0)

    def findEwProjectByID(self, pid):
        if pid == self.app.project_id:
            return (self.app.proj, 0)
        return (None, 8)


class FakeClient:
    @staticmethod
    def Dispatch(x):
        return x


CLIENT = FakeClient()


def world():
    log: list = []
    snaps = [FakeSnapshot(log, 1, "before renumber",
                          "2026-09-20T08:00:00+00:00"),
             FakeSnapshot(log, 2, "after page 102",
                          "2026-09-24T18:00:00+00:00")]
    ios = [FakeIO(log, 10, "DI#1", "%IX0.0", "Panel button start"),
           FakeIO(log, 11, "DO#1", "%QX0.0", "Relay CC_K1 coil"),
           FakeIO(log, 12, "AI#1", "%IW2", "Fuel level")]
    funcs = [FakeFunction(log, 20, "PROP", "Propulsion"),
             FakeFunction(log, 21, "NAV", "Navigation")]
    cables = [FakeCable(log, 30, "W101"), FakeCable(log, 31, "W102")]
    proj = FakeProject(log, snaps, ios, funcs, cables)
    app = FakeApp(proj)
    proj.app = app
    proj.snaps.app = app
    for sn in snaps:
        sn.app = app
    return app, log


mark = len(failures)
# --- A: newest first ------------------------------------------------------
app, log = world()
r = pd.list_snapshots(app, CLIENT)
check([s["id"] for s in r["snapshots"]] == [2, 1],
      f"A: newest first, got {[s['id'] for s in r['snapshots']]}")
check(r["snapshots"][0]["name"] == "after page 102",
      f"A: the rows should carry the name, got {r['snapshots'][0]}")
ok("A ok: snapshots come back newest first", mark)

mark = len(failures)
# --- B: the project is closed for the snapshot, and reopened -------------
# create() returns EW_PROJECT_OPENED and takes nothing while the project is
# open. Getting this wrong is silent: the call reports a restore point that
# does not exist, which is the worst possible thing for a safety net.
app, log = world()
r = pd.create_snapshot(app, CLIENT, name="before the renumber",
                       description="wire labels about to change")
order = [c[0] for c in log]
check(r["ok"], f"B: creating a snapshot should succeed, got {r}")
check("closeEwProjectID" in order
      and order.index("closeEwProjectID") < order.index("create"),
      f"B: the project must be closed before create(), order={order}")
check(order.index("setName") < order.index("create")
      and order.index("setDescription") < order.index("create"),
      f"B: the name and note belong on it before create(), order={order}")
check(order[-1] == "openEwProjectID",
      f"B: and the project must be open again afterwards, order={order}")
check(app.current_id == 9,
      f"B: really open, not just called, got {app.current_id}")
check(r["snapshot"]["name"] == "before the renumber",
      f"B: the result should carry the name, got {r['snapshot']}")
taken = [sn for sn in app.proj.snaps.snaps if getattr(sn, "taken", False)]
check(len(taken) == 1,
      f"B: exactly one snapshot should actually have been taken, "
      f"got {len(taken)}")
ok("B ok: the project is closed for the snapshot, and reopened after", mark)

mark = len(failures)
# --- C: a mismatched confirmation restores nothing ------------------------
# The dangerous one. An id read off a stale listing plus a wrong name should
# stop here rather than discard weeks of work.
app, log = world()
r = pd.restore_snapshot(app, CLIENT, snapshot_id=1,
                        confirm_name="after page 102")
check(not r["ok"] and "does not match" in r["error"],
      f"C: a mismatched name must refuse, got {r}")
check(not any(c[0] == "restore" for c in log),
      f"C: nothing may be restored, log={log}")
ok("C ok: a mismatched confirmation restores nothing", mark)

mark = len(failures)
# --- D: the matching name goes through -----------------------------------
app, log = world()
r = pd.restore_snapshot(app, CLIENT, snapshot_id=1,
                        confirm_name="before renumber")
order = [c[0] for c in log]
check(r["ok"] and "restore" in order,
      f"D: the right name should restore, got {r}")
check(order.index("closeEwProjectID") < order.index("restore")
      < order.index("openEwProjectID"),
      f"D: close, restore, reopen, order={order}")
check(app.current_id == 9,
      f"D: the project must be open again, got {app.current_id}")
ok("D ok: the matching name restores, between a close and a reopen", mark)

mark = len(failures)
# --- E: delete carries the same guard ------------------------------------
app, log = world()
r = pd.delete_snapshot(app, CLIENT, snapshot_id=2, confirm_name="wrong")
check(not r["ok"] and "does not match" in r["error"],
      f"E: delete needs the name too, got {r}")
r = pd.delete_snapshot(app, CLIENT, snapshot_id=2,
                       confirm_name="after page 102")
check(r["ok"], f"E: the right name should delete, got {r}")
check(pd.list_snapshots(app, CLIENT)["count"] == 1,
      "E: and it should be gone from the listing")
ok("E ok: deleting a snapshot needs its name back", mark)

mark = len(failures)
# --- F: a missing id reports rather than raising -------------------------
app, log = world()
for fn in (pd.restore_snapshot, pd.delete_snapshot):
    r = fn(app, CLIENT, snapshot_id=999, confirm_name="x")
    check(not r["ok"] and "no snapshot" in r["error"],
          f"F: {fn.__name__} should report a missing id, got {r}")
ok("F ok: a missing snapshot id is reported, not raised", mark)

mark = len(failures)
# --- G: the I/O rows and their filters -----------------------------------
app, log = world()
r = pd.list_io(app, CLIENT)
check(r["count"] == 3, f"G: three channels, got {r['count']}")
check(r["io"][0]["channel_address"] == "%IX0.0",
      f"G: the channel address is the point of the row, got {r['io'][0]}")
check(r["io"][0]["component_circuit_id"] == 77,
      f"G: and the circuit it sits on, got {r['io'][0]}")
r = pd.list_io(app, CLIENT, mnemonic_contains="di#")
check(r["count"] == 1 and r["io"][0]["id"] == 10,
      f"G: the mnemonic filter should be case-insensitive, got {r}")
r = pd.list_io(app, CLIENT, description_contains="relay")
check(r["count"] == 1 and r["io"][0]["id"] == 11,
      f"G: the description filter should work too, got {r}")
ok("G ok: I/O rows carry the address, and the filters work", mark)

mark = len(failures)
# --- H: update_io writes only what it was given --------------------------
app, log = world()
r = pd.update_io(app, CLIENT, io_id=11, description="CC_K1 coil, 24 VDC")
written = [c[0] for c in log]
check(r["ok"] and r["changed"] == ["description"],
      f"H: only the description should change, got {r}")
check("setMnemonic" not in written,
      f"H: an untouched field must not be written, log={written}")
check(not any("Address" in c[0] for c in log),
      f"H: the channel address is computed and must never be set, "
      f"log={written}")
r = pd.update_io(app, CLIENT, io_id=11)
check(r["ok"] and r["changed"] == [],
      f"H: a call with no fields is a quiet no-op, got {r}")
r = pd.update_io(app, CLIENT, io_id=999)
check(not r["ok"] and "no I/O channel" in r["error"],
      f"H: a missing channel should report, got {r}")
ok("H ok: update_io writes only what it was given", mark)

mark = len(failures)
# --- I: a duplicate function tag is refused ------------------------------
app, log = world()
r = pd.add_function(app, CLIENT, tag="NAV", description="Navigation again")
check(not r["ok"] and "already exists" in r["error"],
      f"I: a duplicate tag must be refused, got {r}")
check(not any(c[0] == "insert" for c in log),
      f"I: nothing may be created, log={log}")
r = pd.add_function(app, CLIENT, tag="PWR", description="Power")
check(r["ok"] and r["function"]["tag"] == "PWR",
      f"I: a new tag should be created, got {r}")
check(pd.list_functions(app, CLIENT)["count"] == 3,
      "I: and show up in the listing")
ok("I ok: a duplicate function tag is refused", mark)

mark = len(failures)
# --- J: deleting a function needs the tag back ---------------------------
app, log = world()
r = pd.delete_function(app, CLIENT, function_id=20, confirm_tag="NAV")
check(not r["ok"] and "does not match" in r["error"],
      f"J: the tag of a DIFFERENT function must not confirm, got {r}")
check(not any(c[0] == "remove" for c in log), "J: nothing may be removed")
r = pd.delete_function(app, CLIENT, function_id=20, confirm_tag="PROP")
check(r["ok"], f"J: the right tag should delete, got {r}")
ok("J ok: deleting a function needs its own tag back", mark)

mark = len(failures)
# --- K: each cable field reaches its setter in the right type ------------
# setLength wants a double and setUpStreamLocationID an integer; handing a
# COM method the wrong VARTYPE is how a write silently does nothing.
app, log = world()
r = pd.update_cable(app, CLIENT, cable_id=30, length=12.5,
                    fixed_length=True, upstream_location_id=26712,
                    colour="BK", description="to the mast")
sent = dict((c[0], c[1]) for c in log)
check(r["ok"], f"K: the write should succeed, got {r}")
check(sent["setLength"] == (12.5,)
      and isinstance(sent["setLength"][0], float),
      f"K: the length must arrive as a float, got {sent.get('setLength')}")
check(sent["setUpStreamLocationID"] == (26712,)
      and isinstance(sent["setUpStreamLocationID"][0], int),
      f"K: the location id must arrive as an int, "
      f"got {sent.get('setUpStreamLocationID')}")
check(sent["setIsFixedLength"] == (True,),
      f"K: the flag must arrive as a bool, "
      f"got {sent.get('setIsFixedLength')}")
check(sent["setDescription"] == ("en", "to the mast"),
      f"K: the description takes a language, got {sent.get('setDescription')}")
check([c[0] for c in log].count("update") == 1,
      f"K: one commit at the end, log={[c[0] for c in log]}")
check(sorted(r["changed"]) == ["colour", "description", "fixed_length",
                               "length", "upstream_location_id"],
      f"K: exactly the five given fields, got {r['changed']}")
ok("K ok: every cable field reaches its setter in the right type", mark)

mark = len(failures)
# --- L: deleting a cable needs its tag, and is confirmed gone ------------
app, log = world()
r = pd.delete_cable(app, CLIENT, cable_id=30, confirm_tag="W102")
check(not r["ok"] and "does not match" in r["error"],
      f"L: another cable's tag must not confirm, got {r}")
r = pd.delete_cable(app, CLIENT, cable_id=30, confirm_tag="W101")
check(r["ok"] and r["confirmed_gone"],
      f"L: the right tag should delete and confirm, got {r}")
r = pd.delete_cable(app, CLIENT, cable_id=30, confirm_tag="W101")
check(not r["ok"] and "no cable" in r["error"],
      f"L: deleting it twice should report, not raise, got {r}")
ok("L ok: deleting a cable needs its tag, and is confirmed gone", mark)

mark = len(failures)
# --- M: a failure inside the closed block still reopens ------------------
# Every other tool acts on "the open project". A create that throws with the
# project closed does not just fail, it strands the session, which is why the
# reopen lives in __exit__ rather than after the call.
app, log = world()
snap = app.proj.snaps.snaps[0]


def boom():
    log.append(("create", ()))
    raise RuntimeError("the snapshot store went away mid-write")


app.proj.snaps.newEwProjectSnapshot = lambda: (snap, 0)
snap.create = boom
try:
    pd.create_snapshot(app, CLIENT, name="doomed")
    check(False, "M: the failure should propagate")
except RuntimeError as e:
    check("went away" in str(e), f"M: unexpected error {e}")
check(app.current_id == 9,
      f"M: the project must be open again even after a failure, "
      f"got {app.current_id}")
check([c[0] for c in log][-1] == "openEwProjectID",
      f"M: the reopen must be the last thing that happened, "
      f"got {[c[0] for c in log][-3:]}")
ok("M ok: a failure inside the closed block still reopens the project", mark)

mark = len(failures)
# --- N: the manager is taken before the close ----------------------------
# getEwProjectSnapshotManager returns NULL on a closed project, so fetching
# it after the close - the obvious reading of "do this with it closed" -
# fails outright. The order is: take the manager and build the snapshot
# while open, close, create, reopen.
app, log = world()
r = pd.create_snapshot(app, CLIENT, name="ordering")
order = [c[0] for c in log]
check(r["ok"], f"N: it should succeed, got {r}")
check(order.index("setName") < order.index("closeEwProjectID")
      < order.index("create") < order.index("openEwProjectID"),
      f"N: build while open, create while closed, order={order}")
ok("N ok: the manager is taken while the project is still open", mark)

mark = len(failures)
# --- O: the EW_PROJECT_OPENED retry in delete_snapshot -------------------
# Removing a restore point does not touch the project, so the normal path
# must not pay for a close and reopen of a project that takes minutes to
# open. But if this build does refuse, the retry has to work rather than
# hand back the refusal.
app, log = world()
r = pd.delete_snapshot(app, CLIENT, snapshot_id=1,
                       confirm_name="before renumber")
check(r["ok"], f"O: the plain delete should succeed, got {r}")
check("closeEwProjectID" not in [c[0] for c in log],
      f"O: and must not close the project to do it, "
      f"log={[c[0] for c in log]}")

app, log = world()
snap = app.proj.snaps.snaps[0]
attempts = {"n": 0}


def refuse_then_go():
    attempts["n"] += 1
    log.append(("remove", ()))
    if app.current_id is not None:
        return 45                      # EW_PROJECT_OPENED
    snap.removed = True
    return 0


snap.remove = refuse_then_go
r = pd.delete_snapshot(app, CLIENT, snapshot_id=1,
                       confirm_name="before renumber")
order = [c[0] for c in log]
check(r["ok"], f"O: the retry should get there, got {r}")
check(attempts["n"] == 2, f"O: it should try twice, not {attempts['n']}")
check(order.index("closeEwProjectID") < order.index("openEwProjectID"),
      f"O: with a close and a reopen around the second try, order={order}")
check(app.current_id == 9,
      f"O: and the project open again, got {app.current_id}")
ok("O ok: the delete retries closed only when SOLIDWORKS asks", mark)

print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("all project-data checks passed")
