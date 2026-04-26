"""Thin pywin32/COM wrapper around SOLIDWORKS Electrical.

SOLIDWORKS Electrical exposes its API through a COM server registered as
``EApp.Application``. We attach via late binding so we do not depend on a
generated typelib (the user's installed version may differ from 2026).

Methods/properties named on COM interfaces are accessed dynamically — the
master reference for what is callable is the SOLIDWORKS 2026 API help
(scraped into the catalog), not a hand-maintained Python wrapper.
"""

from __future__ import annotations

from typing import Any

PROGID = "EApp.Application"


class CodesysNotInstalledError(RuntimeError):
    """Raised when SOLIDWORKS Electrical / its COM server is not registered."""


def _require_pywin32():
    try:
        import win32com.client  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "pywin32 is required for SOLIDWORKS Electrical COM access. "
            "Install it on Windows: pip install pywin32"
        ) from e
    return win32com.client


class ElectricalApp:
    """Lazy singleton wrapper for the SW Electrical COM application."""

    def __init__(self) -> None:
        self._app: Any | None = None

    def connect(self) -> Any:
        if self._app is not None:
            return self._app
        client = _require_pywin32()
        try:
            self._app = client.Dispatch(PROGID)
        except Exception as e:  # pythoncom raises pywintypes.com_error
            raise CodesysNotInstalledError(
                f"Could not create COM dispatch for {PROGID!r}. "
                "Is SOLIDWORKS Electrical installed and registered?"
            ) from e
        return self._app

    def disconnect(self) -> None:
        self._app = None

    def call(self, path: str, args: list[Any] | None = None) -> Any:
        """Resolve a dotted attribute path on the app and call/read it.

        Examples
        --------
        ``call("ApplicationSettings.Language")`` → property read
        ``call("CommandManager.RunCommand", ["NewProject"])`` → method call
        """
        target = self.connect()
        parts = path.split(".")
        for p in parts[:-1]:
            target = getattr(target, p)
        leaf = getattr(target, parts[-1])
        if args is None:
            return leaf
        return leaf(*args)


_singleton = ElectricalApp()


def app() -> ElectricalApp:
    return _singleton
