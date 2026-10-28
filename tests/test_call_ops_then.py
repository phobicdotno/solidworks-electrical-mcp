"""Unit test: a call_ops op with "then" runs further ops on the object it returned.

A child object reached through a fresh navigation is a new wrapper, so an edit made
on it is lost: cable cores set via
``findByManufacturerAndReference(...).getEwCableCoreAt(i)`` read back empty. "then"
keeps the owner retained and runs the child's setters on the object the member
returned, so the owner's ``update()`` can follow in the same sequence.

No COM needed: the evaluator is driven with plain Python stand-ins, including an
``(object, errorCode)`` tuple return as the COM layer produces.

Run directly:
    .venv/Scripts/python.exe tests/test_call_ops_then.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from solidworks_electrical_mcp.com import ElectricalApp  # noqa: E402


class Core:
    def __init__(self):
        self.colour = ""

    def setColorCode(self, c):
        self.colour = c
        return 0

    def getColorCode(self):
        return (self.colour, 0)


class Reference:
    def __init__(self):
        self.cores = [Core(), Core()]
        self.updated = False

    def getEwCableCoreAt(self, i):
        return (self.cores[i], 0)

    def update(self):
        self.updated = True
        return 0


def main() -> int:
    app = ElectricalApp.__new__(ElectricalApp)
    ref = Reference()
    res = app._run_ops(ref, [
        {"member": "getEwCableCoreAt", "args": [0],
         "then": [{"member": "setColorCode", "args": ["WH"]}]},
        {"member": "getEwCableCoreAt", "args": [1],
         "then": [{"member": "setColorCode", "args": ["BN"]},
                  {"member": "getColorCode", "args": []}]},
        {"member": "update", "args": []},
        {"member": "noSuchMember", "args": []},
    ])
    ok = (ref.cores[0].colour == "WH" and ref.cores[1].colour == "BN"
          and ref.updated
          and res[1]["then"][1] == ["BN", 0]
          and "error" in res[3])
    print("PASS" if ok else f"FAIL {res}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
