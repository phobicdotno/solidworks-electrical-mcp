"""The environment library: manufacturer parts, symbols, libraries.

Components on a page reference parts, and parts reference symbols, so any job
that needs a device the library does not carry stops dead until somebody
authors one. Until now that meant a hand-written COM script per part - which
is how the Wago 751-9402 got into this environment.

Two facts about the live API shaped this module, both found by measurement
rather than documentation:

* ``IEwManufacturerPartFiltersX`` has no setters at all. The object exists,
  ``newEwManufacturerPartFilters`` hands one over, and the only thing you can
  do with it is ask for every part back. Filtering happens here instead.
* ``IEwSymbolFiltersX`` does have setters, and they do nothing useful:
  ``setManufacturer("Wago")`` then ``getEwSymbolArray`` returns
  ``(0, ())`` while a plain sweep finds four Wago symbols. So symbols are
  swept too.

A sweep of the 1721 symbols in this environment costs 20 to 30 seconds,
which is too slow to repeat per query, so the index is cached per process
and rebuilt when the symbol count changes or ``refresh`` says so. The 479
parts sweep in about 8 seconds and are cached the same way.
"""

from __future__ import annotations

import os
import time
from typing import Any

from .workflows import (LANG, SYMBOL_TYPE_NAMES, _each, _rc, _rc_name, _text,
                        _u)

# EwManufacturerPartType (get_enum('EwManufacturerPartType')).
PART_TYPE_NAMES = {
    -1: "undefined", 0: "base", 1: "auxiliary", 2: "accessory", 3: "plc",
    4: "plc_rack", 5: "plc_module_with_interface", 6: "plc_module",
    7: "plc_interface_point", 8: "plc_interface_circuit", 9: "super_part",
    10: "wire_accessory",
}
PART_TYPE_CODES = {v: k for k, v in PART_TYPE_NAMES.items()}
SYMBOL_TYPE_CODES = {v: k for k, v in SYMBOL_TYPE_NAMES.items()}

# name -> (rows, built_at, source_count). One entry per index kind. Keyed on
# the manager's own count so a part added through the GUI invalidates it.
_INDEX: dict[str, tuple[list[dict], float, int]] = {}


def _env(app: Any) -> Any:
    env = _u(app.getEwEnvironment())
    if env is None:
        raise RuntimeError("getEwEnvironment returned NULL")
    return env


def _part_manager(app: Any) -> Any:
    return _u(_env(app).getEwManufacturerPartManager())


def _symbol_manager(app: Any) -> Any:
    return _u(_env(app).getEwSymbolManager())


def _matches(row: dict, field: str, needle: str | None) -> bool:
    if not needle:
        return True
    return str(needle).lower() in str(row.get(field) or "").lower()


# --------------------------------------------------------------------------
# Manufacturer parts


def _part_row(p: Any, detail: bool = False) -> dict:
    ptype = _u(p.getEwManufacturerPartType())
    row = {
        "manufacturer": _text(p, "getManufacturer"),
        "reference": _text(p, "getReference"),
        "description": _text(p, "getDescription", LANG),
        "type": PART_TYPE_NAMES.get(ptype, str(ptype)),
        "type_code": ptype,
        "library_code": _text(p, "getLibraryCode"),
    }
    if not detail:
        return row
    row.update({
        "width_mm": _u(p.getWidth()), "height_mm": _u(p.getHeight()),
        "depth_mm": _u(p.getDepth()), "weight": _u(p.getWeight()),
        "supplier": _text(p, "getSupplierName"),
        "article_number": _text(p, "getArticleNumber"),
        "stock_number": _text(p, "getStockNumber"),
        "root_mark": _text(p, "getRootMark"),
        "series": _text(p, "getSerie"),
        "scheme_symbol": _text(p, "getSchemeSymbolName"),
        "line_diagram_symbol": _text(p, "getLineDiagramSymbolName"),
        "footprint_symbol": _text(p, "get2DFootprintSymbolName"),
        "part_3d_path": _text(p, "get3DPartPath"),
        "datasheet": _text(p, "getDataSheetFilePath"),
        "use_voltage": _text(p, "getUseVoltage"),
        "control_voltage": _text(p, "getControlVoltage"),
        "exclude_from_bom": bool(_u(p.getExcludeFromBillOfMaterials())),
        "created_by": _text(p, "getCreatedBy"),
        "modified_by": _text(p, "getModifiedBy"),
        "modification_date": _text(p, "getModificationDate"),
        "circuits": _circuits(p),
    })
    return row


