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

call_ops / array_ops / shift_folio_numbers / get_enum / typelib_members
    Stateful multi-member ops, collection access, page renumbering, and
    live type-library introspection.

Task-level tools (workflows.py): reconnect, project_info, list_folios,
    find_folio, list_locations, list_books_and_folders, list_components,
    find_component, list_cables, folio_symbols, export_folio_pdf,
    regenerate_title_blocks, rename_project, close_and_reopen_folio,
    add_component, clone_component, rename_component,
    renumber_components, audit_tag_roots,
    add_folder, rename_folder, delete_folder,
    add_folio, delete_folio, move_symbol, check_drawing_rules,
    check_page_ink,
    add_location, delete_location, attach_manufacturer_part,
    place_symbol, place_symbols, remove_symbol, remove_symbols,
    move_symbols,
    add_text, add_texts, list_texts, remove_text, remove_texts,
    delete_component.
"""

from __future__ import annotations

import sys
from typing import Any

from fastmcp import FastMCP

from . import automation as auto
from . import catalog as catalog_mod
from . import com as com_mod
from . import library as lib
from . import projectdata as pd
from . import projects as pj
from . import settings as st
from . import workflows as wf

mcp = FastMCP(
    name="solidworks-electrical-mcp",
    instructions=(
        "Drives SOLIDWORKS Electrical via COM (pywin32) and indexes its API "
        "via doc catalogues sourced from help.solidworks.com. Multiple major "
        "releases are supported simultaneously - list_versions() reports "
        "what is shipped and what is installed; every other tool that needs "
        "a catalog accepts an optional version= parameter.\n\n"
        "START HERE - task-level tools (validated live, no COM knowledge "
        "needed; SOLIDWORKS Electrical must be running with a project open): "
        "project_info · list_folios · find_folio · list_locations · "
        "list_books_and_folders · list_components · find_component · "
        "list_cables · folio_symbols · export_folio_pdf (then Read the PDF to "
        "SEE the page) · regenerate_title_blocks · rename_project · "
        "close_and_reopen_folio · add_component (build one from scratch) "
        "· rename_component (retag; lists what follows and what does not) "
        "· renumber_components (retag a whole run, collision-safe) "
        "· audit_tag_roots (mark vs stored root/number) "
        "· add_folder/rename_folder/delete_folder and add_folio/delete_folio "
        "(the document tree and its pages; insert_before_page cascades "
        "the numbers) · add_location · attach_manufacturer_part (a part "
        "the library does not carry) · place_symbol/place_symbols (BATCH: one folio close for the "
        "whole page, strongly preferred)/remove_symbol/"
        "move_symbol/move_symbols (draw an existing component, and move "
        "one WITH its wires; prefer the batch) · add_text/add_texts/list_texts/"
        "remove_text/remove_texts (BATCH forms preferred: one folio close "
        "for the page) · check_drawing_rules (crowding and the drawable "
        "box) · check_page_ink (what is really DRAWN, which catches a "
        "footprint hanging outside the box) · clone_component (\"on sheet 10 clone K32 "
        "into K33\"; dry_run first) · delete_component · reconnect (drop "
        "cached COM state after "
        "SOLIDWORKS was restarted). Reach for the generic call/call_ops/"
        "array_ops tools below ONLY for something these do not cover.\n\n"
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
        "symbols at arbitrary coords; that produces a mess, not a drawing. "
        "Concrete scale: EW_BB_BlackBox is 30x25 units (fills the sheet); set "
        "setXScale/setYScale ~0.13-0.18 -> ~4-5 wide. Drawable area is roughly "
        "x in [1,16], y in [1,13]; EW_ANSI_TERMINAL is point-sized. Clean recipe "
        "that renders well: terminals in ONE vertical column (x~5, y 10 down, "
        "short output stubs to x~10), device boxes small on the right (x~13). "
        "SELF-VERIFY before claiming success: export the folio to PDF and look "
        "at it — proj.newEwProjectExportPDF() -> "
        "initializeFromPrintProjectConfiguration() -> setAllProjectFiles(False) "
        "-> setSelectionFiles(VARIANT VT_ARRAY|VT_I4 [fileID]) -> "
        "setExportToPDFFileName(folder) -> exportPDF(); the PDF lands in "
        "%PROGRAMDATA%\\SOLIDWORKS Electrical\\TEMP\\EW_{guid}\\Pdf\\*.pdf — "
        "render it (e.g. PyMuPDF) and inspect. Rendered pixels are otherwise "
        "unreadable via the API, so this export+render loop is the only way to "
        "check layout.\n\n"
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
def shift_folio_numbers(threshold: int, delta: int,
                        place_file_id: int | None = None,
                        place_at: int | None = None, dry_run: bool = True,
                        root: str = "application") -> dict:
    """Cascade folio page numbers (``getTagNumber``) to insert or remove a sheet.

    Folio page marks must be unique, so slotting a folio in at page N requires
    every folio numbered >= N to move. This derives the shift from the live
    folio set and applies it collision-safe — no hand-built id list, no
    transient duplicate marks.

    Parameters
    ----------
    threshold : the page number at/after which folios move.
    delta : ``+1`` to open a slot (insert), ``-1`` to close a gap (after delete).
    place_file_id : optional folio id to drop into the freed slot. It is parked
        at a temp number during the cascade so it never blocks a target, then
        set to ``place_at``.
    place_at : the final page number for ``place_file_id`` (usually == threshold).
    dry_run : default True — returns the full planned ``plan`` without mutating.
        Set False to apply; the result then carries ``results`` + any ``errors``
        (ops whose ``rc`` was non-zero).

    Example — insert the new 16DI folio (id 26375) as page 27, push the rest +1::

        shift_folio_numbers(27, 1, place_file_id=26375, place_at=27, dry_run=False)
    """
    try:
        out = com_mod.app().shift_folio_numbers(
            threshold, delta, place_file_id=place_file_id, place_at=place_at,
            dry_run=dry_run, root=root)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, **out}


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


# ---------------------------------------------------------------------------
# Task-level tools. Each one is a validated recipe assembled from the COM calls
# above; they need SOLIDWORKS Electrical running with a project open.


def _run(fn, *args, **kwargs) -> dict:
    try:
        out = com_mod.app().run_workflow(fn, *args, **kwargs)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if isinstance(out, dict):
        out.setdefault("ok", True)
        return out
    return {"ok": True, "value": out}


@mcp.tool
def reconnect() -> dict:
    """Drop every cached COM object and attach again.

    Use after SOLIDWORKS Electrical was closed, restarted, or started after
    this server, if a tool reports a COM error or a licence error that a
    fresh process does not. The next tool call re-attaches automatically.
    """
    com_mod.app().disconnect()
    return connect()


# --------------------------------------------------------------------------
# Settings: the values that decide how a project behaves rather than what it
# contains - mark formulas, wire styles, title blocks, the cable types that
# may be specified.


@mcp.tool
def project_config(setting: str | None = None, value: Any = None) -> dict:
    """Read the project's configuration, or set one value.

    With no arguments, reports every setting the installed version defines
    (read from the live type library, so it is that version's own list) with
    what the project holds for it. With ``setting``, reports one; an unknown
    name comes back with near matches rather than an error alone. With
    ``setting`` and ``value``, writes it and reads it back so the result
    shows what actually took.

    These drive mark formulas, cross-reference format, wire numbering rules
    and page format, so a change here changes how the project behaves.
    """
    return _run(st.project_config, setting=setting, value=value)


@mcp.tool
def list_wire_styles(name_contains: str | None = None) -> dict:
    """The wire styles the project draws with.

    A style is electrical and graphical at once: section, colour, tension and
    frequency, plus the formula its label is built from. A project that draws
    every wire in one style has no wiring list worth printing, so this is
    where to look when the reports come out wrong.
    """
    return _run(st.list_wire_styles, name_contains=name_contains)


@mcp.tool
def update_wire_style(wire_style_id: int, description: str | None = None,
                      section_or_gauge: str | None = None,
                      colour: str | None = None, tension: str | None = None,
                      frequency: str | None = None,
                      linear_mass: str | None = None,
                      bend_radius: float | None = None,
                      wire_mark_formula: str | None = None,
                      equipotential_formula: str | None = None) -> dict:
    """Change one wire style; anything left null is untouched.

    Every wire already drawn in this style takes the change, so editing the
    section or the mark formula of a style in use is a project-wide edit.
    """
    return _run(st.update_wire_style, wire_style_id=wire_style_id,
                description=description, section_or_gauge=section_or_gauge,
                colour=colour, tension=tension, frequency=frequency,
                linear_mass=linear_mass, bend_radius=bend_radius,
                wire_mark_formula=wire_mark_formula,
                equipotential_formula=equipotential_formula)


@mcp.tool
def list_title_blocks(name_contains: str | None = None,
                      limit: int = 100) -> dict:
    """The title blocks available to draw pages with.

    ``regenerate_title_blocks`` refreshes what a page prints; this says what
    it could print with.
    """
    return _run(st.list_title_blocks, name_contains=name_contains,
                limit=limit)


@mcp.tool
def search_cable_references(manufacturer: str | None = None,
                            reference: str | None = None,
                            description_contains: str | None = None,
                            limit: int = 50) -> dict:
    """The cable types in the library, which is what a cable is made from.

    ``list_cables`` reports the cables a project has; this reports what could
    be specified. Each row carries the core count, because a reference with
    the wrong number of cores is the usual reason a drawn cable will not take
    the conductors asked of it.
    """
    return _run(st.search_cable_references, manufacturer=manufacturer,
                reference=reference,
                description_contains=description_contains, limit=limit)


@mcp.tool
def list_harnesses() -> dict:
    """The project's harnesses: conductors that are physically bundled."""
    return _run(st.list_harnesses)


# --------------------------------------------------------------------------
# The project's own data: snapshots, PLC I/O, functions, cables. Snapshots
# come first because they are the only undo this server has: the project-wide
# passes below rewrite a whole project at once and nothing else puts it back.


@mcp.tool
def list_snapshots() -> dict:
    """The project's versions, newest first: id, name, created, size.

    These are real restore points, not a log. Taking one before any
    project-wide pass is the only undo available here.
    """
    return _run(pd.list_snapshots)


@mcp.tool
def create_snapshot(name: str, description: str | None = None,
                    generate_automated_drawings: bool = False) -> dict:
    """Take a restore point of the project as it stands.

    Worth doing before number_wires, number_marks, generate_arrows or
    renumber_components: those edit the whole project at once.
    """
    return _run(pd.create_snapshot, name=name, description=description,
                generate_automated_drawings=generate_automated_drawings)


@mcp.tool
def restore_snapshot(snapshot_id: int, confirm_name: str) -> dict:
    """Put the project back to a snapshot. ``confirm_name`` must match.

    Everything done since is discarded - drawings, components, numbering.
    Typing the name back is what stops a stale id rolling a project back by
    weeks.
    """
    return _run(pd.restore_snapshot, snapshot_id=snapshot_id,
                confirm_name=confirm_name)


@mcp.tool
def delete_snapshot(snapshot_id: int, confirm_name: str) -> dict:
    """Remove a snapshot. ``confirm_name`` must match its name."""
    return _run(pd.delete_snapshot, snapshot_id=snapshot_id,
                confirm_name=confirm_name)


@mcp.tool
def list_io(mnemonic_contains: str | None = None,
            description_contains: str | None = None,
            limit: int = 500) -> dict:
    """The project's PLC I/O channels.

    Each row ties a mnemonic and a channel address to the component circuit
    it sits on, which is the join between this drawing set and the PLC
    program that drives it.
    """
    return _run(pd.list_io, mnemonic_contains=mnemonic_contains,
                description_contains=description_contains, limit=limit)


@mcp.tool
def update_io(io_id: int, mnemonic: str | None = None,
              description: str | None = None, key_code: str | None = None,
              macro_name: str | None = None,
              function_id: int | None = None) -> dict:
    """Change one I/O channel; anything left null is untouched.

    The channel address is computed from the component and its circuit, so it
    is read-only: move a channel by changing what it is attached to.
    """
    return _run(pd.update_io, io_id=io_id, mnemonic=mnemonic,
                description=description, key_code=key_code,
                macro_name=macro_name, function_id=function_id)


@mcp.tool
def list_functions() -> dict:
    """The project's functional groups (the ``=`` part of a tag path).

    A location says where a device is; a function says what job it belongs
    to. Both prefix a component's full mark.
    """
    return _run(pd.list_functions)


@mcp.tool
def add_function(tag: str, description: str | None = None) -> dict:
    """Create a functional group."""
    return _run(pd.add_function, tag=tag, description=description)


@mcp.tool
def delete_function(function_id: int, confirm_tag: str) -> dict:
    """Remove a functional group. ``confirm_tag`` must match its tag.

    Components filed under it lose that part of their mark, so this changes
    how they are named.
    """
    return _run(pd.delete_function, function_id=function_id,
                confirm_tag=confirm_tag)


@mcp.tool
def update_cable(cable_id: int, description: str | None = None,
                 length: float | None = None,
                 fixed_length: bool | None = None,
                 diameter: float | None = None,
                 bend_radius: float | None = None,
                 colour: str | None = None, family: str | None = None,
                 supplier: str | None = None,
                 stock_number: str | None = None,
                 article_number: str | None = None,
                 linear_mass: str | None = None,
                 standard: str | None = None,
                 applied_voltage: float | None = None,
                 full_load_current: float | None = None,
                 voltage_drop: float | None = None,
                 upstream_location_id: int | None = None,
                 downstream_location_id: int | None = None,
                 function_id: int | None = None) -> dict:
    """Change one cable's properties; anything left null is untouched.

    ``fixed_length`` decides whether the length survives a routing
    recalculation: a cut length somebody measured on the boat is fixed, one
    the software worked out is not.
    """
    return _run(pd.update_cable, cable_id=cable_id, description=description,
                length=length, fixed_length=fixed_length, diameter=diameter,
                bend_radius=bend_radius, colour=colour, family=family,
                supplier=supplier, stock_number=stock_number,
                article_number=article_number, linear_mass=linear_mass,
                standard=standard, applied_voltage=applied_voltage,
                full_load_current=full_load_current,
                voltage_drop=voltage_drop,
                upstream_location_id=upstream_location_id,
                downstream_location_id=downstream_location_id,
                function_id=function_id)


@mcp.tool
def delete_cable(cable_id: int, confirm_tag: str) -> dict:
    """Remove a cable. ``confirm_tag`` must match its tag.

    Its cores go with it, so anything drawn as one of those cores loses its
    conductor.
    """
    return _run(pd.delete_cable, cable_id=cable_id, confirm_tag=confirm_tag)


# --------------------------------------------------------------------------
# Project-wide operations: numbering, arrows, terminal-strip drawings,
# reports, DWG export, and the wiring list. Each is a bulk edit with no undo,
# so each takes an explicit action and an explicit selection - try it on one
# page before turning it loose on the whole project.


@mcp.tool
def number_wires(action: str = "new", selection: str = "all",
                 book_id: int | None = None, folder_id: int | None = None,
                 pages: list | None = None,
                 file_ids: list[int] | None = None,
                 renumber_manual: bool = False,
                 reset_position: bool = False) -> dict:
    """Run the wire numbering pass.

    ``action``: "new" numbers only wires that have none (the safe one),
    "new_and_recalculate" also recomputes existing marks, "renumber" throws
    every wire number away and starts again, "remove" strips them.

    ``selection``: "all", "book" (+book_id), "folder" (+folder_id), or
    "folios" (+pages or file_ids). ``renumber_manual`` decides whether a
    number somebody typed by hand is fair game; it is off by default.
    """
    return _run(auto.number_wires, action=action, selection=selection,
                book_id=book_id, folder_id=folder_id, pages=pages,
                file_ids=file_ids, renumber_manual=renumber_manual,
                reset_position=reset_position)


@mcp.tool
def number_marks(action: str = "update",
                 object_types: list[str] | None = None,
                 start_number: int | None = None,
                 step_increment: int | None = None,
                 renumber_manual: bool = False) -> dict:
    """Run the component-mark numbering pass.

    ``action`` is "update" (fill in what has no mark) or "renumber" (assign
    every mark again). A renumber rewrites the tags the whole project
    cross-references.

    ``object_types``: component (the default), cable, terminal,
    terminal_strip, location, function, harness.
    """
    return _run(auto.number_marks, action=action, object_types=object_types,
                start_number=start_number, step_increment=step_increment,
                renumber_manual=renumber_manual)


@mcp.tool
def generate_arrows(action: str = "auto_connect", selection: str = "all",
                    book_id: int | None = None,
                    folder_id: int | None = None, pages: list | None = None,
                    file_ids: list[int] | None = None,
                    origin_symbol: str | None = None,
                    destination_symbol: str | None = None,
                    replace_manual: bool = False) -> dict:
    """Place, refresh or strip the origin/destination arrows.

    Arrows are how a wire leaving one sheet is picked up on another, so
    "auto_connect" is what turns a set of drawn pages into a wired project.
    "reconnect" refreshes them; "remove" breaks those links.
    """
    return _run(auto.generate_arrows, action=action, selection=selection,
                book_id=book_id, folder_id=folder_id, pages=pages,
                file_ids=file_ids, origin_symbol=origin_symbol,
                destination_symbol=destination_symbol,
                replace_manual=replace_manual)


@mcp.tool
def optimize_wire_order(selection: str = "all", book_id: int | None = None,
                        folder_id: int | None = None,
                        pages: list | None = None,
                        file_ids: list[int] | None = None,
                        remove_wire_cable_cores: bool = False,
                        remove_bridges: bool = False,
                        replace_manual: bool = False) -> dict:
    """Recompute the connection order within each equipotential.

    This decides which terminal a wire physically lands on when several share
    a potential, which is what makes a from-to wiring list buildable rather
    than merely correct.
    """
    return _run(auto.optimize_wire_order, selection=selection,
                book_id=book_id, folder_id=folder_id, pages=pages,
                file_ids=file_ids,
                remove_wire_cable_cores=remove_wire_cable_cores,
                remove_bridges=remove_bridges,
                replace_manual=replace_manual)


@mcp.tool
def generate_terminal_strip_drawings(component_ids: list[int] | None = None,
                                     book_id: int | None = None,
                                     keep_existing: bool = True) -> dict:
    """Draw the terminal-strip sheets for the strips in the project.

    A strip is a part-less parent component carrying numbered children, one
    per terminal, so pass the parents (CC_T1, CC_T2) and not their children.
    ``keep_existing=False`` deletes and regenerates, losing hand editing.
    """
    return _run(auto.generate_terminal_strip_drawings,
                component_ids=component_ids, book_id=book_id,
                keep_existing=keep_existing)


@mcp.tool
def export_dwg(output_dir: str, pages: list | None = None,
               file_ids: list[int] | None = None, all_pages: bool = False,
               save_type: str = "dwg", dwg_version: str = "2018",
               single_file: bool = False,
               file_name_formula: str | None = None,
               generate_automated_drawings: bool = False) -> dict:
    """Export folios as DWG or DXF, which is how a project leaves here.

    ``save_type`` is dwg, dxf or dxb; ``dwg_version`` one of 2000, 2004,
    2007, 2010, 2013, 2018. ``single_file`` packs every folio into one
    drawing instead of a file per page.

    A file-per-page export will not run without a naming formula, and the
    formula takes a bare variable name - ``FILE_TAG`` names each file after
    its page mark - not the percent-delimited form used elsewhere, so one is
    supplied by default. Files land in a subfolder tree under output_dir and
    are reported relative to it.
    """
    return _run(auto.export_dwg, output_dir=output_dir, pages=pages,
                file_ids=file_ids, all_pages=all_pages, save_type=save_type,
                dwg_version=dwg_version, single_file=single_file,
                file_name_formula=file_name_formula,
                generate_automated_drawings=generate_automated_drawings)


@mcp.tool
def list_reports() -> dict:
    """The report configurations attached to the project.

    These are the saved queries - bill of materials, wire list, terminal
    list, cable list - that ``export_reports`` and
    ``generate_report_drawings`` run.
    """
    return _run(auto.list_reports)


@mcp.tool
def export_reports(output_dir: str, report_ids: list[int] | None = None,
                   all_reports: bool = False, file_format: str = "xlsx",
                   include_column_header: bool = True,
                   one_sheet_per_break: bool = False,
                   add_to_project: bool = False) -> dict:
    """Export the project's reports to xlsx, xls, csv, txt or xml.

    Known not to deliver on SOLIDWORKS Electrical 2025 SP5: every writer
    returns success and produces no file, whatever the format and however
    the target folder and report ids are set. The result is judged by what
    lands on disk, so such a run reports ok=false with an explanation rather
    than a success. Use ``generate_report_drawings`` to put a report into
    the project as a folio instead.
    """
    return _run(auto.export_reports, output_dir=output_dir,
                report_ids=report_ids, all_reports=all_reports,
                file_format=file_format,
                include_column_header=include_column_header,
                one_sheet_per_break=one_sheet_per_break,
                add_to_project=add_to_project)


@mcp.tool
def generate_report_drawings(report_ids: list[int] | None = None,
                             all_reports: bool = False,
                             book_or_folder_id: int | None = None) -> dict:
    """Render reports as folios in the project rather than to a file.

    This puts the bill of materials or the wire list into the document tree
    as printable pages, so the drawing set carries its own tables.
    """
    return _run(auto.generate_report_drawings, report_ids=report_ids,
                all_reports=all_reports, book_or_folder_id=book_or_folder_id)


@mcp.tool
def list_wires(mark_contains: str | None = None,
               equipotential_contains: str | None = None,
               component_id: int | None = None, limit: int = 500) -> dict:
    """Every wire as a from-to row: both ends, component and terminal.

    A wire here is the logical conductor, not a line on a page. Each row
    carries mark, equipotential, signal, colour, section, length, cable, and
    both ends, which makes this a wiring list somebody can build from.
    ``component_id`` narrows it to what lands on one device.
    """
    return _run(auto.list_wires, mark_contains=mark_contains,
                equipotential_contains=equipotential_contains,
                component_id=component_id, limit=limit)


@mcp.tool
def find_wire(wire_id: int) -> dict:
    """One wire in full, by id."""
    return _run(auto.find_wire, wire_id=wire_id)


@mcp.tool
def update_wire(wire_id: int, mark: str | None = None,
                signal: str | None = None, colour: str | None = None,
                section_or_gauge: str | None = None,
                diameter: float | None = None, length: float | None = None,
                fixed_length: bool | None = None) -> dict:
    """Change one wire's properties; anything left null is untouched.

    Setting ``mark`` by hand makes it a manual number, which a later
    ``number_wires`` pass leaves alone unless told otherwise.
    """
    return _run(auto.update_wire, wire_id=wire_id, mark=mark, signal=signal,
                colour=colour, section_or_gauge=section_or_gauge,
                diameter=diameter, length=length, fixed_length=fixed_length)


# --------------------------------------------------------------------------
# The environment library: the catalogue of manufacturer parts and the symbol
# set components are drawn from. A job that needs a device the library does
# not carry stops dead until one is authored, so this is a write surface as
# much as a read one.


@mcp.tool
def search_manufacturer_parts(manufacturer: str | None = None,
                              reference: str | None = None,
                              description_contains: str | None = None,
                              library_code: str | None = None,
                              part_type: str | None = None,
                              limit: int = 50,
                              refresh: bool = False) -> dict:
    """Search the manufacturer-part catalogue; every filter is a substring.

    ``part_type`` is exact: base, auxiliary, accessory, plc, plc_rack,
    plc_module, plc_module_with_interface, plc_interface_point,
    plc_interface_circuit, super_part, wire_accessory. The catalogue is
    cached per process; ``refresh`` rebuilds it after an edit made elsewhere.
    """
    return _run(lib.search_manufacturer_parts, manufacturer=manufacturer,
                reference=reference,
                description_contains=description_contains,
                library_code=library_code, part_type=part_type, limit=limit,
                refresh=refresh)


@mcp.tool
def get_manufacturer_part(manufacturer: str, reference: str) -> dict:
    """One part in full: dimensions, symbols, and its circuits/terminals.

    The circuits are the electrical half - what a component made from this
    part can be wired to. A part with no circuits places as a blank box.
    """
    return _run(lib.get_manufacturer_part, manufacturer=manufacturer,
                reference=reference)


@mcp.tool
def create_manufacturer_part(manufacturer: str, reference: str,
                             description: str | None = None,
                             part_type: str | None = None,
                             width_mm: float | None = None,
                             height_mm: float | None = None,
                             depth_mm: float | None = None,
                             weight: float | None = None,
                             library_code: str | None = None,
                             supplier: str | None = None,
                             article_number: str | None = None,
                             stock_number: str | None = None,
                             root_mark: str | None = None,
                             series: str | None = None,
                             scheme_symbol: str | None = None,
                             line_diagram_symbol: str | None = None,
                             footprint_symbol: str | None = None,
                             datasheet: str | None = None,
                             use_voltage: str | None = None,
                             control_voltage: str | None = None,
                             circuits: list[dict] | None = None,
                             replace: bool = False) -> dict:
    """Author a manufacturer part, with its circuits and terminals.

    ``circuits`` is a list of ``{"code": "PID", "terminals": [{"text":
    "X12:3", "mnemonic": "DI#1"}]}``. Codes are the environment's circuit
    types: PID digital in, POD digital out, PIA analog in, POA analog out,
    777/888 a plain terminal.

    The width/height/depth are the real body dimensions, which is what a
    cabinet layout reasons about - worth getting right even when a vendor
    footprint drawing shows something larger.
    """
    return _run(lib.create_manufacturer_part, manufacturer=manufacturer,
                reference=reference, description=description,
                part_type=part_type, width_mm=width_mm, height_mm=height_mm,
                depth_mm=depth_mm, weight=weight, library_code=library_code,
                supplier=supplier, article_number=article_number,
                stock_number=stock_number, root_mark=root_mark,
                series=series, scheme_symbol=scheme_symbol,
                line_diagram_symbol=line_diagram_symbol,
                footprint_symbol=footprint_symbol, datasheet=datasheet,
                use_voltage=use_voltage, control_voltage=control_voltage,
                circuits=circuits, replace=replace)


@mcp.tool
def update_manufacturer_part(manufacturer: str, reference: str,
                             description: str | None = None,
                             part_type: str | None = None,
                             width_mm: float | None = None,
                             height_mm: float | None = None,
                             depth_mm: float | None = None,
                             weight: float | None = None,
                             library_code: str | None = None,
                             supplier: str | None = None,
                             article_number: str | None = None,
                             stock_number: str | None = None,
                             root_mark: str | None = None,
                             series: str | None = None,
                             scheme_symbol: str | None = None,
                             line_diagram_symbol: str | None = None,
                             footprint_symbol: str | None = None,
                             datasheet: str | None = None,
                             use_voltage: str | None = None,
                             control_voltage: str | None = None) -> dict:
    """Change fields on an existing part; anything left null is untouched.

    Circuits are not editable here: adding one to a part that components
    already reference changes what those components offer, so rebuild with
    ``create_manufacturer_part(replace=True)`` instead.
    """
    return _run(lib.update_manufacturer_part, manufacturer=manufacturer,
                reference=reference, description=description,
                part_type=part_type, width_mm=width_mm, height_mm=height_mm,
                depth_mm=depth_mm, weight=weight, library_code=library_code,
                supplier=supplier, article_number=article_number,
                stock_number=stock_number, root_mark=root_mark,
                series=series, scheme_symbol=scheme_symbol,
                line_diagram_symbol=line_diagram_symbol,
                footprint_symbol=footprint_symbol, datasheet=datasheet,
                use_voltage=use_voltage, control_voltage=control_voltage)


@mcp.tool
def delete_manufacturer_part(manufacturer: str, reference: str,
                             confirm_reference: str) -> dict:
    """Remove a part from the catalogue. ``confirm_reference`` must match.

    Components already carrying it keep the assignment as a dangling
    reference, so check ``list_components(with_parts=True)`` first.
    """
    return _run(lib.delete_manufacturer_part, manufacturer=manufacturer,
                reference=reference, confirm_reference=confirm_reference)


@mcp.tool
def search_symbols(name: str | None = None, symbol_type: str | None = None,
                   manufacturer: str | None = None,
                   reference: str | None = None,
                   library_code: str | None = None,
                   limit: int = 50, refresh: bool = False) -> dict:
    """Search the symbol library; every filter is a substring except type.

    ``symbol_type`` is exact: component, blackbox, 2d_footprint,
    terminal_drawing, connection, xref, passive, pid, synoptic and so on.
    The first call in a process sweeps the library (20 to 30 seconds for
    1700 symbols) because the API's own symbol filter returns nothing; after
    that it is cached.
    """
    return _run(lib.search_symbols, name=name, symbol_type=symbol_type,
                manufacturer=manufacturer, reference=reference,
                library_code=library_code, limit=limit, refresh=refresh)


@mcp.tool
def get_symbol(name: str) -> dict:
    """One symbol in full, by its exact library name."""
    return _run(lib.get_symbol, name=name)


@mcp.tool
def import_symbol(name: str, drawing_path: str,
                  symbol_type: str = "2d_footprint",
                  library_code: str | None = None,
                  description: str | None = None,
                  manufacturer: str | None = None,
                  reference: str | None = None,
                  root_mark: str | None = None,
                  replace: bool = False) -> dict:
    """Create a library symbol from a DWG or DXF file.

    insertFromDwg takes DXF as happily as DWG, which makes a hand-written DXF
    a practical way to author a footprint at the dimensions a part really has
    rather than the ones a vendor drawing happens to show.

    Attributes live in the drawing, not in the API - there is no way to add
    one afterwards - so a symbol that should print its component mark needs a
    ``#TAG`` ATTDEF in the file before import. ``#TAG`` is the one SOLIDWORKS
    resolves to the mark; ``#MARK``, ``#COMPONENT_TAG`` and the other obvious
    guesses render as their own literal text.
    """
    return _run(lib.import_symbol, name=name, drawing_path=drawing_path,
                symbol_type=symbol_type, library_code=library_code,
                description=description, manufacturer=manufacturer,
                reference=reference, root_mark=root_mark, replace=replace)


@mcp.tool
def delete_symbol(name: str, confirm_name: str) -> dict:
    """Remove a symbol from the library. ``confirm_name`` must match.

    Parts pointing at it keep the name as a dangling reference and pages
    already drawn keep their copy of the geometry, so this breaks future
    placements rather than existing drawings.
    """
    return _run(lib.delete_symbol, name=name, confirm_name=confirm_name)


@mcp.tool
def list_libraries() -> dict:
    """The library codes parts and symbols are filed under, with part counts."""
    return _run(lib.list_libraries)


# --------------------------------------------------------------------------
# Project lifecycle. Every other tool acts on "the open project"; these pick
# which project that is, and create, archive or retire one.


@mcp.tool
def list_projects(name_contains: str | None = None,
                  project_type: str | None = "project") -> dict:
    """Every project in the environment, open or not, newest change first.

    Works with nothing open, so this is where a session starts. Each row:
    id, name, description, customer, contract_number, created_by,
    modified_by, modification_date, open_by_me, open_by_another, is_current.
    ``project_type`` filters "project" vs "macro"; pass null for both.
    """
    return _run(pj.list_projects, name_contains=name_contains,
                project_type=project_type)


@mcp.tool
def open_project(project_id: int | None = None,
                 name: str | None = None) -> dict:
    """Open a project and make it the one every other tool acts on.

    Give an id from ``list_projects``, or a name - exact first, then a unique
    substring, so "65021" resolves without the full mark. Refuses a project
    another user holds open.
    """
    return _run(pj.open_project, project_id=project_id, name=name)


@mcp.tool
def close_project(project_id: int | None = None,
                  name: str | None = None) -> dict:
    """Close a project; with no argument, close whichever one is current.

    Closing is how SOLIDWORKS commits a project's pending state and releases
    it for other users, so it belongs at the end of a session rather than
    being left to the GUI.
    """
    return _run(pj.close_project, project_id=project_id, name=name)


@mcp.tool
def list_project_templates() -> dict:
    """The template names ``create_project`` accepts (ANSI, IEC, JIS ...)."""
    return _run(pj.list_project_templates)


@mcp.tool
def create_project(name: str, template: str | None = None,
                   description: str | None = None,
                   customer: str | None = None,
                   contract_number: str | None = None,
                   open_after: bool = False) -> dict:
    """Create a project, optionally from one of the shipped templates.

    The template carries the drawing standard - symbol set, wire styles,
    title blocks, page format - so a project made without one starts bare and
    has to be configured by hand. Refuses a name that already exists.
    """
    return _run(pj.create_project, name=name, template=template,
                description=description, customer=customer,
                contract_number=contract_number, open_after=open_after)


@mcp.tool
def delete_project(project_id: int, confirm_name: str) -> dict:
    """Permanently remove a project. ``confirm_name`` must match its name.

    There is no undo: drawings, components and the database row all go.
    Typing the name back is what stops a stale id deleting the wrong project.
    """
    return _run(pj.delete_project, project_id=project_id,
                confirm_name=confirm_name)


@mcp.tool
def archive_project(output_path: str, project_id: int | None = None,
                    name: str | None = None,
                    with_dependencies: bool = True) -> dict:
    """Write a project out as a .tewzip archive (default: the open one).

    ``with_dependencies`` also packs the library content the project
    references (symbols, parts, title blocks), which is what makes the
    archive restorable on a machine with a different environment.
    """
    return _run(pj.archive_project, output_path=output_path,
                project_id=project_id, name=name,
                with_dependencies=with_dependencies)


@mcp.tool
def unarchive_project(archive_path: str,
                      with_dependencies: bool = True) -> dict:
    """Restore a .tewzip archive and report which project it became."""
    return _run(pj.unarchive_project, archive_path=archive_path,
                with_dependencies=with_dependencies)


@mcp.tool
def project_properties(project_id: int | None = None,
                       name: str | None = None,
                       description: str | None = None,
                       customer: str | None = None,
                       customer_address1: str | None = None,
                       customer_address2: str | None = None,
                       customer_address3: str | None = None,
                       drawing_office: str | None = None,
                       drawing_office_address1: str | None = None,
                       drawing_office_address2: str | None = None,
                       drawing_office_address3: str | None = None,
                       contract_number: str | None = None,
                       extern_id: str | None = None) -> dict:
    """Read, and optionally set, the title-block fields of a project.

    Anything left null is read rather than written. These feed the title
    block, so run ``regenerate_title_blocks`` afterwards to see a change on
    the sheets. The project name is not settable here - use
    ``rename_project``.
    """
    return _run(pj.project_properties, project_id=project_id, name=name,
                description=description, customer=customer,
                customer_address1=customer_address1,
                customer_address2=customer_address2,
                customer_address3=customer_address3,
                drawing_office=drawing_office,
                drawing_office_address1=drawing_office_address1,
                drawing_office_address2=drawing_office_address2,
                drawing_office_address3=drawing_office_address3,
                contract_number=contract_number, extern_id=extern_id)


@mcp.tool
def project_info() -> dict:
    """Name, id, customer, folder path and object counts of the open project."""
    return _run(wf.project_info)


@mcp.tool
def list_folios(book_id: int | None = None, folder_id: int | None = None,
                file_type: str | None = None,
                description_contains: str | None = None) -> dict:
    """List the project's pages (folios) in document-tree order.

    Each row: id, page (the printed page mark), page_number, description,
    file_type (cover_page / mixed_scheme / line_diagram / 2d_cabinet_layout /
    terminal / bom ...), position, book_id, folder_id, location_id, is_open.
    Filter by book, folder, file_type name, or a substring of the description.
    """
    return _run(wf.list_folios, book_id=book_id, folder_id=folder_id,
                file_type=file_type, description_contains=description_contains)


@mcp.tool
def find_folio(page: str | int | None = None,
               file_id: int | None = None) -> dict:
    """Resolve one folio by page mark (e.g. "61" or "07") or by file id."""
    def _go(app, client):
        return wf._folio_row(wf.find_folio(app, client, page=page,
                                           file_id=file_id))
    return _run(_go)


@mcp.tool
def list_locations() -> dict:
    """All locations (+L1, +L2 ...) with id, tag, tag path and description."""
    return _run(wf.list_locations)


@mcp.tool
def list_books_and_folders() -> dict:
    """Books and folders of the document tree (ids, tags, descriptions)."""
    return _run(wf.list_books_and_folders)


@mcp.tool
def list_components(tag_contains: str | None = None,
                    location_id: int | None = None,
                    parent_id: int | None = None, with_parts: bool = True,
                    limit: int = 200) -> dict:
    """List components (devices) with tag, tag path, description, parent,
    location and their assigned manufacturer parts.

    Filter by a substring of the tag/tag path (e.g. "N1N" for the WAGO
    modules, "K" for relays), by location id, or by parent component id
    (children of a coupler). ``limit`` caps the rows returned (0 = no cap);
    ``matched`` and ``truncated`` say whether more exist.
    """
    return _run(wf.list_components, tag_contains=tag_contains,
                location_id=location_id, parent_id=parent_id,
                with_parts=with_parts, limit=limit)


@mcp.tool
def find_component(tag: str) -> dict:
    """Find components by exact tag ("A1", "-A1") or full tag path
    ("=F1+L1+L4+L2-A1"), with their manufacturer parts."""
    return _run(wf.find_component, tag=tag)


@mcp.tool
def list_cables(limit: int = 500) -> dict:
    """All cables (W1 ...) with reference, manufacturer, cores, length and
    upstream/downstream location ids."""
    return _run(wf.list_cables, limit=limit)


@mcp.tool
def folio_symbols(page: str | int | None = None,
                  file_id: int | None = None) -> dict:
    """What is drawn on one page: every symbol with its library name, type
    (component / blackbox / connection / link ...), linked component tag path,
    position and rotation. Give the page mark or the file id."""
    return _run(wf.folio_symbols, page=page, file_id=file_id)


@mcp.tool
def export_folio_pdf(output_path: str, pages: list[str | int] | None = None,
                     file_ids: list[int] | None = None,
                     all_pages: bool = False) -> dict:
    """Export selected pages (by page mark or file id), or the whole project,
    to one PDF at ``output_path``. The folder is created if missing. Read the
    resulting PDF to see the drawing and verify a change visually."""
    return _run(wf.export_folio_pdf, output_path=output_path, pages=pages,
                file_ids=file_ids, all_pages=all_pages)


@mcp.tool
def regenerate_title_blocks() -> dict:
    """Refresh every title block from project data (after renaming the
    project, renumbering pages, etc.). Fails with EW_PROJECT_OPENED (45) while
    drawings are open in the GUI; the result lists the open folios."""
    return _run(wf.regenerate_title_blocks)


@mcp.tool
def rename_project(new_name: str) -> dict:
    """Rename the open project (drives the cover-sheet title)."""
    return _run(wf.rename_project, new_name=new_name)


@mcp.tool
def close_and_reopen_folio(page: str | int | None = None,
                           file_id: int | None = None) -> dict:
    """Close and reopen one folio so the GUI redraws it (after moving
    symbols or refreshing title-block data)."""
    return _run(wf.close_and_reopen_folio, page=page, file_id=file_id)


@mcp.tool
def clone_component(source_tag: str, new_tag: str,
                    page: str | int | None = None, file_id: int | None = None,
                    offset_x: float | None = None, offset_y: float = 0.0,
                    dry_run: bool = True) -> dict:
    """Clone a component into a new tag: "On sheet 10, clone K32 into K33".

    Copies tag-independent data (description, location, parent, class,
    function, manufacturer parts) to a new component tagged ``new_tag``. If a
    page is given, every symbol of the source on that page is copied for the
    new component, shifted by ``offset_x``/``offset_y`` (default: the next
    free slot at the pitch of the neighbouring symbols). The new tag must
    keep the source's tag root (MR rule: relays are always K). ``dry_run``
    (default True) returns the plan only; call again with ``dry_run=False``
    to write. The result carries an ``undo`` hint.
    """
    return _run(wf.clone_component, source_tag=source_tag, new_tag=new_tag,
                page=page, file_id=file_id, offset_x=offset_x,
                offset_y=offset_y, dry_run=dry_run)


@mcp.tool
def delete_component(component_id: int | None = None,
                     tag: str | None = None,
                     pages: list[str | int] | None = None,
                     close_gap: bool = False) -> dict:
    """Remove a component by id or by unambiguous tag. If symbols block the
    remove they are deleted first: from ``pages`` when given (fast), else
    from every folio (slow on a large project).

    ``close_gap`` pulls the rest of the rail back over the hole, which is the
    undo for an ``add_component(..., shift_following=True)`` insert."""
    return _run(wf.delete_component, component_id=component_id, tag=tag,
                pages=pages, close_gap=close_gap)


@mcp.tool
def add_component(tag: str, manufacturer: str, reference: str,
                  description: str | None = None,
                  location_tag: str | None = None,
                  location_id: int | None = None,
                  parent_tag: str | None = None,
                  page: str | int | None = None, file_id: int | None = None,
                  x: float | None = None, y: float | None = None,
                  after_tag: str | None = None,
                  shift_following: bool = False,
                  symbol_name: str | None = None,
                  allow_new_root: bool = False,
                  dry_run: bool = True) -> dict:
    """Build a component from scratch: "add an ABB ESB20-11N-01 as K39 on
    sheet 10 after K38".

    The counterpart to clone_component when there is no unit to copy. The
    symbol comes from the manufacturer part's own library symbol rather than
    from a source component, and the position comes from ``x``/``y`` or from
    ``after_tag`` (the next free slot after that component's symbol). Scale
    and rotation are taken from an existing symbol of the same kind on the
    page, because a cabinet footprint is drawn scaled to the part's real
    millimetres and would otherwise land at scale 1.

    The part must already be in the project catalogue. If other components
    already use it, the new tag must share their tag root, which keeps a
    relay rooted K without a source to inherit from.

    ``shift_following`` INSERTS into a rail instead of appending: the device
    goes directly after ``after_tag`` and everything further right on that
    row is pushed along by one device pitch. Use it when the neighbours are
    adjacent, e.g. adding a relay between K29 and K30.

    ``dry_run`` is the default and returns the plan, including every symbol
    that would move; the result carries an ``undo`` hint.
    """
    return _run(wf.add_component, tag=tag, manufacturer=manufacturer,
                reference=reference, description=description,
                location_tag=location_tag, location_id=location_id,
                parent_tag=parent_tag, page=page, file_id=file_id, x=x, y=y,
                after_tag=after_tag, shift_following=shift_following,
                symbol_name=symbol_name, allow_new_root=allow_new_root,
                dry_run=dry_run)


@mcp.tool
def rename_component(tag: str, new_tag: str, scan_text: bool = True,
                     refresh_folios: bool = True,
                     dry_run: bool = True) -> dict:
    """Rename a component and account for every reference to it.

    Drawn instances, cross-references between pages and the BOM are linked
    by component id, so they follow the new mark on their own and the
    affected pages are simply re-rendered. The mark spelled out as literal
    TEXT does not follow, so every such place is listed in
    ``text_references`` for a human to judge instead of being rewritten
    blindly. The new mark must keep the tag root, so a relay stays rooted K.

    ``dry_run`` is the default and returns the plan, including every symbol
    that will follow and every text reference that will not.
    """
    return _run(wf.rename_component, tag=tag, new_tag=new_tag,
                scan_text=scan_text, refresh_folios=refresh_folios,
                dry_run=dry_run)


@mcp.tool
def renumber_components(renames: list, scan_text: bool = True,
                        refresh_folios: bool = True,
                        dry_run: bool = True) -> dict:
    """Retag a run of devices in one pass: [["K31","K41"],["K32","K42"], ...].

    Renaming a run one at a time is not safe once the old and new marks
    overlap, because an intermediate step collides with a mark still in use.
    Every target is validated first, and overlapping sets are parked on
    temporary marks and then moved into place. The text scan runs once for
    the whole run rather than once per device.

    Symbols and cross-references follow by component id; literal text does
    not and is reported. ``dry_run`` is the default.
    """
    return _run(wf.renumber_components, renames=renames, scan_text=scan_text,
                refresh_folios=refresh_folios, dry_run=dry_run)


@mcp.tool
def audit_tag_roots(tag_contains: str | None = None,
                    fix: bool = False) -> dict:
    """Find components whose stored tag root/number disagree with their mark.

    The mark is what the drawings show; SOLIDWORKS renumbers from the
    separate TagRoot and TagNumber. When they disagree a device reads
    correctly on every sheet while being filed under another class, and a GUI
    renumber can move it out of its series. ``fix`` writes the root and
    number implied by the mark and leaves the mark itself alone.
    """
    return _run(wf.audit_tag_roots, tag_contains=tag_contains, fix=fix)


@mcp.tool
def rename_folder(tag: str | None = None, folder_id: int | None = None,
                  book_id: int | None = None, new_tag: str | None = None,
                  new_description: str | None = None,
                  dry_run: bool = True) -> dict:
    """Retag or re-describe a folder in the document tree.

    The tree shows "<tag> - <description>", so renaming "7 - Reports" to
    "8 - Reports" changes the tag alone. Folder tags are unique within a
    book, so the target is checked before anything is written.
    """
    return _run(wf.rename_folder, tag=tag, folder_id=folder_id,
                book_id=book_id, new_tag=new_tag,
                new_description=new_description, dry_run=dry_run)


@mcp.tool
def add_folder(tag: str, description: str, book_id: int | None = None,
               book_tag: str | None = None,
               parent_folder_id: int | None = None,
               position: int | None = None, after_tag: str | None = None,
               dry_run: bool = True) -> dict:
    """Create a folder in the document tree, e.g. "7 - CC100 Enclosure".

    Give ``after_tag`` to slot it directly behind an existing folder; the
    tree order is driven by an internal position index, not by the tag.
    """
    return _run(wf.add_folder, tag=tag, description=description,
                book_id=book_id, book_tag=book_tag,
                parent_folder_id=parent_folder_id, position=position,
                after_tag=after_tag, dry_run=dry_run)


@mcp.tool
def delete_folder(folder_id: int) -> dict:
    """Remove an empty folder. Refuses while it still holds folios."""
    return _run(wf.delete_folder, folder_id=folder_id)


@mcp.tool
def add_folio(description: str, file_type: str = "folio",
              folder_id: int | None = None, book_id: int | None = None,
              location_id: int | None = None,
              page_number: int | None = None,
              insert_before_page: int | None = None,
              dry_run: bool = True) -> dict:
    """Create a page, optionally slotting it in at a given page number.

    ``file_type`` is a name such as "2d_cabinet_layout", "mixed_scheme" or
    "line_diagram". ``insert_before_page`` gives the new page that number and
    pushes every page from there on one number down, using the same
    collision-safe cascade as shift_folio_numbers.
    """
    return _run(wf.add_folio, description=description, file_type=file_type,
                folder_id=folder_id, book_id=book_id,
                location_id=location_id, page_number=page_number,
                insert_before_page=insert_before_page, dry_run=dry_run)


@mcp.tool
def delete_folio(file_id: int) -> dict:
    """Remove a page. Refuses while it still carries symbols."""
    return _run(wf.delete_folio, file_id=file_id)


@mcp.tool
def move_symbol(symbol_id: int, dx: float = 0.0, dy: float = 0.0,
                to_x: float | None = None, to_y: float | None = None,
                move_lines: bool = True, box: dict | None = None,
                dry_run: bool = True) -> dict:
    """Move a symbol and drag every wire end sitting on its connections.

    Lines do not follow a symbol on their own: moving one alone leaves the
    wires behind, so the sheet shows a break while the database still says
    connected. The move is refused if it would put a connection point or a
    line end outside the drawable box.
    """
    return _run(wf.move_symbol, symbol_id=symbol_id, dx=dx, dy=dy, to_x=to_x,
                to_y=to_y, move_lines=move_lines, box=box, dry_run=dry_run)


@mcp.tool
def check_drawing_rules(page: str | int | None = None,
                        file_id: int | None = None,
                        min_spacing: float | None = None,
                        symbol_spacing: float | None = None,
                        box: dict | None = None) -> dict:
    """Report crowding, colliding labels and anything outside the box.

    Three checks: connection points of different symbols sharing a row or
    column closer than ``min_spacing`` (default one grid step, 10 mm - the
    dot-to-dot rule), including points that coincide exactly; symbols
    sharing a row whose ORIGINS are closer than ``symbol_spacing`` (default
    30 mm, because a relay label is three grid pitches wide, so labels
    collide long before the points do - not applied on a 2D cabinet layout,
    where devices are drawn at real width and legitimately abut); and any
    point or line end outside the drawable box. Reports only.
    """
    return _run(wf.check_drawing_rules, page=page, file_id=file_id,
                min_spacing=min_spacing, symbol_spacing=symbol_spacing,
                box=box)


@mcp.tool
def check_page_ink(page: str | int | None = None,
                   file_id: int | None = None,
                   box: dict | None = None,
                   sheet_width_mm: float | None = None,
                   max_frame_mm: float = 250.0,
                   frame_thickness_mm: float = 3.0) -> dict:
    """Measure what is actually DRAWN on a folio and flag ink outside the box.

    Use this alongside ``check_drawing_rules``, which only sees connection
    points and line ends. A 2D footprint imported from a DWG reports
    ``getWidth`` 0 and carries no connection points, so a footprint whose
    body hangs outside the drawable box passes that check even though the
    exported sheet plainly shows it hanging out. This exports the folio and
    measures the real vector ink, ignoring the sheet frame and title block.

    Returns the drawn extent in page millimetres plus, per side, how far it
    overflows the box. Requires PyMuPDF.

    Frame geometry is anything longer than ``max_frame_mm`` that is also
    thinner than ``frame_thickness_mm`` - a border rule is long AND thin.
    Raise ``max_frame_mm`` on a sheet carrying a device bigger than it (the
    AN-2823-AB enclosure is 260 mm wide) if that device is being skipped.
    """
    return _run(wf.check_page_ink, page=page, file_id=file_id, box=box,
                sheet_width_mm=sheet_width_mm, max_frame_mm=max_frame_mm,
                frame_thickness_mm=frame_thickness_mm)


@mcp.tool
def place_symbols(placements: list[dict], page: str | int | None = None,
                  file_id: int | None = None, box: dict | None = None,
                  dry_run: bool = True) -> dict:
    """Draw several components on ONE page, closing the folio only once.

    PREFER THIS over repeated place_symbol calls whenever more than one
    device goes on the same page. place_symbol closes and reopens the folio
    around every insert, and that per-device churn of the editor tab can
    take SOLIDWORKS Electrical down. Open and close per TASK, not per unit.

    Each placement is a dict with tag, symbol_name, x, y, and optionally
    symbol_type (default 20), rotation, x_scale, y_scale.
    """
    return _run(wf.place_symbols, placements=placements, page=page,
                file_id=file_id, box=box, dry_run=dry_run)


@mcp.tool
def remove_symbols(symbol_ids: list[int] | None = None,
                   page: str | int | None = None,
                   file_id: int | None = None,
                   all_on_page: bool = False) -> dict:
    """Delete several drawn symbols, closing the folio only once.

    PREFER THIS over repeated remove_symbol calls. Pass all_on_page=True
    with a page to clear the whole folio. Open and close per TASK, not per
    unit: per-symbol churn of the editor tab can take SOLIDWORKS Electrical
    down.
    """
    return _run(wf.remove_symbols, symbol_ids=symbol_ids, page=page,
                file_id=file_id, all_on_page=all_on_page)


@mcp.tool
def add_texts(texts: list[dict], page: str | int | None = None,
              file_id: int | None = None, box: dict | None = None,
              dry_run: bool = True) -> dict:
    """Put several free texts on ONE page, closing the folio only once.

    PREFER THIS over repeated add_text calls. Annotating a page runs to
    dozens of texts and one editor cycle each can take SOLIDWORKS Electrical
    down. Each entry is a dict with text, x, y and optionally rotation.
    """
    return _run(wf.add_texts, texts=texts, page=page, file_id=file_id,
                box=box, dry_run=dry_run)


@mcp.tool
def remove_texts(text_ids: list[int] | None = None,
                 page: str | int | None = None,
                 file_id: int | None = None,
                 all_on_page: bool = False) -> dict:
    """Delete several free texts, closing the folio only once.

    PREFER THIS over repeated remove_text calls, which also cost a
    project-wide scan per text. Pass all_on_page=True with a page to clear
    every text off a folio.
    """
    return _run(wf.remove_texts, text_ids=text_ids, page=page,
                file_id=file_id, all_on_page=all_on_page)


@mcp.tool
def add_location(tag: str, description: str,
                 parent_location_id: int | None = None,
                 dry_run: bool = True) -> dict:
    """Create a location, e.g. "L6" / "CC100 Enclosure"."""
    return _run(wf.add_location, tag=tag, description=description,
                parent_location_id=parent_location_id, dry_run=dry_run)


@mcp.tool
def attach_manufacturer_part(tag: str, manufacturer: str, reference: str,
                             description: str | None = None,
                             width: float | None = None,
                             height: float | None = None,
                             depth: float | None = None,
                             dry_run: bool = True) -> dict:
    """Give a component a manufacturer part the library does not carry.

    assignManufacturerPart resolves against the environment catalogue and
    answers EW_BAD_INPUTS (2) for anything not in it. This creates the
    project part and binds it to the component instead, which is the same
    link the component reads its parts back through.
    """
    return _run(wf.attach_manufacturer_part, tag=tag,
                manufacturer=manufacturer, reference=reference,
                description=description, width=width, height=height,
                depth=depth, dry_run=dry_run)


@mcp.tool
def place_symbol(tag: str, symbol_name: str, x: float, y: float,
                 page: str | int | None = None, file_id: int | None = None,
                 symbol_type: int = 20, rotation: float = 0.0,
                 x_scale: float | None = None, y_scale: float | None = None,
                 box: dict | None = None, dry_run: bool = True) -> dict:
    """Draw an EXISTING component on a page.

    The counterpart to add_component when the device already exists: a
    device is normally drawn several times, its footprint on the cabinet
    layout and a contact or coil on each schematic that uses it. Note the
    page type constrains the symbol type: a 2D cabinet layout takes
    footprints (105) and returns NULL for a black box (30).
    """
    return _run(wf.place_symbol, tag=tag, symbol_name=symbol_name, x=x, y=y,
                page=page, file_id=file_id, symbol_type=symbol_type,
                rotation=rotation, x_scale=x_scale, y_scale=y_scale, box=box,
                dry_run=dry_run)


@mcp.tool
def remove_symbol(symbol_id: int) -> dict:
    """Delete one drawn symbol, closing the folio first if the GUI has it."""
    return _run(wf.remove_symbol, symbol_id=symbol_id)


@mcp.tool
def move_symbols(moves: list, box: dict | None = None,
                 dry_run: bool = True) -> dict:
    """Move several symbols on ONE folio in a single pass.

    Prefer this to repeated move_symbol: the project line array is the only
    route to a folio's lines, so moving one at a time walks every line in
    the project per symbol and closes and reopens the folio each time. Here
    the lines are read once and the folio is closed once for the batch.
    ``moves`` is a list of {"symbol_id", "dx", "dy"}.
    """
    return _run(wf.move_symbols, moves=moves, box=box, dry_run=dry_run)


@mcp.tool
def add_text(text: str, x: float, y: float, page: str | int | None = None,
             file_id: int | None = None, rotation: float = 0.0,
             box: dict | None = None, dry_run: bool = True) -> dict:
    """Put a free text on a page: a circuit number, a wire spec, a note.

    Text is the exception to this project's usual create order. Inserting an
    EMPTY text answers EW_BAD_INPUTS (2) and leaves it at id -1, so the
    content and position are set BEFORE the insert, not after.
    """
    return _run(wf.add_text, text=text, x=x, y=y, page=page, file_id=file_id,
                rotation=rotation, box=box, dry_run=dry_run)


@mcp.tool
def list_texts(page: str | int | None = None,
               file_id: int | None = None) -> dict:
    """Every free text on a page, with id, content and position."""
    return _run(wf.list_texts, page=page, file_id=file_id)


@mcp.tool
def remove_text(text_id: int) -> dict:
    """Delete one free text by id."""
    return _run(wf.remove_text, text_id=text_id)


@mcp.tool
def delete_location(location_id: int) -> dict:
    """Remove a location nothing references. Refuses while a component, a
    folio or a cable still points at it."""
    return _run(wf.delete_location, location_id=location_id)


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
