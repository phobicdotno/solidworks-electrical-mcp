"""Unit test: renumber resolves all old marks in ONE pass over the components.

Each per-rename ``_find_one_component`` read every component again; 41 marks on a
1636-component project took minutes before any work started. The batch lookup
must match exactly like the single one (tag, bare tag, tag path, path suffix)
and raise the same missing/ambiguous errors.

Run directly:
    .venv/Scripts/python.exe tests/test_find_components_one_pass.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from solidworks_electrical_mcp import workflows  # noqa: E402

PASSES = [0]


class C:
    def __init__(self, tag, path):
        self.tag, self.path = tag, path

    def getTag(self):
        return self.tag

    def getTagPath(self):
        return self.path


class Mgr:
    def getEwProjectComponentArray(self):
        PASSES[0] += 1
        return [C("X1", "=F1+L1-X1"), C("X2", "=F1+L1-X2"),
                C("X320", "=F1+L1+L4-X320"), C("X320", "=F1+L1+L1-X320")]


class Proj:
    def getEwProjectComponentManager(self):
        return Mgr()


def main() -> int:
    workflows._project = lambda app: Proj()
    workflows._each = lambda client, arr: list(arr)
    got = workflows._find_components_by_tags(
        None, None, ["X1", "-X2", "=F1+L1+L4-X320"])
    ok = (PASSES[0] == 1 and got["X1"].path == "=F1+L1-X1"
          and got["-X2"].tag == "X2"
          and got["=F1+L1+L4-X320"].path == "=F1+L1+L4-X320")
    for bad, msg in (("X320", "ambiguous"), ("X9", "no component")):
        try:
            workflows._find_components_by_tags(None, None, ["X1", bad])
            ok = False
        except LookupError as exc:
            ok = ok and msg in str(exc)
    print("PASS" if ok else f"FAIL passes={PASSES[0]} {got}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
