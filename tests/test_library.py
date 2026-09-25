"""Regression test: the environment-library tools, without SOLIDWORKS.

Two things here are worth a test rather than a live poke.

The caching, because it is not an optimisation: a full sweep of this
environment's 1721 symbols takes about 20 seconds, and it only exists because
the API's own symbol filter does not work (setManufacturer("Wago") then
getEwSymbolArray returns an empty array while a sweep finds four). A cache
that silently goes stale would be worse than the 20 seconds.

And the guards on the write side, because the library is shared: a part or
symbol removed here breaks every project that references it, and there is no
undo.

Cases:
  A. searching filters on substrings and honours the limit
  B. a second search reuses the index rather than sweeping again
  C. a changed count invalidates the cache on its own
  D. refresh rebuilds even when the count has not moved
  E. an unknown part_type or symbol_type is refused, with the valid names
  F. create refuses an existing part unless replace is asked for
  G. replace removes the old part before inserting the new one
  H. circuits and their terminals are written, and failures are collected
  I. a write invalidates the cached index
  J. delete refuses a mismatched confirmation
  K. import_symbol refuses a missing drawing file
  L. import_symbol refuses an existing name unless replace is asked for
  M. update writes only the fields it was given

Run directly:
    .venv/Scripts/python.exe tests/test_library.py
"""
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from solidworks_electrical_mcp import library as lib

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)
        print("FAIL:", msg)


def ok(msg: str, mark: int) -> None:
    if len(failures) == mark:
        print(msg)


# --------------------------------------------------------------------------
# Fakes. ``sweeps`` counts how often the array was asked for, which is the
# only way to tell a cache hit from a cache miss from the outside.


class FakeTerminal:
    def __init__(self, fail=False):
        self.text = self.mnemonic = None
        self.fail = fail

    def setText(self, v):
        self.text = v
        return 2 if self.fail else 0

    def setMnemonic(self, v):
        self.mnemonic = v
        return 0

    def getText(self):
        return (self.text, 0)

    def getMnemonic(self):
        return (self.mnemonic, 0)


class FakeCircuit:
    def __init__(self, code, fail_terminal=False):
        self.code = code
        self.terminals: list[FakeTerminal] = []
        self.fail_terminal = fail_terminal

    def addEwManufacturerPartTerminal(self):
        t = FakeTerminal(self.fail_terminal)
        self.terminals.append(t)
        return (t, 0)

    def getType(self):
        return (self.code, 0)

    def getEwManufacturerPartTerminalCount(self):
        return len(self.terminals)

    def getEwManufacturerPartTerminalAt(self, i):
        return (self.terminals[i], 0)


class FakePart:
    def __init__(self, manufacturer="", reference="", description="",
                 ptype=0, library_code="", log=None, fail_terminal=False):
        self.f = {"setManufacturer": manufacturer, "setReference": reference,
                  "setDescription": description, "setLibraryCode":
                  library_code}
        self.ptype = ptype
        self.circuits: list[FakeCircuit] = []
        self.calls = log if log is not None else []
        self.removed = False
        self.fail_terminal = fail_terminal

    def __getattr__(self, name):
        if name.startswith("get"):
            return lambda *a: (self.f.get("set" + name[3:], ""), 0)
        if name.startswith("set"):
            def _set(*a):
                self.calls.append((name, a))
                self.f[name] = a[-1]
                return 0
            return _set
        raise AttributeError(name)

    # Numeric and enum getters need a sane zero rather than "".
    def getWidth(self):
        return (float(self.f.get("setWidth") or 0.0), 0)

    def getHeight(self):
        return (float(self.f.get("setHeight") or 0.0), 0)

    def getDepth(self):
        return (float(self.f.get("setDepth") or 0.0), 0)

    def getWeight(self):
        return (float(self.f.get("setWeight") or 0.0), 0)

    def getExcludeFromBillOfMaterials(self):
        return (False, 0)

    def getEwManufacturerPartType(self):
        return (self.f.get("setEwManufacturerPartType", self.ptype), 0)

    def addEwManufacturerPartCircuit(self, code):
        c = FakeCircuit(code, self.fail_terminal)
        self.circuits.append(c)
        return (c, 0)

    def getEwManufacturerPartCircuitCount(self):
        return len(self.circuits)

    def getEwManufacturerPartCircuitAt(self, i):
        return (self.circuits[i], 0)

    def insert(self):
        self.calls.append(("insert", ()))
        return 0

    def update(self):
        self.calls.append(("update", ()))
        return 0

    def remove(self):
        self.calls.append(("remove", ()))
        self.removed = True
        return 0


