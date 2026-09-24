"""Regression test: a bulk retag must never let two devices share a mark.

renumber_components is the riskiest write in this server: it retags a run of
real devices in one pass. Renaming a run one at a time is not safe as soon as
the old and new sets overlap - shifting K41..K48 down to K40..K47 collides on
the very first step, because K40 is still held by the device that has not
moved yet. The implementation parks the devices on temporary marks first and
then moves them into place.

Nothing verified that. The test tracks every mark every component holds at
every step, so a collision at ANY intermediate moment fails, not just a wrong
final answer.

Cases:
  A. a non-overlapping run retags directly, with no temporary marks
  B. an overlapping run (K41..K48 -> K40..K47) parks on temps first, and no
     two components ever hold the same mark at any point
  C. the reverse overlap (shifting UP, K40..K47 -> K41..K48) is also safe
  D. a target belonging to a device that is NOT part of the run is refused
  E. two renames aiming at the same target are refused
  F. a rename that would change the device class is refused
  G. namespaced marks survive: CC_K1 -> CC_K2 keeps root K and the CC_
     prefix on the printed mark
  H. every device ends on the mark it was supposed to reach

Run directly:
    .venv/Scripts/python.exe tests/test_renumber.py
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from solidworks_electrical_mcp import workflows as wf

failures: list[str] = []
history: list[tuple] = []          # (component id, mark) in write order


def check(cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)
        print("FAIL:", msg)


def ok(msg: str, mark: int) -> None:
    if len(failures) == mark:
        print(msg)


class FakeComponent:
    def __init__(self, cid, tag, root=None, number=None):
        self.cid, self.tag = cid, tag
        self.root = root if root is not None else wf._split_mark(tag)[0]
        self.number = number if number is not None else wf._split_mark(tag)[1]

    def getID(self):
        return self.cid

    def getTag(self):
        return self.tag

    def getTagRoot(self):
        return self.root

    def getTagNumber(self):
        return self.number

    def setTag(self, v):
        self.tag = v
        history.append((self.cid, v))
        return 0

    def setTagRoot(self, v):
        self.root = v
        return 0

    def setTagNumber(self, v):
        self.number = v
        return 0

    def update(self):
        return 0


class FakeMgr:
    def __init__(self, comps):
        self.comps = comps

    def getEwProjectComponentArray(self):
        return self.comps


class FakeProject:
    def __init__(self, comps):
        self.comps = comps

    def getEwProjectComponentManager(self):
        return FakeMgr(self.comps)


def run(comps, renames, **kw):
    history.clear()
    by_tag = {c.tag: c for c in comps}
    orig = {n: getattr(wf, n) for n in
            ("_project", "_each", "_find_one_component",
             "_text_references_multi", "_symbols_bound_to", "find_folio")}
    wf._project = lambda app: FakeProject(comps)
    wf._each = lambda client, arr: list(arr or ())
    wf._find_one_component = lambda app, client, tag: (
        by_tag[tag] if tag in by_tag
        else next(c for c in comps if c.tag == tag))
    wf._text_references_multi = lambda app, client, tags: []
    wf._symbols_bound_to = lambda app, client, cid: []
    wf.find_folio = lambda app, client, **k: None
    try:
        return wf.renumber_components(None, None, renames=renames,
                                      dry_run=False, refresh_folios=False,
                                      **kw)
    finally:
        for n, v in orig.items():
            setattr(wf, n, v)


def replay_collides(comps, start_marks):
    """Walk the write history and report any moment two devices agreed.

    This is the property that matters: not just the final state, but that no
    intermediate step ever put two components on one mark.
    """
    live = dict(start_marks)
    for cid, mark in history:
        live[cid] = mark
        held = [c for c, m in live.items() if m == mark]
        if len(held) > 1:
            return f"after setting {cid} to {mark!r}, components {held} agree"
    return None


mark = len(failures)
# --- A: no overlap, so no temporary marks needed --------------------------
comps = [FakeComponent(1, "K10"), FakeComponent(2, "K11")]
r = run(comps, [["K10", "K20"], ["K11", "K21"]])
check(r["ok"], f"A: a clean run should succeed, got {r.get('errors')}")
check(not r["used_temp_marks"],
      "A: a non-overlapping run needs no temporary marks")
check([c.tag for c in comps] == ["K20", "K21"],
      f"A: expected K20/K21, got {[c.tag for c in comps]}")
ok("A ok: direct retag, no temps", mark)

mark = len(failures)
# --- B: the real case - shifting a run DOWN onto its own marks ------------
# K41..K48 -> K40..K47. Step one alone would collide: K40 is still held.
comps = [FakeComponent(i, f"K{40 + i}") for i in range(1, 9)]
start = {c.cid: c.tag for c in comps}
r = run(comps, [[f"K{40 + i}", f"K{39 + i}"] for i in range(1, 9)])
check(r["ok"], f"B: the shift should succeed, got {r.get('errors')}")
check(r["used_temp_marks"],
      "B: an overlapping run must park on temporary marks")
clash = replay_collides(comps, start)
check(clash is None, f"B: two devices shared a mark mid-run - {clash}")
# The parking pass must actually have RUN, not merely been planned. A
# descending shift applied in ascending order is safe by luck, so the
# collision replay alone would not notice the temps being skipped.
parked = [m for _cid, m in history if m.startswith("K99")]
check(len(parked) == 8,
      f"B: all 8 devices must be parked on temporary marks, saw "
      f"{len(parked)}")
check(all(m.startswith("K99") for _cid, m in history[:8]),
      "B: every device must be parked before any takes its final mark")
check([c.tag for c in comps] == [f"K{39 + i}" for i in range(1, 9)],
      f"B: expected K40..K47, got {[c.tag for c in comps]}")
ok(f"B ok: K41..K48 -> K40..K47 via temps, {len(history)} writes, "
   f"no collision", mark)

mark = len(failures)
# --- C: shifting UP overlaps the other way --------------------------------
comps = [FakeComponent(i, f"K{39 + i}") for i in range(1, 9)]
start = {c.cid: c.tag for c in comps}
r = run(comps, [[f"K{39 + i}", f"K{40 + i}"] for i in range(1, 9)])
check(r["ok"], f"C: the upward shift should succeed, got {r.get('errors')}")
clash = replay_collides(comps, start)
check(clash is None, f"C: two devices shared a mark mid-run - {clash}")
check([c.tag for c in comps] == [f"K{40 + i}" for i in range(1, 9)],
      f"C: expected K41..K48, got {[c.tag for c in comps]}")
ok("C ok: upward shift also collision-free", mark)

mark = len(failures)
# --- D: a target held by a device outside the run -------------------------
comps = [FakeComponent(1, "K10"), FakeComponent(2, "K99")]
try:
    run(comps, [["K10", "K99"]])
    check(False, "D: renaming onto an uninvolved device must be refused")
except ValueError as e:
    check("already belong" in str(e), f"D: unexpected message: {e}")
    check(comps[0].tag == "K10" and comps[1].tag == "K99",
          "D: nothing may be written when the run is refused")
ok("D ok: a target held by an uninvolved device is refused", mark)

mark = len(failures)
# --- E: two renames aiming at one mark ------------------------------------
comps = [FakeComponent(1, "K10"), FakeComponent(2, "K11")]
try:
    run(comps, [["K10", "K20"], ["K11", "K20"]])
    check(False, "E: two renames onto one target must be refused")
except ValueError as e:
    check("more than once" in str(e), f"E: unexpected message: {e}")
ok("E ok: duplicate targets refused", mark)

mark = len(failures)
# --- F: the device class may not change -----------------------------------
comps = [FakeComponent(1, "K10")]
try:
    run(comps, [["K10", "Q10"]])
    check(False, "F: changing a relay to root Q must be refused")
except ValueError as e:
    check("tag root must stay" in str(e), f"F: unexpected message: {e}")
    check(comps[0].tag == "K10", "F: nothing may be written when refused")
ok("F ok: a relay cannot be retagged out of root K", mark)

mark = len(failures)
# --- G: namespaced marks -------------------------------------------------
# CC_K1 is a K relay; the run must keep the printed prefix and the root.
comps = [FakeComponent(1, "CC_K1"), FakeComponent(2, "CC_K2")]
start = {c.cid: c.tag for c in comps}
r = run(comps, [["CC_K1", "CC_K2"], ["CC_K2", "CC_K3"]])
check(r["ok"], f"G: a namespaced run should succeed, got {r.get('errors')}")
clash = replay_collides(comps, start)
check(clash is None, f"G: namespaced run collided - {clash}")
check([c.tag for c in comps] == ["CC_K2", "CC_K3"],
      f"G: expected CC_K2/CC_K3, got {[c.tag for c in comps]}")
check([c.root for c in comps] == ["K", "K"],
      f"G: both must stay rooted K, got {[c.root for c in comps]}")
ok("G ok: CC_K1 -> CC_K2 keeps the prefix and the K root", mark)

mark = len(failures)
# --- H: the reported result matches what the components actually hold -----
comps = [FakeComponent(i, f"K{40 + i}") for i in range(1, 5)]
r = run(comps, [[f"K{40 + i}", f"K{39 + i}"] for i in range(1, 5)])
check(not r["mismatched"],
      f"H: nothing should be left on the wrong mark, got {r['mismatched']}")
check(all(x["now"] == x["new"] for x in r["results"]),
      f"H: results must read back the new marks, got {r['results']}")
ok("H ok: every device ends where it was sent", mark)

print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("all renumber checks passed")
