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
import re
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

    ``only`` restricts the result to those component ids, which skips the
    text getters for every other part.

    getObjectID is reused for location-owned parts (rails, ducts), so an id
    that is also a location id is ambiguous and has to be resolved through
    getEwProjectComponent. That call is a COM round trip per part, so it is
    made only for the handful of ambiguous ids rather than for all of them.
    """
    out: dict[int, list[dict]] = {}
    loc_mgr = _u(proj.getEwProjectLocationManager())
    location_ids = {_u(loc.getID())
                    for loc in _each(client, _u(loc_mgr.getEwProjectLocationArray()))}
    mgr = _u(proj.getEwProjectManufacturerPartManager())
    for p in _each(client, _u(mgr.getEwProjectManufacturerPartArray())):
        comp_id = _u(p.getObjectID())
        if comp_id in location_ids:
            try:
                owner = _u(p.getEwProjectComponent())
            except Exception:  # noqa: BLE001
                owner = None
            if owner is None:
                continue  # the part belongs to the location, not a component
            comp_id = _u(owner.getID())
        if only is not None and comp_id not in only:
            continue
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
    """Letters before the number: "K32" -> "K", "N1N15" -> "N", "T58" -> "T"."""
    m = re.match(r"([A-Za-z]+)", tag.strip().lstrip("-"))
    return m.group(1).upper() if m else ""


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
    row_xs: list[float] = []
    for s in _each(client, _u(sym_mgr.getProjectSymbolsFromFileID(file_id))):
        if _u(s.getEwSymbolName()) != name:
            continue
        y = _u(s.getYPosition())
        if isinstance(y, (int, float)) and abs(y - y0) < 0.5:
            row_xs.append(float(_u(s.getXPosition())))
    row_xs.sort()
    gaps = [b - a for a, b in zip(row_xs, row_xs[1:]) if b - a > 0.01]
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
    src_root = str(_u(src.getTagRoot()) or "").strip() or _tag_root(str(_u(src.getTag())))
    new_root = _tag_root(new_bare)
    if new_root != src_root:
        raise ValueError(
            f"tag root must stay {src_root!r} when cloning "
            f"{_u(src.getTagPath())!r} (got {new_bare!r}, root {new_root!r}). "
            "MR rule: a clone keeps its device class; relays are always "
            "rooted K.")
    try:
        _find_one_component(app, client, new_bare)
    except LookupError:
        pass
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
    step("setTag", lambda: new.setTag(new_bare))
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
        "undo": f"delete_component(component_id={new_id})",
    }


def delete_component(app: Any, client: Any, component_id: int | None = None,
                     tag: str | None = None,
                     pages: list[str | int] | None = None) -> dict:
    """Remove a component by id or tag.

    A plain remove is tried first. If SOLIDWORKS answers EW_CANNOT_REMOVE
    (35) the component still has symbols bound to it: those are removed
    from ``pages`` if given (fast), otherwise from every folio (a full sweep,
    minutes on a large project), and the remove is retried.
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
        for f in folios:
            fid = _u(f.getID())
            for sym in _each(client,
                             _u(sym_mgr.getProjectSymbolsFromFileID(fid))):
                if _u(sym.getObjectID()) != cid:
                    continue
                symbols_removed.append({"file_id": fid, "page": _u(f.getTag()),
                                        "symbol_id": _u(sym.getID()),
                                        "rc": _rc(sym.remove())})
        rc = _rc(comp.remove())
    return {"ok": rc in (0, None), "removed": row, "rc": rc,
            "rc_name": _rc_name(rc), "symbols_removed": symbols_removed,
            "swept_all_folios": rc != 0 or (bool(symbols_removed) and not pages)}
