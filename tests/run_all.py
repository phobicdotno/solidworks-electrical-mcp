"""Run every test and report, so "did I break anything" is one command.

The tests here are standalone scripts rather than pytest cases, which means
plain `pytest` collects nothing and exits GREEN with "no tests ran" - a false
all-clear. This runs each one in its own process and fails loudly.

Tests that need SOLIDWORKS Electrical running with a project open are marked
LIVE below. They are skipped by default and reported as skipped, never as
passed; pass --live to include them.

Several of the others also skip at runtime when the application is not
attached, and they exit 0 when they do - so exit code alone reports green for
a test that checked nothing. Their SKIP marker is read out of the output and
reported as a skip. Six tests genuinely run with no application:
test_batch_symbol_ops, test_drawing_rules, test_page_ink, test_tag_marks,
test_stale_factory_retry and test_stdout_isolation.

    .venv/Scripts/python.exe tests/run_all.py
    .venv/Scripts/python.exe tests/run_all.py --live
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable

# These drive the real application; everything else fakes COM and runs anywhere.
LIVE = {"test_drawing_tools.py", "test_workflow_tools.py"}
TIMEOUT = 600


def main() -> int:
    want_live = "--live" in sys.argv
    scripts = sorted(p for p in HERE.glob("test_*.py"))
    if not scripts:
        print("no tests found next to", HERE)
        return 1

    passed, failed, skipped = [], [], []
    for s in scripts:
        if s.name in LIVE and not want_live:
            skipped.append(s.name)
            print(f"SKIP  {s.name}  (needs SOLIDWORKS; use --live)")
            continue
        print(f"----- {s.name}")
        try:
            r = subprocess.run([PY, str(s)], cwd=HERE.parent, timeout=TIMEOUT,
                               check=False,
                               capture_output=True, text=True)
        except subprocess.TimeoutExpired:
            failed.append((s.name, f"timed out after {TIMEOUT}s"))
            print(f"FAIL  {s.name}: timed out after {TIMEOUT}s")
            continue
        tail = (r.stdout or "").strip().splitlines()
        # Several tests exit 0 after skipping because the application is not
        # attached. Exit code alone therefore reports green for a test that
        # checked nothing, so read the SKIP marker out of the output.
        skip_line = next((ln for ln in tail
                          if ln.lstrip().upper().startswith("SKIP")), None)
        if r.returncode == 0 and skip_line:
            why = skip_line.strip()
            skipped.append(f"{s.name} ({why[:70]})")
            print(f"SKIP  {s.name}  {why[:90]}")
        elif r.returncode == 0:
            passed.append(s.name)
            print(f"PASS  {s.name}" + (f"  ({tail[-1][:90]})" if tail else ""))
        else:
            failed.append((s.name, f"exit {r.returncode}"))
            print(f"FAIL  {s.name}  exit {r.returncode}")
            for line in (tail[-15:] if tail else []):
                print("     ", line)
            for line in (r.stderr or "").strip().splitlines()[-10:]:
                print("      stderr:", line)

    print()
    print(f"{len(passed)} passed, {len(failed)} failed, {len(skipped)} skipped")
    for name in skipped:
        print("  skipped:", name)
    for name, why in failed:
        print("  FAILED: ", name, "-", why)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
