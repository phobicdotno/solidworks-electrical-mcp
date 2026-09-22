"""Regression test: a factory cached before SW Electrical started must not
poison every later application attach.

Observed 2026-09-22: the MCP server was started (and its
EwAPI.EwInteropFactoryX dispatched) while SOLIDWORKS Electrical was not
running. After the program was launched, every connect() still reported
"getEwApplication returned NULL (EW_INVALID_LICENSE)", while a fresh factory
in a new process attached fine with the same key. The cached factory was the
problem, not the licence.

This test fakes pywin32 so it runs anywhere: the first factory instance
always answers NULL, the second answers a live application. The attach must
succeed on the retry, and a re-dispatch must have happened exactly once.

Run directly:
    .venv/Scripts/python.exe tests/test_stale_factory_retry.py
"""
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from solidworks_electrical_mcp import com as com_mod  # noqa: E402


class _App:
    def getApplicationVersion(self):
        return "2025.5.0"


class _Factory:
    def __init__(self, stale: bool):
        self.stale = stale
        self.calls = 0

    def getEwApplication(self, key):
        self.calls += 1
        if self.stale:
            return (None, 39)  # EW_INVALID_LICENSE, as the real one reports
        return (_App(), 0)


def main() -> int:
    factories: list[_Factory] = []

    def dispatch(progid):
        factories.append(_Factory(stale=(len(factories) == 0)))
        return factories[-1]

    fake_client = SimpleNamespace(Dispatch=dispatch)
    orig = com_mod._require_pywin32
    com_mod._require_pywin32 = lambda: fake_client
    try:
        app = com_mod.ElectricalApp()
        # Simulate the server's startup: factory dispatched early, while the
        # program is not running.
        app.factory()
        assert len(factories) == 1, "expected one factory after startup"

        live = app.connect_application("KEY")
        assert live is not None and live.getApplicationVersion() == "2025.5.0", \
            "application did not attach after the stale factory was dropped"
        assert len(factories) == 2, \
            f"expected exactly one re-dispatch, got {len(factories)} factories"
        assert factories[0].calls == 1 and factories[1].calls == 1, \
            "each factory should have been asked once"

        # A second connect must reuse the cached application, not dispatch again.
        again = app.connect_application("KEY")
        assert again is live
        assert len(factories) == 2

        # A factory that is stale twice in a row is a genuine licence failure.
        factories.clear()
        app2 = com_mod.ElectricalApp()

        def dispatch_always_stale(progid):
            factories.append(_Factory(stale=True))
            return factories[-1]
        fake_client.Dispatch = dispatch_always_stale
        try:
            app2.connect_application("KEY")
        except com_mod.SolidworksElectricalLicenceError as e:
            assert "EW_INVALID_LICENSE" in str(e)
            assert len(factories) == 2, "should retry exactly once, then fail"
        else:
            print("FAIL: persistent NULL did not raise a licence error")
            return 1

        print("PASS: stale factory is dropped and the attach retried once; "
              "a persistent NULL still surfaces as a licence error.")
        return 0
    finally:
        com_mod._require_pywin32 = orig


if __name__ == "__main__":
    sys.exit(main())
