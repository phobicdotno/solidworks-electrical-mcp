"""Unit test: renumber looks up the drawn symbols of all devices in ONE sweep.

Before, ``renumber_components`` called ``_symbols_bound_to`` once per device and
each call read every symbol of every folio: a 41-device run on a 118-folio project
passed the 30-minute client timeout. ``_symbols_bound_to_many`` sweeps once and
buckets by object id. This test counts the folio sweeps with stand-in objects.

Run directly:
    .venv/Scripts/python.exe tests/test_symbols_bound_one_sweep.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from solidworks_electrical_mcp import workflows  # noqa: E402


class Sym:
    def __init__(self, sid, oid):
        self.sid, self.oid = sid, oid

    def getObjectID(self):
        return self.oid

    def getID(self):
        return self.sid

    def getEwSymbolName(self):
        return "S"

    def getEwSymbolType(self):
        return 20


class Folio:
    def __init__(self, fid, tag):
        self.fid, self.tag = fid, tag

    def getID(self):
        return self.fid

    def getTag(self):
        return self.tag

    def isOpen(self):
        return False


SWEEPS = [0]


class FileMgr:
    def getEwProjectFileArray(self):
        SWEEPS[0] += 1
        return [Folio(1, "10"), Folio(2, "11")]


class SymMgr:
    def getProjectSymbolsFromFileID(self, fid):
        return {1: [Sym(11, 100), Sym(12, 200)], 2: [Sym(21, 100), Sym(22, 300)]}[fid]


class Proj:
    def getEwProjectFileManager(self):
        return FileMgr()

    def getEwProjectSymbolManager(self):
        return SymMgr()


def main() -> int:
    workflows._project = lambda app: Proj()
    workflows._each = lambda client, arr: list(arr)
    res = workflows._symbols_bound_to_many(None, None, [100, 300, 999])
    ok = (SWEEPS[0] == 1
          and [s["symbol_id"] for s in res[100]] == [11, 21]
          and [s["page"] for s in res[100]] == ["10", "11"]
          and [s["symbol_id"] for s in res[300]] == [22]
          and res[999] == [])
    one = workflows._symbols_bound_to(None, None, 100)
    ok = ok and [s["symbol_id"] for s in one] == [11, 21]
    print("PASS" if ok else f"FAIL sweeps={SWEEPS[0]} {res}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