class FakeSymbol:
    def __init__(self, name="", stype=105, manufacturer="", reference="",
                 library_code="", log=None):
        self.f = {"setName": name, "setManufacturer": manufacturer,
                  "setReference": reference, "setLibraryCode": library_code}
        self.stype = stype
        self.calls = log if log is not None else []
        self.removed = False
        self.dwg = None

    def __getattr__(self, name):
        if name.startswith("get"):
            return lambda *a: (self.f.get("set" + name[3:], ""), 0)
        if name.startswith("set"):
            def _set(*a):
                self.calls.append((name, a))
                self.f[name] = a[-1]
                return 0
            return _set
        raise AttributeError(name)

    def getID(self):
        return (id(self) % 10000, 0)

    def getEwSymbolType(self):
        return (self.f.get("setEwSymbolType", self.stype), 0)

    def getEwSymbolPointCount(self):
        return 0

    def getEwSymbolCircuitCount(self):
        return 0

    def insertFromDwg(self, path):
        self.calls.append(("insertFromDwg", (path,)))
        self.dwg = path
        return 0

    def update(self):
        self.calls.append(("update", ()))
        return 0

    def remove(self):
        self.calls.append(("remove", ()))
        self.removed = True
        return 0


class FakePartManager:
    def __init__(self, parts, log):
        self.parts, self.calls, self.sweeps = parts, log, 0
        self.count_override = None

    def getCount(self):
        if self.count_override is not None:
            return self.count_override
        return len([p for p in self.parts if not p.removed])

    def getEwManufacturerPartArray(self):
        self.sweeps += 1
        return ([p for p in self.parts if not p.removed], 0)

    def findByManufacturerAndReference(self, man, ref):
        for p in self.parts:
            if p.removed:
                continue
            if p.f["setManufacturer"] == man and p.f["setReference"] == ref:
                return (p, 0)
        return (None, 8)

    def newEwManufacturerPart(self):
        p = FakePart(log=self.calls)
        self.parts.append(p)
        return (p, 0)


class FakeSymbolManager:
    def __init__(self, symbols, log):
        self.symbols, self.calls, self.sweeps = symbols, log, 0

    def getCount(self):
        return len([s for s in self.symbols if not s.removed])

    def getEwSymbolArray(self):
        self.sweeps += 1
        return ([s for s in self.symbols if not s.removed], 0)

    def findEwSymbolXByName(self, name):
        for s in self.symbols:
            if not s.removed and s.f["setName"] == name:
                return (s, 0)
        return (None, 8)

    def newEwSymbol(self):
        s = FakeSymbol(log=self.calls)
        self.symbols.append(s)
        return (s, 0)


class FakeLibraryManager:
    def getEwLibraryArray(self):
        return ([], 0)


class FakeEnv:
    def __init__(self, pm, sm):
        self.pm, self.sm = pm, sm

    def getEwManufacturerPartManager(self):
        return (self.pm, 0)

    def getEwSymbolManager(self):
        return (self.sm, 0)

    def getEwLibraryManager(self):
        return (FakeLibraryManager(), 0)


class FakeApp:
    def __init__(self, parts, symbols, log=None):
        # One shared call log across the managers and every part and symbol,
        # so a test can assert on an order that spans them - the old part
        # being removed before the new one is inserted, for instance.
        self.calls: list = [] if log is None else log
        self.pm = FakePartManager(parts, self.calls)
        self.sm = FakeSymbolManager(symbols, self.calls)
        self.env = FakeEnv(self.pm, self.sm)

    def getEwEnvironment(self):
        return (self.env, 0)


