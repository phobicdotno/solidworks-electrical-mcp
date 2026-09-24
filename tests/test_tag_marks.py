"""Regression test: a device mark must file under the right tag root.

SOLIDWORKS keeps the device class in TagRoot with TagNumber beside it, and
drives its own automatic renumbering from those, NOT from the mark that is
printed on the drawing. setTag writes only the printed mark, so a component
can read "K38" on every sheet while being filed as J12 underneath - which is
exactly what happened to 38 components in the SeaLeopard project. _set_mark
keeps all three in step.

The second trap is the namespace prefix. Relays added alongside an existing K
series were marked CC_K1..CC_K105 to keep them out of its way. Reading the
letters before the first digit made the root "CC" and left the number unset
entirely, so all ten relays were filed under one rootless heading with
nothing for SOLIDWORKS to order them by, and the house rule that a relay is
always root K was silently broken.

Cases:
  A. plain marks split into root, number and suffix
  B. a bare number (a folder tag) has an empty root, not a digit root
  C. an all-letter prefix before an underscore is a namespace: CC_K1 is a K
     relay numbered 1, so the house "relays are root K" rule survives
  D. marks that are NOT namespaced keep their old reading, so existing
     project tags such as N1N15 and K1_2 do not move
  E. _set_mark writes tag, root AND number together
  F. _set_mark puts the printed mark back when setting the root rewrote it
  G. a mark with no number does not call setTagNumber with None
  H. a CC_ relay goes in end to end as a numbered K
  I. _tag_root agrees with _split_mark, so the clone and rename root guards
     do not refuse a namespaced device
  J. audit_tag_roots reports the CC_ components already in the project as
     drift, and fix=True files them under K without touching the marks

Run directly:
    .venv/Scripts/python.exe tests/test_tag_marks.py
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from solidworks_electrical_mcp import workflows as wf

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)
        print("FAIL:", msg)


def ok(msg: str, mark: int) -> None:
    if len(failures) == mark:
        print(msg)


def split_is(mark, expected):
    got = wf._split_mark(mark)
    check(got == expected, f"_split_mark({mark!r}) -> {got!r}, want {expected!r}")


class FakeComponent:
    """Mimics the real object, including setTagRoot rewriting the mark.

    SOLIDWORKS regenerates the displayed mark from root+number when either is
    set, so a component marked "K29B" comes back as "K29" unless the mark is
    written again afterwards. _set_mark has to notice and restore it.
    """

    def __init__(self, tag="", rewrites=True):
        self.tag, self.root, self.number = tag, "", None
        self.rewrites = rewrites
        self.calls = []

    def setTag(self, v):
        self.calls.append(("setTag", v))
        self.tag = v
        return 0

    def setTagRoot(self, v):
        self.calls.append(("setTagRoot", v))
        self.root = v
        if self.rewrites:
            self.tag = f"{self.root}{self.number if self.number else ''}"
        return 0

    def setTagNumber(self, v):
        self.calls.append(("setTagNumber", v))
        self.number = v
        if self.rewrites:
            self.tag = f"{self.root}{v}"
        return 0

    def update(self):
        self.calls.append(("update", None))
        return 0

    def getTag(self):
        return self.tag


mark = len(failures)
# --- A: the ordinary shapes ----------------------------------------------
split_is("K29B", ("K", 29, "B"))
split_is("K38", ("K", 38, ""))
split_is("K", ("K", None, ""))
split_is("-K30", ("K", 30, ""))        # a leading dash is display only
split_is("T58", ("T", 58, ""))
ok("A ok: plain marks split into root / number / suffix", mark)

mark = len(failures)
# --- B: a folder tag is a bare number ------------------------------------
split_is("7", ("", 7, ""))
ok("B ok: a bare number has an empty root, not a digit root", mark)

mark = len(failures)
# --- C: the namespace prefix ---------------------------------------------
# These were filed as ("CC", None, "_K1") - root CC, no number at all.
split_is("CC_K1", ("K", 1, ""))
split_is("CC_K105", ("K", 105, ""))
split_is("CC_A1", ("A", 1, ""))
split_is("CC_N1", ("N", 1, ""))
roots = {wf._split_mark(f"CC_K{n}")[0] for n in (1, 2, 3, 4, 100, 105)}
check(roots == {"K"},
      f"C: every CC_ relay must be root K, got {roots}")
nums = [wf._split_mark(f"CC_K{n}")[1] for n in (1, 2, 3, 4, 100, 105)]
check(all(isinstance(n, int) for n in nums),
      f"C: every CC_ relay must carry a number to renumber by, got {nums}")
ok("C ok: CC_K1 is a K relay numbered 1, so the root rule holds", mark)

mark = len(failures)
# --- D: everything else keeps its old reading -----------------------------
split_is("N1N15", ("N", 1, "N15"))     # a real project tag: must not move
split_is("K1_2", ("K", 1, "_2"))       # prefix has digits, so not a namespace
split_is("CC_1", ("CC", None, "_1"))   # tail is not a letter, so not a class
ok("D ok: non-namespaced marks unchanged, existing tags stay put", mark)

mark = len(failures)
# --- E: the mark, the root and the number all get written -----------------
c = FakeComponent(rewrites=False)
steps = wf._set_mark(c, "K38")
check(c.tag == "K38" and c.root == "K" and c.number == 38,
      f"E: expected K/38/K38, got root={c.root!r} num={c.number!r} "
      f"tag={c.tag!r}")
check(all(v in (0, None) for v in steps.values()),
      f"E: every step should report rc 0, got {steps}")
ok(f"E ok: tag={c.tag} root={c.root} number={c.number}", mark)

mark = len(failures)
# --- F: the printed mark survives a root that rewrites it -----------------
# "K29B" regenerates as "K29" when root and number are set; without the
# restore the B suffix is lost off every drawing.
c = FakeComponent(rewrites=True)
wf._set_mark(c, "K29B")
check(c.tag == "K29B",
      f"F: the printed mark must be restored, got {c.tag!r}")
check(c.root == "K" and c.number == 29,
      f"F: root/number should still be K/29, got {c.root!r}/{c.number!r}")
ok("F ok: K29B keeps its suffix while filing under K / 29", mark)

mark = len(failures)
# --- G: no number means no setTagNumber call ------------------------------
c = FakeComponent(rewrites=False)
wf._set_mark(c, "K")
check(not any(n == "setTagNumber" for n, _ in c.calls),
      f"G: setTagNumber must not be called with no number, got {c.calls}")
check(c.root == "K", f"G: the root should still be set, got {c.root!r}")
ok("G ok: a numberless mark sets the root only", mark)

# A CC_ relay must now go in as a numbered K, end to end.
mark = len(failures)
c = FakeComponent(rewrites=True)
wf._set_mark(c, "CC_K105")
check(c.root == "K" and c.number == 105,
      f"H: CC_K105 must file as K/105, got {c.root!r}/{c.number!r}")
check(c.tag == "CC_K105",
      f"H: the printed mark must stay CC_K105, got {c.tag!r}")
ok("H ok: CC_K105 prints as CC_K105 and files as K / 105", mark)

mark = len(failures)
# --- I: _tag_root must agree with _split_mark ----------------------------
# They used to disagree on a namespaced mark: "CC_K1" read as root "CC" here
# and root "K" there. Once audit_tag_roots had filed those components under
# their real root, the clone and rename guards compared the stored root "K"
# against _tag_root("CC_K2") = "CC" and refused every one of them with "tag
# root must stay K". The two have to be one definition.
for t in ("CC_K1", "CC_K105", "CC_A1", "K30", "K29B", "N1N15", "T58", "7", ""):
    check(wf._tag_root(t) == wf._split_mark(t)[0],
          f"I: _tag_root({t!r})={wf._tag_root(t)!r} disagrees with "
          f"_split_mark -> {wf._split_mark(t)[0]!r}")
check(wf._tag_root("CC_K105") == "K",
      f"I: a namespaced relay is rooted K, got {wf._tag_root('CC_K105')!r}")
# the rename guard compares these two, so they must match for a CC_ device
stored_root = wf._split_mark("CC_K1")[0]      # what the repair writes
new_root = wf._tag_root("CC_K2")              # what rename checks against
check(stored_root.casefold() == new_root.casefold(),
      f"I: renaming CC_K1 to CC_K2 must pass the root guard, "
      f"{stored_root!r} vs {new_root!r}")
ok("I ok: _tag_root and _split_mark agree, so CC_ renames pass the guard",
   mark)

mark = len(failures)
# --- J: audit_tag_roots finds and repairs the CC_ components --------------
# The ten relays are already in the project filed under root "CC" with no
# number, created before the namespace rule existed. The audit has to see
# that as drift and fix=True has to put it right, since that is the repair
# path for the live project.
class AuditComponent(FakeComponent):
    def __init__(self, tag, root, number):
        super().__init__(tag, rewrites=False)
        self.root, self.number = root, number
        self.cid = abs(hash(tag)) % 10000

    def getID(self):
        return self.cid

    def getTagPath(self):
        return f"=CC+L6-{self.tag}"

    def getTagRoot(self):
        return self.root

    def getTagNumber(self):
        return self.number


class AuditMgr:
    def __init__(self, comps):
        self.comps = comps

    def getEwProjectComponentArray(self):
        return self.comps


class AuditProject:
    def __init__(self, mgr):
        self.mgr = mgr

    def getEwProjectComponentManager(self):
        return self.mgr


# as created: mark CC_Kn, but filed as root "CC" with no number at all
broken = [AuditComponent(f"CC_K{n}", "CC", None) for n in (1, 2, 3, 100, 105)]
healthy = [AuditComponent("K40", "K", 40), AuditComponent("T58", "T", 58)]
orig_project = wf._project
orig_each = wf._each
wf._project = lambda app: AuditProject(AuditMgr(broken + healthy))
wf._each = lambda client, arr: list(arr or ())
try:
    r = wf.audit_tag_roots(None, None, tag_contains="CC_")
    check(r["count"] == 5,
          f"J: expected the 5 CC_ relays to show as drift, got {r['count']}")
    check(all(x["root_mismatch"] and x["number_mismatch"]
              for x in r["components"]),
          "J: both the root AND the missing number should be reported")
    check(not any(x["tag"].startswith("K4") for x in r["components"]),
          "J: a correctly filed component must not be reported as drift")

    r = wf.audit_tag_roots(None, None, tag_contains="CC_", fix=True)
    check(all(c.root == "K" for c in broken),
          f"J: fix should file them all under K, got "
          f"{[c.root for c in broken]}")
    check([c.number for c in broken] == [1, 2, 3, 100, 105],
          f"J: fix should set each number, got {[c.number for c in broken]}")
    check([c.tag for c in broken]
          == [f"CC_K{n}" for n in (1, 2, 3, 100, 105)],
          f"J: the printed marks must be untouched, got "
          f"{[c.tag for c in broken]}")
    ok("J ok: audit reports the 5 CC_ relays and fix files them as K 1..105",
       mark)
finally:
    wf._project = orig_project
    wf._each = orig_each

print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("all tag-mark checks passed")
