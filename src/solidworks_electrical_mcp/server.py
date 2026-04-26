"""FastMCP server exposing SOLIDWORKS Electrical via COM, indexed by the
SOLIDWORKS 2026 API help.

Tools exposed
-------------
search_api(query, limit=25)
    Search the catalog of SW Electrical interfaces and members.

get_api(interface, member=None)
    Return the doc summary, full member list, or one member's signature/URL.

connect()
    Attach to the running SOLIDWORKS Electrical COM server.

call(path, args=None)
    Resolve a dotted attribute path on the app and read or invoke it.
    e.g. ``call("ApplicationSettings.Language")``.

list_interfaces()
    Return all interface names known to the catalog.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from . import catalog as catalog_mod
from . import com as com_mod

mcp = FastMCP(
    name="solidworks-electrical-mcp",
    instructions=(
        "Drives SOLIDWORKS Electrical via COM (pywin32). The interface and "
        "member catalog is sourced from the SOLIDWORKS 2026 API help "
        "(https://help.solidworks.com/2026/english/api/sldworkselecapihelp/). "
        "Use search_api / get_api to discover what is callable, then call() to "
        "invoke it. The user must run the scraper once to populate the "
        "catalog: `python -m solidworks_electrical_mcp.scrape`."
    ),
)

_catalog = catalog_mod.load()


@mcp.tool
def list_interfaces() -> list[str]:
    """Return every interface name known to the catalog."""
    return sorted(i.name for i in _catalog.interfaces)


@mcp.tool
def search_api(query: str, limit: int = 25) -> list[dict]:
    """Search the SW Electrical API catalog (interfaces + members).

    Returns ranked hits with kind, interface, optional member, signature,
    summary and a link back to the master help page.
    """
    return _catalog.search(query, limit=limit)


@mcp.tool
def get_api(interface: str, member: str | None = None) -> dict:
    """Look up one interface (or one member of one interface) in the catalog."""
    iface = _catalog.get_interface(interface)
    if iface is None:
        return {"error": f"Unknown interface {interface!r}",
                "hint": "Use list_interfaces() to see what is known."}
    if member is None:
        return {
            "interface": iface.name,
            "summary": iface.summary,
            "url": iface.url,
            "members": [
                {"name": m.name, "kind": m.kind, "signature": m.signature,
                 "summary": m.summary}
                for m in iface.members
            ],
        }
    for m in iface.members:
        if m.name.lower() == member.lower():
            return {
                "interface": iface.name,
                "member": m.name,
                "kind": m.kind,
                "signature": m.signature,
                "summary": m.summary,
                "url": m.url(iface.page),
            }
    return {"error": f"Member {member!r} not found on {iface.name}",
            "available": [m.name for m in iface.members]}


@mcp.tool
def connect(license_key: str | None = None) -> dict:
    """Attach to SOLIDWORKS Electrical via COM.

    Dispatches the EwAPI.EwInteropFactoryX factory and (if a licence key is
    available) immediately fetches an IEwApplicationX. Without a key, the
    factory is still attached and ``api``/``factory`` roots remain usable;
    the application root reports a licence error until ``connect`` is called
    again with a key (or ``$SWELE_LICENCE_KEY`` is set in the env).
    """
    try:
        factory = com_mod.app().factory()
    except com_mod.SolidworksElectricalNotInstalledError as e:
        return {"connected": False, "factory": False, "error": str(e)}
    except Exception as e:
        return {"connected": False, "factory": False,
                "error": f"{type(e).__name__}: {e}"}

    out: dict[str, Any] = {
        "connected": True,
        "factory": True,
        "progid": com_mod.FACTORY_PROGID,
    }
    try:
        com_mod.app().connect_application(license_key)
        out["application"] = True
    except com_mod.SolidworksElectricalLicenceError as e:
        out["application"] = False
        out["licence_error"] = str(e)
    except Exception as e:
        out["application"] = False
        out["licence_error"] = f"{type(e).__name__}: {e}"
    return out


@mcp.tool
def call(path: str, args: list[Any] | None = None,
         root: str = "application") -> dict:
    """Resolve a dotted attribute path on a COM root and read or call it.

    The ``root`` argument selects the top-level COM object:

    * ``"application"`` — IEwApplicationX (default; requires licence key).
    * ``"api"`` — IEwAPIX.
    * ``"factory"`` — IEwInteropFactoryX (no licence required).

    Examples
    --------
    ``call("ApplicationSettings.Language")`` reads a property on the app.
    ``call("getEwAPI", [0], root="factory")`` invokes a factory method.
    """
    try:
        result = com_mod.app().call(path, args, root=root)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "value": _coerce(result)}


def _coerce(v: Any) -> Any:
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    try:
        return list(v)
    except TypeError:
        return repr(v)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