class FakeClient:
    @staticmethod
    def Dispatch(x):
        return x


CLIENT = FakeClient()


def world():
    lib._INDEX.clear()          # the cache is module state; start clean
    log: list = []
    parts = [
        FakePart("Wago", "751-9402", "Compact Controller 100", 3,
                 "Maritime_Robotics", log),
        FakePart("Wago", "859-304", "Interposing relay", 0, "Wago", log),
        FakePart("Wago", "2002-1401", "Pass-through terminal", 0, "Wago",
                 log),
        FakePart("Phoenix", "3044092", "Terminal block", 0, "Phoenix", log),
    ]
    symbols = [
        FakeSymbol("751-9402", 105, "Wago", "751-9402", "Maritime_Robotics",
                   log),
        FakeSymbol("859-304", 105, "Wago", "859-304", "Wago", log),
        FakeSymbol("EW_RELAY_COIL", 20, "", "", "IEC", log),
        FakeSymbol("EW_PLC_BLACKBOX", 30, "", "", "IEC", log),
    ]
    return FakeApp(parts, symbols, log=log)


mark = len(failures)
# --- A: substring filters and the limit -----------------------------------
app = world()
r = lib.search_manufacturer_parts(app, CLIENT, manufacturer="wago")
check(r["matched"] == 3, f"A: three Wago parts expected, got {r['matched']}")
check(r["total_in_catalogue"] == 4,
      f"A: the whole catalogue should be reported, got {r}")
r = lib.search_manufacturer_parts(app, CLIENT, description_contains="relay")
check(r["matched"] == 1 and r["parts"][0]["reference"] == "859-304",
      f"A: the description filter should find the relay, got {r}")
r = lib.search_manufacturer_parts(app, CLIENT, manufacturer="wago", limit=2)
check(r["matched"] == 3 and r["returned"] == 2 and len(r["parts"]) == 2,
      f"A: the limit caps what is returned, not what matched, got {r}")
ok("A ok: substring filters work and the limit caps the rows", mark)

mark = len(failures)
# --- B: the second search reuses the index --------------------------------
app = world()
lib.search_manufacturer_parts(app, CLIENT)
lib.search_symbols(app, CLIENT)
before = (app.pm.sweeps, app.sm.sweeps)
lib.search_manufacturer_parts(app, CLIENT, manufacturer="wago")
lib.search_symbols(app, CLIENT, symbol_type="2d_footprint")
check((app.pm.sweeps, app.sm.sweeps) == before,
      f"B: a cached search must not sweep again, {before} -> "
      f"{(app.pm.sweeps, app.sm.sweeps)}")
ok("B ok: a repeat search reuses the index", mark)

mark = len(failures)
# --- C: a changed count invalidates the cache -----------------------------
# The count is the cheap proxy for "somebody edited the library in the GUI".
app = world()
lib.search_symbols(app, CLIENT)
app.sm.symbols.append(FakeSymbol("NEW_SYMBOL", 20, log=app.calls))
r = lib.search_symbols(app, CLIENT, name="NEW_SYMBOL")
check(r["matched"] == 1,
      f"C: a symbol added since the sweep should be found, got {r}")
check(app.sm.sweeps == 2, f"C: it should have re-swept, {app.sm.sweeps}")
ok("C ok: a changed count rebuilds the index", mark)

mark = len(failures)
# --- D: refresh rebuilds even when the count has not moved ----------------
# Editing a part in place leaves the count alone, so the count proxy cannot
# catch it and the caller needs an explicit way out.
app = world()
lib.search_manufacturer_parts(app, CLIENT)
app.pm.parts[1].f["setDescription"] = "Interposing relay, 24 VDC"
r = lib.search_manufacturer_parts(app, CLIENT,
                                  description_contains="24 VDC")
