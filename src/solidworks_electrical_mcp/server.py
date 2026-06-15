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

import sys
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
        "a catalog accepts an optional version= parameter.\n\n"
        "Tools: search_api/get_api/compare_versions (discover the API surface) "
        "· get_enum (enum integer values — NOT in the catalog, read from the "
        "live typelib; needed because call/call_ops take plain ints for enum "
        "args) · connect · call (one member on a navigated path) · call_ops "
        "(several members on ONE retained object — required for stateful "
        "get→set→update→read) · array_ops (reach into VARIANT collections).\n\n"
        "Validated recipes (project must be open):\n"
        "• Read the current project name: call('getEwProjectCurrent.getName').\n"
        "• Rename the project (drives the cover-sheet title): call_ops("
        "'getEwProjectCurrent', [{'member':'setName','args':[NEW]},"
        "{'member':'update','args':[]}]).\n"
        "• List project files (cover page is EwFileType kFileCoverPage=5, "
        "tag '01'): array_ops('getEwProjectCurrent.getEwProjectFileManager."
        "getEwProjectFileArray', [{'member':'getTag','args':[]},"
        "{'member':'getDescription','args':['en']},"
        "{'member':'getFileType','args':[]}]).\n"
        "• Regenerate/refresh title-block data project-wide: call_ops("
        "'getEwProjectCurrent.getEwProjectUpdateData', "
        "[{'member':'resetProjectDataObjectType','args':[]},"
        "{'member':'addProjectDataObjectType','args':[3]},"  # kProjectDataTitleBlock
        "{'member':'process','args':[0]}]).  3=kProjectDataTitleBlock, "
        "0=kProjectDataUpdate. NOTE: process returns 45 (EW_PROJECT_OPENED) if "
        "drawings are open — close open documents first, or just close+reopen a "
        "single folio to re-render its title block.\n"
        "• List/edit what's drawn ON a folio: symbols come from "
        "getEwProjectSymbolManager.getProjectSymbolsFromFileID(fileID) — use "
        "array_ops(..., array_args=[fileID]). Each IEwProjectSymbolX has "
        "getObjectID (the component, resolve via getEwProjectComponentManager."
        "findEwProjectComponentByID), getX/YPosition (+set to move/align), "
        "getRotationAngle, getRow/ColumnMark, getWidth/getHeight. Line-diagram "
        "folios (kFileLineDiagram=1) carry symbols + IEwProjectLineX lines, NOT "
        "IEwProjectWireX wires (wires live on schematic folios). After moving "
        "symbols, close+reopen the folio to redraw. ⚠ ALWAYS check for OVERLAP "
        "when moving symbols: aligning one axis (e.g. same Y to line up on a "
        "line) collides symbols whose other-axis coords are close — 'line up on "
        "a line' means same coord on one axis AND adequate spacing on the other "
        "(even-space along it); compare planned positions pairwise before "
        "committing.\n\n"
        "Creating objects (build a project): every manager exposes "
        "newEwProjectX() returning an in-memory object — the pattern is "
        "newX() -> insert() -> THEN set fields -> update(). CRITICAL: "
        "setDescription/setTag/setLocationID only persist AFTER insert() (set "
        "before insert and they are silently dropped). Recipes via call_ops "
        "(target navigates manager.newX, which is auto-called):\n"
        "• Location: call_ops('getEwProjectLocationManager.newEwProjectLocation',"
        "[{insert,[]},{setTag,['L2']},{setDescription,['en','Engine room']},{update,[]}]).\n"
        "• Folio: newProjectFile -> setFileType(0=kFileFolio) -> "
        "setEwProjectBookID(1) -> insert -> setDescription('en',..) -> setTag('05') "
        "-> setLocationID(locID) -> update.\n"
        "• Component: newEwProjectComponent -> insert -> setTag('B1') -> "
        "setDescription -> setLocationID(locID) -> update. setTag stores the mark "
        "WITHOUT the leading '-'. assignManufacturerPart(mfg,ref) needs the part "
        "already in the project catalog, else returns 2 (EW_BAD_INPUTS).\n"
        "• Cable: newEwProjectCable -> insert -> setTag('W1') -> setDescription -> "
        "setArticleNumber(ref) -> setSupplierName(mfg) -> setLength(m) -> "
        "setUpStreamLocationID/setDownStreamLocationID(locID) -> update. Cable "
        "reference+supplier are FREE TEXT (no catalog part needed).\n"
        "⚠ getEwProjectCurrent follows GUI focus and can FLIP mid-session — for "
        "bulk writes, target a project explicitly via app.openEwProjectID(id) and "
        "assert getID() before writing, or objects leak into the wrong project.\n\n"
        "SCHEMATIC DRAWING (placing symbols on a folio) — WORKS, with the right "
        "recipe. newEwProjectSymbol() fails (rc 2); you MUST use "
        "newEwProjectSymbolFromSymbolType(symType) -> setObjectID(componentID) "
        "(link to a real project component) -> setEwSymbolName(libraryName) -> "
        "setXPosition/setYPosition -> insert() (rc 0). Key symTypes: 30="
        "kSymbolBlackbox (devices, name 'EW_BB_BlackBox'), 80=kSymbolConnection "
        "(terminals, name 'EW_ANSI_TERMINAL'), 20=kSymbolComponent. Library "
        "symbol names come from app.getEwEnvironment().getEwSymbolManager() "
        "(~1647 symbols; findEwSymbolXByName / at(i) / getEwSymbolArray; each "
        "IEwSymbolX has getName + getEwSymbolType). Optional: setRotationAngle, "
        "setX/YScale, setWidth/Height. Wires: newEwProjectLine(0=kLineSchematic) "
        "-> setStart/EndPointX/YPosition -> insert (rc 0). After drawing, "
        "close+reopen the folio to redraw. (insertMacroAt needs a real macro "
        "name; assignManufacturerPart still needs the part in the catalog.)\n"
        "⚠ LAYOUT MATTERS: symbols default to a LARGE size and the sheet "
        "coordinate space is big (get drawable size from the title block's "
        "getSheetSize). Placing many symbols at small/guessed coordinates makes "
        "them overlap, overflow the frame and render as unusable garbage. Real "
        "schematic layout (sane coordinates within the sheet, setWidth/Height/"
        "scale, no overlap, correct net wiring) is required — DO NOT bulk-dump "
        "symbols at arbitrary coords; that produces a mess, not a drawing.\n\n"
        "Known limits: the doc catalog is INCOMPLETE — use typelib_members(iface) "
        "for ground truth (e.g. IEwProjectFileX.setRevisionTranslatableTextAt is "
        "real but absent from get_api). REVISIONS: the only revision member in "
        "the typelib is IEwProjectFileX.setRevisionTranslatableTextAt(revNo, "
        "fieldIndex, lang, text); it EDITS an existing revision (returns 8 = "
        "EW_DOES_NOT_EXIST for a missing revNo) — there is NO API to create a "
        "revision, set its date, or set its index, so new revision rows must be "
        "added in the GUI."
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

    iface_seen = any(
        info.get("present") or info.get("interface_present")
        for info in per_version.values()
    )
    member_seen = (
        member is None
        or any(info.get("present") for info in per_version.values())
    )

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

    out: dict[str, Any] = {
        "interface": interface,
        "member": member,
        "versions_examined": versions,
        "per_version": per_version,
        "changes": changes,
    }
    if not iface_seen:
        out["hint"] = (
            f"Interface {interface!r} is not present in any shipped catalog. "
            "Try search_api() to find the right name."
        )
    elif not member_seen:
        out["hint"] = (
            f"Interface {interface!r} exists but member {member!r} is "
            "not present in any version. Use get_api(interface) to list "
            "available members."
        )
    return out