def _circuits(p: Any) -> list[dict]:
    """The part's circuits and their terminals.

    This is the electrical half of a part: what a component made from it can
    actually be wired to. A part with no circuits places as a blank box.
    """
    out: list[dict] = []
    n = int(_u(p.getEwManufacturerPartCircuitCount()) or 0)
    for i in range(n):
        c = _u(p.getEwManufacturerPartCircuitAt(i))
        if c is None:
            continue
        terms = []
        tn = int(_u(c.getEwManufacturerPartTerminalCount()) or 0)
        for j in range(tn):
            t = _u(c.getEwManufacturerPartTerminalAt(j))
            if t is None:
                continue
            terms.append({"text": _text(t, "getText"),
                          "mnemonic": _text(t, "getMnemonic")})
        out.append({"index": i, "code": _text(c, "getType"),
                    "terminals": terms})
    return out


def _part_index(app: Any, client: Any, refresh: bool = False) -> list[dict]:
    mgr = _part_manager(app)
    count = int(_u(mgr.getCount()) or 0)
    cached = _INDEX.get("parts")
    if cached and not refresh and cached[2] == count:
        return cached[0]
    t0 = time.time()
    rows = [_part_row(p) for p in _each(client, _u(
        mgr.getEwManufacturerPartArray()))]
    _INDEX["parts"] = (rows, time.time() - t0, count)
    return rows


def search_manufacturer_parts(app: Any, client: Any,
                              manufacturer: str | None = None,
                              reference: str | None = None,
                              description_contains: str | None = None,
                              library_code: str | None = None,
                              part_type: str | None = None,
                              limit: int = 50,
                              refresh: bool = False) -> dict:
    """Search the manufacturer-part catalogue. Every filter is a substring.

    ``part_type`` is an exact name from ``PART_TYPE_NAMES`` ("plc", "base",
    "plc_module" ...). The index is cached per process; ``refresh`` rebuilds
    it after the catalogue was edited outside this server.
    """
    rows = _part_index(app, client, refresh)
    if part_type and part_type not in PART_TYPE_CODES:
        raise ValueError(f"unknown part_type {part_type!r}; "
                         f"have {sorted(PART_TYPE_CODES)}")
    hits = [r for r in rows
            if _matches(r, "manufacturer", manufacturer)
            and _matches(r, "reference", reference)
            and _matches(r, "description", description_contains)
            and _matches(r, "library_code", library_code)
            and (not part_type or r["type"] == part_type)]
    hits.sort(key=lambda r: (str(r["manufacturer"]), str(r["reference"])))
    return {"total_in_catalogue": len(rows), "matched": len(hits),
            "returned": min(len(hits), limit),
            "index_build_seconds": round(_INDEX["parts"][1], 2),
            "parts": hits[:limit]}


def get_manufacturer_part(app: Any, client: Any, manufacturer: str,
                          reference: str) -> dict:
    """One part in full, including its circuits and their terminals."""
    p = _u(_part_manager(app).findByManufacturerAndReference(
        str(manufacturer), str(reference)))
    if p is None:
        return {"ok": False,
                "error": f"no part {manufacturer!r} / {reference!r}"}
    return {"ok": True, "part": _part_row(p, detail=True)}


# Field name -> (setter, coercion). Ordered so the identity fields land
# before insert() and the rest after it.
_PART_SETTERS = (
    ("description", "setDescription", "lang"),
    ("part_type", "setEwManufacturerPartType", "part_type"),
    ("width_mm", "setWidth", "float"),
    ("height_mm", "setHeight", "float"),
    ("depth_mm", "setDepth", "float"),
    ("weight", "setWeight", "float"),
    ("library_code", "setLibraryCode", "str"),
    ("supplier", "setSupplierName", "str"),
    ("article_number", "setArticleNumber", "str"),
    ("stock_number", "setStockNumber", "str"),
    ("root_mark", "setRootMark", "str"),
    ("series", "setSerie", "str"),
    ("scheme_symbol", "setSchemeSymbolName", "str"),
    ("line_diagram_symbol", "setLineDiagramSymbolName", "str"),
    ("footprint_symbol", "set2DFootprintSymbolName", "str"),
    ("datasheet", "setDataSheetFilePath", "str"),
    ("use_voltage", "setUseVoltage", "str"),
    ("control_voltage", "setControlVoltage", "str"),
)


