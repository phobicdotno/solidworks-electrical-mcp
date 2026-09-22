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

import os
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

EW_ERROR_NAMES = {
    0: "EW_NO_ERROR", 2: "EW_BAD_INPUTS", 3: "EW_FILE_NOT_FOUND",
    8: "EW_DOES_NOT_EXIST", 13: "EW_ALREADY_INSERTED", 22: "EW_OBJECT_NOT_FOUND",
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
    try:
        return coerce_value(_u(getattr(obj, getter)(*args)))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


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
    rows.sort(key=lambda r: (r["position"] if isinstance(r["position"], int)
                             else 0))
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
    for f in _each(client, _u(mgr.getEwProjectFileArray())):
        tag = _u(f.getTag())
        if str(tag) == want or str(tag).lstrip("0") == want.lstrip("0"):
            return f
    raise LookupError(f"no folio with page mark {want!r}")


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


def _part_map(proj: Any, client: Any) -> dict[int, list[dict]]:
    """component id -> manufacturer parts assigned to it."""
    out: dict[int, list[dict]] = {}
    mgr = _u(proj.getEwProjectManufacturerPartManager())
    for p in _each(client, _u(mgr.getEwProjectManufacturerPartArray())):
        comp_id = _u(p.getObjectID())
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
    parts = _part_map(proj, client) if with_parts else None
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
        if parts is not None:
            row["parts"] = parts.get(row["id"], [])
        rows.append(row)
    return {"count": len(rows), "matched": matched, "total_in_project": total,
            "truncated": matched > len(rows), "components": rows}


def find_component(app: Any, client: Any, tag: str) -> dict:
    """Exact match on the tag ("A1") or the full tag path ("=F1+L1+L4+L2-A1")."""
    proj = _project(app)
    mgr = _u(proj.getEwProjectComponentManager())
    want = tag.strip()
    want_bare = want.lstrip("-")
    parts = _part_map(proj, client)
    matches = []
    for c in _each(client, _u(mgr.getEwProjectComponentArray())):
        t = str(_u(c.getTag()))
        tp = str(_u(c.getTagPath()))
        if t == want or t == want_bare or tp == want \
                or tp.endswith("-" + want_bare):
            matches.append(_component_row(c, parts))
    return {"query": tag, "count": len(matches), "components": matches}


def list_cables(app: Any, client: Any, limit: int = 500) -> dict:
    proj = _project(app)
    mgr = _u(proj.getEwProjectCableManager())
    rows = []
    for cb in _each(client, _u(mgr.getEwProjectCableArray())):
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
        if limit and len(rows) >= limit:
            break
    return {"count": len(rows), "cables": rows}


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