@mcp.tool
def connect(license_key: str | None = None) -> dict:
    """Attach to SOLIDWORKS Electrical via COM.

    Dispatches the EwAPI.EwInteropFactoryX factory and fetches an
    IEwApplicationX. A shared licence code is bundled by default, so the
    application root works out of the box; pass ``license_key`` (or set
    ``SWELE_LICENCE_KEY``) to override it. SOLIDWORKS Electrical must be
    running for the application attach to succeed.
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

    * ``"application"`` — IEwApplicationX (default; uses the bundled licence
      code unless overridden, and needs SW Electrical running).
    * ``"api"`` — IEwAPIX.
    * ``"factory"`` — IEwInteropFactoryX (no licence required).
    """
    try:
        # com.call already coerces the result to a JSON-safe value on the COM
        # apartment thread, so nothing thread-bound crosses back here.
        value = com_mod.app().call(path, args, root=root)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "value": value}


@mcp.tool
def call_ops(target: str | None, ops: list[dict],
             root: str = "application") -> dict:
    """Run several members on ONE retained COM object, in order.

    Use this for stateful sequences where the object must survive across
    operations — e.g. fetch the current project, then ``setName`` + ``update``
    + ``getName`` on that same project. Doing those as separate ``call``
    invocations fails: each re-navigates and releases a fresh wrapper, so the
    edit is discarded before it is committed.

    Parameters
    ----------
    target : dotted path to the object (every segment is auto-called and
        ``(object, errorCode)`` tuples unwrapped). Pass ``null``/empty to
        operate directly on the ``root`` object.
    ops : list of ``{"member": str, "args": [...] | null}`` applied in order.
    root : ``"application"`` (default) / ``"api"`` / ``"factory"``.

    Example
    -------
    Rename the current project and read it back in one call::

        call_ops("getEwProjectCurrent",
                 [{"member": "setName", "args": ["New Title"]},
                  {"member": "update", "args": []},
                  {"member": "getName", "args": []}])
    """
    try:
        values = com_mod.app().call_ops(target, ops, root=root)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "values": values}