check(r["matched"] == 0, f"D: the stale index should not see the edit, {r}")
r = lib.search_manufacturer_parts(app, CLIENT,
                                  description_contains="24 VDC",
                                  refresh=True)
check(r["matched"] == 1, f"D: refresh should pick the edit up, got {r}")
ok("D ok: refresh rebuilds an index the count could not invalidate", mark)

mark = len(failures)
# --- E: an unknown type is refused with the valid names -------------------
app = world()
for fn, kw in ((lib.search_manufacturer_parts, {"part_type": "plc_module_x"}),
               (lib.search_symbols, {"symbol_type": "footprint"})):
    try:
        fn(app, CLIENT, **kw)
        check(False, f"E: {fn.__name__} should refuse {kw}")
    except ValueError as e:
        check("have" in str(e), f"E: the error should list the valid names: "
                                f"{e}")
ok("E ok: an unknown type is refused and the valid names are listed", mark)

mark = len(failures)
# --- F: an existing part is not silently overwritten ----------------------
app = world()
r = lib.create_manufacturer_part(app, CLIENT, manufacturer="Wago",
                                 reference="859-304", description="new")
check(not r["ok"] and "already exists" in r["error"],
      f"F: an existing part must not be overwritten by default, got {r}")
check(not any(c[0] == "remove" for c in app.calls),
      f"F: nothing may be removed, calls={app.calls}")
ok("F ok: an existing part is reported, not overwritten", mark)

mark = len(failures)
# --- G: replace removes the old one first ---------------------------------
app = world()
old = app.pm.parts[1]
r = lib.create_manufacturer_part(app, CLIENT, manufacturer="Wago",
                                 reference="859-304",
                                 description="Interposing relay 24 VDC",
                                 part_type="base", width_mm=6.0,
                                 height_mm=91.5, depth_mm=63.0,
                                 replace=True)
check(r["ok"], f"G: the replace should succeed, got {r}")
check(old.removed, "G: the old part should have been removed")
order = [c[0] for c in app.calls]
check(order.index("remove") < order.index("insert"),
      f"G: remove must come before insert, order={order}")
check(r["part"]["height_mm"] == 91.5,
      f"G: the new dimensions should be readable back, got {r['part']}")
ok("G ok: replace removes the old part before inserting the new one", mark)

mark = len(failures)
# --- H: circuits and terminals, and their failures ------------------------
app = world()
r = lib.create_manufacturer_part(
    app, CLIENT, manufacturer="Wago", reference="750-1405",
    description="16 DI module", part_type="plc_module",
    circuits=[{"code": "PID", "terminals": [{"text": f"X1:{n}",
                                             "mnemonic": f"DI#{n}"}]}
              for n in range(1, 5)])
check(r["ok"], f"H: the part should be created, got {r.get('error')}")
made = r["part"]["circuits"]
check(len(made) == 4, f"H: four circuits expected, got {len(made)}")
check(made[0]["code"] == "PID"
      and made[0]["terminals"][0]["text"] == "X1:1"
      and made[0]["terminals"][0]["mnemonic"] == "DI#1",
      f"H: the terminal text and mnemonic should be written, got {made[0]}")

# A terminal that refuses its text must be reported, not swallowed: a part
# whose terminals are unlabelled looks fine until somebody wires it.
app = world()
app.pm.newEwManufacturerPart = lambda: (FakePart(log=app.calls,
                                                 fail_terminal=True), 0)
r = lib.create_manufacturer_part(app, CLIENT, manufacturer="Wago",
                                 reference="750-1406",
                                 circuits=[{"code": "PID",
                                            "terminals": [{"text": "X1:1"}]}])
check(not r["ok"] and r["circuit_errors"],
      f"H: a failing terminal must fail the create, got {r}")
ok("H ok: circuits are written, and a failing terminal is reported", mark)

mark = len(failures)
# --- I: a write invalidates the index -------------------------------------
app = world()
lib.search_manufacturer_parts(app, CLIENT)
lib.create_manufacturer_part(app, CLIENT, manufacturer="Wago",
                             reference="750-1405", description="16 DI")
