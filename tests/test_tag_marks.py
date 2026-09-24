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

print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("all tag-mark checks passed")
