"""Regression test: cached COM objects must not outlive SOLIDWORKS Electrical.

Observed 2026-09-22: the MCP server was started (and its
EwAPI.EwInteropFactoryX dispatched) while SOLIDWORKS Electrical was not
running. After the program was launched, every connect() still reported
"getEwApplication returned NULL (EW_INVALID_LICENSE)", while a fresh factory
in a new process attached fine with the same key. The cached factory was the
problem, not the licence. The cached IEwApplicationX has the mirror problem:
close the program after a successful attach and the pointer is dead forever.

This test fakes pywin32 so it runs anywhere. Cases:
  A. first factory live: attach succeeds with ONE dispatch (no blind retry)
  B. first factory stale (NULL), second live: attach succeeds on the retry,
     and the derived API object is dropped with the factory
  C. stale twice: a genuine licence error is raised after exactly one retry
  D. factory that raises instead of returning NULL is treated as stale
  E. cached application dies (program closed): next attach re-dispatches

Run directly:
    .venv/Scripts/python.exe tests/test_stale_factory_retry.py
"""
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from solidworks_electrical_mcp import com as com_mod  # noqa: E402

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)
        print("FAIL:", msg)


class _App:
    def __init__(self):
        self.alive = True

    def getApplicationVersion(self):
        if not self.alive:
            raise RuntimeError("com_error: RPC server unavailable")
        return "2025.5.0"


class _Factory:
    def __init__(self, mode: str):
        self.mode = mode  # "live" | "null" | "raise"
        self.calls = 0
        self.app = _App()

    def getEwApplication(self, key):
        self.calls += 1
        if self.mode == "null":
            return (None, 39)  # EW_INVALID_LICENSE, as the real one reports
        if self.mode == "raise":
            raise RuntimeError("com_error: RPC_E_DISCONNECTED")
        return (self.app, 0)

    def getEwAPI(self):
        return (object(), 0)


def make_app(modes: list[str]):
    factories: list[_Factory] = []

    def dispatch(progid):
        mode = modes[len(factories)] if len(factories) < len(modes) else modes[-1]
        factories.append(_Factory(mode))
        return factories[-1]

    com_mod._require_pywin32 = lambda: SimpleNamespace(Dispatch=dispatch)
    return com_mod.ElectricalApp(), factories


def main() -> int:
    orig = com_mod._require_pywin32
    try:
        # A. happy path: no retry when the first factory is live.
        app, fs = make_app(["live"])
        app.factory()
        live = app.connect_application("KEY")
        check(live is not None and live.getApplicationVersion() == "2025.5.0",
              "A: attach failed on a live factory")
        check(len(fs) == 1 and fs[0].calls == 1,
              f"A: expected 1 dispatch/1 call, got {len(fs)} factories")

        # B. stale then live; API object derived from the stale factory dropped.
        app, fs = make_app(["null", "live"])
        app.factory()
        app.get_api()  # derive an API object from the (stale) factory
        check(app._api is not None, "B: setup: API object should be cached")
        live = app.connect_application("KEY")
        check(live is not None and live.getApplicationVersion() == "2025.5.0",
              "B: attach did not succeed after dropping the stale factory")
        check(len(fs) == 2, f"B: expected exactly one re-dispatch, got {len(fs)}")
        check(fs[0].calls == 1 and fs[1].calls == 1,
              "B: each factory should have been asked once")
        check(app._api is None,
              "B: API object derived from the stale factory survived the reset")
        again = app.connect_application("KEY")
        check(again is live and len(fs) == 2,
              "B: second connect should reuse the cached application")

        # C. stale twice: licence error after exactly one retry.
        app, fs = make_app(["null", "null"])
        try:
            app.connect_application("KEY")
            check(False, "C: persistent NULL did not raise a licence error")
        except com_mod.SolidworksElectricalLicenceError as e:
            check("EW_INVALID_LICENSE" in str(e), f"C: wrong error text: {e}")
            check(len(fs) == 2, f"C: should retry exactly once, got {len(fs)}")

        # D. a factory that raises is treated like a NULL return.
        app, fs = make_app(["raise", "live"])
        live = app.connect_application("KEY")
        check(live is not None and len(fs) == 2,
              "D: raising factory was not retried with a fresh dispatch")

        # E. application pointer dies after attach (program closed): re-attach.
        app, fs = make_app(["live", "live"])
        first = app.connect_application("KEY")
        fs[0].app.alive = False
        second = app.connect_application("KEY")
        check(second is not first and second.getApplicationVersion() == "2025.5.0",
              "E: dead application pointer was not replaced")
        check(len(fs) == 2, f"E: expected a fresh dispatch, got {len(fs)}")
        third = app.connect_application("KEY")
        check(third is second and len(fs) == 2,
              "E: healthy application should be reused without re-dispatch")
    finally:
        com_mod._require_pywin32 = orig

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: live factory attaches once; stale/raising factory is dropped "
          "and retried once (API object included); persistent NULL raises; "
          "a dead application pointer is re-attached.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