@mcp.tool
def array_ops(array: str, ops: list[dict], select: dict | None = None,
              root: str = "application", limit: int | None = None,
              array_args: list | None = None) -> dict:
    """Enumerate a COM array and run members on each (optionally filtered) item.

    A plain ``call`` that returns a collection (``VARIANT`` of ``IDispatch``)
    yields opaque handles you can't index or invoke. This reaches into the
    array and operates on its elements.

    Parameters
    ----------
    array : dotted path that resolves to the array, e.g.
        ``"getEwProjectCurrent.getEwProjectFileManager.getEwProjectFileArray"``.
    ops : ``[{"member": str, "args": [...] | null}, ...]`` run on each element.
    select : optional ``{"member": str, "args": [...], "equals": value}`` —
        keep only elements whose ``member(*args)`` equals ``value``.
    limit : optional cap on number of returned elements.
    array_args : arguments for the final array-producing call when it takes
        parameters, e.g. ``getProjectSymbolsFromFileID(fileID)``.

    Returns ``{"ok", "rows": [{"index", "results": [...]}, ...]}``.

    Examples
    --------
    Read tag + description of every project file::

        array_ops("getEwProjectCurrent.getEwProjectFileManager.getEwProjectFileArray",
                  [{"member": "getTag", "args": []},
                   {"member": "getDescription", "args": ["en"]}])

    List the symbols on a folio (array from a method with an argument)::

        array_ops("getEwProjectCurrent.getEwProjectSymbolManager.getProjectSymbolsFromFileID",
                  [{"member": "getObjectID", "args": []},
                   {"member": "getXPosition", "args": []},
                   {"member": "getYPosition", "args": []}],
                  array_args=[<fileID>])
    """
    try:
        rows = com_mod.app().array_ops(array, ops, select=select, root=root,
                                       limit=limit, array_args=array_args)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "rows": rows}


@mcp.tool
def get_enum(name: str | None = None) -> dict:
    """Resolve COM enum members from the installed SW Electrical type library.

    Enum values are NOT in the scraped doc catalog, so this reads them from the
    live typelib — letting you supply correct integer arguments to ``call`` /
    ``call_ops`` (which take plain ints for enum parameters).

    Pass a name (e.g. ``"EwProjectDataObjectType"``, ``"EwErrorCode"``,
    ``"EwFileType"``) to get its members; pass nothing to list all enum names.
    """
    try:
        enums = com_mod.app().list_enums(name)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if name is None:
        return {"ok": True, "enums": sorted(enums)}
    return {"ok": True, "name": name, "members": enums.get(name, {})}


@mcp.tool
def typelib_members(interface: str) -> dict:
    """List an interface's members from the live COM type library.

    The scraped doc catalog (search_api/get_api) can be INCOMPLETE — it omits
    some real, callable methods. This reads the interface straight from the
    installed type library (ground truth), surfacing undocumented members with
    parameter names/types. Use it when get_api seems to be missing a method you
    expect, or to confirm whether a capability exists at all.
    """
    try:
        result = com_mod.app().typelib_members(interface)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, **result}


def _isolate_stdout_from_native_pollution() -> None:
    """Stop native libraries from corrupting the JSON-RPC stream.

    SOLIDWORKS Electrical's COM DLLs write diagnostic lines straight to OS
    file descriptor 1 (e.g. ``...isSOLIDWORKSApplication ... Services are not
    initialized``) whenever the application isn't fully started. The MCP stdio
    transport multiplexes JSON-RPC over that same fd, so the raw text breaks
    message framing and the client drops the connection ("Connection closed").
    The write happens inside native code, below Python, so a ``try/except``
    around the COM call cannot intercept it.

    Preserve the real client-facing stdout on a private fd that the transport
    writes JSON-RPC to, and point fd 1 at stderr so any native chatter lands in
    the server log instead of the protocol stream.
    """
    import io
    import os

    try:
        # 1. Take a private copy of the client-facing stdout pipe and route the
        #    transport's JSON-RPC writes there first, so a working client
        #    channel exists before fd 1 is touched.
        saved_fd = os.dup(1)
        sys.stdout = io.TextIOWrapper(
            io.BufferedWriter(io.FileIO(saved_fd, mode="w")),
            encoding="utf-8", newline="\n", line_buffering=True,
        )
        # 2. Point fd 1 at stderr so native chatter shows up in the server log;
        #    if stderr is unavailable, fall back to the null device.
        try:
            os.dup2(2, 1)
        except OSError:
            null_fd = os.open(os.devnull, os.O_WRONLY)
            os.dup2(null_fd, 1)
            os.close(null_fd)
    except OSError:
        # Best effort: if isolation can't be set up, leave stdout as-is rather
        # than taking the server down. The pollution risk returns, but the
        # transport still functions.
        pass


def main() -> None:
    _isolate_stdout_from_native_pollution()
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
