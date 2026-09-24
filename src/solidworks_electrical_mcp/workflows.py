"""Task-level operations on the open SOLIDWORKS Electrical project.

Every function here is one thing a user actually asks for ("list the pages",
"find component -A1", "export page 61 as PDF") assembled from the COM calls
that were validated live in June 2026. They run entirely on the COM worker
thread: ``ElectricalApp.run_workflow`` hands them the live ``IEwApplicationX``
root plus the pywin32 client, and everything they return is already JSON-safe.

Conventions
-----------
* Getters with an ``EwErrorCode`` out-param come back late-bound as
  ``(value, rc)``; ``_u`` unwraps them.
* Text getters that take a language use ``LANG`` ("en").
* Arrays are VARIANTs of raw ``PyIDispatch``; ``_each`` wraps each element so
  late binding works.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from typing import Any, Callable, Iterable

from .com import coerce_value

LANG = "en"

# EwFileType, read from the live typelib on 2025.5 (get_enum('EwFileType')).
FILE_TYPE_NAMES = {
    -2: "folder", -1: "unknown", 0: "folio", 1: "line_diagram", 2: "bom",
    3: "terminal", 4: "other", 5: "cover_page", 6: "sw_cabinet_layout",
    7: "appendix_dxf_dwg", 8: "2d_drawing_from_3d", 9: "2d_cabinet_layout",
    10: "flattened_route", 11: "design_rule", 12: "mixed_scheme", 13: "pid",
    14: "not_supported", 15: "fluid", 16: "exported_report",
    17: "exported_design_rule", 18: "physical_product",
    19: "sw_physical_product",
}

# EwSymbolType (get_enum('EwSymbolType')).
SYMBOL_TYPE_NAMES = {
    -1: "undefined", 20: "component", 25: "synoptic", 30: "blackbox",
    80: "connection", 85: "link", 90: "terminal_drawing", 95: "xref",
    100: "automate_drawing", 105: "2d_footprint", 110: "cable_label",
    115: "connection_label", 120: "wire_label", 125: "equipotential_label",
    130: "location_label", 135: "harness_label", 140: "passive", 150: "pid",
    160: "fluid", 161: "report",
}

# Symbol types whose getObjectID points at something other than a component
# (a location, cable, wire, equipotential or harness). Never remove one
# because its id happens to match a component id.
NON_COMPONENT_SYMBOL_TYPES = frozenset({110, 115, 120, 125, 130, 135})

EW_ERROR_NAMES = {
    0: "EW_NO_ERROR", 2: "EW_BAD_INPUTS", 3: "EW_FILE_NOT_FOUND",
    8: "EW_DOES_NOT_EXIST", 9: "EW_INVALID_OBJECT", 13: "EW_ALREADY_INSERTED",
    22: "EW_OBJECT_NOT_FOUND",
    34: "EW_INVALID_INDEX", 35: "EW_CANNOT_REMOVE", 37: "EW_FOLDER_NOT_FOUND",
    39: "EW_INVALID_LICENSE", 45: "EW_PROJECT_OPENED", 56: "EW_MISSING_MANDATORY",
}

# EwProjectDataObjectType / EwProjectDataActionType.
K_PROJECT_DATA_TITLE_BLOCK = 3
K_PROJECT_DATA_UPDATE = 0


def _u(v: Any) -> Any:
    """Unwrap a late-bound ``(value, rc)`` tuple to its value."""
    return v[0] if isinstance(v, tuple) else v


def _rc(v: Any) -> int | None:
    """Return the EwErrorCode of a ``(value, rc)`` tuple, or the bare int."""
    if isinstance(v, tuple):
        return v[-1]
    return v if isinstance(v, int) else None


def _rc_name(rc: int | None) -> str:
    if rc is None:
        return "n/a"
    return EW_ERROR_NAMES.get(rc, f"EwErrorCode {rc}")


def _each(client: Any, arr: Iterable[Any]) -> Iterable[Any]:
    for raw in arr or ():
        try:
            yield client.Dispatch(raw)
        except Exception:
            yield raw


def _project(app: Any) -> Any:
    proj = _u(app.getEwProjectCurrent())
    if proj is None:
        raise RuntimeError(
            "No project is open in SOLIDWORKS Electrical (getEwProjectCurrent "
            "returned NULL). Open a project in the GUI first.")
    return proj


def _text(obj: Any, getter: str, *args: Any) -> Any:
    """Read a text getter; a failing getter yields None (never a dict that a
    substring filter could accidentally match)."""
    try:
        return coerce_value(_u(getattr(obj, getter)(*args)))
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# Read-only queries


def project_info(app: Any, client: Any) -> dict:
    proj = _project(app)
    files = _u(proj.getEwProjectFileManager())
    comps = _u(proj.getEwProjectComponentManager())
    cables = _u(proj.getEwProjectCableManager())
    locs = _u(proj.getEwProjectLocationManager())
    books = _u(proj.getEwProjectBookManager())
    return {
        "id": _u(proj.getID()),
        "name": _u(proj.getName()),
        "description": _text(proj, "getDescription", LANG),
        "customer": _text(proj, "getCustomerName"),
        "contract_number": _text(proj, "getContractNumber"),
        "folder_path": _text(proj, "getFolderPath"),
        "modified_by": _text(proj, "getModifiedBy"),
        "modification_date": _text(proj, "getModificationDate"),
        "counts": {
            "folios": _u(files.getCount()),
            "components": _u(comps.getCount()),
            "cables": _u(cables.getCount()),
            "locations": _u(locs.getCount()),
            "books": _u(books.getCount()),
        },
        "application_version": _u(app.getApplicationVersion()),
    }


def _folio_row(f: Any) -> dict:
    ftype = _u(f.getFileType())
    return {
        "id": _u(f.getID()),
        "page": _u(f.getTag()),
        "page_number": _u(f.getTagNumber()),
        "description": _text(f, "getDescription", LANG),
        "file_type": FILE_TYPE_NAMES.get(ftype, str(ftype)),
        "file_type_code": ftype,
        "position": _u(f.getPosition()),
        "book_id": _u(f.getEwProjectBookID()),
        "folder_id": _u(f.getEwProjectFolderID()),
        "location_id": _u(f.getLocationID()),
        "is_open": bool(_u(f.isOpen())),
    }


def list_folios(app: Any, client: Any, book_id: int | None = None,
                folder_id: int | None = None, file_type: str | None = None,
                description_contains: str | None = None) -> dict:
    proj = _project(app)
    mgr = _u(proj.getEwProjectFileManager())
    rows = []
    needle = (description_contains or "").lower()
    for f in _each(client, _u(mgr.getEwProjectFileArray())):
        row = _folio_row(f)
        if book_id is not None and row["book_id"] != book_id:
            continue
        if folder_id is not None and row["folder_id"] != folder_id:
            continue
        if file_type and row["file_type"] != file_type:
            continue
        if needle and needle not in str(row["description"]).lower():
            continue
        rows.append(row)
    def _key(r: dict) -> tuple:
        b = r["book_id"] if isinstance(r["book_id"], int) else 0
        pos = r["position"] if isinstance(r["position"], int) else 0
        return (b, pos, r["id"])
    rows.sort(key=_key)
    return {"count": len(rows), "folios": rows}


def find_folio(app: Any, client: Any, page: str | int | None = None,
               file_id: int | None = None) -> Any:
    """Return the ``IEwProjectFileX`` for a page mark ("61") or file id."""
    proj = _project(app)
    mgr = _u(proj.getEwProjectFileManager())
    if file_id is not None:
        f = _u(mgr.findEwProjectFileByID(int(file_id)))
        if f is None:
            raise LookupError(f"no folio with id {file_id}")
        return f
    if page is None:
        raise ValueError("give either page or file_id")
    want = str(page).strip()
    exact, loose = [], []
    for f in _each(client, _u(mgr.getEwProjectFileArray())):
        tag = str(_u(f.getTag()))
        if tag == want:
            exact.append(f)
        elif tag.lstrip("0") == want.lstrip("0"):
            loose.append(f)
    hits = exact or loose
    if not hits:
        raise LookupError(f"no folio with page mark {want!r}")
    if len(hits) > 1:
        ids = [_u(f.getID()) for f in hits]
        raise LookupError(
            f"page mark {want!r} is ambiguous (folio ids {ids}); use file_id")
    return hits[0]


def list_locations(app: Any, client: Any) -> dict:
    proj = _project(app)
    mgr = _u(proj.getEwProjectLocationManager())
    rows = []
    for loc in _each(client, _u(mgr.getEwProjectLocationArray())):
        rows.append({
            "id": _u(loc.getID()),
            "tag": _u(loc.getTag()),
            "tag_path": _text(loc, "getTagPath"),
            "description": _text(loc, "getDescription", LANG),
        })
    return {"count": len(rows), "locations": rows}


def list_books_and_folders(app: Any, client: Any) -> dict:
    proj = _project(app)
    books = []
    for b in _each(client, _u(_u(proj.getEwProjectBookManager())
                              .getEwProjectBookArray())):
        books.append({
            "id": _u(b.getID()), "tag": _u(b.getTag()),
            "description": _text(b, "getDescription", LANG),
        })
    folders = []
    for fo in _each(client, _u(_u(proj.getEwProjectFolderManager())
                               .getEwProjectFolderArray())):
        folders.append({
            "id": _u(fo.getID()), "tag": _u(fo.getTag()),
            "description": _text(fo, "getDescription", LANG),
            "book_id": _u(fo.getEwProjectBookID()),
            "parent_folder_id": _u(fo.getEwProjectFolderID()),
            "position": _u(fo.getPosition()),
        })
    folders.sort(key=lambda r: (r["book_id"] or 0, r["position"] or 0))
    return {"books": books, "folders": folders}


def _part_map(proj: Any, client: Any,
              only: set[int] | None = None) -> dict[int, list[dict]]:
    """component id -> manufacturer parts assigned to it.

    ``only`` restricts the result to those component ids.

    getObjectID alone is NOT proof of ownership: a manufacturer part can
    belong to a location (rails, ducts) and object ids are per-table, so the
    same number means different things. getEwProjectComponent is the only
    authority, and it costs a COM round trip per part. So getObjectID is used
    as a cheap pre-filter and every surviving candidate is then confirmed
    through getEwProjectComponent. Pass ``only`` (callers that want one
    component's parts always can) and the confirmation runs a handful of
    times instead of once per part in the project.
    """
    out: dict[int, list[dict]] = {}
    mgr = _u(proj.getEwProjectManufacturerPartManager())
    for p in _each(client, _u(mgr.getEwProjectManufacturerPartArray())):
        if only is not None and _u(p.getObjectID()) not in only:
            continue  # cheap pre-filter, no COM round trip
        try:
            owner = _u(p.getEwProjectComponent())
        except Exception:  # noqa: BLE001
            owner = None
        if owner is None:
            continue  # owned by a location or nothing, not by a component
        comp_id = _u(owner.getID())
        if only is not None and comp_id not in only:
            continue  # the id collided; the real owner is a different component
        out.setdefault(comp_id, []).append({
            "part_id": _u(p.getID()),
            "manufacturer": _u(p.getManufacturer()),
            "reference": _u(p.getReference()),
            "description": _text(p, "getDescription", LANG),
        })
    return out


def _component_row(c: Any, parts: dict[int, list[dict]] | None) -> dict:
    cid = _u(c.getID())
    row = {
        "id": cid,
        "tag": _u(c.getTag()),
        "tag_path": _u(c.getTagPath()),
        "description": _text(c, "getDescription", LANG),
        "parent_id": _u(c.getParentID()),
        "location_id": _u(c.getLocationID()),
        "type": _u(c.getType()),
        "children": _u(c.getChildrenCount()),
    }
    if parts is not None:
        row["parts"] = parts.get(cid, [])
    return row


def list_components(app: Any, client: Any, tag_contains: str | None = None,
                    location_id: int | None = None,
                    parent_id: int | None = None, with_parts: bool = True,
                    limit: int = 200) -> dict:
    proj = _project(app)
    mgr = _u(proj.getEwProjectComponentManager())
    needle = (tag_contains or "").lower()
    rows = []
    total = 0
    matched = 0
    for c in _each(client, _u(mgr.getEwProjectComponentArray())):
        total += 1
        row = _component_row(c, None)
        if needle and needle not in str(row["tag_path"]).lower() \
                and needle not in str(row["tag"]).lower():
            continue
        if location_id is not None and row["location_id"] != location_id:
            continue
        if parent_id is not None and row["parent_id"] != parent_id:
            continue
        matched += 1
        if limit and len(rows) >= limit:
            continue  # keep counting, stop collecting
        rows.append(row)
    if with_parts and rows:
        parts = _part_map(proj, client, only={r["id"] for r in rows})
        for row in rows:
            row["parts"] = parts.get(row["id"], [])
    return {"count": len(rows), "matched": matched, "total_in_project": total,
            "truncated": matched > len(rows), "components": rows}


def find_component(app: Any, client: Any, tag: str) -> dict:
    """Exact match on the tag ("A1") or the full tag path ("=F1+L1+L4+L2-A1")."""
    proj = _project(app)
    mgr = _u(proj.getEwProjectComponentManager())
    want = tag.strip()
    want_bare = want.lstrip("-")
    hits = []
    for c in _each(client, _u(mgr.getEwProjectComponentArray())):
        t = str(_u(c.getTag()))
        tp = str(_u(c.getTagPath()))
        if t == want or t == want_bare or tp == want \
                or tp.endswith("-" + want_bare):
            hits.append(c)
    parts = _part_map(proj, client, only={_u(c.getID()) for c in hits})
    matches = [_component_row(c, parts) for c in hits]
    return {"query": tag, "count": len(matches), "components": matches}


def list_cables(app: Any, client: Any, limit: int = 500) -> dict:
    proj = _project(app)
    mgr = _u(proj.getEwProjectCableManager())
    rows = []
    total = 0
    for cb in _each(client, _u(mgr.getEwProjectCableArray())):
        total += 1
        if limit and len(rows) >= limit:
            continue  # keep counting, stop collecting
        rows.append({
            "id": _u(cb.getID()),
            "tag": _u(cb.getTag()),
            "description": _text(cb, "getDescription", LANG),
            "manufacturer": _u(cb.getManufacturer()),
            "reference": _u(cb.getReference()),
            "article_number": _u(cb.getArticleNumber()),
            "core_count": _u(cb.getCoreCount()),
            "length": _u(cb.getLength()),
            "upstream_location_id": _u(cb.getUpStreamLocationID()),
            "downstream_location_id": _u(cb.getDownStreamLocationID()),
        })
    return {"count": len(rows), "total_in_project": total,
            "truncated": total > len(rows), "cables": rows}


def folio_symbols(app: Any, client: Any, page: str | int | None = None,
                  file_id: int | None = None) -> dict:
    proj = _project(app)
    f = find_folio(app, client, page=page, file_id=file_id)
    fid = _u(f.getID())
    comp_mgr = _u(proj.getEwProjectComponentManager())
    sym_mgr = _u(proj.getEwProjectSymbolManager())
    rows = []
    for s in _each(client, _u(sym_mgr.getProjectSymbolsFromFileID(fid))):
        stype = _u(s.getEwSymbolType())
        obj_id = _u(s.getObjectID())
        comp_tag = None
        if obj_id and obj_id > 0:
            try:
                comp = _u(comp_mgr.findEwProjectComponentByID(obj_id))
                if comp is not None:
                    comp_tag = _u(comp.getTagPath())
            except Exception:  # noqa: BLE001
                comp_tag = None
        rows.append({
            "symbol_id": _u(s.getID()),
            "symbol_name": _u(s.getEwSymbolName()),
            "symbol_type": SYMBOL_TYPE_NAMES.get(stype, str(stype)),
            "component_id": obj_id,
            "component_tag_path": comp_tag,
            "x": _u(s.getXPosition()),
            "y": _u(s.getYPosition()),
            "rotation": _u(s.getRotationAngle()),
            "points": _u(s.getEwProjectSymbolPointCount()),
        })
    return {"folio": _folio_row(f), "count": len(rows), "symbols": rows}


# --------------------------------------------------------------------------
# Actions


def export_folio_pdf(app: Any, client: Any, output_path: str,
                     pages: list[str | int] | None = None,
                     file_ids: list[int] | None = None,
                     all_pages: bool = False) -> dict:
    """Export selected folios (or the whole project) to one PDF.

    The output folder is created if missing: SOLIDWORKS returns
    EW_FOLDER_NOT_FOUND (37) otherwise, and the older setTargetFolder route
    turns that into a raw COM crash.
    """
    import pythoncom  # type: ignore
    from win32com.client import VARIANT  # type: ignore

    proj = _project(app)
    output_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    ids: list[int] = []
    resolved: list[dict] = []
    if not all_pages:
        for fid in file_ids or []:
            f = find_folio(app, client, file_id=fid)
            ids.append(_u(f.getID()))
            resolved.append(_folio_row(f))
        for pg in pages or []:
            f = find_folio(app, client, page=pg)
            ids.append(_u(f.getID()))
            resolved.append(_folio_row(f))
        if not ids:
            raise ValueError("give pages, file_ids, or all_pages=True")

    exp = _u(proj.newEwProjectExportPDF())
    steps: list[dict] = []

    def step(name: str, fn: Callable[[], Any]) -> int | None:
        try:
            r = fn()
        except Exception as exc:  # noqa: BLE001
            steps.append({"step": name, "error": f"{type(exc).__name__}: {exc}"})
            return -1
        rc = _rc(r)
        steps.append({"step": name, "rc": rc, "rc_name": _rc_name(rc)})
        return rc

    step("initializeFromPrintProjectConfiguration",
         exp.initializeFromPrintProjectConfiguration)
    step("setSilentMode", lambda: exp.setSilentMode(True))
    if all_pages:
        step("setAllProjectFiles", lambda: exp.setAllProjectFiles(True))
    else:
        step("setAllProjectFiles", lambda: exp.setAllProjectFiles(False))
        sel = VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_I4, ids)
        step("setSelectionFiles", lambda: exp.setSelectionFiles(sel))
    rc_name = step("setExportToPDFFileName",
                   lambda: exp.setExportToPDFFileName(output_path))
    if rc_name not in (0, None):
        return {"ok": False, "output_path": output_path, "folios": resolved,
                "steps": steps,
                "error": f"setExportToPDFFileName failed: {_rc_name(rc_name)}"}
    rc_exp = step("exportPDF", exp.exportPDF)
    exists = os.path.isfile(output_path)
    return {
        "ok": rc_exp in (0, None) and exists,
        "output_path": output_path,
        "file_exists": exists,
        "size_bytes": os.path.getsize(output_path) if exists else 0,
        "folios": resolved,
        "steps": steps,
    }


def regenerate_title_blocks(app: Any, client: Any) -> dict:
    """Refresh every title block from project data.

    Returns EW_PROJECT_OPENED (45) if any drawing is open in the GUI; close
    the open documents (or close and reopen one folio) first.
    """
    proj = _project(app)
    upd = _u(proj.getEwProjectUpdateData())
    r1 = upd.resetProjectDataObjectType()
    r2 = upd.addProjectDataObjectType(K_PROJECT_DATA_TITLE_BLOCK)
    r3 = upd.process(K_PROJECT_DATA_UPDATE)
    rc = _rc(r3)
    open_folios = [row["page"] for row in
                   list_folios(app, client)["folios"] if row["is_open"]]
    return {
        "ok": rc == 0,
        "rc": rc, "rc_name": _rc_name(rc),
        "reset_rc": _rc(r1), "add_rc": _rc(r2),
        "open_folios": open_folios,
        "hint": (None if rc == 0 else
                 "Close the open drawings in SOLIDWORKS Electrical and retry."),
    }


def rename_project(app: Any, client: Any, new_name: str) -> dict:
    proj = _project(app)
    old = _u(proj.getName())
    r1 = proj.setName(new_name)
    r2 = proj.update()
    now = _u(proj.getName())
    return {"ok": now == new_name, "old_name": old, "new_name": now,
            "set_rc": _rc(r1), "update_rc": _rc(r2)}


def close_and_reopen_folio(app: Any, client: Any, page: str | int | None = None,
                           file_id: int | None = None) -> dict:
    """Force a folio to redraw (after moving symbols or refreshing title data)."""
    f = find_folio(app, client, page=page, file_id=file_id)
    rc_close = _rc(f.close())
    rc_open = _rc(f.open())
    return {"ok": rc_open in (0, None), "folio": _folio_row(f),
            "close_rc": rc_close, "open_rc": rc_open}


# --------------------------------------------------------------------------
# Write-side: clone / delete a component


def _split_mark(mark: str) -> tuple:
    """Split a device mark into (root, number, suffix): "K29B" -> ("K", 29,
    "B"), "K38" -> ("K", 38, ""), "K" -> ("K", None, "")."""
    # The letters are optional: a folder tag is a bare number ("7"), and its
    # root is empty rather than the digits.
    m = re.match(r"^([A-Za-z]*)(\d+)?(.*)$", mark.strip().lstrip("-"))
    if not m:
        return mark, None, ""
    root, num, suffix = m.group(1), m.group(2), m.group(3) or ""
    if not root and num is None:
        return mark, None, ""
    return root, (int(num) if num is not None else None), suffix


def _set_mark(comp: Any, mark: str) -> dict:
    """Set a component's mark AND the tag root/number behind it.

    setTag only writes the mark that is displayed. SOLIDWORKS keeps the
    device class in a separate TagRoot, with TagNumber, and drives its own
    automatic renumbering from those. A component created through the API and
    then given a mark keeps whatever root it was born with, so it can read as
    "K38" on every drawing while being filed as J12 underneath, and a GUI
    renumber would then move it out of the K series. Keep all three in step.
    """
    root, number, _suffix = _split_mark(mark)
    out = {"setTag": _rc(comp.setTag(mark)),
           "setTagRoot": _rc(comp.setTagRoot(root))}
    if number is not None:
        out["setTagNumber"] = _rc(comp.setTagNumber(number))
    out["update"] = _rc(comp.update())
    # setTagRoot/setTagNumber can rewrite the displayed mark; put it back.
    if str(_u(comp.getTag())) != mark:
        out["setTag(restore)"] = _rc(comp.setTag(mark))
        out["update2"] = _rc(comp.update())
    return out


def _find_one_component(app: Any, client: Any, tag: str) -> Any:
    """The IEwProjectComponentX for an unambiguous tag / tag path."""
    proj = _project(app)
    mgr = _u(proj.getEwProjectComponentManager())
    want = tag.strip()
    want_bare = want.lstrip("-")
    hits = []
    for c in _each(client, _u(mgr.getEwProjectComponentArray())):
        t = str(_u(c.getTag()))
        tp = str(_u(c.getTagPath()))
        if t == want or t == want_bare or tp == want \
                or tp.endswith("-" + want_bare):
            hits.append(c)
    if not hits:
        raise LookupError(f"no component with tag {tag!r}")
    if len(hits) > 1:
        paths = [_u(c.getTagPath()) for c in hits]
        raise LookupError(f"tag {tag!r} is ambiguous: {paths}; give the full "
                          "tag path")
    return hits[0]


def _symbols_of(app: Any, client: Any, file_id: int, component_id: int) -> list:
    proj = _project(app)
    sym_mgr = _u(proj.getEwProjectSymbolManager())
    out = []
    for s in _each(client, _u(sym_mgr.getProjectSymbolsFromFileID(file_id))):
        if _u(s.getObjectID()) == component_id:
            out.append(s)
    return out


def _tag_root(tag: str) -> str:
    """Letters before the number: "K32" -> "K", "N1N15" -> "N", "T58" -> "T".

    Case is preserved; compare roots with ``casefold()``.
    """
    m = re.match(r"([A-Za-z]+)", tag.strip().lstrip("-"))
    return m.group(1) if m else ""


def _next_free_slot(app: Any, client: Any, file_id: int,
                    src_symbols: list[dict]) -> float:
    """X offset that puts the copy in the next free slot to the right.

    The pitch is the spacing between symbols of the same library name on
    the same row (footprints report width 0, so the neighbours are the only
    reliable size). If a symbol already sits at the candidate slot, keep
    stepping right. Falls back to the symbol width, then 10 mm.
    """
    ref = src_symbols[0]
    name, x0, y0 = ref["symbol_name"], ref["x"], ref["y"]
    proj = _project(app)
    sym_mgr = _u(proj.getEwProjectSymbolManager())
    same_name_xs: list[float] = []   # for the pitch
    row_xs: list[float] = []         # for occupancy, whatever the symbol is
    for s in _each(client, _u(sym_mgr.getProjectSymbolsFromFileID(file_id))):
        y = _u(s.getYPosition())
        if not isinstance(y, (int, float)) or abs(y - y0) >= 0.5:
            continue
        x = float(_u(s.getXPosition()))
        row_xs.append(x)
        if _u(s.getEwSymbolName()) == name:
            same_name_xs.append(x)
    same_name_xs.sort()
    row_xs.sort()
    gaps = [b - a for a, b in zip(same_name_xs, same_name_xs[1:])
            if b - a > 0.01]
    if gaps:
        pitch = min(gaps)
    elif isinstance(ref["width"], (int, float)) and ref["width"] > 0:
        pitch = float(ref["width"])
    else:
        pitch = 10.0
    target = x0 + pitch
    while any(abs(x - target) < pitch / 2 for x in row_xs):
        target += pitch
    return round(target - x0, 6)


def clone_component(app: Any, client: Any, source_tag: str, new_tag: str,
                    page: str | int | None = None, file_id: int | None = None,
                    offset_x: float | None = None, offset_y: float = 0.0,
                    dry_run: bool = True) -> dict:
    """Clone a component (description, location, parent, class, function,
    manufacturer parts) into ``new_tag`` and, if a page is given, copy the
    source's symbols on that page for the new component, shifted by
    ``offset_x``/``offset_y`` (default: one symbol width to the right, so a
    cabinet-layout footprint lands in the next slot).

    ``dry_run`` (default) returns the plan and writes nothing.
    """
    proj = _project(app)
    src = _find_one_component(app, client, source_tag)
    src_id = _u(src.getID())
    parts = _part_map(proj, client, only={src_id}).get(src_id, [])
    new_bare = new_tag.strip().lstrip("-")
    src_root = (str(_u(src.getTagRoot()) or "").strip()
                or _tag_root(str(_u(src.getTag()))))
    new_root = _tag_root(new_bare)
    if new_root.casefold() != src_root.casefold():
        raise ValueError(
            f"tag root must stay {src_root!r} when cloning "
            f"{_u(src.getTagPath())!r} (got {new_bare!r}, root {new_root!r}). "
            "MR rule: a clone keeps its device class; relays are always "
            "rooted K.")
    if new_root != src_root:
        # Same class, different casing ("k33"): write the project's casing so
        # the mark cannot drift from the root the rule names.
        new_bare = src_root + new_bare[len(new_root):]
    try:
        _find_one_component(app, client, new_bare)
    except LookupError as e:
        if "ambiguous" in str(e):
            # Several components already carry this mark; adding another is
            # never what the caller meant.
            raise ValueError(
                f"{new_bare!r} already exists more than once: {e}") from e
    else:
        raise ValueError(f"a component tagged {new_bare!r} already exists")

    src_symbols: list[dict] = []
    folio_row = None
    if page is not None or file_id is not None:
        f = find_folio(app, client, page=page, file_id=file_id)
        folio_row = _folio_row(f)
        for s in _symbols_of(app, client, folio_row["id"], src_id):
            src_symbols.append({
                "symbol_id": _u(s.getID()),
                "symbol_name": _u(s.getEwSymbolName()),
                "symbol_type_code": _u(s.getEwSymbolType()),
                "x": _u(s.getXPosition()), "y": _u(s.getYPosition()),
                "rotation": _u(s.getRotationAngle()),
                "x_scale": _u(s.getXScale()), "y_scale": _u(s.getYScale()),
                "width": _u(s.getWidth()), "height": _u(s.getHeight()),
            })
        if not src_symbols:
            raise LookupError(
                f"{source_tag!r} has no symbol on page "
                f"{folio_row['page']!r} ({folio_row['description']})")
    if offset_x is None and src_symbols:
        offset_x = _next_free_slot(app, client, folio_row["id"], src_symbols)
    elif offset_x is None:
        offset_x = 10.0

    plan = {
        "source": _component_row(src, {src_id: parts}),
        "new_tag": new_bare,
        "folio": folio_row,
        "symbols_to_copy": [
            {**s, "new_x": s["x"] + offset_x, "new_y": s["y"] + offset_y}
            for s in src_symbols],
        "offset": {"x": offset_x, "y": offset_y},
    }
    if dry_run:
        return {"ok": True, "dry_run": True, "plan": plan}

    steps: list[dict] = []

    def step(name: str, fn: Callable[[], Any]) -> Any:
        try:
            r = fn()
        except Exception as exc:  # noqa: BLE001
            steps.append({"step": name, "error": f"{type(exc).__name__}: {exc}"})
            return None
        steps.append({"step": name, "rc": _rc(r), "rc_name": _rc_name(_rc(r))})
        return r

    # 1. the component: newX -> insert -> set fields -> update
    mgr = _u(proj.getEwProjectComponentManager())
    new = _u(mgr.newEwProjectComponent())
    step("insert", new.insert)
    step("setTag+root+number", lambda: _set_mark(new, new_bare))
    desc = _text(src, "getDescription", LANG)
    if desc:
        step("setDescription", lambda: new.setDescription(LANG, desc))
    for getter, setter in (("getLocationID", "setLocationID"),
                           ("getParentID", "setParentID"),
                           ("getClassID", "setClassID"),
                           ("getClassNodeID", "setClassNodeID"),
                           ("getFunctionID", "setFunctionID")):
        val = _u(getattr(src, getter)())
        if isinstance(val, int) and val > 0:
            step(setter, lambda s_=setter, v_=val: getattr(new, s_)(v_))
    step("update", new.update)
    new_id = _u(new.getID())

    # 2. manufacturer parts
    for p in parts:
        step(f"assignManufacturerPart({p['manufacturer']},{p['reference']})",
             lambda p_=p: new.assignManufacturerPart(p_["manufacturer"],
                                                     p_["reference"]))
    # assignManufacturerPart commits on its own; a further update() on the
    # component answers EW_INVALID_OBJECT (9) once several parts are bound.

    # 3. symbols on the page
    placed: list[dict] = []
    if folio_row is not None:
        f = find_folio(app, client, file_id=folio_row["id"])
        # A folio open in the GUI holds its own copy of the drawing. Symbols
        # inserted through the API while it is open are lost when the editor
        # writes that stale copy back on close. Close it first, reopen after
        # so the new symbols render.
        reopened = False
        if folio_row["is_open"]:
            step("folio.close (was open in the GUI)", f.close)
            close_step = steps[-1]
            rc_close = close_step.get("rc")
            if close_step.get("error") or rc_close not in (0, None):
                # Inserting now would hand the symbols to the editor's stale
                # copy, which is exactly the silent loss this guard exists to
                # stop. The component is already created; report and stop.
                return {
                    "ok": False, "dry_run": False,
                    "new_component": _component_row(new, None),
                    "symbols_placed": [], "plan": plan, "steps": steps,
                    "errors": [st for st in steps if st.get("error")
                               or st.get("rc") not in (0, None)],
                    "folio_was_open": True,
                    "error": (f"could not close folio {folio_row['page']!r} "
                              f"({close_step.get('error') or _rc_name(rc_close)}); "
                              "refusing to insert "
                              "symbols into a folio open in the GUI because "
                              "the editor would discard them"),
                    "undo": f"delete_component(component_id={new_id})",
                }
            reopened = True
        for s in plan["symbols_to_copy"]:
            sym = _u(f.newEwProjectSymbolFromSymbolType(s["symbol_type_code"]))
            if sym is None:
                steps.append({"step": "newEwProjectSymbolFromSymbolType",
                              "error": "returned NULL"})
                continue
            step("sym.setObjectID", lambda: sym.setObjectID(new_id))
            step("sym.setEwSymbolName",
                 lambda: sym.setEwSymbolName(s["symbol_name"]))
            step("sym.setXPosition", lambda: sym.setXPosition(s["new_x"]))
            step("sym.setYPosition", lambda: sym.setYPosition(s["new_y"]))
            if s["rotation"]:
                step("sym.setRotationAngle",
                     lambda: sym.setRotationAngle(s["rotation"]))
            if s["x_scale"] and s["x_scale"] != 1:
                step("sym.setXScale", lambda: sym.setXScale(s["x_scale"]))
            if s["y_scale"] and s["y_scale"] != 1:
                step("sym.setYScale", lambda: sym.setYScale(s["y_scale"]))
            step("sym.insert", sym.insert)
            placed.append({"symbol_id": _u(sym.getID()),
                           "symbol_name": s["symbol_name"],
                           "x": s["new_x"], "y": s["new_y"]})
        if reopened:
            step("folio.open (restore the GUI view)", f.open)

    # 4. read back
    parts_after = _part_map(proj, client, only={new_id}).get(new_id, [])
    new_row = _component_row(new, {new_id: parts_after})
    errors = [st for st in steps
              if st.get("error") or st.get("rc") not in (0, None)]
    return {
        "ok": not errors and new_row["tag"] == new_bare,
        "dry_run": False,
        "new_component": new_row,
        "symbols_placed": placed,
        "plan": plan,
        "steps": steps,
        "errors": errors,
        "folio_was_open": bool(folio_row and folio_row["is_open"]),
        # A clone never shifts a rail, so its undo never needs close_gap;
        # that belongs to add_component(shift_following=True).
        "undo": (f"delete_component(component_id={new_id}"
                 + (f", pages=['{folio_row['page']}']" if folio_row else "")
                 + ")"),
    }


def delete_component(app: Any, client: Any, component_id: int | None = None,
                     tag: str | None = None,
                     pages: list[str | int] | None = None,
                     close_gap: bool = False) -> dict:
    """Remove a component by id or tag.

    A plain remove is tried first. If SOLIDWORKS answers EW_CANNOT_REMOVE
    (35) the component still has symbols bound to it: those are removed
    from ``pages`` if given (fast), otherwise from every folio (a full sweep,
    minutes on a large project), and the remove is retried.

    ``close_gap`` pulls the rest of the rail back over the hole: everything
    right of each removed symbol moves left by one device pitch. It is the
    undo for an ``add_component(..., shift_following=True)`` insert, which
    would otherwise leave the row one device wider than it started.
    """
    proj = _project(app)
    if component_id is not None:
        mgr = _u(proj.getEwProjectComponentManager())
        comp = _u(mgr.findEwProjectComponentByID(int(component_id)))
        if comp is None:
            raise LookupError(f"no component with id {component_id}")
    elif tag:
        comp = _find_one_component(app, client, tag)
    else:
        raise ValueError("give component_id or tag")
    row = _component_row(comp, None)
    cid = row["id"]
    symbols_removed: list[dict] = []
    gaps_closed: list[dict] = []
    swept_all = False
    rc = _rc(comp.remove())
    if rc == 35:
        # Symbols bound to the component block the remove. Manufacturer
        # parts go with the component, symbols do not.
        sym_mgr = _u(proj.getEwProjectSymbolManager())
        if pages:
            folios = [find_folio(app, client, page=p) for p in pages]
        else:
            file_mgr = _u(proj.getEwProjectFileManager())
            folios = list(_each(client, _u(file_mgr.getEwProjectFileArray())))
            swept_all = True
        for f in folios:
            fid = _u(f.getID())
            victims = []
            for sym in _each(client,
                             _u(sym_mgr.getProjectSymbolsFromFileID(fid))):
                # getObjectID is not proof of ownership: label symbols carry
                # the id of a location, cable, wire or harness, and object ids
                # are per-table, so one can collide with this component id.
                # Only symbol types that bind to a component may be removed.
                if _u(sym.getObjectID()) != cid:
                    continue
                stype = _u(sym.getEwSymbolType())
                if stype in NON_COMPONENT_SYMBOL_TYPES:
                    symbols_removed.append(
                        {"file_id": fid, "page": _u(f.getTag()),
                         "symbol_id": _u(sym.getID()),
                         "skipped": SYMBOL_TYPE_NAMES.get(stype, str(stype))})
                    continue
                victims.append(sym)
            if not victims:
                continue
            # Same open-folio hazard as clone: the editor's copy of an open
            # drawing is written back on close and would resurrect the
            # symbols (or make the delete look like it never happened).
            was_open = bool(_u(f.isOpen()))
            closed_ok = True
            if was_open:
                closed_ok = _rc(f.close()) in (0, None)
            if not closed_ok:
                for sym in victims:
                    symbols_removed.append(
                        {"file_id": fid, "page": _u(f.getTag()),
                         "symbol_id": _u(sym.getID()),
                         "skipped": "folio open in the GUI and would not close"})
                continue
            # Measure the rail BEFORE removing: once the symbol is gone its
            # group may have no neighbour left to measure the pitch from.
            gap_jobs = []
            if close_gap:
                for sym in victims:
                    sx = float(_u(sym.getXPosition()))
                    sy = _u(sym.getYPosition())
                    pitch = _group_pitch(app, client, fid,
                                         _u(sym.getEwSymbolName()), sy)
                    if pitch > 0:
                        gap_jobs.append((sx, sy, pitch))
            for sym in victims:
                symbols_removed.append({"file_id": fid, "page": _u(f.getTag()),
                                        "symbol_id": _u(sym.getID()),
                                        "rc": _rc(sym.remove())})
            for sx, sy, pitch in sorted(gap_jobs):
                for cx, cand in _row_symbols_right_of(app, client, fid, sy, sx):
                    cand.setXPosition(cx - pitch)
                    cand.update()
                    gaps_closed.append({"file_id": fid,
                                        "symbol_id": _u(cand.getID()),
                                        "from_x": cx, "to_x": cx - pitch})
            if was_open:
                f.open()
        rc = _rc(comp.remove())
    skipped = [r for r in symbols_removed if r.get("skipped")]
    return {"ok": rc in (0, None), "removed": row, "rc": rc,
            "rc_name": _rc_name(rc), "symbols_removed": symbols_removed,
            "symbols_skipped": skipped, "gaps_closed": gaps_closed,
            "swept_all_folios": swept_all}


# --------------------------------------------------------------------------
# Write-side: build a component from scratch (no source to copy)


def _symbol_name_for_part(app: Any, part: Any, file_type_code: int) -> tuple:
    """(library symbol name, symbol type) a manufacturer part draws itself with.

    A part carries its own symbol ids: a 2D footprint for a cabinet layout and
    a scheme symbol for a schematic. They are ids into the ENVIRONMENT symbol
    library, so they resolve through getEwEnvironment().getEwSymbolManager(),
    not through the project. Returns ``(None, type)`` when the part declares no
    symbol for that kind of page.
    """
    if file_type_code == 9:          # kFile2DCabinetLayout
        getter, stype = "get2DFootPrintSymbolID", 105   # kSymbol2dFootprint
    elif file_type_code == 1:        # kFileLineDiagram
        getter, stype = "getLineDiagramSymbolID", 20    # kSymbolComponent
    else:
        getter, stype = "getSchemeSymbolID", 20
    try:
        sym_id = _u(getattr(part, getter)())
    except Exception:  # noqa: BLE001
        return None, stype
    if not isinstance(sym_id, int) or sym_id <= 0:
        return None, stype
    try:
        env = _u(app.getEwEnvironment())
        lib = _u(env.getEwSymbolManager())
        sym = _u(lib.findEwSymbolXById(sym_id))
        if sym is None:
            return None, stype
        return _u(sym.getName()), stype
    except Exception:  # noqa: BLE001
        return None, stype


def _reference_symbol_on_page(app: Any, client: Any, file_id: int,
                              symbol_name: str | None,
                              after_component_id: int | None) -> dict | None:
    """An existing symbol on the page to take scale (and row) from.

    A fresh symbol defaults to scale 1, but a cabinet footprint is drawn at
    the scale that fits the part's real millimetres onto the sheet, so placing
    one at scale 1 puts a wildly oversized box on the layout. Prefer the
    symbol of ``after_component_id``; otherwise any symbol with the same
    library name.
    """
    proj = _project(app)
    sym_mgr = _u(proj.getEwProjectSymbolManager())
    fallback = None
    for s in _each(client, _u(sym_mgr.getProjectSymbolsFromFileID(file_id))):
        row = {
            "symbol_id": _u(s.getID()), "symbol_name": _u(s.getEwSymbolName()),
            "symbol_type_code": _u(s.getEwSymbolType()),
            "x": _u(s.getXPosition()), "y": _u(s.getYPosition()),
            "rotation": _u(s.getRotationAngle()),
            "x_scale": _u(s.getXScale()), "y_scale": _u(s.getYScale()),
            "width": _u(s.getWidth()), "height": _u(s.getHeight()),
        }
        if after_component_id and _u(s.getObjectID()) == after_component_id:
            return row
        if fallback is None and symbol_name and row["symbol_name"] == symbol_name:
            fallback = row
    return fallback


def _root_in_use_for_part(app: Any, client: Any, manufacturer: str,
                          reference: str) -> tuple:
    """(tag root, example tag paths) of the components already using this part.

    A manufacturer part that is already in the project carries an established
    device class: every ABB ESB20 contactor on this project is a K. That makes
    the root checkable without a source component to inherit from, which is
    how the relays-are-always-K rule is enforced on a from-scratch add.
    Returns ``(None, [])`` when the part is not in use yet.
    """
    proj = _project(app)
    mgr = _u(proj.getEwProjectManufacturerPartManager())
    owners: list[Any] = []
    for p in _each(client, _u(mgr.getEwProjectManufacturerPartArray())):
        if str(_u(p.getReference())) != str(reference):
            continue
        if manufacturer and str(_u(p.getManufacturer())) != str(manufacturer):
            continue
        try:
            owner = _u(p.getEwProjectComponent())
        except Exception:  # noqa: BLE001
            owner = None
        if owner is not None:
            owners.append(owner)
    roots: dict[str, list[str]] = {}
    for o in owners:
        root = (str(_u(o.getTagRoot()) or "").strip()
                or _tag_root(str(_u(o.getTag()))))
        if root:
            roots.setdefault(root, []).append(str(_u(o.getTagPath())))
    if not roots:
        return None, []
    best = max(roots, key=lambda r: len(roots[r]))
    return best, roots[best][:5]


def _group_pitch(app: Any, client: Any, file_id: int, symbol_name: str,
                 y0: float, fallback: float = 0.0) -> float:
    """Centre-to-centre spacing of the devices of one kind on a rail.

    A cabinet footprint reports getWidth 0, so the only reliable measure of
    how much rail a device occupies is the spacing between its neighbours of
    the same library symbol on the same row.
    """
    proj = _project(app)
    sym_mgr = _u(proj.getEwProjectSymbolManager())
    xs = []
    for s in _each(client, _u(sym_mgr.getProjectSymbolsFromFileID(file_id))):
        if _u(s.getEwSymbolName()) != symbol_name:
            continue
        y = _u(s.getYPosition())
        if isinstance(y, (int, float)) and abs(y - y0) < 0.5:
            xs.append(float(_u(s.getXPosition())))
    xs.sort()
    gaps = [b - a for a, b in zip(xs, xs[1:]) if b - a > 0.01]
    return min(gaps) if gaps else fallback


def _row_symbols_right_of(app: Any, client: Any, file_id: int, y0: float,
                          x_from: float) -> list:
    """Every symbol on the row at ``y0`` sitting strictly right of ``x_from``.

    Everything to the right has to move when a device is inserted into a
    rail, whatever kind of symbol it is, because the devices are physically
    adjacent.
    """
    proj = _project(app)
    sym_mgr = _u(proj.getEwProjectSymbolManager())
    out = []
    for s in _each(client, _u(sym_mgr.getProjectSymbolsFromFileID(file_id))):
        y = _u(s.getYPosition())
        if not isinstance(y, (int, float)) or abs(y - y0) >= 0.5:
            continue
        x = float(_u(s.getXPosition()))
        if x > x_from + 0.001:
            out.append((x, s))
    out.sort(key=lambda t: t[0])
    return out


def add_component(app: Any, client: Any, tag: str, manufacturer: str,
                  reference: str, description: str | None = None,
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
    """Create a component from scratch and give it a manufacturer part.

    Unlike ``clone_component`` there is no source to copy: the symbol comes
    from the manufacturer part's own library symbol, and the placement comes
    from ``x``/``y`` or from ``after_tag`` (place in the next free slot after
    that component's symbol on the page).

    With ``shift_following`` the device is INSERTED into the rail directly
    after ``after_tag`` instead of appended: everything further right on that
    row is pushed along by one device pitch to open the slot. Use it when the
    neighbours are adjacent and there is no free space between them.

    The manufacturer part must already be in the project catalogue;
    ``assignManufacturerPart`` answers EW_BAD_INPUTS (2) otherwise. If other
    components already use this part, the new tag must share their tag root,
    which is what keeps a relay rooted K without a source to inherit from.
    """
    proj = _project(app)
    new_bare = tag.strip().lstrip("-")
    new_root = _tag_root(new_bare)

    # 1. the tag must be free, and must match the class this part already has
    try:
        _find_one_component(app, client, new_bare)
    except LookupError as e:
        if "ambiguous" in str(e):
            raise ValueError(f"{new_bare!r} already exists more than once: {e}") from e
    else:
        raise ValueError(f"a component tagged {new_bare!r} already exists")

    part_root, examples = _root_in_use_for_part(app, client, manufacturer,
                                                reference)
    if allow_new_root:
        # A deliberate new series for this part, e.g. a CC_ prefix kept
        # separate from the existing marks. The caller has said so.
        part_root = None
    if part_root and new_root.casefold() != part_root.casefold():
        raise ValueError(
            f"tag root must be {part_root!r} for {manufacturer} {reference}: "
            f"the components already using this part are {examples}. "
            f"Got {new_bare!r} (root {new_root!r}). MR rule: a device keeps "
            "its class; relays are always rooted K.")
    if part_root and new_root != part_root:
        new_bare = part_root + new_bare[len(new_root):]

    # 2. where it lives
    if location_id is None and location_tag:
        loc_mgr = _u(proj.getEwProjectLocationManager())
        hits = [l for l in _each(client, _u(loc_mgr.getEwProjectLocationArray()))
                if str(_u(l.getTag())) == str(location_tag).lstrip("+")]
        if not hits:
            raise LookupError(f"no location tagged {location_tag!r}")
        if len(hits) > 1:
            raise LookupError(
                f"location tag {location_tag!r} is ambiguous "
                f"({[_u(l.getTagPath()) for l in hits]}); give location_id")
        location_id = _u(hits[0].getID())
    parent_id = None
    if parent_tag:
        parent_id = _u(_find_one_component(app, client, parent_tag).getID())

    # 3. the page, the symbol it will be drawn with, and where it goes
    folio_row = None
    ref_symbol = None
    after_id = None
    sym_type = None
    shift_plan: list[dict] = []
    if page is not None or file_id is not None:
        f = find_folio(app, client, page=page, file_id=file_id)
        folio_row = _folio_row(f)
        if after_tag:
            after_id = _u(_find_one_component(app, client, after_tag).getID())
        # the part's own symbol for this kind of page
        part_mgr = _u(proj.getEwProjectManufacturerPartManager())
        sample = None
        for p in _each(client, _u(part_mgr.getEwProjectManufacturerPartArray())):
            if str(_u(p.getReference())) == str(reference):
                sample = p
                break
        derived_name, sym_type = (None, 20)
        if sample is not None:
            derived_name, sym_type = _symbol_name_for_part(
                app, sample, folio_row["file_type_code"])
        symbol_name = symbol_name or derived_name
        ref_symbol = _reference_symbol_on_page(app, client, folio_row["id"],
                                               symbol_name, after_id)
        if symbol_name is None and ref_symbol is not None:
            symbol_name = ref_symbol["symbol_name"]
        if symbol_name is None:
            raise LookupError(
                f"cannot tell which library symbol to draw {reference!r} with "
                f"on page {folio_row['page']!r}: the part declares none for "
                "this page type and no symbol of it is already on the page. "
                "Pass symbol_name explicitly.")
        if ref_symbol is not None:
            sym_type = ref_symbol["symbol_type_code"]
        if shift_following:
            if ref_symbol is None or after_id is None:
                raise ValueError(
                    "shift_following needs after_tag naming a component whose "
                    "symbol is on this page; the new device is inserted "
                    "directly after it.")
            pitch = _group_pitch(app, client, folio_row["id"],
                                 ref_symbol["symbol_name"], ref_symbol["y"])
            if pitch <= 0:
                raise ValueError(
                    f"cannot tell how much rail {reference!r} occupies: "
                    f"{after_tag!r} has no neighbour of the same kind on its "
                    "row to measure the pitch from. Pass x and y explicitly.")
            y = ref_symbol["y"] if y is None else y
            x = ref_symbol["x"] + pitch if x is None else x
            shift_plan = [
                {"symbol_id": _u(sym.getID()),
                 "symbol_name": _u(sym.getEwSymbolName()),
                 "from_x": sx, "to_x": sx + pitch}
                for sx, sym in _row_symbols_right_of(
                    app, client, folio_row["id"], y, ref_symbol["x"])]
        elif x is None or y is None:
            if ref_symbol is None:
                raise ValueError(
                    "no x/y given and nothing on the page to place after: "
                    "pass x and y, or after_tag naming a component whose "
                    "symbol sits where the new one should follow.")
            y = ref_symbol["y"] if y is None else y
            if x is None:
                off = _next_free_slot(app, client, folio_row["id"],
                                      [ref_symbol])
                x = ref_symbol["x"] + off

    warnings: list[str] = []
    if shift_following and folio_row is not None and not shift_plan:
        warnings.append(
            f"nothing sits right of {after_tag!r} on that row, so no shift "
            "was needed; the device is simply appended")
    if folio_row is not None and ref_symbol is None:
        warnings.append(
            "no existing symbol of this kind on the page, so the new one is "
            "drawn at scale 1; a cabinet footprint is normally scaled to the "
            "part's real millimetres, so check its size on the sheet")

    plan = {
        "tag": new_bare, "manufacturer": manufacturer, "reference": reference,
        "description": description, "location_id": location_id,
        "parent_id": parent_id, "folio": folio_row,
        "symbol": (None if folio_row is None else {
            "symbol_name": symbol_name, "symbol_type_code": sym_type,
            "x": x, "y": y,
            "x_scale": (ref_symbol or {}).get("x_scale", 1.0),
            "y_scale": (ref_symbol or {}).get("y_scale", 1.0),
            "rotation": (ref_symbol or {}).get("rotation", 0.0),
            "scale_taken_from": (ref_symbol or {}).get("symbol_id"),
        }),
        "tag_root_in_use_for_part": part_root,
        "shift_following": bool(shift_following),
        "pitch": (shift_plan and
                  round(shift_plan[0]["to_x"] - shift_plan[0]["from_x"], 6)
                  or None),
        "symbols_to_shift": shift_plan,
        "warnings": warnings,
    }
    if dry_run:
        return {"ok": True, "dry_run": True, "plan": plan}

    steps: list[dict] = []

    def step(name: str, fn: Callable[[], Any]) -> Any:
        try:
            r = fn()
        except Exception as exc:  # noqa: BLE001
            steps.append({"step": name, "error": f"{type(exc).__name__}: {exc}"})
            return None
        steps.append({"step": name, "rc": _rc(r), "rc_name": _rc_name(_rc(r))})
        return r

    # 4. the component
    mgr = _u(proj.getEwProjectComponentManager())
    new = _u(mgr.newEwProjectComponent())
    step("insert", new.insert)
    step("setTag+root+number", lambda: _set_mark(new, new_bare))
    if description:
        step("setDescription", lambda: new.setDescription(LANG, description))
    if isinstance(location_id, int) and location_id > 0:
        step("setLocationID", lambda: new.setLocationID(location_id))
    if isinstance(parent_id, int) and parent_id > 0:
        step("setParentID", lambda: new.setParentID(parent_id))
    step("update", new.update)
    new_id = _u(new.getID())

    # 5. the manufacturer part (must already be in the project catalogue)
    rc_part = _rc(step(f"assignManufacturerPart({manufacturer},{reference})",
                       lambda: new.assignManufacturerPart(manufacturer,
                                                          reference)))
    if rc_part == 2:
        warnings.append(
            f"assignManufacturerPart returned EW_BAD_INPUTS (2): "
            f"{manufacturer} {reference} is not in this project's catalogue. "
            "Add the part to the project first.")

    # 6. the symbol
    placed = None
    shifted: list[dict] = []
    if folio_row is not None:
        f = find_folio(app, client, file_id=folio_row["id"])
        reopened = False
        if folio_row["is_open"]:
            step("folio.close (was open in the GUI)", f.close)
            cs = steps[-1]
            if cs.get("error") or cs.get("rc") not in (0, None):
                return {
                    "ok": False, "dry_run": False,
                    "new_component": _component_row(new, None),
                    "symbol_placed": None, "plan": plan, "steps": steps,
                    "warnings": warnings,
                    "error": (f"could not close folio {folio_row['page']!r}; "
                              "refusing to insert a symbol into a folio open "
                              "in the GUI because the editor would discard it"),
                    "undo": f"delete_component(component_id={new_id})",
                }
            reopened = True
        # Open the slot first, while the folio is closed, so the row never
        # renders with two devices on top of each other.
        shifted: list[dict] = []
        if shift_plan:
            pitch = plan["pitch"] or 0.0
            # Move the rightmost first so no intermediate position collides
            # with a device that has not moved yet.
            for mv in sorted(shift_plan, key=lambda m: m["from_x"],
                             reverse=True):
                target = None
                for sx, cand in _row_symbols_right_of(
                        app, client, folio_row["id"], plan["symbol"]["y"],
                        ref_symbol["x"]):
                    if _u(cand.getID()) == mv["symbol_id"]:
                        target = cand
                        break
                if target is None:
                    shifted.append({**mv, "error": "symbol vanished"})
                    continue
                rc_set = _rc(target.setXPosition(mv["to_x"]))
                rc_upd = _rc(target.update())
                shifted.append({**mv, "set_rc": rc_set, "update_rc": rc_upd})
            steps.append({"step": f"shift {len(shifted)} symbols by {pitch}",
                          "rc": 0 if all(m.get("set_rc") in (0, None)
                                         and m.get("update_rc") in (0, None)
                                         for m in shifted) else 1,
                          "rc_name": "n/a"})
        sym = _u(f.newEwProjectSymbolFromSymbolType(sym_type))
        if sym is None:
            steps.append({"step": "newEwProjectSymbolFromSymbolType",
                          "error": "returned NULL"})
        else:
            spec = plan["symbol"]
            step("sym.setObjectID", lambda: sym.setObjectID(new_id))
            step("sym.setEwSymbolName",
                 lambda: sym.setEwSymbolName(spec["symbol_name"]))
            step("sym.setXPosition", lambda: sym.setXPosition(spec["x"]))
            step("sym.setYPosition", lambda: sym.setYPosition(spec["y"]))
            if spec["rotation"]:
                step("sym.setRotationAngle",
                     lambda: sym.setRotationAngle(spec["rotation"]))
            if spec["x_scale"] and spec["x_scale"] != 1:
                step("sym.setXScale", lambda: sym.setXScale(spec["x_scale"]))
            if spec["y_scale"] and spec["y_scale"] != 1:
                step("sym.setYScale", lambda: sym.setYScale(spec["y_scale"]))
            step("sym.insert", sym.insert)
            placed = {"symbol_id": _u(sym.getID()),
                      "symbol_name": spec["symbol_name"],
                      "x": spec["x"], "y": spec["y"]}
        if reopened:
            step("folio.open (restore the GUI view)", f.open)

    parts_after = _part_map(proj, client, only={new_id}).get(new_id, [])
    new_row = _component_row(new, {new_id: parts_after})
    errors = [st for st in steps
              if st.get("error") or st.get("rc") not in (0, None)]
    return {
        "ok": not errors and new_row["tag"] == new_bare,
        "dry_run": False, "new_component": new_row, "symbol_placed": placed,
        "symbols_shifted": (shifted if folio_row is not None else []),
        "plan": plan, "steps": steps, "errors": errors, "warnings": warnings,
        "folio_was_open": bool(folio_row and folio_row["is_open"]),
        "undo": f"delete_component(component_id={new_id})",
    }


# --------------------------------------------------------------------------
# Write-side: rename a component and account for every reference to it


def _tag_pattern(tag: str):
    """Match the mark as a whole word: K30 but not K300, K3 or MK30."""
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(tag) + r"(?![0-9])")


def _text_references(app: Any, client: Any, tag: str) -> list:
    return _text_references_multi(app, client, [tag])


def _text_references_multi(app: Any, client: Any, tags: list) -> list:
    """Every place the mark appears as literal TEXT rather than as a link.

    Symbols point at a component by id, so they re-render after a rename on
    their own. Text that merely spells the mark out (a description someone
    typed, a free text on a drawing) does not, and is what actually goes
    stale. This is the list a rename has to hand back.
    """
    pats = [(t, _tag_pattern(t)) for t in tags]

    def hit(text: str):
        for t, p in pats:
            if p.search(text):
                return t
        return None

    proj = _project(app)
    hits: list[dict] = []

    for c in _each(client, _u(_u(proj.getEwProjectComponentManager())
                              .getEwProjectComponentArray())):
        d = str(_u(c.getDescription(LANG)) or "")
        m = hit(d)
        if m:
            hits.append({"kind": "component description", "mark": m,
                         "id": _u(c.getID()), "tag": _u(c.getTag()),
                         "text": d})
    for cb in _each(client, _u(_u(proj.getEwProjectCableManager())
                               .getEwProjectCableArray())):
        d = str(_u(cb.getDescription(LANG)) or "")
        m = hit(d)
        if m:
            hits.append({"kind": "cable description", "mark": m,
                         "id": _u(cb.getID()),
                         "tag": _u(cb.getTag()), "text": d})

    file_mgr = _u(proj.getEwProjectFileManager())
    sym_mgr = _u(proj.getEwProjectSymbolManager())
    for f in _each(client, _u(file_mgr.getEwProjectFileArray())):
        fid = _u(f.getID())
        page = _u(f.getTag())
        d = str(_u(f.getDescription(LANG)) or "")
        m = hit(d)
        if m:
            hits.append({"kind": "folio description", "mark": m, "id": fid,
                         "page": page, "text": d})
        for sym in _each(client, _u(sym_mgr.getProjectSymbolsFromFileID(fid))):
            try:
                n = int(_u(sym.getTranslatableTextCount()) or 0)
            except Exception:  # noqa: BLE001
                continue
            for i in range(n):
                try:
                    tx = str(_u(sym.getTranslatableTextAt(i)) or "")
                except Exception:  # noqa: BLE001
                    continue
                m = hit(tx)
                if m:
                    hits.append({"kind": "symbol text", "mark": m,
                                 "page": page,
                                 "id": _u(sym.getID()), "index": i,
                                 "text": tx})
    return hits


def _symbols_bound_to(app: Any, client: Any, component_id: int) -> list:
    """Every drawn instance of a component, across all folios."""
    proj = _project(app)
    file_mgr = _u(proj.getEwProjectFileManager())
    sym_mgr = _u(proj.getEwProjectSymbolManager())
    out = []
    for f in _each(client, _u(file_mgr.getEwProjectFileArray())):
        fid = _u(f.getID())
        for sym in _each(client, _u(sym_mgr.getProjectSymbolsFromFileID(fid))):
            if _u(sym.getObjectID()) != component_id:
                continue
            out.append({"page": _u(f.getTag()), "file_id": fid,
                        "symbol_id": _u(sym.getID()),
                        "symbol_name": _u(sym.getEwSymbolName()),
                        "symbol_type": SYMBOL_TYPE_NAMES.get(
                            _u(sym.getEwSymbolType()),
                            str(_u(sym.getEwSymbolType()))),
                        "is_open": bool(_u(f.isOpen()))})
    return out


def rename_component(app: Any, client: Any, tag: str, new_tag: str,
                     scan_text: bool = True, refresh_folios: bool = True,
                     dry_run: bool = True) -> dict:
    """Rename a component and account for everything that refers to it.

    A component's drawn instances point at it by id, so every symbol, every
    cross-reference between pages and the BOM follow the new mark on their
    own; the pages only need re-rendering. What does NOT follow is the mark
    spelled out as literal text somewhere, so those places are listed
    (``text_references``) for a human to decide on rather than being
    rewritten blindly.

    The new mark must keep the tag root, so a relay stays rooted K.
    """
    src = _find_one_component(app, client, tag)
    src_id = _u(src.getID())
    old_bare = str(_u(src.getTag()))
    new_bare = new_tag.strip().lstrip("-")

    src_root = (str(_u(src.getTagRoot()) or "").strip()
                or _tag_root(old_bare))
    new_root = _tag_root(new_bare)
    if new_root.casefold() != src_root.casefold():
        raise ValueError(
            f"tag root must stay {src_root!r} when renaming {old_bare!r} "
            f"(got {new_bare!r}, root {new_root!r}). MR rule: a device keeps "
            "its class; relays are always rooted K.")
    if new_root != src_root:
        new_bare = src_root + new_bare[len(new_root):]
    if new_bare == old_bare:
        raise ValueError(f"{old_bare!r} already has that mark")
    try:
        _find_one_component(app, client, new_bare)
    except LookupError as e:
        if "ambiguous" in str(e):
            raise ValueError(
                f"{new_bare!r} already exists more than once: {e}") from e
    else:
        raise ValueError(f"a component tagged {new_bare!r} already exists")

    bound = _symbols_bound_to(app, client, src_id)
    text_refs = _text_references(app, client, old_bare) if scan_text else None
    plan = {
        "component": _component_row(src, None),
        "old_tag": old_bare, "new_tag": new_bare,
        "symbols_following_the_rename": bound,
        "text_references": text_refs,
        "note": ("symbols and cross-references are linked by component id and "
                 "follow the rename; any text_references spell the old mark "
                 "out and must be judged by hand"),
    }
    if dry_run:
        return {"ok": True, "dry_run": True, "plan": plan}

    steps: list[dict] = []

    def step(name: str, fn: Callable[[], Any]) -> Any:
        try:
            r = fn()
        except Exception as exc:  # noqa: BLE001
            steps.append({"step": name, "error": f"{type(exc).__name__}: {exc}"})
            return None
        steps.append({"step": name, "rc": _rc(r), "rc_name": _rc_name(_rc(r))})
        return r

    step("setTag+root+number", lambda: _set_mark(src, new_bare))
    now = str(_u(src.getTag()))

    # Re-render every page that draws it, so the sheets show the new mark.
    refreshed = []
    if refresh_folios and now == new_bare:
        for fid in sorted({b["file_id"] for b in bound}):
            f = find_folio(app, client, file_id=fid)
            was_open = bool(_u(f.isOpen()))
            rc_c = _rc(f.close()) if was_open else None
            rc_o = _rc(f.open()) if was_open else None
            refreshed.append({"file_id": fid, "page": _u(f.getTag()),
                              "was_open": was_open,
                              "close_rc": rc_c, "open_rc": rc_o})

    still_bound = _symbols_bound_to(app, client, src_id)
    errors = [st for st in steps
              if st.get("error") or st.get("rc") not in (0, None)]
    return {
        "ok": not errors and now == new_bare
              and len(still_bound) == len(bound),
        "dry_run": False,
        "old_tag": old_bare, "new_tag": now,
        "component": _component_row(src, None),
        "symbols_still_bound": still_bound,
        "folios_refreshed": refreshed,
        "text_references": text_refs,
        "steps": steps, "errors": errors,
        "undo": f"rename_component(tag={new_bare!r}, new_tag={old_bare!r})",
    }


def renumber_components(app: Any, client: Any, renames: list,
                        scan_text: bool = True, refresh_folios: bool = True,
                        dry_run: bool = True) -> dict:
    """Retag a run of devices in one pass, collision-safe.

    ``renames`` is a list of ``[old_mark, new_mark]`` pairs. Renaming a run
    one device at a time is not safe in general: as soon as the old and new
    sets overlap (K31..K38 becoming K30..K37, say) an intermediate step
    collides with a mark that is still in use. Every target is checked first,
    and when the sets overlap the devices are parked on temporary marks and
    then moved into place, so no two components ever hold the same mark.

    The text scan that ``rename_component`` runs per device runs ONCE here
    for every old mark together, which is what makes a run affordable.
    """
    proj = _project(app)
    pairs = [(str(o).strip().lstrip("-"), str(n).strip().lstrip("-"))
             for o, n in renames]
    if not pairs:
        raise ValueError("no renames given")

    resolved = []
    for old, new in pairs:
        comp = _find_one_component(app, client, old)
        root = (str(_u(comp.getTagRoot()) or "").strip()
                or _tag_root(str(_u(comp.getTag()))))
        new_root = _tag_root(new)
        if new_root.casefold() != root.casefold():
            raise ValueError(
                f"tag root must stay {root!r} when renaming {old!r} (got "
                f"{new!r}, root {new_root!r}). MR rule: a device keeps its "
                "class; relays are always rooted K.")
        if new_root != root:
            new = root + new[len(new_root):]
        resolved.append({"old": old, "new": new, "root": root,
                         "id": _u(comp.getID()), "component": comp})

    sources = {r["old"] for r in resolved}
    targets = [r["new"] for r in resolved]
    dupes = sorted({t for t in targets if targets.count(t) > 1})
    if dupes:
        raise ValueError(f"the same target mark is used more than once: {dupes}")

    # every existing mark, so a target cannot land on a device we are not moving
    taken = set()
    for c in _each(client, _u(_u(proj.getEwProjectComponentManager())
                              .getEwProjectComponentArray())):
        taken.add(str(_u(c.getTag())))
    clashes = sorted(t for t in targets if t in taken and t not in sources)
    if clashes:
        raise ValueError(
            f"these target marks already belong to devices that are not part "
            f"of this renumber: {clashes}")

    overlap = sorted(set(targets) & sources)
    temps: dict[str, str] = {}
    if overlap:
        n = 9900
        for r in resolved:
            while True:
                cand = f"{r['root']}{n}"
                n += 1
                if (cand not in taken and cand not in temps.values()
                        and cand not in targets):
                    break
            temps[r["old"]] = cand

    text_refs = (_text_references_multi(app, client, sorted(sources))
                 if scan_text else None)
    bound = {r["old"]: _symbols_bound_to(app, client, r["id"])
             for r in resolved}

    plan = {
        "renames": [{"old": r["old"], "new": r["new"], "id": r["id"],
                     "symbols_following": len(bound[r["old"]]),
                     "pages": sorted({b["page"] for b in bound[r["old"]]})}
                    for r in resolved],
        "needs_temp_marks": bool(overlap),
        "temp_marks": temps or None,
        "text_references": text_refs,
        "note": ("symbols and cross-references are linked by component id and "
                 "follow the rename; any text_references spell an old mark "
                 "out and must be judged by hand"),
    }
    if dry_run:
        return {"ok": True, "dry_run": True, "plan": plan}

    steps: list[dict] = []

    def retag(comp: Any, mark: str, label: str) -> None:
        try:
            rcs = _set_mark(comp, mark)
            bad = [k for k, v in rcs.items() if v not in (0, None)]
            steps.append({"step": label, "rc": 0 if not bad else 1,
                          "rc_name": "n/a" if not bad else f"failed: {bad}",
                          "detail": rcs})
        except Exception as exc:  # noqa: BLE001
            steps.append({"step": label, "error": f"{type(exc).__name__}: {exc}"})

    if temps:
        for r in resolved:
            retag(r["component"], temps[r["old"]],
                  f"{r['old']} -> {temps[r['old']]} (temp)")
    for r in resolved:
        retag(r["component"], r["new"], f"{r['old']} -> {r['new']}")

    results = [{"old": r["old"], "new": r["new"], "id": r["id"],
                "now": str(_u(r["component"].getTag()))} for r in resolved]
    wrong = [x for x in results if x["now"] != x["new"]]

    refreshed = []
    if refresh_folios and not wrong:
        fids = sorted({b["file_id"] for bs in bound.values() for b in bs})
        for fid in fids:
            f = find_folio(app, client, file_id=fid)
            if bool(_u(f.isOpen())):
                rc_c = _rc(f.close())
                rc_o = _rc(f.open())
                refreshed.append({"file_id": fid, "page": _u(f.getTag()),
                                  "close_rc": rc_c, "open_rc": rc_o})
            else:
                refreshed.append({"file_id": fid, "page": _u(f.getTag()),
                                  "was_open": False})

    errors = [st for st in steps
              if st.get("error") or st.get("rc") not in (0, None)]
    return {
        "ok": not errors and not wrong,
        "dry_run": False, "results": results, "mismatched": wrong,
        "used_temp_marks": bool(temps), "folios_refreshed": refreshed,
        "text_references": text_refs, "steps": steps, "errors": errors,
        "undo": "renumber_components with the pairs reversed",
    }


def audit_tag_roots(app: Any, client: Any, tag_contains: str | None = None,
                    fix: bool = False) -> dict:
    """Find components whose stored TagRoot/TagNumber disagree with the mark.

    The mark is what every drawing shows; TagRoot and TagNumber are what
    SOLIDWORKS renumbers from. When they disagree a device reads correctly on
    every sheet yet is filed under another class, and a GUI renumber can move
    it out of its series. ``fix`` writes the root and number implied by the
    mark, leaving the mark itself untouched.
    """
    proj = _project(app)
    mgr = _u(proj.getEwProjectComponentManager())
    needle = (tag_contains or "").lower()
    drift = []
    for c in _each(client, _u(mgr.getEwProjectComponentArray())):
        mark = str(_u(c.getTag()))
        if needle and needle not in mark.lower():
            continue
        root, number, _sfx = _split_mark(mark)
        cur_root = str(_u(c.getTagRoot()) or "")
        cur_num = _u(c.getTagNumber())
        root_bad = cur_root != root
        num_bad = number is not None and cur_num != number
        if not (root_bad or num_bad):
            continue
        row = {"id": _u(c.getID()), "tag": mark, "tag_path": _u(c.getTagPath()),
               "stored_root": cur_root, "expected_root": root,
               "stored_number": cur_num, "expected_number": number,
               "root_mismatch": root_bad, "number_mismatch": num_bad}
        if fix:
            row["applied"] = _set_mark(c, mark)
            row["now_root"] = str(_u(c.getTagRoot()) or "")
            row["now_number"] = _u(c.getTagNumber())
            row["now_tag"] = str(_u(c.getTag()))
        drift.append(row)
    return {"count": len(drift), "fixed": bool(fix), "components": drift}


# --------------------------------------------------------------------------
# Write-side: the document tree (folders)


def _folder_row(f: Any) -> dict:
    return {
        "id": _u(f.getID()),
        "tag": _u(f.getTag()),
        "description": _text(f, "getDescription", LANG),
        "book_id": _u(f.getEwProjectBookID()),
        "parent_folder_id": _u(f.getEwProjectFolderID()),
        "position": _u(f.getPosition()),
        "tag_root": _u(f.getTagRoot()),
        "tag_number": _u(f.getTagNumber()),
    }


def _find_folder(app: Any, client: Any, tag: str | None = None,
                 folder_id: int | None = None, book_id: int | None = None,
                 description: str | None = None) -> Any:
    proj = _project(app)
    mgr = _u(proj.getEwProjectFolderManager())
    if folder_id is not None:
        f = _u(mgr.findEwProjectFolderByID(int(folder_id)))
        if f is None:
            raise LookupError(f"no folder with id {folder_id}")
        return f
    hits = []
    for f in _each(client, _u(mgr.getEwProjectFolderArray())):
        if book_id is not None and _u(f.getEwProjectBookID()) != book_id:
            continue
        if tag is not None and str(_u(f.getTag())) != str(tag):
            continue
        if description is not None and str(
                _u(f.getDescription(LANG)) or "") != description:
            continue
        hits.append(f)
    if not hits:
        raise LookupError(
            f"no folder matching tag={tag!r} description={description!r} "
            f"book_id={book_id}")
    if len(hits) > 1:
        raise LookupError(
            f"several folders match tag={tag!r} book_id={book_id}: "
            f"{[_folder_row(h) for h in hits]}; give folder_id")
    return hits[0]


def rename_folder(app: Any, client: Any, tag: str | None = None,
                  folder_id: int | None = None, book_id: int | None = None,
                  new_tag: str | None = None,
                  new_description: str | None = None,
                  dry_run: bool = True) -> dict:
    """Retag or re-describe a folder in the document tree.

    The tree shows "<tag> - <description>", so renaming "7 - Reports" to
    "8 - Reports" is a change of tag alone. Folder tags must be unique within
    their book, so the target is checked before anything is written.
    """
    f = _find_folder(app, client, tag=tag, folder_id=folder_id,
                     book_id=book_id)
    before = _folder_row(f)
    if new_tag is None and new_description is None:
        raise ValueError("give new_tag and/or new_description")
    if new_tag is not None and str(new_tag) != str(before["tag"]):
        try:
            clash = _find_folder(app, client, tag=str(new_tag),
                                 book_id=before["book_id"])
        except LookupError:
            clash = None
        if clash is not None:
            raise ValueError(
                f"book {before['book_id']} already has a folder tagged "
                f"{new_tag!r}: {_folder_row(clash)}")
    plan = {"folder": before, "new_tag": new_tag,
            "new_description": new_description}
    if dry_run:
        return {"ok": True, "dry_run": True, "plan": plan}

    steps = {}
    if new_tag is not None:
        root, number, _sfx = _split_mark(str(new_tag))
        steps["setTag"] = _rc(f.setTag(str(new_tag)))
        if root:
            steps["setTagRoot"] = _rc(f.setTagRoot(root))
        if number is not None:
            steps["setTagNumber"] = _rc(f.setTagNumber(number))
    if new_description is not None:
        steps["setDescription"] = _rc(f.setDescription(LANG, new_description))
    steps["update"] = _rc(f.update())
    after = _folder_row(f)
    ok = ((new_tag is None or str(after["tag"]) == str(new_tag))
          and (new_description is None
               or after["description"] == new_description))
    return {"ok": ok, "dry_run": False, "before": before, "after": after,
            "steps": steps,
            "undo": (f"rename_folder(folder_id={before['id']}, "
                     f"new_tag={before['tag']!r})")}


def add_folder(app: Any, client: Any, tag: str, description: str,
               book_id: int | None = None, book_tag: str | None = None,
               parent_folder_id: int | None = None,
               position: int | None = None, after_tag: str | None = None,
               dry_run: bool = True) -> dict:
    """Create a folder in the document tree.

    ``position`` is the internal sort index that drives the order shown in
    the tree; it is NOT the tag. Give ``after_tag`` instead to slot the new
    folder directly behind an existing one, which is usually what is meant.
    """
    proj = _project(app)
    if book_id is None:
        bmgr = _u(proj.getEwProjectBookManager())
        books = list(_each(client, _u(bmgr.getEwProjectBookArray())))
        if book_tag is not None:
            hits = [b for b in books if str(_u(b.getTag())) == str(book_tag)]
            if len(hits) != 1:
                raise LookupError(
                    f"book tag {book_tag!r} matched {len(hits)} books")
            book_id = _u(hits[0].getID())
        elif len(books) == 1:
            book_id = _u(books[0].getID())
        else:
            raise ValueError(
                "several books in this project: give book_id or book_tag "
                f"({[{'id': _u(b.getID()), 'tag': _u(b.getTag())} for b in books]})")
    try:
        clash = _find_folder(app, client, tag=str(tag), book_id=book_id)
    except LookupError:
        clash = None
    if clash is not None:
        raise ValueError(
            f"book {book_id} already has a folder tagged {tag!r}: "
            f"{_folder_row(clash)}")

    siblings = []
    mgr = _u(proj.getEwProjectFolderManager())
    for f in _each(client, _u(mgr.getEwProjectFolderArray())):
        if _u(f.getEwProjectBookID()) == book_id:
            siblings.append(_folder_row(f))
    siblings.sort(key=lambda r: (r["position"]
                                 if isinstance(r["position"], int) else 0))
    if position is None and after_tag is not None:
        prev = [r for r in siblings if str(r["tag"]) == str(after_tag)]
        if not prev:
            raise LookupError(
                f"no folder tagged {after_tag!r} in book {book_id}")
        position = (prev[0]["position"] or 0) + 1
    plan = {"book_id": book_id, "tag": tag, "description": description,
            "parent_folder_id": parent_folder_id, "position": position,
            "siblings": siblings}
    if dry_run:
        return {"ok": True, "dry_run": True, "plan": plan}

    new = _u(mgr.newEwProjectFolder())
    steps = {}
    # The book (and parent) must be set BEFORE insert, as for a folio:
    # setting them afterwards leaves the object orphaned in the tree.
    steps["setEwProjectBookID"] = _rc(new.setEwProjectBookID(book_id))
    if parent_folder_id is not None:
        steps["setEwProjectFolderID"] = _rc(
            new.setEwProjectFolderID(parent_folder_id))
    steps["insert"] = _rc(new.insert())
    root, number, _sfx = _split_mark(str(tag))
    steps["setTag"] = _rc(new.setTag(str(tag)))
    if root:
        steps["setTagRoot"] = _rc(new.setTagRoot(root))
    if number is not None:
        steps["setTagNumber"] = _rc(new.setTagNumber(number))
    steps["setDescription"] = _rc(new.setDescription(LANG, description))
    if position is not None:
        steps["setPosition"] = _rc(new.setPosition(int(position)))
    steps["update"] = _rc(new.update())
    row = _folder_row(new)
    ok = (str(row["tag"]) == str(tag) and row["description"] == description
          and row["book_id"] == book_id)
    return {"ok": ok, "dry_run": False, "folder": row, "steps": steps,
            "undo": f"delete_folder(folder_id={row['id']})"}


def delete_folder(app: Any, client: Any, folder_id: int) -> dict:
    """Remove an empty folder from the document tree."""
    f = _find_folder(app, client, folder_id=folder_id)
    row = _folder_row(f)
    proj = _project(app)
    fmgr = _u(proj.getEwProjectFileManager())
    inside = [_u(x.getTag()) for x in _each(client, _u(fmgr.getEwProjectFileArray()))
              if _u(x.getEwProjectFolderID()) == folder_id]
    if inside:
        raise ValueError(
            f"folder {row['tag']!r} still holds {len(inside)} folios "
            f"({inside[:10]}); move them out first")
    rc = _rc(f.remove())
    return {"ok": rc in (0, None), "removed": row, "rc": rc,
            "rc_name": _rc_name(rc)}


# --------------------------------------------------------------------------
# Write-side: pages (folios)

FILE_TYPE_CODES = {v: k for k, v in FILE_TYPE_NAMES.items()}


def add_folio(app: Any, client: Any, description: str,
              file_type: str = "folio", folder_id: int | None = None,
              book_id: int | None = None, location_id: int | None = None,
              page_number: int | None = None, insert_before_page: int | None = None,
              dry_run: bool = True) -> dict:
    """Create a page and, optionally, slot it in at a given page number.

    ``file_type`` is a name from FILE_TYPE_NAMES, e.g. "2d_cabinet_layout"
    or "mixed_scheme". ``insert_before_page`` takes the number the new page
    should end up with and pushes every page from there on one number down,
    which is what "insert a sheet here" means; the cascade is the same
    collision-safe one ``shift_folio_numbers`` uses.

    File type, book and folder MUST be set before insert: setting them after
    leaves the page in folder -1, invisible in the tree.
    """
    proj = _project(app)
    code = FILE_TYPE_CODES.get(file_type)
    if code is None:
        raise ValueError(
            f"unknown file_type {file_type!r}; known: "
            f"{sorted(FILE_TYPE_CODES)}")
    if folder_id is not None and book_id is None:
        f = _find_folder(app, client, folder_id=folder_id)
        book_id = _u(f.getEwProjectBookID())
    if book_id is None:
        raise ValueError("give book_id or folder_id")

    existing = list_folios(app, client)["folios"]
    taken = {r["page_number"] for r in existing
             if isinstance(r["page_number"], int)}
    target = page_number if page_number is not None else insert_before_page
    plan = {"description": description, "file_type": file_type,
            "file_type_code": code, "book_id": book_id,
            "folder_id": folder_id, "location_id": location_id,
            "target_page_number": target,
            "cascade": (None if insert_before_page is None else {
                "threshold": insert_before_page, "delta": 1,
                "pages_moved": sorted(n for n in taken
                                      if n >= insert_before_page)}),
            "page_number_free": target not in taken if target else None}
    if dry_run:
        return {"ok": True, "dry_run": True, "plan": plan}

    mgr = _u(proj.getEwProjectFileManager())
    new = _u(mgr.newProjectFile())
    steps: dict[str, Any] = {}
    steps["setFileType"] = _rc(new.setFileType(code))
    steps["setEwProjectBookID"] = _rc(new.setEwProjectBookID(book_id))
    if folder_id is not None:
        steps["setEwProjectFolderID"] = _rc(new.setEwProjectFolderID(folder_id))
    steps["insert"] = _rc(new.insert())
    steps["setDescription"] = _rc(new.setDescription(LANG, description))
    if location_id is not None:
        steps["setLocationID"] = _rc(new.setLocationID(location_id, False))
    steps["update"] = _rc(new.update())
    new_id = _u(new.getID())

    cascade = None
    if insert_before_page is not None:
        from .com import app as _app_singleton
        cascade = _app_singleton()._shift_folio_numbers_locked(
            insert_before_page, 1, new_id, insert_before_page, False,
            "application")
    elif page_number is not None:
        steps["setTagNumber"] = _rc(new.setTagNumber(int(page_number)))
        steps["update2"] = _rc(new.update())

    # Read the row back AFTER the cascade: during it the new folio is parked
    # on a temporary number, and reporting that number would be a lie.
    row = _folio_row(find_folio(app, client, file_id=new_id))
    bad = {k: v for k, v in steps.items() if v not in (0, None)}
    landed = (target is None or row["page_number"] == target)
    return {"ok": not bad and landed and row["id"] == new_id,
            "bad_steps": bad, "landed_on_requested_page": landed,
            "_legacy_ok": row["folder_id"] == (
                folder_id if folder_id is not None else row["folder_id"]),
            "dry_run": False, "folio": row, "steps": steps,
            "cascade": cascade, "plan": plan,
            "undo": f"delete_folio(file_id={new_id})"}


def delete_folio(app: Any, client: Any, file_id: int) -> dict:
    """Remove a page. Refuses while it still carries symbols."""
    f = find_folio(app, client, file_id=file_id)
    row = _folio_row(f)
    proj = _project(app)
    sym_mgr = _u(proj.getEwProjectSymbolManager())
    syms = [_u(s.getID()) for s in
            _each(client, _u(sym_mgr.getProjectSymbolsFromFileID(file_id)))]
    if syms:
        raise ValueError(
            f"page {row['page']!r} still carries {len(syms)} symbols; remove "
            "them first")
    texts = list_texts(app, client, file_id=file_id)["texts"]
    if texts:
        raise ValueError(
            f"page {row['page']!r} still carries {len(texts)} free text(s) "
            f"({[t['text'] for t in texts][:5]}); remove them first")
    rc = _rc(f.remove())
    return {"ok": rc in (0, None), "removed": row, "rc": rc,
            "rc_name": _rc_name(rc)}


# --------------------------------------------------------------------------
# Drawing rules: spacing, the drawable box, and moving without breaking wires

# MR design rule: every drawn coordinate and every line end stays inside this
# box on a schematic sheet. Override per call where a sheet differs.
GO_BOX = {"x_min": 50.0, "x_max": 370.0, "y_min": 80.0, "y_max": 240.0}

# MR design rule: leave at least ONE grid dot between connections. The sheet
# grid is 10 mm, so two connection points must be at least 20 mm apart, which
# puts a dot between them. Corroborated twice on this project: 20 mm is by far
# the most common gap between points of different symbols (156 occurrences),
# and relay contacts sit 30 mm apart by ORIGIN on all 12 sheets that carry a
# row of them, which places their points exactly 20 mm apart.
GRID_PITCH = 10.0
# Dot to dot is the minimum: two connection points may sit on adjacent grid
# dots but no closer.
MIN_POINT_SPACING = GRID_PITCH
# A device's TEXT is wider than its connections. A relay contact's label runs
# about three grid pitches, so two of them on one row need their ORIGINS that
# far apart or the labels collide. This is why every relay row on this project
# is drawn at 30 mm origin spacing.
MIN_TEXT_SYMBOL_ORIGIN_SPACING = 3 * GRID_PITCH

_SNAP = 0.001


def _symbol_points(s: Any) -> list:
    out = []
    try:
        n = int(_u(s.getEwProjectSymbolPointCount()) or 0)
    except Exception:  # noqa: BLE001
        return out
    for i in range(n):
        p = _u(s.getEwProjectSymbolPointAt(i))
        if p is None:
            continue
        pos = _u(p.getPointPosition())
        if pos is None:
            continue
        out.append({"i": i, "mesh": _u(p.getMeshID()),
                    "x": float(_u(pos.getXCoordinate())),
                    "y": float(_u(pos.getYCoordinate()))})
    return out


def _folio_lines(app: Any, client: Any, file_id: int) -> list:
    """The lines drawn on one folio.

    getEwProjectLineArrayFromFileID asks SOLIDWORKS for exactly this folio's
    lines. Walking the whole project's line array and filtering on getFileID
    costs a COM round trip per line in the PROJECT, which made moving a rail
    of ten symbols slow enough to abandon. The full scan stays as a fallback
    in case the per-file call is missing on some release.
    """
    proj = _project(app)
    lmgr = _u(proj.getEwProjectLineManager())
    arr = None
    try:
        arr = _u(lmgr.getEwProjectLineArrayFromFileID(int(file_id)))
        scoped = True
    except Exception:  # noqa: BLE001
        arr = _u(lmgr.getEwProjectLineArray())
        scoped = False
    out = []
    for ln in _each(client, arr):
        if not scoped and _u(ln.getFileID()) != file_id:
            continue
        out.append({"obj": ln, "id": _u(ln.getID()),
                    "x1": float(_u(ln.getStartPointXPosition())),
                    "y1": float(_u(ln.getStartPointYPosition())),
                    "x2": float(_u(ln.getEndPointXPosition())),
                    "y2": float(_u(ln.getEndPointYPosition()))})
    return out


def move_symbol(app: Any, client: Any, symbol_id: int, dx: float = 0.0,
                dy: float = 0.0, to_x: float | None = None,
                to_y: float | None = None, move_lines: bool = True,
                box: dict | None = None, dry_run: bool = True) -> dict:
    """Move a symbol and drag every wire end that sits on its connections.

    A symbol carries its connection points with it, but the lines drawn to
    those points do NOT follow: move the symbol alone and the wires stay
    where they were, leaving the drawing connected in the database and wrong
    on the sheet. Every line end that coincides with one of this symbol's
    connection points is moved by the same delta.

    The move is refused when it would put a connection point or a line end
    outside the drawable box.
    """
    proj = _project(app)
    smgr = _u(proj.getEwProjectSymbolManager())
    sym = _u(smgr.getProjectSymbolByID(int(symbol_id)))
    if sym is None:
        raise LookupError(f"no symbol with id {symbol_id}")
    fid = _u(sym.getFileID())
    ox, oy = float(_u(sym.getXPosition())), float(_u(sym.getYPosition()))
    if to_x is not None:
        dx = to_x - ox
    if to_y is not None:
        dy = to_y - oy
    if abs(dx) < _SNAP and abs(dy) < _SNAP:
        raise ValueError("the move is zero")

    pts = _symbol_points(sym)
    lines = _folio_lines(app, client, fid)
    moves = []
    for ln in lines:
        ends = []
        for tag, lx, ly in (("start", ln["x1"], ln["y1"]),
                            ("end", ln["x2"], ln["y2"])):
            for p in pts:
                if abs(lx - p["x"]) < 0.01 and abs(ly - p["y"]) < 0.01:
                    ends.append(tag)
                    break
        if ends:
            moves.append({"line_id": ln["id"], "ends": ends, "obj": ln["obj"],
                          "from": [ln["x1"], ln["y1"], ln["x2"], ln["y2"]]})

    bx = {**GO_BOX, **(box or {})}
    after_pts = [{"x": p["x"] + dx, "y": p["y"] + dy} for p in pts]
    out_of_box = [p for p in after_pts
                  if not (bx["x_min"] - _SNAP <= p["x"] <= bx["x_max"] + _SNAP
                          and bx["y_min"] - _SNAP <= p["y"] <= bx["y_max"] + _SNAP)]
    for m in moves:
        x1, y1, x2, y2 = m["from"]
        nx1 = x1 + dx if "start" in m["ends"] else x1
        ny1 = y1 + dy if "start" in m["ends"] else y1
        nx2 = x2 + dx if "end" in m["ends"] else x2
        ny2 = y2 + dy if "end" in m["ends"] else y2
        m["to"] = [nx1, ny1, nx2, ny2]
        for px, py in ((nx1, ny1), (nx2, ny2)):
            if not (bx["x_min"] - _SNAP <= px <= bx["x_max"] + _SNAP
                    and bx["y_min"] - _SNAP <= py <= bx["y_max"] + _SNAP):
                out_of_box.append({"x": px, "y": py, "line_id": m["line_id"]})

    plan = {"symbol_id": symbol_id, "file_id": fid,
            "from": [ox, oy], "to": [ox + dx, oy + dy], "dx": dx, "dy": dy,
            "points_before": [{"x": p["x"], "y": p["y"]} for p in pts],
            "points_after": after_pts,
            "lines_to_drag": [{"line_id": m["line_id"], "ends": m["ends"],
                               "from": m["from"], "to": m["to"]}
                              for m in moves],
            "box": bx, "out_of_box": out_of_box}
    if out_of_box:
        return {"ok": False, "dry_run": dry_run, "plan": plan,
                "error": f"the move puts {len(out_of_box)} point(s) outside "
                         f"the drawable box {bx}"}
    if dry_run:
        return {"ok": True, "dry_run": True, "plan": plan}

    f = find_folio(app, client, file_id=fid)
    was_open = bool(_u(f.isOpen()))
    steps: dict[str, Any] = {}
    if was_open:
        steps["folio.close"] = _rc(f.close())
        if steps["folio.close"] not in (0, None):
            return {"ok": False, "dry_run": False, "plan": plan, "steps": steps,
                    "error": "could not close the folio; refusing to move "
                             "into a stale editor copy"}
    steps["setXPosition"] = _rc(sym.setXPosition(ox + dx))
    steps["setYPosition"] = _rc(sym.setYPosition(oy + dy))
    steps["symbol.update"] = _rc(sym.update())
    if move_lines:
        for m in moves:
            ln = m["obj"]
            nx1, ny1, nx2, ny2 = m["to"]
            if "start" in m["ends"]:
                _rc(ln.setStartPointXPosition(nx1))
                _rc(ln.setStartPointYPosition(ny1))
            if "end" in m["ends"]:
                _rc(ln.setEndPointXPosition(nx2))
                _rc(ln.setEndPointYPosition(ny2))
            m["update_rc"] = _rc(ln.update())
    if was_open:
        steps["folio.open"] = _rc(f.open())
    return {"ok": True, "dry_run": False, "plan": plan, "steps": steps,
            "lines_moved": [{"line_id": m["line_id"], "to": m["to"],
                             "update_rc": m.get("update_rc")} for m in moves]}


def move_symbols(app: Any, client: Any, moves: list,
                 box: dict | None = None, dry_run: bool = True) -> dict:
    """Move several symbols on ONE folio in a single pass.

    Doing it one at a time is quadratic in practice: the project line array
    is the only way to reach a folio's lines, so every single move walks
    every line in the project, and the folio is closed and reopened each
    time. Here the lines are read once, the folio is closed once, and each
    symbol and its wire ends move inside that window.

    ``moves`` is a list of ``{"symbol_id": int, "dx": float, "dy": float}``.
    """
    proj = _project(app)
    smgr = _u(proj.getEwProjectSymbolManager())
    bx = {**GO_BOX, **(box or {})}
    seen_ids = set()
    for m in moves:
        if int(m["symbol_id"]) in seen_ids:
            raise ValueError(
                f"symbol {m['symbol_id']} appears twice in one batch; the "
                "second delta would be computed from the same original "
                "position and silently win")
        seen_ids.add(int(m["symbol_id"]))
    jobs = []
    fid = None
    for m in moves:
        sym = _u(smgr.getProjectSymbolByID(int(m["symbol_id"])))
        if sym is None:
            raise LookupError(f"no symbol with id {m['symbol_id']}")
        f_id = _u(sym.getFileID())
        if fid is None:
            fid = f_id
        elif f_id != fid:
            raise ValueError(
                "all the symbols must be on one folio; got "
                f"{fid} and {f_id}")
        jobs.append({"sym": sym, "id": _u(sym.getID()),
                     "dx": float(m.get("dx", 0.0)),
                     "dy": float(m.get("dy", 0.0)),
                     "ox": float(_u(sym.getXPosition())),
                     "oy": float(_u(sym.getYPosition())),
                     "points": _symbol_points(sym)})

    lines = _folio_lines(app, client, fid)      # ONE scan for the whole batch
    for j in jobs:
        j["line_moves"] = []
        for ln in lines:
            ends = []
            for tag, lx, ly in (("start", ln["x1"], ln["y1"]),
                                ("end", ln["x2"], ln["y2"])):
                if any(abs(lx - p["x"]) < 0.01 and abs(ly - p["y"]) < 0.01
                       for p in j["points"]):
                    ends.append(tag)
            if ends:
                j["line_moves"].append({"line": ln, "ends": ends})

    def _in_box(px, py):
        return (bx["x_min"] - _SNAP <= px <= bx["x_max"] + _SNAP
                and bx["y_min"] - _SNAP <= py <= bx["y_max"] + _SNAP)

    out_of_box = []
    for j in jobs:
        for p in j["points"]:
            nx, ny = p["x"] + j["dx"], p["y"] + j["dy"]
            if not _in_box(nx, ny):
                out_of_box.append({"symbol_id": j["id"], "x": nx, "y": ny})
        # The dragged wire ends have to stay in the frame too. Checking only
        # the symbol points let a batch move push a wire off the sheet, where
        # the same move one at a time was refused.
        for lm in j["line_moves"]:
            ln, ends = lm["line"], lm["ends"]
            if "start" in ends and not _in_box(ln["x1"] + j["dx"],
                                               ln["y1"] + j["dy"]):
                out_of_box.append({"line_id": ln["id"], "end": "start",
                                   "x": ln["x1"] + j["dx"],
                                   "y": ln["y1"] + j["dy"]})
            if "end" in ends and not _in_box(ln["x2"] + j["dx"],
                                             ln["y2"] + j["dy"]):
                out_of_box.append({"line_id": ln["id"], "end": "end",
                                   "x": ln["x2"] + j["dx"],
                                   "y": ln["y2"] + j["dy"]})
    plan = {"file_id": fid, "count": len(jobs), "box": bx,
            "moves": [{"symbol_id": j["id"], "from": [j["ox"], j["oy"]],
                       "to": [j["ox"] + j["dx"], j["oy"] + j["dy"]],
                       "lines": len(j["line_moves"])} for j in jobs],
            "out_of_box": out_of_box}
    if out_of_box:
        return {"ok": False, "dry_run": dry_run, "plan": plan,
                "error": "some points would land outside the drawable box"}
    if dry_run:
        return {"ok": True, "dry_run": True, "plan": plan}

    f = find_folio(app, client, file_id=fid)
    was_open = bool(_u(f.isOpen()))
    steps = {}
    if was_open:
        steps["folio.close"] = _rc(f.close())
        if steps["folio.close"] not in (0, None):
            return {"ok": False, "dry_run": False, "plan": plan,
                    "steps": steps, "error": "could not close the folio"}
    for j in jobs:
        _rc(j["sym"].setXPosition(j["ox"] + j["dx"]))
        _rc(j["sym"].setYPosition(j["oy"] + j["dy"]))
        _rc(j["sym"].update())
        for lm in j["line_moves"]:
            ln, ends = lm["line"], lm["ends"]
            if "start" in ends:
                _rc(ln["obj"].setStartPointXPosition(ln["x1"] + j["dx"]))
                _rc(ln["obj"].setStartPointYPosition(ln["y1"] + j["dy"]))
            if "end" in ends:
                _rc(ln["obj"].setEndPointXPosition(ln["x2"] + j["dx"]))
                _rc(ln["obj"].setEndPointYPosition(ln["y2"] + j["dy"]))
            _rc(ln["obj"].update())
    if was_open:
        steps["folio.open"] = _rc(f.open())
    return {"ok": True, "dry_run": False, "plan": plan, "steps": steps}


def check_drawing_rules(app: Any, client: Any, page: str | int | None = None,
                        file_id: int | None = None,
                        min_spacing: float | None = None,
                        box: dict | None = None) -> dict:
    """Report connection points that crowd each other or fall outside the box.

    Two checks, both on the real geometry rather than on intent: connection
    points of DIFFERENT symbols closer than ``min_spacing`` along a shared
    row or column, and any connection point or line end outside the drawable
    box. Reports only; nothing is moved.
    """
    f = find_folio(app, client, page=page, file_id=file_id)
    row = _folio_row(f)
    fid = row["id"]
    proj = _project(app)
    smgr = _u(proj.getEwProjectSymbolManager())
    cmgr = _u(proj.getEwProjectComponentManager())
    bx = {**GO_BOX, **(box or {})}
    gap = MIN_POINT_SPACING if min_spacing is None else float(min_spacing)

    pts = []
    for s in _each(client, _u(smgr.getProjectSymbolsFromFileID(fid))):
        oid = _u(s.getObjectID())
        tag = None
        if isinstance(oid, int) and oid > 0:
            c = _u(cmgr.findEwProjectComponentByID(oid))
            if c is not None:
                tag = str(_u(c.getTag()))
        for p in _symbol_points(s):
            pts.append({"symbol_id": _u(s.getID()), "tag": tag,
                        "name": _u(s.getEwSymbolName()), **p})

    crowded = []
    for i, p in enumerate(pts):
        for q in pts[i + 1:]:
            if p["symbol_id"] == q["symbol_id"]:
                continue
            same_row = abs(p["y"] - q["y"]) < 0.5
            same_col = abs(p["x"] - q["x"]) < 0.5
            if not (same_row or same_col):
                continue
            d = abs(p["x"] - q["x"]) if same_row else abs(p["y"] - q["y"])
            if d <= _SNAP:
                crowded.append({"gap": 0.0, "axis": "coincident",
                                "a": {"tag": p["tag"], "symbol_id": p["symbol_id"],
                                      "x": p["x"], "y": p["y"]},
                                "b": {"tag": q["tag"], "symbol_id": q["symbol_id"],
                                      "x": q["x"], "y": q["y"]}})
            elif d < gap - _SNAP:
                crowded.append({"gap": round(d, 3), "axis":
                                "row" if same_row else "column",
                                "a": {"tag": p["tag"], "symbol_id": p["symbol_id"],
                                      "x": p["x"], "y": p["y"]},
                                "b": {"tag": q["tag"], "symbol_id": q["symbol_id"],
                                      "x": q["x"], "y": q["y"]}})

    def outside(x, y):
        return not (bx["x_min"] - _SNAP <= x <= bx["x_max"] + _SNAP
                    and bx["y_min"] - _SNAP <= y <= bx["y_max"] + _SNAP)

    out = [{"kind": "connection point", "tag": p["tag"],
            "symbol_id": p["symbol_id"], "x": p["x"], "y": p["y"]}
           for p in pts if outside(p["x"], p["y"])]
    for ln in _folio_lines(app, client, fid):
        for tag, x, y in (("start", ln["x1"], ln["y1"]),
                          ("end", ln["x2"], ln["y2"])):
            if outside(x, y):
                out.append({"kind": f"line {tag}", "line_id": ln["id"],
                            "x": x, "y": y})
    crowded.sort(key=lambda r: r["gap"])
    return {"folio": row, "min_spacing": gap, "box": bx,
            "crowded_count": len(crowded), "crowded": crowded,
            "outside_count": len(out), "outside_box": out,
            "ok": not crowded and not out}


def add_location(app: Any, client: Any, tag: str, description: str,
                 parent_location_id: int | None = None,
                 dry_run: bool = True) -> dict:
    """Create a location. Tags are unique among siblings."""
    proj = _project(app)
    mgr = _u(proj.getEwProjectLocationManager())
    sibs = []
    for l in _each(client, _u(mgr.getEwProjectLocationArray())):
        sibs.append({"id": _u(l.getID()), "tag": str(_u(l.getTag())),
                     "tag_path": _u(l.getTagPath()),
                     "description": _text(l, "getDescription", LANG)})
    clash = [x for x in sibs if str(x["tag"]) == str(tag)]
    if clash:
        raise ValueError(
            f"a location tagged {tag!r} already exists: {clash}")
    plan = {"tag": tag, "description": description,
            "parent_location_id": parent_location_id, "siblings": sibs}
    if dry_run:
        return {"ok": True, "dry_run": True, "plan": plan}
    new = _u(mgr.newEwProjectLocation())
    st = {"insert": _rc(new.insert())}
    st.update(_set_mark(new, str(tag)))
    st["setDescription"] = _rc(new.setDescription(LANG, description))
    if parent_location_id is not None:
        st["setParentID"] = _rc(new.setParentID(int(parent_location_id)))
    st["update"] = _rc(new.update())
    bad = {k: v for k, v in st.items() if v not in (0, None)}
    return {"ok": not bad and str(_u(new.getTag())) == str(tag),
            "bad_steps": bad, "dry_run": False,
            "location": {"id": _u(new.getID()), "tag": _u(new.getTag()),
                         "tag_path": _u(new.getTagPath()),
                         "description": _text(new, "getDescription", LANG)},
            "steps": st}


def attach_manufacturer_part(app: Any, client: Any, tag: str,
                             manufacturer: str, reference: str,
                             description: str | None = None,
                             width: float | None = None,
                             height: float | None = None,
                             depth: float | None = None,
                             dry_run: bool = True) -> dict:
    """Give a component a manufacturer part the LIBRARY does not carry.

    ``assignManufacturerPart`` resolves against the environment catalogue and
    answers EW_BAD_INPUTS (2) for anything not in it, which is what happens
    with a part that only exists on this project. Creating the project part
    and binding it to the component with setObjectID does the same job: that
    is the link getEwProjectComponent reads back, so the part then shows up
    on the component exactly like a catalogue one.
    """
    comp = _find_one_component(app, client, tag)
    cid = _u(comp.getID())
    proj = _project(app)
    mgr = _u(proj.getEwProjectManufacturerPartManager())
    existing = []
    for p in _each(client, _u(mgr.getEwProjectManufacturerPartArray())):
        if (_u(p.getObjectID()) == cid
                and str(_u(p.getReference())) == reference
                and str(_u(p.getManufacturer())) == manufacturer):
            existing.append(_u(p.getID()))
    plan = {"component": _component_row(comp, None),
            "manufacturer": manufacturer, "reference": reference,
            "description": description, "already_attached": existing}
    if dry_run:
        return {"ok": True, "dry_run": True, "plan": plan}
    if existing:
        return {"ok": True, "dry_run": False, "plan": plan,
                "note": "already attached", "part_ids": existing}
    new = _u(mgr.newEwProjectManufacturerPart())
    st = {"insert": _rc(new.insert())}
    st["setManufacturer"] = _rc(new.setManufacturer(manufacturer))
    st["setReference"] = _rc(new.setReference(reference))
    if description:
        st["setDescription"] = _rc(new.setDescription(LANG, description))
    for name, val in (("setWidth", width), ("setHeight", height),
                      ("setDepth", depth)):
        if val is not None:
            st[name] = _rc(getattr(new, name)(float(val)))
    st["setObjectID"] = _rc(new.setObjectID(cid))
    st["update"] = _rc(new.update())
    parts = _part_map(proj, client, only={cid}).get(cid, [])
    bad = {k: v for k, v in st.items() if v not in (0, None)}
    return {"ok": not bad and any(p["reference"] == reference for p in parts),
            "dry_run": False, "part_id": _u(new.getID()), "steps": st,
            "bad_steps": bad, "component_parts": parts,
            "undo": f"remove project manufacturer part {_u(new.getID())}"}


def place_symbol(app: Any, client: Any, tag: str, symbol_name: str,
                 x: float, y: float, page: str | int | None = None,
                 file_id: int | None = None, symbol_type: int = 20,
                 rotation: float = 0.0, x_scale: float | None = None,
                 y_scale: float | None = None, box: dict | None = None,
                 dry_run: bool = True) -> dict:
    """Draw an existing component on a page.

    The counterpart to add_component for a component that already exists:
    a device is normally drawn several times, its footprint on the cabinet
    layout and a contact or coil on each schematic that uses it. The folio is
    closed first when the GUI has it open, because symbols inserted into an
    open folio are discarded when the editor writes its copy back.
    """
    comp = _find_one_component(app, client, tag)
    cid = _u(comp.getID())
    f = find_folio(app, client, page=page, file_id=file_id)
    row = _folio_row(f)
    bx = {**GO_BOX, **(box or {})}
    if not (bx["x_min"] - _SNAP <= x <= bx["x_max"] + _SNAP
            and bx["y_min"] - _SNAP <= y <= bx["y_max"] + _SNAP):
        raise ValueError(f"({x}, {y}) is outside the drawable box {bx}")
    plan = {"component": _component_row(comp, None), "folio": row,
            "symbol_name": symbol_name, "symbol_type": symbol_type,
            "x": x, "y": y, "rotation": rotation,
            "x_scale": x_scale, "y_scale": y_scale,
            "note": ("a dry run can only check the origin: a symbol's "
                     "connection points exist only after the insert, so the "
                     "live call may still refuse this position")}
    if dry_run:
        return {"ok": True, "dry_run": True, "plan": plan}

    was_open = row["is_open"]
    steps: dict[str, Any] = {}
    if was_open:
        steps["folio.close"] = _rc(f.close())
        if steps["folio.close"] not in (0, None):
            return {"ok": False, "dry_run": False, "plan": plan, "steps": steps,
                    "error": "could not close the folio; refusing to insert "
                             "into a stale editor copy"}
    sym = _u(f.newEwProjectSymbolFromSymbolType(symbol_type))
    if sym is None:
        if was_open:
            f.open()
        return {"ok": False, "dry_run": False, "plan": plan,
                "error": "newEwProjectSymbolFromSymbolType returned NULL"}
    steps["setObjectID"] = _rc(sym.setObjectID(cid))
    steps["setEwSymbolName"] = _rc(sym.setEwSymbolName(symbol_name))
    steps["setXPosition"] = _rc(sym.setXPosition(float(x)))
    steps["setYPosition"] = _rc(sym.setYPosition(float(y)))
    if rotation:
        steps["setRotationAngle"] = _rc(sym.setRotationAngle(float(rotation)))
    if x_scale is not None:
        steps["setXScale"] = _rc(sym.setXScale(float(x_scale)))
    if y_scale is not None:
        steps["setYScale"] = _rc(sym.setYScale(float(y_scale)))
    steps["insert"] = _rc(sym.insert())
    # A symbol's connection points are only knowable after the insert: the
    # origin can sit inside the box while a terminal hangs outside it, which
    # is how a coil placed at y=88 put its A2 at y=78. Check the real points
    # and take the symbol back out rather than leave a bad one on the sheet.
    pts = _symbol_points(sym)
    stray = [p for p in pts
             if not (bx["x_min"] - _SNAP <= p["x"] <= bx["x_max"] + _SNAP
                     and bx["y_min"] - _SNAP <= p["y"] <= bx["y_max"] + _SNAP)]
    if stray:
        sid = _u(sym.getID())
        rc_rm = _rc(sym.remove())
        steps["remove (points outside the box)"] = rc_rm
        if was_open:
            steps["folio.open"] = _rc(f.open())
        return {"ok": False, "dry_run": False, "plan": plan, "steps": steps,
                "points": pts, "outside_box": stray,
                "removed_cleanly": rc_rm in (0, None),
                "error": (f"placed at ({x}, {y}) the symbol puts "
                          f"{len(stray)} connection point(s) outside the "
                          f"drawable box {bx}; symbol {sid} "
                          + ("was removed" if rc_rm in (0, None) else
                             f"could NOT be removed (rc {rc_rm}) and is still "
                             "on the sheet"))}
    if was_open:
        steps["folio.open"] = _rc(f.open())
    bad = {k: v for k, v in steps.items() if v not in (0, None)}
    return {"ok": not bad, "dry_run": False, "symbol_id": _u(sym.getID()),
            "plan": plan, "points": pts, "steps": steps, "bad_steps": bad,
            "undo": f"remove symbol {_u(sym.getID())}"}


def remove_symbol(app: Any, client: Any, symbol_id: int) -> dict:
    """Delete one drawn symbol, closing the folio first if the GUI has it."""
    proj = _project(app)
    smgr = _u(proj.getEwProjectSymbolManager())
    sym = _u(smgr.getProjectSymbolByID(int(symbol_id)))
    if sym is None:
        raise LookupError(f"no symbol with id {symbol_id}")
    fid = _u(sym.getFileID())
    f = find_folio(app, client, file_id=fid)
    was_open = bool(_u(f.isOpen()))
    steps = {}
    if was_open:
        steps["folio.close"] = _rc(f.close())
    # Lines drawn to this symbol do not disappear with it, so the sheet would
    # show wires running into nothing. Report them; the caller decides.
    pts = _symbol_points(sym)
    dangling = []
    for ln in _folio_lines(app, client, fid):
        for tag, lx, ly in (("start", ln["x1"], ln["y1"]),
                            ("end", ln["x2"], ln["y2"])):
            if any(abs(lx - p["x"]) < 0.01 and abs(ly - p["y"]) < 0.01
                   for p in pts):
                dangling.append({"line_id": ln["id"], "end": tag,
                                 "x": lx, "y": ly})
    steps["remove"] = _rc(sym.remove())
    if was_open:
        steps["folio.open"] = _rc(f.open())
    return {"ok": steps["remove"] in (0, None), "symbol_id": symbol_id,
            "file_id": fid, "steps": steps,
            "dangling_line_ends": dangling,
            "note": (None if not dangling else
                     f"{len(dangling)} wire end(s) now hang on nothing where "
                     "this symbol was; remove or redraw them")}


def add_text(app: Any, client: Any, text: str, x: float, y: float,
             page: str | int | None = None, file_id: int | None = None,
             rotation: float = 0.0, box: dict | None = None,
             dry_run: bool = True) -> dict:
    """Put a free text on a page (a circuit number, a note, a wire spec).

    Text carries no connection points, so it is not part of any net; it is
    annotation. It still has to sit inside the drawable box.
    """
    f = find_folio(app, client, page=page, file_id=file_id)
    row = _folio_row(f)
    bx = {**GO_BOX, **(box or {})}
    if not (bx["x_min"] - _SNAP <= x <= bx["x_max"] + _SNAP
            and bx["y_min"] - _SNAP <= y <= bx["y_max"] + _SNAP):
        raise ValueError(f"({x}, {y}) is outside the drawable box {bx}")
    plan = {"folio": row, "text": text, "x": x, "y": y, "rotation": rotation}
    if dry_run:
        return {"ok": True, "dry_run": True, "plan": plan}
    was_open = row["is_open"]
    steps: dict[str, Any] = {}
    if was_open:
        steps["folio.close"] = _rc(f.close())
        if steps["folio.close"] not in (0, None):
            return {"ok": False, "dry_run": False, "plan": plan,
                    "steps": steps, "error": "could not close the folio"}
    t = _u(f.newEwProjectMultilingualText())
    if t is None:
        if was_open:
            f.open()
        return {"ok": False, "dry_run": False, "plan": plan,
                "error": "newEwProjectMultilingualText returned NULL"}
    # Text is the exception to this project's usual newX -> insert -> set
    # order: inserting an EMPTY text answers EW_BAD_INPUTS (2) and leaves the
    # object at id -1, after which update answers EW_INVALID_OBJECT (9). The
    # content and position have to be set BEFORE the insert.
    steps["setText"] = _rc(t.setText(LANG, text))
    steps["setXPosition"] = _rc(t.setXPosition(float(x)))
    steps["setYPosition"] = _rc(t.setYPosition(float(y)))
    if rotation:
        steps["setRotationAngle"] = _rc(t.setRotationAngle(float(rotation)))
    steps["insert"] = _rc(t.insert())
    steps["update"] = _rc(t.update())
    if was_open:
        steps["folio.open"] = _rc(f.open())
    bad = {k: v for k, v in steps.items() if v not in (0, None)}
    tid = _u(t.getID())
    return {"ok": not bad and isinstance(tid, int) and tid > 0,
            "dry_run": False, "text_id": tid,
            "read_back": _u(t.getText(LANG)), "steps": steps, "bad_steps": bad}


def list_texts(app: Any, client: Any, page: str | int | None = None,
               file_id: int | None = None) -> dict:
    """Every free text on a page, with id, content and position."""
    f = find_folio(app, client, page=page, file_id=file_id)
    fid = _u(f.getID())
    proj = _project(app)
    rows = []
    # Ask for this folio's texts rather than filtering the project's, the
    # same reason as the lines: the scan costs a round trip per text in the
    # whole project.
    mgr = _u(proj.getEwProjectMultilingualTextManager())
    arr, scoped = None, True
    try:
        arr = _u(mgr.getEwProjectMultilingualTextByFileIDArray(int(fid)))
    except Exception:  # noqa: BLE001
        scoped = False
        try:
            arr = _u(mgr.getEwProjectMultilingualTextArray())
        except Exception:  # noqa: BLE001
            arr = None
    if arr is not None:
        for t in _each(client, arr):
            if not scoped and _u(t.getFileID()) != fid:
                continue
            rows.append({"id": _u(t.getID()), "text": _u(t.getText(LANG)),
                         "x": _u(t.getXPosition()), "y": _u(t.getYPosition())})
    return {"folio": _folio_row(f), "count": len(rows), "texts": rows}


def remove_text(app: Any, client: Any, text_id: int) -> dict:
    """Delete one free text by id."""
    proj = _project(app)
    mgr = _u(proj.getEwProjectMultilingualTextManager())
    for t in _each(client, _u(mgr.getEwProjectMultilingualTextArray())):
        if _u(t.getID()) != int(text_id):
            continue
        fid = _u(t.getFileID())
        f = find_folio(app, client, file_id=fid)
        was_open = bool(_u(f.isOpen()))
        steps = {}
        if was_open:
            steps["folio.close"] = _rc(f.close())
        steps["remove"] = _rc(t.remove())
        if was_open:
            steps["folio.open"] = _rc(f.open())
        return {"ok": steps["remove"] in (0, None), "text_id": text_id,
                "file_id": fid, "steps": steps}
    raise LookupError(f"no text with id {text_id}")


def delete_location(app: Any, client: Any, location_id: int) -> dict:
    """Remove a location that nothing references.

    Refuses while a component, a folio or a cable still points at it, since
    removing it would leave those objects pointing at nothing.
    """
    proj = _project(app)
    mgr = _u(proj.getEwProjectLocationManager())
    loc = _u(mgr.findEwProjectLocationByID(int(location_id)))
    if loc is None:
        raise LookupError(f"no location with id {location_id}")
    row = {"id": _u(loc.getID()), "tag": _u(loc.getTag()),
           "tag_path": _u(loc.getTagPath()),
           "description": _text(loc, "getDescription", LANG)}
    users: list[dict] = []
    for c in _each(client, _u(_u(proj.getEwProjectComponentManager())
                              .getEwProjectComponentArray())):
        if _u(c.getLocationID()) == int(location_id):
            users.append({"kind": "component", "tag": _u(c.getTag())})
    for f in _each(client, _u(_u(proj.getEwProjectFileManager())
                              .getEwProjectFileArray())):
        if _u(f.getLocationID()) == int(location_id):
            users.append({"kind": "folio", "page": _u(f.getTag())})
    for cb in _each(client, _u(_u(proj.getEwProjectCableManager())
                               .getEwProjectCableArray())):
        if int(location_id) in (_u(cb.getUpStreamLocationID()),
                                _u(cb.getDownStreamLocationID())):
            users.append({"kind": "cable", "tag": _u(cb.getTag())})
    if users:
        raise ValueError(
            f"location {row['tag']!r} is still used by {len(users)} object(s): "
            f"{users[:8]}")
    rc = _rc(loc.remove())
    return {"ok": rc in (0, None), "removed": row, "rc": rc,
            "rc_name": _rc_name(rc)}


# Sheet widths in millimetres, keyed by the PDF page width in points.
_SHEET_WIDTH_MM = {1191: 420.0, 842: 297.0, 1684: 594.0, 2384: 841.0}


def check_page_ink(app: Any, client: Any, page: str | int | None = None,
                   file_id: int | None = None, box: dict | None = None,
                   sheet_width_mm: float | None = None,
                   max_frame_mm: float = 250.0,
                   frame_thickness_mm: float = 3.0) -> dict:
    """Measure what is actually DRAWN on a folio and flag ink outside the box.

    ``check_drawing_rules`` only sees connection points and line ends. A 2D
    footprint imported from a DWG reports ``getWidth`` 0 and carries no
    connection points, so a footprint whose body hangs outside the drawable
    box passes that check while the exported sheet clearly shows it hanging
    out. This exports the folio and measures the real vector ink instead.

    Frame and title-block geometry is excluded: any path longer than
    ``max_frame_mm`` that is also thinner than ``frame_thickness_mm`` (a
    border rule is long AND thin - length alone would discard a large device,
    and the AN-2823-AB enclosure is 260 mm wide), and anything lying wholly
    below the box. What remains is device geometry.
    """
    try:
        import fitz
    except ImportError:
        return {"ok": False,
                "error": "PyMuPDF (fitz) is not installed; "
                         "pip install pymupdf to use check_page_ink"}
    bx = {**GO_BOX, **(box or {})}
    f = find_folio(app, client, page=page, file_id=file_id)
    row = _folio_row(f)
    tmp = os.path.join(tempfile.gettempdir(),
                       f"swe_ink_{row['id']}_{os.getpid()}.pdf")
    try:
        exp = export_folio_pdf(app, client, output_path=tmp, file_ids=[row["id"]])
        if not exp.get("ok"):
            return {"ok": False, "folio": row, "error": "export failed",
                    "export": exp}
        doc = fitz.open(tmp)
        try:
            pg = doc[0]
            w_pt, h_pt = pg.rect.width, pg.rect.height
            sw = (float(sheet_width_mm) if sheet_width_mm
                  else _SHEET_WIDTH_MM.get(round(w_pt)))
            if not sw:
                return {"ok": False, "folio": row,
                        "error": f"unknown sheet size {w_pt:.0f}x{h_pt:.0f} pt; "
                                 "pass sheet_width_mm"}
            s = w_pt / sw
            rects = [p["rect"] for p in pg.get_drawings()]
        finally:
            doc.close()
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)

    ink = []
    for r in rects:
        long_mm = max(r.width, r.height) / s
        short_mm = min(r.width, r.height) / s
        # A border rule is long AND thin. Testing length alone would discard
        # a genuinely large device: the AN-2823-AB enclosure is 260 mm wide.
        if long_mm > max_frame_mm and short_mm < frame_thickness_mm:
            continue
        if (h_pt - r.y0) / s < bx["y_min"]:
            continue                      # wholly below the drawable box
        ink.append(r)
    if not ink:
        return {"ok": True, "folio": row, "box": bx, "paths": 0,
                "extent": None, "inside": True, "overflow": {}}

    x0 = min(r.x0 for r in ink) / s
    x1 = max(r.x1 for r in ink) / s
    y0 = (h_pt - max(r.y1 for r in ink)) / s
    y1 = (h_pt - min(r.y0 for r in ink)) / s
    over = {}
    if x0 < bx["x_min"] - _SNAP:
        over["left"] = round(bx["x_min"] - x0, 2)
    if x1 > bx["x_max"] + _SNAP:
        over["right"] = round(x1 - bx["x_max"], 2)
    if y0 < bx["y_min"] - _SNAP:
        over["bottom"] = round(bx["y_min"] - y0, 2)
    if y1 > bx["y_max"] + _SNAP:
        over["top"] = round(y1 - bx["y_max"], 2)
    return {"ok": True, "folio": row, "box": bx, "paths": len(ink),
            "extent": {"x_min": round(x0, 2), "x_max": round(x1, 2),
                       "y_min": round(y0, 2), "y_max": round(y1, 2)},
            "inside": not over, "overflow": over,
            "note": ("drawn ink is inside the drawable box" if not over
                     else "drawn geometry hangs outside the box by the "
                          "millimetres listed in overflow")}


def place_symbols(app: Any, client: Any, placements: list[dict],
                  page: str | int | None = None, file_id: int | None = None,
                  box: dict | None = None, dry_run: bool = True) -> dict:
    """Draw several existing components on ONE page, closing the folio once.

    ``place_symbol`` closes and reopens the folio around every single insert,
    because a symbol written into a folio the GUI has open is discarded when
    the editor saves its copy back. Doing that per device churns the editor
    hard - laying out a twelve-device cabinet page meant twelve close/open
    cycles - and that churn can take SOLIDWORKS Electrical down.

    This closes the folio once, inserts every placement, and reopens once at
    the end, so the tab is opened and closed per TASK rather than per device.

    Each entry of ``placements`` is a dict with ``tag`` and ``symbol_name``,
    ``x`` and ``y``, and optionally ``symbol_type`` (default 20),
    ``rotation``, ``x_scale`` and ``y_scale``. A placement whose origin or
    connection points fall outside the box is rejected and taken back out;
    the rest still go in, and the result reports each one separately.
    """
    if not placements:
        return {"ok": False, "error": "no placements given"}
    bx = {**GO_BOX, **(box or {})}
    f = find_folio(app, client, page=page, file_id=file_id)
    row = _folio_row(f)

    prepared, planned = [], []
    for i, p in enumerate(placements):
        try:
            tag = str(p["tag"])
            name = str(p["symbol_name"])
            x, y = float(p["x"]), float(p["y"])
        except (KeyError, TypeError, ValueError) as exc:
            return {"ok": False, "folio": row,
                    "error": f"placement {i} is malformed: {exc}"}
        comp = _find_one_component(app, client, tag)
        inside = (bx["x_min"] - _SNAP <= x <= bx["x_max"] + _SNAP
                  and bx["y_min"] - _SNAP <= y <= bx["y_max"] + _SNAP)
        planned.append({"tag": tag, "symbol_name": name, "x": x, "y": y,
                        "origin_inside_box": inside})
        if not inside:
            return {"ok": False, "folio": row, "planned": planned,
                    "error": f"placement {i} ({tag}) origin ({x}, {y}) is "
                             f"outside the drawable box {bx}"}
        prepared.append((p, tag, name, x, y, _u(comp.getID())))

    if dry_run:
        return {"ok": True, "dry_run": True, "folio": row,
                "count": len(planned), "planned": planned,
                "note": "origins checked; connection points are only "
                        "knowable after the insert"}

    was_open = row["is_open"]
    steps: dict[str, Any] = {}
    if was_open:
        steps["folio.close"] = _rc(f.close())
        if steps["folio.close"] not in (0, None):
            return {"ok": False, "folio": row, "steps": steps,
                    "error": "could not close the folio; refusing to insert "
                             "into a stale editor copy"}
    results = []
    try:
        for p, tag, name, x, y, cid in prepared:
            st: dict[str, Any] = {}
            sym = _u(f.newEwProjectSymbolFromSymbolType(
                int(p.get("symbol_type", 20))))
            if sym is None:
                results.append({"tag": tag, "ok": False,
                                "error": "newEwProjectSymbolFromSymbolType "
                                         "returned NULL"})
                continue
            st["setObjectID"] = _rc(sym.setObjectID(cid))
            st["setEwSymbolName"] = _rc(sym.setEwSymbolName(name))
            st["setXPosition"] = _rc(sym.setXPosition(x))
            st["setYPosition"] = _rc(sym.setYPosition(y))
            if p.get("rotation"):
                st["setRotationAngle"] = _rc(
                    sym.setRotationAngle(float(p["rotation"])))
            if p.get("x_scale") is not None:
                st["setXScale"] = _rc(sym.setXScale(float(p["x_scale"])))
            if p.get("y_scale") is not None:
                st["setYScale"] = _rc(sym.setYScale(float(p["y_scale"])))
            st["insert"] = _rc(sym.insert())
            pts = _symbol_points(sym)
            stray = [q for q in pts
                     if not (bx["x_min"] - _SNAP <= q["x"] <= bx["x_max"] + _SNAP
                             and bx["y_min"] - _SNAP <= q["y"] <= bx["y_max"] + _SNAP)]
            if stray:
                sid = _u(sym.getID())
                rc_rm = _rc(sym.remove())
                results.append({"tag": tag, "ok": False, "steps": st,
                                "outside_box": stray,
                                "removed_cleanly": rc_rm in (0, None),
                                "error": f"symbol {sid} put {len(stray)} "
                                         "connection point(s) outside the box"})
                continue
            bad = {k: v for k, v in st.items() if v not in (0, None)}
            results.append({"tag": tag, "ok": not bad,
                            "symbol_id": _u(sym.getID()),
                            "x": x, "y": y, "points": len(pts),
                            "bad_steps": bad})
    finally:
        if was_open:
            steps["folio.open"] = _rc(f.open())

    placed = [r for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok")]
    return {"ok": not failed, "dry_run": False, "folio": row,
            "requested": len(prepared), "placed": len(placed),
            "failed": len(failed), "results": results, "steps": steps,
            "undo": "remove symbols "
                    + ", ".join(str(r["symbol_id"]) for r in placed),
            "note": "the folio was closed once and reopened once for the "
                    "whole batch"}


def remove_symbols(app: Any, client: Any, symbol_ids: list[int] | None = None,
                   page: str | int | None = None,
                   file_id: int | None = None,
                   all_on_page: bool = False) -> dict:
    """Delete several drawn symbols, closing the folio only once.

    The batch counterpart to ``remove_symbol``, which closes and reopens the
    folio around every single deletion. Clearing a twelve-device page one
    symbol at a time means twelve editor close/open cycles; that churn can
    take SOLIDWORKS Electrical down, so open and close per TASK.

    Give either ``symbol_ids``, or ``all_on_page=True`` with a page, which
    clears every symbol on that folio. Lines left hanging where a symbol was
    are reported, not removed.
    """
    proj = _project(app)
    smgr = _u(proj.getEwProjectSymbolManager())
    if all_on_page:
        f = find_folio(app, client, page=page, file_id=file_id)
        fid = _u(f.getID())
        ids = [_u(s.getID())
               for s in _each(client, _u(smgr.getProjectSymbolsFromFileID(fid)))]
    else:
        ids = [int(i) for i in (symbol_ids or [])]
        if not ids:
            return {"ok": False, "error": "give symbol_ids or all_on_page=True"}
        first = _u(smgr.getProjectSymbolByID(ids[0]))
        if first is None:
            raise LookupError(f"no symbol with id {ids[0]}")
        fid = _u(first.getFileID())
        f = find_folio(app, client, file_id=fid)
    if not ids:
        return {"ok": True, "file_id": fid, "requested": 0, "removed": 0,
                "results": [], "note": "nothing to remove"}

    was_open = bool(_u(f.isOpen()))
    steps: dict[str, Any] = {}
    if was_open:
        steps["folio.close"] = _rc(f.close())
        if steps["folio.close"] not in (0, None):
            return {"ok": False, "file_id": fid, "steps": steps,
                    "error": "could not close the folio; refusing to edit a "
                             "stale editor copy"}
    lines = _folio_lines(app, client, fid)
    results, dangling = [], []
    try:
        for sid in ids:
            sym = _u(smgr.getProjectSymbolByID(int(sid)))
            if sym is None:
                results.append({"symbol_id": sid, "ok": False,
                                "error": "no such symbol"})
                continue
            if _u(sym.getFileID()) != fid:
                results.append({"symbol_id": sid, "ok": False,
                                "error": "symbol is on another folio; "
                                         "batch one page at a time"})
                continue
            pts = _symbol_points(sym)
            for ln in lines:
                for end, lx, ly in (("start", ln["x1"], ln["y1"]),
                                    ("end", ln["x2"], ln["y2"])):
                    if any(abs(lx - q["x"]) < 0.01 and abs(ly - q["y"]) < 0.01
                           for q in pts):
                        dangling.append({"line_id": ln["id"], "end": end,
                                         "x": lx, "y": ly, "symbol_id": sid})
            rc = _rc(sym.remove())
            results.append({"symbol_id": sid, "ok": rc in (0, None), "rc": rc})
    finally:
        if was_open:
            steps["folio.open"] = _rc(f.open())

    gone = [r for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok")]
    return {"ok": not failed, "file_id": fid, "requested": len(ids),
            "removed": len(gone), "failed": len(failed), "results": results,
            "steps": steps, "dangling_line_ends": dangling,
            "note": ("the folio was closed once and reopened once for the "
                     "whole batch"
                     + ("" if not dangling else
                        f"; {len(dangling)} wire end(s) now hang on nothing"))}


def add_texts(app: Any, client: Any, texts: list[dict],
              page: str | int | None = None, file_id: int | None = None,
              box: dict | None = None, dry_run: bool = True) -> dict:
    """Put several free texts on ONE page, closing the folio only once.

    The batch counterpart to ``add_text``. Annotating a page - terminal
    numbers, wire specs, circuit numbers - runs to dozens of texts, and one
    editor close/open cycle each is what takes SOLIDWORKS Electrical down.

    Each entry is a dict with ``text``, ``x`` and ``y``, and optionally
    ``rotation``. Every position is checked against the drawable box BEFORE
    the folio is touched, so a bad one cannot leave the page half annotated.
    """
    if not texts:
        return {"ok": False, "error": "no texts given"}
    bx = {**GO_BOX, **(box or {})}
    f = find_folio(app, client, page=page, file_id=file_id)
    row = _folio_row(f)

    prepared = []
    for i, t in enumerate(texts):
        try:
            body = str(t["text"])
            x, y = float(t["x"]), float(t["y"])
        except (KeyError, TypeError, ValueError) as exc:
            return {"ok": False, "folio": row,
                    "error": f"text {i} is malformed: {exc}"}
        if not (bx["x_min"] - _SNAP <= x <= bx["x_max"] + _SNAP
                and bx["y_min"] - _SNAP <= y <= bx["y_max"] + _SNAP):
            return {"ok": False, "folio": row,
                    "error": f"text {i} at ({x}, {y}) is outside the "
                             f"drawable box {bx}"}
        prepared.append((body, x, y, float(t.get("rotation") or 0.0)))

    if dry_run:
        return {"ok": True, "dry_run": True, "folio": row,
                "count": len(prepared),
                "planned": [{"text": b, "x": x, "y": y, "rotation": r}
                            for b, x, y, r in prepared]}

    was_open = row["is_open"]
    steps: dict[str, Any] = {}
    if was_open:
        steps["folio.close"] = _rc(f.close())
        if steps["folio.close"] not in (0, None):
            return {"ok": False, "folio": row, "steps": steps,
                    "error": "could not close the folio"}
    results = []
    try:
        for body, x, y, rot in prepared:
            t = _u(f.newEwProjectMultilingualText())
            if t is None:
                results.append({"text": body, "ok": False,
                                "error": "newEwProjectMultilingualText "
                                         "returned NULL"})
                continue
            # Content and position go in BEFORE the insert: inserting an
            # empty text answers EW_BAD_INPUTS (2) and leaves id -1.
            st = {"setText": _rc(t.setText(LANG, body)),
                  "setXPosition": _rc(t.setXPosition(x)),
                  "setYPosition": _rc(t.setYPosition(y))}
            if rot:
                st["setRotationAngle"] = _rc(t.setRotationAngle(rot))
            st["insert"] = _rc(t.insert())
            st["update"] = _rc(t.update())
            bad = {k: v for k, v in st.items() if v not in (0, None)}
            tid = _u(t.getID())
            results.append({"text": body, "x": x, "y": y,
                            "text_id": tid, "bad_steps": bad,
                            "ok": not bad and isinstance(tid, int) and tid > 0})
    finally:
        if was_open:
            steps["folio.open"] = _rc(f.open())

    added = [r for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok")]
    return {"ok": not failed, "dry_run": False, "folio": row,
            "requested": len(prepared), "added": len(added),
            "failed": len(failed), "results": results, "steps": steps,
            "undo": "remove texts "
                    + ", ".join(str(r["text_id"]) for r in added),
            "note": "the folio was closed once and reopened once for the "
                    "whole batch"}


def remove_texts(app: Any, client: Any, text_ids: list[int] | None = None,
                 page: str | int | None = None, file_id: int | None = None,
                 all_on_page: bool = False) -> dict:
    """Delete several free texts, closing the folio only once.

    The batch counterpart to ``remove_text``, which besides cycling the
    editor per text also scans the WHOLE project's text array to resolve one
    id - a project scan per text when called in a loop. This resolves every
    id from a single folio-scoped fetch.

    Give either ``text_ids``, or ``all_on_page=True`` with a page to clear
    every text off a folio.
    """
    proj = _project(app)
    mgr = _u(proj.getEwProjectMultilingualTextManager())
    f = find_folio(app, client, page=page, file_id=file_id)
    fid = _u(f.getID())
    # One scoped fetch for the whole batch, the same reason as the lines.
    arr, scoped = None, True
    try:
        arr = _u(mgr.getEwProjectMultilingualTextByFileIDArray(int(fid)))
    except Exception:  # noqa: BLE001
        scoped = False
        arr = _u(mgr.getEwProjectMultilingualTextArray())
    on_page = {}
    for t in _each(client, arr or ()):
        if not scoped and _u(t.getFileID()) != fid:
            continue
        on_page[_u(t.getID())] = t

    if all_on_page:
        ids = list(on_page)
    else:
        ids = [int(i) for i in (text_ids or [])]
        if not ids:
            return {"ok": False, "error": "give text_ids or all_on_page=True"}
    if not ids:
        return {"ok": True, "file_id": fid, "requested": 0, "removed": 0,
                "results": [], "note": "nothing to remove"}

    was_open = bool(_u(f.isOpen()))
    steps: dict[str, Any] = {}
    if was_open:
        steps["folio.close"] = _rc(f.close())
        if steps["folio.close"] not in (0, None):
            return {"ok": False, "file_id": fid, "steps": steps,
                    "error": "could not close the folio"}
    results = []
    try:
        for tid in ids:
            t = on_page.get(tid)
            if t is None:
                results.append({"text_id": tid, "ok": False,
                                "error": "no such text on this folio; "
                                         "batch one page at a time"})
                continue
            rc = _rc(t.remove())
            results.append({"text_id": tid, "ok": rc in (0, None), "rc": rc})
    finally:
        if was_open:
            steps["folio.open"] = _rc(f.open())

    gone = [r for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok")]
    return {"ok": not failed, "file_id": fid, "requested": len(ids),
            "removed": len(gone), "failed": len(failed), "results": results,
            "steps": steps,
            "note": "the folio was closed once and reopened once for the "
                    "whole batch"}
