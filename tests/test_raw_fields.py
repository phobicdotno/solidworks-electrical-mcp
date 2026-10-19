"""Raw database fields: probe, guarded writes, moving pages and folders.

The typed setters refuse to move an inserted page to another book, folder
or position (EW_BAD_INPUTS); getProperty/setProperty with the database
column name do it. That is a write past every guard SOLIDWORKS has, so the
guards live here and are checked against fakes:

  A. the probe keeps only the names an object answers to, including ones
     no template lists (fil_bun_id is built from loc_bun_id's suffix)
  B. set_fields refuses an unknown name and writes nothing
  C. set_fields dry run writes nothing; a real run writes, updates and
     reads back
  D. move_folio into a folder of another book is refused
  E. move_folio to another book takes the page out of its old folder
  F. move_folder moves sub-folders and their pages too
  G. add_book places the new book after the existing ones

Run directly:
    .venv/Scripts/python.exe tests/test_raw_fields.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from solidworks_electrical_mcp import rawfields as rf  # noqa: E402

failures: list[str] = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print("FAIL:", msg)


class Obj:
    def __init__(self, fields, log):
        self.f, self.log = dict(fields), log

    def getProperty(self, n):
        return (self.f[n], 0) if n in self.f else (None, 2)

    def setProperty(self, n, v):
        self.log.append(("set", n, v))
        if n not in self.f:
            return 2
        self.f[n] = v
        return 0

    def update(self):
        self.log.append(("update",))
        return 0

    def getID(self):
        return (self.f.get("bun_id"), 0)

    def getTag(self):
        return (self.f.get("bun_tag"), 0)


class Mgr:
    def __init__(self, objs, key):
        self.objs, self.key = objs, key

    def __getattr__(self, name):
        if name.startswith("find"):
            return lambda i: (next((o for o in self.objs
                                    if o.f[self.key] == i), None), 0)
        if name.endswith("Array"):
            return lambda: (self.objs, 0)
        raise AttributeError(name)


class Proj:
    def __init__(self, files, folders, books, log):
        self.m = {"getEwProjectFileManager": Mgr(files, "fil_id"),
                  "getEwProjectFolderManager": Mgr(folders, "fol_id"),
                  "getEwProjectBookManager": Mgr(books, "bun_id")}
        self.log, self.books = log, books

    def __getattr__(self, name):
        if name in self.m:
            return lambda: (self.m[name], 0)
        raise AttributeError(name)


class App:
    def __init__(self, proj):
        self.p = proj

    def getEwProjectCurrent(self):
        return (self.p, 0)


class Client:
    @staticmethod
    def Dispatch(x):
        return x


def world():
    log = []
    books = [Obj({"bun_id": 1, "bun_tag": "1", "bun_tagposition": 0}, log),
             Obj({"bun_id": 2, "bun_tag": "2", "bun_tagposition": 1}, log)]
    folders = [Obj({"fol_id": 10, "fol_bun_id": 1, "fol_fol_id": -1}, log),
               Obj({"fol_id": 11, "fol_bun_id": 1, "fol_fol_id": 10}, log),
               Obj({"fol_id": 20, "fol_bun_id": 2, "fol_fol_id": -1}, log)]
    files = [Obj({"fil_id": 100, "fil_bun_id": 1, "fil_fol_id": 10,
                  "fil_tagposition": 5}, log),
             Obj({"fil_id": 101, "fil_bun_id": 1, "fil_fol_id": 11,
                  "fil_tagposition": 6}, log),
             Obj({"fil_id": 102, "fil_bun_id": 1, "fil_fol_id": -1,
                  "fil_tagposition": 1}, log)]
    proj = Proj(files, folders, books, log)
    return App(proj), log, files, folders, books


C = Client()
COLS = {"fil": ["fil_id", "fil_tagposition"], "loc": ["loc_bun_id"],
        "fol": ["fol_id"]}

# A
cand = rf.candidates("fil", COLS)
check("fil_bun_id" in cand and "fil_tagposition" in cand,
      f"A: candidates must combine prefix and suffixes, got {cand}")
app, log, files, folders, books = world()
rf.template_columns = lambda dirs=None: COLS
r = rf.probe_fields(app, C, "file", 100)
check(set(r["fields"]) == {"fil_id", "fil_bun_id", "fil_fol_id",
                           "fil_tagposition"},
      f"A: probe keeps only answered names, got {r['fields']}")

# B
app, log, *_ = world()
r = rf.set_fields(app, C, "file", 100, {"fil_bogus": 1}, dry_run=False)
check(not r["ok"] and not [e for e in log if e[0] == "set"],
      f"B: unknown name must be refused before any write, got {r} {log}")

# C
app, log, files, *_ = world()
r = rf.set_fields(app, C, "file", 100, {"fil_tagposition": 9})
check(r["ok"] and r["dry_run"] and not log,
      f"C: dry run must not write, got {log}")
r = rf.set_fields(app, C, "file", 100, {"fil_tagposition": 9},
                  dry_run=False)
check(r["ok"] and files[0].f["fil_tagposition"] == 9
      and ("update",) in log, f"C: real run writes and updates, got {r}")

# D
app, log, *_ = world()
r = rf.move_folio(app, C, 100, book_id=1, folder_id=20, dry_run=False)
check(not r["ok"] and not [e for e in log if e[0] == "set"],
      f"D: a folder of another book must be refused, got {r}")

# E
app, log, files, *_ = world()
r = rf.move_folio(app, C, 100, book_id=2, dry_run=False)
check(r["ok"] and files[0].f["fil_bun_id"] == 2
      and files[0].f["fil_fol_id"] == -1,
      f"E: page leaves its old folder when changing book, got {files[0].f}")

# F
app, log, files, folders, _ = world()
r = rf.move_folder(app, C, 10, 2, dry_run=False)
check(r["ok"] and [f.f["fol_bun_id"] for f in folders] == [2, 2, 2]
      and [f.f["fil_bun_id"] for f in files] == [2, 2, 1],
      f"F: folder, sub-folder and their pages move, got "
      f"{[f.f for f in folders]} {[f.f for f in files]}")

# G
app, log, *_ = world()
r = rf.add_book(app, C, "2", "DUP")
check(not r["ok"], f"G: a taken tag must be refused, got {r}")
r = rf.add_book(app, C, "3", "NEW")
check(r["ok"] and r["position"] == 2,
      f"G: new book goes after the others, got {r}")

print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    sys.exit(1)
print("all raw field checks passed")
