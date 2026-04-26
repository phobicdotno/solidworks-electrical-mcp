"""Thin pywin32/COM wrapper around SOLIDWORKS Electrical.

The documented entry point on a SOLIDWORKS Electrical install is the COM
factory ``EwAPI.EwInteropFactoryX`` (interface ``IEwInteropFactoryX``,
inheriting ``IInteropFactoryX``). The factory hands out two top-level objects:

* ``getEwApplication(licenseKey, errorCode)`` → ``IEwApplicationX``
* ``getEwAPI(errorCode)`` → ``IEwAPIX``

A licence key is required for ``getEwApplication`` — SW Electrical add-ins
distribute their own keys; for development against an unsigned add-in, set
``SWELE_LICENCE_KEY`` in the environment or pass ``license_key`` to
``connect_application``.

Methods/properties are accessed dynamically through pywin32 late binding so
the wrapper does not need a generated typelib (the user's installed version
may differ from the 2026 docs the catalogue is built from).
"""

from __future__ import annotations

import os
import re
from typing import Any

# COM ProgID for the SW Electrical factory. The unversioned form picks the
# latest install; ``EwAPI.EwInteropFactoryX.<year>.<sp>`` pins a specific
# release (e.g. ``EwAPI.EwInteropFactoryX.2025.5``).
FACTORY_PROGID = "EwAPI.EwInteropFactoryX"
VERSIONED_PROGID_RE = re.compile(
    r"^EwAPI\.EwInteropFactoryX\.(\d{4})\.(\d+)$"
)

# Environment variable read by ``connect_application`` if no explicit key is
# passed. Lets the user wire a licence key in once via Claude Code's MCP env
# config rather than embedding it in conversation history.
LICENCE_ENV_VAR = "SWELE_LICENCE_KEY"


class SolidworksElectricalNotInstalledError(RuntimeError):
    """Raised when the SW Electrical COM factory cannot be created."""


class SolidworksElectricalLicenceError(RuntimeError):
    """Raised when no licence key is available for getEwApplication."""


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
    """Lazy singleton wrapper for the SW Electrical COM surface."""

    def __init__(self) -> None:
        self._factory: Any | None = None
        self._app: Any | None = None
        self._api: Any | None = None

    def factory(self) -> Any:
        if self._factory is not None:
            return self._factory
        client = _require_pywin32()
        try:
            self._factory = client.Dispatch(FACTORY_PROGID)
        except Exception as e:
            raise SolidworksElectricalNotInstalledError(
                f"Could not create COM dispatch for {FACTORY_PROGID!r}. "
                "Is SOLIDWORKS Electrical installed and registered?"
            ) from e
        return self._factory

    def connect_application(self, license_key: str | None = None) -> Any:
        if self._app is not None:
            return self._app
        key = license_key or os.environ.get(LICENCE_ENV_VAR)
        if not key:
            raise SolidworksElectricalLicenceError(
                "No licence key provided. Pass license_key= or set "
                f"${LICENCE_ENV_VAR}. SW Electrical add-ins ship their own "
                "key; use yours."
            )
        factory = self.factory()
        # IInteropFactoryX::getEwApplication(BSTR licenceKey, EwErrorCode* err)
        # win32com returns out-params as a tuple alongside the return value.
        result = factory.getEwApplication(key, 0)
        if isinstance(result, tuple):
            self._app, _err = result[0], result[-1]
        else:
            self._app = result
        if self._app is None:
            raise SolidworksElectricalLicenceError(
                "getEwApplication returned NULL — licence key rejected."
            )
        return self._app

    def get_api(self) -> Any:
        if self._api is not None:
            return self._api
        factory = self.factory()
        result = factory.getEwAPI(0)
        if isinstance(result, tuple):
            self._api = result[0]
        else:
            self._api = result
        return self._api

    def disconnect(self) -> None:
        self._factory = self._app = self._api = None

    def call(self, path: str, args: list[Any] | None = None,
             root: str = "application") -> Any:
        """Resolve a dotted attribute path against one of the COM roots.

        ``root`` selects which top-level object to walk from:

        * ``"application"`` — ``IEwApplicationX`` (default; needs licence key)
        * ``"api"`` — ``IEwAPIX``
        * ``"factory"`` — ``IEwInteropFactoryX`` (no licence required)

        Examples
        --------
        ``call("ApplicationSettings.Language")`` reads a property on the app.
        ``call("getEwAPI", [0], root="factory")`` calls a factory method.
        """
        if root == "application":
            target = self.connect_application()
        elif root == "api":
            target = self.get_api()
        elif root == "factory":
            target = self.factory()
        else:
            raise ValueError(f"unknown COM root {root!r}")

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


def installed_versions() -> list[tuple[int, int]]:
    """Probe HKLM\\SOFTWARE\\Classes for ``EwAPI.EwInteropFactoryX.<y>.<sp>``
    keys and return the parsed (year, service-pack) pairs sorted newest-first.

    Returns an empty list if the registry key is missing (SW Electrical not
    installed) or winreg is unavailable (non-Windows).
    """
    try:
        import winreg  # type: ignore
    except ImportError:
        return []
    out: list[tuple[int, int]] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\Classes") as root:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(root, i)
                except OSError:
                    break
                m = VERSIONED_PROGID_RE.match(name)
                if m:
                    out.append((int(m.group(1)), int(m.group(2))))
                i += 1
    except OSError:
        return []
    return sorted(set(out), reverse=True)


def detect_installed_version() -> str | None:
    """Return the major-year of the newest installed SW Electrical, as a
    string (e.g. ``"2025"``), or ``None`` if nothing is installed."""
    vs = installed_versions()
    return str(vs[0][0]) if vs else None
