"""FastMCP server exposing SOLIDWORKS Electrical via COM, indexed by the
SOLIDWORKS API help catalogues for one or more major releases.

Tools exposed
-------------
list_versions()
    Versions for which a doc catalog ships, plus which versions are
    installed locally and which version is the current default.

list_interfaces(version=None)
    All interface names known to a given catalog (defaults to current).

search_api(query, limit=25, version=None)
    Search interfaces + members in the given catalog.

get_api(interface, member=None, version=None)
    One interface (or one member) from the given catalog.

compare_versions(interface, member=None)
    Cross-version diff for one interface (or one of its members) across
    every shipped catalog.

connect(license_key=None)
    Dispatch the SW Electrical COM factory and (if a key is available)
    attach an IEwApplicationX.

call(path, args=None, root='application')
    Late-bound dotted attribute access on the live COM surface.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from . import catalog as catalog_mod
from . import com as com_mod

mcp = FastMCP(
    name="solidworks-electrical-mcp",
    instructions=(
        "Drives SOLIDWORKS Electrical via COM (pywin32) and indexes its API "
        "via doc catalogues sourced from help.solidworks.com. Multiple major "
        "releases are supported simultaneously — list_versions() reports "
        "what is shipped and what is installed; every other tool that needs "
        "a catalog accepts an optional version= parameter."
    ),
)

_catalogs: dict[str, catalog_mod.Catalog] = {
    v: catalog_mod.load(v) for v in catalog_mod.available_versions()
}
_default_version = (
    com_mod.detect_installed_version()
    if com_mod.detect_installed_version() in _catalogs
    else (max(_catalogs) if _catalogs else catalog_mod.DEFAULT_VERSION)
)


def _resolve_version(version: str | None) -> tuple[str, catalog_mod.Catalog | None]:
    v = version or _default_version
    return v, _catalogs.get(v)


def _missing_catalog(v: str) -> dict:
    return {
        "error": f"No catalog shipped for version {v!r}",
        "available_versions": sorted(_catalogs),
    }


@mcp.tool
def list_versions() -> dict:
    """Catalog and install state for every supported version."""
    installed = com_mod.installed_versions()
    return {
        "default": _default_version,
        "shipped_catalogs": sorted(_catalogs),
        "installed_versions": [f"{y}.{sp}" for y, sp in installed],
        "installed_majors": sorted({str(y) for y, _ in installed}),
    }


@mcp.tool
def list_interfaces(version: str | None = None) -> list[str] | dict:
    """All interface names known to the catalog for the given version."""
    v, cat = _resolve_version(version)
    if cat is None:
        return _missing_catalog(v)
    return sorted(i.name for i in cat.interfaces)


@mcp.tool
def search_api(query: str, limit: int = 25,
               version: str | None = None) -> list[dict] | dict:
    """Ranked search across interfaces and members in the given catalog."""
    v, cat = _resolve_version(version)
    if cat is None:
        return _missing_catalog(v)
    return cat.search(query, limit=limit)


@mcp.tool
def get_api(interface: str, member: str | None = None,
            version: str | None = None) -> dict:
    """Look up an interface (or one of its members) in the given catalog."""
    v, cat = _resolve_version(version)
    if cat is None:
        return _missing_catalog(v)
    iface = cat.get_interface(interface)
    if iface is None:
        return {"error": f"Unknown interface {interface!r} in {v}",
                "hint": "Use list_interfaces() to see what is known."}
    if member is None:
        return {
            "version": v,
            "interface": iface.name,
            "summary": iface.summary,
            "url": iface.url(v),
            "members": [
                {"name": m.name, "kind": m.kind, "signature": m.signature,
                 "summary": m.summary}
                for m in iface.members
            ],
        }
    for m in iface.members:
        if m.name.lower() == member.lower():
            return {
                "version": v,
                "interface": iface.name,
                "member": m.name,
                "kind": m.kind,
                "signature": m.signature,
                "summary": m.summary,
                "url": m.url(iface.page, v),
            }
    return {"error": f"Member {member!r} not found on {iface.name} in {v}",
            "available": [m.name for m in iface.members]}


@mcp.tool
def compare_versions(interface: str,
                     member: str | None = None) -> dict:
    """Cross-version view of one interface (or one of its members).

    Returns the per-version state plus a flat changes block (added /
    removed / signature changes / summary changes) computed pairwise across
    adjacent shipped versions.
    """
    versions = sorted(_catalogs)
    if not versions:
        return {"error": "No catalogues are loaded."}

    per_version: dict[str, Any] = {}
    for v in versions:
        cat = _catalogs[v]
        iface = cat.get_interface(interface)
        if iface is None:
            per_version[v] = {"present": False}
            continue
        if member is None:
            per_version[v] = {
                "present": True,
                "summary": iface.summary,
                "member_count": len(iface.members),
                "url": iface.url(v),
            }
        else:
            hit = next((m for m in iface.members
                        if m.name.lower() == member.lower()), None)
            if hit is None:
                per_version[v] = {"present": False, "interface_present": True}
            else:
                per_version[v] = {
                    "present": True,
                    "kind": hit.kind,
                    "signature": hit.signature,
                    "summary": hit.summary,
                    "url": hit.url(iface.page, v),
                }

    changes: list[dict] = []
    for older, newer in zip(versions, versions[1:]):
        d = catalog_mod.diff(_catalogs[older], _catalogs[newer])
        if interface in d["added_interfaces"]:
            changes.append({"between": f"{older}->{newer}",
                            "kind": "interface_added"})
        if interface in d["removed_interfaces"]:
            changes.append({"between": f"{older}->{newer}",
                            "kind": "interface_removed"})
        for ic in d["interface_changes"]:
            if ic["interface"] != interface:
                continue
            if member is None:
                if ic["summary_changed"]:
                    changes.append({"between": f"{older}->{newer}",
                                    "kind": "interface_summary",
                                    "old": ic["old_summary"],
                                    "new": ic["new_summary"]})
                for a in ic["added_members"]:
                    changes.append({"between": f"{older}->{newer}",
                                    "kind": "member_added", "member": a})
                for r in ic["removed_members"]:
                    changes.append({"between": f"{older}->{newer}",
                                    "kind": "member_removed", "member": r})
            else:
                if member in ic["added_members"]:
                    changes.append({"between": f"{older}->{newer}",
                                    "kind": "member_added", "member": member})
                if member in ic["removed_members"]:
                    changes.append({"between": f"{older}->{newer}",
                                    "kind": "member_removed", "member": member})
                for sc in ic["signature_changes"]:
                    if sc["member"] == member:
                        changes.append({
                            "between": f"{older}->{newer}",
                            "kind": "signature_change",
                            "member": member,
                            "old_signature": sc["old_signature"],
                            "new_signature": sc["new_signature"],
                        })
                for sc in ic["summary_changes"]:
                    if sc["member"] == member:
                        changes.append({
                            "between": f"{older}->{newer}",
                            "kind": "summary_change",
                            "member": member,
                            "old_summary": sc["old_summary"],
                            "new_summary": sc["new_summary"],
                        })

    return {
        "interface": interface,
        "member": member,
        "versions_examined": versions,
        "per_version": per_version,
        "changes": changes,
    }


@mcp.tool
def connect(license_key: str | None = None) -> dict:
    """Attach to SOLIDWORKS Electrical via COM.

    Dispatches the EwAPI.EwInteropFactoryX factory and (if a licence key is
    available) immediately fetches an IEwApplicationX. Without a key, the
    factory is still attached and ``api``/``factory`` roots remain usable.
    """
    try:
        com_mod.app().factory()
    except com_mod.SolidworksElectricalNotInstalledError as e:
        return {"connected": False, "factory": False, "error": str(e)}
    except Exception as e:
        return {"connected": False, "factory": False,
                "error": f"{type(e).__name__}: {e}"}

    out: dict[str, Any] = {
        "connected": True,
        "factory": True,
        "progid": com_mod.FACTORY_PROGID,
        "installed_versions": [f"{y}.{sp}" for y, sp
                               in com_mod.installed_versions()],
        "active_catalog": _default_version,
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