r = lib.search_manufacturer_parts(app, CLIENT, reference="750-1405")
check(r["matched"] == 1,
      f"I: a part this server just created must be searchable, got {r}")
ok("I ok: creating a part invalidates the cached index", mark)

mark = len(failures)
# --- J: a mismatched confirmation deletes nothing -------------------------
app = world()
r = lib.delete_manufacturer_part(app, CLIENT, manufacturer="Wago",
                                 reference="859-304",
                                 confirm_reference="859-305")
check(not r["ok"] and "does not match" in r["error"],
      f"J: a mismatched confirmation must refuse, got {r}")
check(not any(p.removed for p in app.pm.parts), "J: nothing may be removed")
r = lib.delete_manufacturer_part(app, CLIENT, manufacturer="Wago",
                                 reference="859-304",
                                 confirm_reference="859-304")
check(r["ok"] and r["confirmed_gone"], f"J: the real delete should work, {r}")

app = world()
r = lib.delete_symbol(app, CLIENT, name="859-304", confirm_name="859-30")
check(not r["ok"] and "does not match" in r["error"],
      f"J: the symbol delete needs the same guard, got {r}")
ok("J ok: a mismatched confirmation deletes nothing", mark)

mark = len(failures)
# --- K: a missing drawing file is refused before anything is created ------
app = world()
try:
    lib.import_symbol(app, CLIENT, name="ZZ", drawing_path=str(
        Path(tempfile.gettempdir(), "definitely-not-here.dxf")))
    check(False, "K: a missing drawing must raise")
except FileNotFoundError:
    pass
check(not any(s.f["setName"] == "ZZ" for s in app.sm.symbols),
      "K: no symbol may be left behind by the failed import")
ok("K ok: a missing drawing file is refused before anything is created",
   mark)

mark = len(failures)
# --- L: an existing symbol name is not silently overwritten ---------------
tmp = Path(tempfile.mkdtemp(), "fp.dxf")
tmp.write_text("0\nSECTION\n0\nENDSEC\n0\nEOF\n")
app = world()
r = lib.import_symbol(app, CLIENT, name="859-304", drawing_path=str(tmp))
check(not r["ok"] and "already exists" in r["error"],
      f"L: an existing symbol must not be overwritten by default, got {r}")
r = lib.import_symbol(app, CLIENT, name="859-304", drawing_path=str(tmp),
                      symbol_type="2d_footprint",
                      library_code="Maritime_Robotics",
                      description="house footprint at true dimensions",
                      replace=True)
check(r["ok"], f"L: replace should succeed, got {r}")
order = [c[0] for c in app.calls]
check(order.index("remove") < order.index("insertFromDwg"),
      f"L: the old symbol goes before the new drawing lands, order={order}")
check(order.index("setEwSymbolType") < order.index("insertFromDwg"),
      f"L: the type belongs on the symbol before the geometry, order={order}")
ok("L ok: an existing symbol is reported, and replace orders correctly",
   mark)

mark = len(failures)
# --- M: update writes only the fields it was given ------------------------
app = world()
r = lib.update_manufacturer_part(app, CLIENT, manufacturer="Wago",
                                 reference="859-304", width_mm=6.0,
                                 height_mm=91.5)
check(r["ok"] and sorted(r["changed"]) == ["height_mm", "width_mm"],
      f"M: exactly the two given fields should change, got {r}")
written = [c[0] for c in app.calls]
check("setDepth" not in written and "setSupplierName" not in written,
      f"M: an untouched field must not be written, calls={written}")
check(written.count("update") == 1,
      f"M: one commit at the end, calls={written}")
r = lib.update_manufacturer_part(app, CLIENT, manufacturer="Wago",
                                 reference="859-304")
check(r["ok"] and r["changed"] == [],
      f"M: a call with no fields should be a quiet no-op, got {r}")
ok("M ok: update writes only what it was given", mark)

print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("all library checks passed")