def _apply_part_fields(p: Any, values: dict, steps: list) -> None:
    for field, setter, kind in _PART_SETTERS:
        v = values.get(field)
        if v is None:
            continue
        if kind == "lang":
            rc = _rc(getattr(p, setter)(LANG, str(v)))
        elif kind == "float":
            rc = _rc(getattr(p, setter)(float(v)))
        elif kind == "part_type":
            if v not in PART_TYPE_CODES:
                raise ValueError(f"unknown part_type {v!r}; "
                                 f"have {sorted(PART_TYPE_CODES)}")
            rc = _rc(getattr(p, setter)(PART_TYPE_CODES[v]))
        else:
            rc = _rc(getattr(p, setter)(str(v)))
        steps.append({"field": field, "rc": rc, "rc_name": _rc_name(rc)})


def create_manufacturer_part(app: Any, client: Any, manufacturer: str,
                             reference: str, description: str | None = None,
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

    ``circuits`` is the electrical half and the part is close to useless
    without it: a list of ``{"code": "PID", "terminals": [{"text": "X12:3",
    "mnemonic": "DI#1"}, ...]}``. The codes are the environment's circuit
    types - PID digital in, POD digital out, PIA analog in, POA analog out,
    777/888 a plain terminal - and a component made from this part offers
    exactly these connection points.

    ``width_mm``/``height_mm``/``depth_mm`` are the real body dimensions and
    are what a cabinet layout reasons about, so they are worth getting right
    even when a vendor footprint drawing says something larger.

    ``replace`` removes an existing part of the same manufacturer/reference
    first; without it an existing part is left alone and reported.
    """
    mgr = _part_manager(app)
    existing = _u(mgr.findByManufacturerAndReference(str(manufacturer),
                                                     str(reference)))
    steps: list[dict] = []
    if existing is not None:
        if not replace:
            return {"ok": False, "part": _part_row(existing, detail=True),
                    "error": f"{manufacturer} {reference} already exists; "
                             f"pass replace=True to overwrite it"}
        rc = _rc(existing.remove())
        steps.append({"step": "remove existing", "rc": rc,
                      "rc_name": _rc_name(rc)})

    p = _u(mgr.newEwManufacturerPart())
    if p is None:
        return {"ok": False, "error": "newEwManufacturerPart returned NULL"}

    for label, rc in (("setManufacturer", _rc(p.setManufacturer(
                          str(manufacturer)))),
                      ("setReference", _rc(p.setReference(str(reference))))):
        steps.append({"step": label, "rc": rc, "rc_name": _rc_name(rc)})

    # Read only the declared field names out of the frame, so a local that
    # happens to share a name with a field cannot leak into the part.
    _here = locals()
    _apply_part_fields(p, {f: _here.get(f) for f, _, _ in _PART_SETTERS},
                       steps)

    rc = _rc(p.insert())
    steps.append({"step": "insert", "rc": rc, "rc_name": _rc_name(rc)})
    if rc not in (0, None):
        return {"ok": False, "steps": steps,
                "error": f"insert failed: {_rc_name(rc)}"}

    bad = _add_circuits(p, circuits or [], steps)
    rc = _rc(p.update())
    steps.append({"step": "update", "rc": rc, "rc_name": _rc_name(rc)})

    _INDEX.pop("parts", None)
    fresh = _u(mgr.findByManufacturerAndReference(str(manufacturer),
                                                 str(reference)))
    return {"ok": not bad and all(s.get("rc") in (0, None) for s in steps),
            "part": None if fresh is None else _part_row(fresh, detail=True),
            "circuit_errors": bad, "steps": steps}


def _add_circuits(p: Any, circuits: list[dict], steps: list) -> list:
    bad: list[dict] = []
    for spec in circuits:
        code = str(spec.get("code") or "").strip()
        if not code:
            bad.append({"circuit": spec, "error": "no code"})
            continue
        c = _u(p.addEwManufacturerPartCircuit(code))
        if c is None:
            bad.append({"circuit": code, "error": "addCircuit returned NULL"})
            continue
        for term in spec.get("terminals") or [{}]:
            t = _u(c.addEwManufacturerPartTerminal())
            if t is None:
                bad.append({"circuit": code, "terminal": term,
                            "error": "addTerminal returned NULL"})
                continue
            rcs = {}
            if term.get("text") is not None:
                rcs["setText"] = _rc(t.setText(str(term["text"])))
            if term.get("mnemonic") is not None:
                rcs["setMnemonic"] = _rc(t.setMnemonic(str(term["mnemonic"])))
            off = {k: v for k, v in rcs.items() if v not in (0, None)}
            if off:
                bad.append({"circuit": code, "terminal": term, "rcs": off})
    steps.append({"step": "circuits", "added": len(circuits),
                  "errors": len(bad)})
    return bad


def update_manufacturer_part(app: Any, client: Any, manufacturer: str,
                             reference: str, description: str | None = None,
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
    already reference changes what those components offer, so replacing the
    part with ``create_manufacturer_part(replace=True)`` is the honest route.
    """
    mgr = _part_manager(app)
    p = _u(mgr.findByManufacturerAndReference(str(manufacturer),
                                              str(reference)))
    if p is None:
        return {"ok": False,
                "error": f"no part {manufacturer!r} / {reference!r}"}
    steps: list[dict] = []
    _here = locals()
    _apply_part_fields(p, {f: _here.get(f) for f, _, _ in _PART_SETTERS},
                       steps)
    if not steps:
        return {"ok": True, "changed": [],
                "part": _part_row(p, detail=True),
                "note": "nothing to change"}
    rc = _rc(p.update())
    steps.append({"step": "update", "rc": rc, "rc_name": _rc_name(rc)})
    _INDEX.pop("parts", None)
    return {"ok": all(s.get("rc") in (0, None) for s in steps),
            "changed": [s["field"] for s in steps if "field" in s],
            "part": _part_row(p, detail=True), "steps": steps}


def delete_manufacturer_part(app: Any, client: Any, manufacturer: str,
                             reference: str, confirm_reference: str) -> dict:
    """Remove a part from the catalogue. ``confirm_reference`` must match.

    Components already carrying this part keep their assignment as a dangling
    reference, so check ``list_components(with_parts=True)`` before removing
    anything a project uses.
    """
    mgr = _part_manager(app)
    p = _u(mgr.findByManufacturerAndReference(str(manufacturer),
                                              str(reference)))
    if p is None:
        return {"ok": False,
                "error": f"no part {manufacturer!r} / {reference!r}"}
    if str(confirm_reference) != str(reference):
        return {"ok": False, "part": _part_row(p),
                "error": f"confirm_reference {confirm_reference!r} does not "
                         f"match {reference!r}; nothing was deleted"}
    row = _part_row(p, detail=True)
    rc = _rc(p.remove())
    _INDEX.pop("parts", None)
    gone = _u(mgr.findByManufacturerAndReference(str(manufacturer),
                                                 str(reference))) is None
    return {"ok": rc in (0, None) and gone, "deleted": row, "rc": rc,
            "rc_name": _rc_name(rc), "confirmed_gone": gone}


# --------------------------------------------------------------------------
# Symbols


def _symbol_row(s: Any, detail: bool = False) -> dict:
    stype = _u(s.getEwSymbolType())
    row = {
        "id": _u(s.getID()),
        "name": _text(s, "getName"),
        "type": SYMBOL_TYPE_NAMES.get(stype, str(stype)),
        "type_code": stype,
        "manufacturer": _text(s, "getManufacturer"),
        "reference": _text(s, "getReference"),
        "library_code": _text(s, "getLibraryCode"),
    }
    if not detail:
        return row
    row.update({
        "description": _text(s, "getDescription", LANG),
        "root_mark": _text(s, "getRootMark"),
        "standard": _text(s, "getStandard"),
        "associated_macro": _text(s, "getAssociatedMacro"),
        "connection_points": int(_u(s.getEwSymbolPointCount()) or 0),
        "circuits": int(_u(s.getEwSymbolCircuitCount()) or 0),
        "created_by": _text(s, "getCreatedBy"),
        "modified_by": _text(s, "getModifiedBy"),
        "modification_date": _text(s, "getModificationDate"),
    })
    return row


def _symbol_index(app: Any, client: Any, refresh: bool = False) -> list[dict]:
    mgr = _symbol_manager(app)
    count = int(_u(mgr.getCount()) or 0)
    cached = _INDEX.get("symbols")
    if cached and not refresh and cached[2] == count:
        return cached[0]
    t0 = time.time()
    rows = [_symbol_row(s)
            for s in _each(client, _u(mgr.getEwSymbolArray()))]
    _INDEX["symbols"] = (rows, time.time() - t0, count)
    return rows


def search_symbols(app: Any, client: Any, name: str | None = None,
                   symbol_type: str | None = None,
                   manufacturer: str | None = None,
                   reference: str | None = None,
                   library_code: str | None = None,
                   limit: int = 50, refresh: bool = False) -> dict:
    """Search the symbol library. Every filter is a substring except type.

    ``symbol_type`` is an exact name: component, blackbox, 2d_footprint,
    terminal_drawing, connection, xref, passive, pid and so on.

    The first search in a process sweeps the whole library (20 to 30 seconds
    for 1700 symbols) because the API's own symbol filter returns nothing -
    ``setManufacturer("Wago")`` then ``getEwSymbolArray`` gives an empty
    array while a sweep finds four. After that it is cached.
    """
    rows = _symbol_index(app, client, refresh)
    if symbol_type and symbol_type not in SYMBOL_TYPE_CODES:
        raise ValueError(f"unknown symbol_type {symbol_type!r}; "
                         f"have {sorted(SYMBOL_TYPE_CODES)}")
    hits = [r for r in rows
            if _matches(r, "name", name)
            and _matches(r, "manufacturer", manufacturer)
            and _matches(r, "reference", reference)
            and _matches(r, "library_code", library_code)
            and (not symbol_type or r["type"] == symbol_type)]
    hits.sort(key=lambda r: str(r["name"]))
    return {"total_in_library": len(rows), "matched": len(hits),
            "returned": min(len(hits), limit),
            "index_build_seconds": round(_INDEX["symbols"][1], 2),
            "symbols": hits[:limit]}


def get_symbol(app: Any, client: Any, name: str) -> dict:
    """One symbol in full, by its exact library name."""
    s = _u(_symbol_manager(app).findEwSymbolXByName(str(name)))
    if s is None:
        return {"ok": False, "error": f"no symbol named {name!r}"}
    return {"ok": True, "symbol": _symbol_row(s, detail=True)}


def import_symbol(app: Any, client: Any, name: str, drawing_path: str,
                  symbol_type: str = "2d_footprint",
                  library_code: str | None = None,
                  description: str | None = None,
                  manufacturer: str | None = None,
                  reference: str | None = None,
                  root_mark: str | None = None,
                  replace: bool = False) -> dict:
    """Create a library symbol from a DWG or DXF file.

    ``insertFromDwg`` takes DXF as happily as DWG and stores the geometry
    verbatim, which makes a hand-written DXF a practical way to author a
    footprint at the dimensions a part really has rather than the ones a
    vendor drawing happens to show.

    Attributes live in the drawing, not in the API: there is no way to add
    one to a symbol afterwards, so a symbol that should print its component
    mark needs a ``#TAG`` ATTDEF in the file before it is imported. ``#TAG``
    is the attribute SOLIDWORKS resolves to the mark; the obvious-looking
    alternatives (``#MARK``, ``#COMPONENT_TAG``, ``#TAGREF`` ...) render as
    their own literal text.
    """
    if symbol_type not in SYMBOL_TYPE_CODES:
        raise ValueError(f"unknown symbol_type {symbol_type!r}; "
                         f"have {sorted(SYMBOL_TYPE_CODES)}")
    drawing_path = os.path.abspath(drawing_path)
    if not os.path.isfile(drawing_path):
        raise FileNotFoundError(drawing_path)

    mgr = _symbol_manager(app)
    steps: list[dict] = []
    old = _u(mgr.findEwSymbolXByName(str(name)))
    if old is not None:
        if not replace:
            return {"ok": False, "symbol": _symbol_row(old, detail=True),
                    "error": f"a symbol named {name!r} already exists; "
                             f"pass replace=True to overwrite it"}
        rc = _rc(old.remove())
        steps.append({"step": "remove existing", "rc": rc,
                      "rc_name": _rc_name(rc)})

    s = _u(mgr.newEwSymbol())
    if s is None:
        return {"ok": False, "error": "newEwSymbol returned NULL"}

    def step(label: str, value: Any) -> None:
        rc = _rc(value)
        steps.append({"step": label, "rc": rc, "rc_name": _rc_name(rc)})

    step("setName", s.setName(str(name)))
    step("setEwSymbolType", s.setEwSymbolType(SYMBOL_TYPE_CODES[symbol_type]))
    if library_code is not None:
        step("setLibraryCode", s.setLibraryCode(str(library_code)))
    if manufacturer is not None:
        step("setManufacturer", s.setManufacturer(str(manufacturer)))
    if reference is not None:
        step("setReference", s.setReference(str(reference)))
    if root_mark is not None:
        step("setRootMark", s.setRootMark(str(root_mark)))
    if description is not None:
        step("setDescription", s.setDescription(LANG, str(description)))
    step("insertFromDwg", s.insertFromDwg(drawing_path))
    step("update", s.update())

    _INDEX.pop("symbols", None)
    fresh = _u(mgr.findEwSymbolXByName(str(name)))
    return {"ok": fresh is not None
            and all(st.get("rc") in (0, None) for st in steps),
            "symbol": None if fresh is None else _symbol_row(fresh,
                                                             detail=True),
            "drawing_path": drawing_path, "steps": steps}


def delete_symbol(app: Any, client: Any, name: str,
                  confirm_name: str) -> dict:
    """Remove a symbol from the library. ``confirm_name`` must match.

    Parts pointing at it keep the name as a dangling reference and pages
    already drawn keep their copy of the geometry, so this breaks future
    placements rather than existing drawings.
    """
    mgr = _symbol_manager(app)
    s = _u(mgr.findEwSymbolXByName(str(name)))
    if s is None:
        return {"ok": False, "error": f"no symbol named {name!r}"}
    if str(confirm_name) != str(name):
        return {"ok": False, "symbol": _symbol_row(s),
                "error": f"confirm_name {confirm_name!r} does not match "
                         f"{name!r}; nothing was deleted"}
    row = _symbol_row(s, detail=True)
    rc = _rc(s.remove())
    _INDEX.pop("symbols", None)
    gone = _u(mgr.findEwSymbolXByName(str(name))) is None
    return {"ok": rc in (0, None) and gone, "deleted": row, "rc": rc,
            "rc_name": _rc_name(rc), "confirmed_gone": gone}


# --------------------------------------------------------------------------
# Libraries


def list_libraries(app: Any, client: Any) -> dict:
    """The library codes parts and symbols are filed under.

    Reported from the library manager, and cross-checked against the codes
    actually in use, because a code can be referenced without being
    registered.
    """
    mgr = _u(_env(app).getEwLibraryManager())
    rows = []
    for lib in _each(client, _u(mgr.getEwLibraryArray())):
        rows.append({"id": _text(lib, "getID"),
                     "name": _text(lib, "getName"),
                     "code": _text(lib, "getCode"),
                     "description": _text(lib, "getDescription", LANG)})
    used_parts: dict[str, int] = {}
    for r in _part_index(app, client):
        used_parts[str(r["library_code"] or "")] = \
            used_parts.get(str(r["library_code"] or ""), 0) + 1
    return {"count": len(rows), "libraries": rows,
            "part_counts_by_library_code": dict(sorted(
                used_parts.items(), key=lambda kv: -kv[1]))}
