"""Project configuration, wire styles, title blocks, cable references.

The settings half of a project: the values that decide how marks are formed,
what a wire of a given style looks like and what it is made of, which title
block a page prints with, and which cables the project may draw from.

``IEwProjectConfigurationX`` exposes the whole configuration through one
enum-driven pair, ``getEwProjectConfigValue`` / ``setEwProjectConfigValue``
over ``EwProjectConfigValue``. Rather than hard-coding a list of setting
names that would go stale on the next release, the enum is read from the
live type library, so this reports whatever the installed version actually
has.
"""

from __future__ import annotations

from typing import Any

from .workflows import LANG, _each, _rc, _rc_name, _text, _u, _project

def _num(obj: Any, getter: str, *args: Any) -> Any:
    """Read a numeric getter, or None if this build does not have it.

    The interface lists here come from the published help, which has been
    wrong about member names before. A row that is missing one field is
    useful; a row that raises is not.
    """
    try:
        return _u(getattr(obj, getter)(*args))
    except Exception:  # noqa: BLE001
        return None


def _enum_members(obj: Any, enum_name: str) -> dict[str, int]:
    """Read one enum out of the live type library.

    The catalogue shipped with this server is built from the published help,
    which does not carry enum values at all. The type library does, and it is
    the installed version's own, so a release that adds a setting shows up
    without a rebuild.
    """
    import pythoncom  # type: ignore

    tlib, _idx = obj._oleobj_.GetTypeInfo().GetContainingTypeLib()
    for i in range(tlib.GetTypeInfoCount()):
        if tlib.GetDocumentation(i)[0] != enum_name:
            continue
        info = tlib.GetTypeInfo(i)
        if info.GetTypeAttr().typekind != pythoncom.TKIND_ENUM:
            continue
        ta = info.GetTypeAttr()
        return {info.GetNames(info.GetVarDesc(v).memid)[0]:
                info.GetVarDesc(v).value for v in range(ta.cVars)}
    return {}


def _config(app: Any) -> Any:
    proj = _project(app)
    cfg = _u(proj.getEwProjectConfiguration())
    if cfg is None:
        raise RuntimeError("getEwProjectConfiguration returned NULL")
    return cfg


def project_config(app: Any, client: Any, setting: str | None = None,
                   value: Any = None) -> dict:
    """Read the project's configuration, or set one value.

    With no arguments this reports every ``EwProjectConfigValue`` the
    installed version defines, with what the project currently holds for it.
    With ``setting`` it reports one. With ``setting`` and ``value`` it writes
    that one and reads it back, so the result shows what actually took.

    These are the settings behind mark formulas, cross-reference format, wire
    numbering rules, page format and the rest, so changing one changes how
    the project behaves rather than what it contains.
    """
    cfg = _config(app)
    names = _enum_members(cfg, "EwProjectConfigValue")
    if not names:
        return {"ok": False,
                "error": "EwProjectConfigValue is not in the type library"}

    if setting is not None and setting not in names:
        near = sorted(n for n in names if setting.lower() in n.lower())
        return {"ok": False,
                "error": f"unknown setting {setting!r}",
                "did_you_mean": near[:10], "count_available": len(names)}

    steps: list[dict] = []
    if setting is not None and value is not None:
        rc = _rc(cfg.setEwProjectConfigValue(names[setting], value))
        steps.append({"step": "setEwProjectConfigValue", "rc": rc,
                      "rc_name": _rc_name(rc)})
        rc = _rc(cfg.update())
        steps.append({"step": "update", "rc": rc, "rc_name": _rc_name(rc)})

    wanted = [setting] if setting is not None else sorted(names)
    rows = []
    for n in wanted:
        try:
            v = _u(cfg.getEwProjectConfigValue(names[n]))
        except Exception as exc:  # noqa: BLE001
            v = f"{type(exc).__name__}: {exc}"
        rows.append({"setting": n, "code": names[n], "value": v})
    return {"ok": all(s["rc"] in (0, None) for s in steps),
            "count": len(rows), "settings": rows,
            "measurement": _num(cfg, "getEwMeasurementType"),
            "code_language": _text(cfg, "getCurrentCodeLanguage"),
            "cabinet_title_block": _text(cfg, "getCabinetLayoutTitleBlock"),
            "steps": steps}


# --------------------------------------------------------------------------
# Wire styles


def _wire_style_row(w: Any) -> dict:
    return {"id": _u(w.getID()),
            "name": _text(w, "getName"),
            "description": _text(w, "getDescription", LANG),
            "section_or_gauge": _text(w, "getWireSectionGauge"),
            "colour": _text(w, "getWireColorName"),
            "colour_value": _num(w, "getColor"),
            "tension": _text(w, "getTension"),
            "frequency": _text(w, "getFrequence"),
            "linear_mass": _text(w, "getLinearMass"),
            "bend_radius": _num(w, "getBendRadius"),
            "wire_mark_formula": _text(w, "getWireTagFormula"),
            "equipotential_formula": _text(w, "getEquipTagFormula")}


def list_wire_styles(app: Any, client: Any,
                     name_contains: str | None = None) -> dict:
    """The wire styles the project draws with.

    A style is both electrical and graphical: the section, colour, tension
    and frequency a wire of that style carries, and the mark formula its
    label is built from. A schematic that draws every wire in one style has
    no wiring list worth printing, so this is the first thing to check on a
    project whose reports look wrong.
    """
    proj = _project(app)
    mgr = _u(proj.getEwProjectWireStyleManagerX())
    if mgr is None:
        return {"ok": False,
                "error": "getEwProjectWireStyleManagerX returned NULL"}
    needle = (name_contains or "").lower()
    rows = []
    for w in _each(client, _u(mgr.getEwProjectWireStyleArray())):
        row = _wire_style_row(w)
        if needle and needle not in str(row["name"] or "").lower() \
                and needle not in str(row["description"] or "").lower():
            continue
        rows.append(row)
    rows.sort(key=lambda r: str(r["name"] or ""))
    return {"count": len(rows), "wire_styles": rows}


def update_wire_style(app: Any, client: Any, wire_style_id: int,
                      description: str | None = None,
                      section_or_gauge: str | None = None,
                      colour: str | None = None,
                      tension: str | None = None,
                      frequency: str | None = None,
                      linear_mass: str | None = None,
                      bend_radius: float | None = None,
                      wire_mark_formula: str | None = None,
                      equipotential_formula: str | None = None) -> dict:
    """Change one wire style; anything left null is untouched.

    Every wire already drawn in this style takes the change, so editing the
    section or the mark formula of a style in use is a project-wide edit
    wearing a small hat.
    """
    proj = _project(app)
    mgr = _u(proj.getEwProjectWireStyleManagerX())
    if mgr is None:
        return {"ok": False,
                "error": "getEwProjectWireStyleManagerX returned NULL"}
    w = _u(mgr.findEwProjectWireStyleByID(int(wire_style_id)))
    if w is None:
        return {"ok": False,
                "error": f"no wire style with id {wire_style_id}"}
    setters = (
        ("description", "setDescription", "lang", description),
        ("section_or_gauge", "setWireSectionGauge", "str", section_or_gauge),
        ("colour", "setWireColorName", "str", colour),
        ("tension", "setTension", "str", tension),
        ("frequency", "setFrequence", "str", frequency),
        ("linear_mass", "setLinearMass", "str", linear_mass),
        ("bend_radius", "setBendRadius", "float", bend_radius),
        ("wire_mark_formula", "setWireTagFormula", "str", wire_mark_formula),
        ("equipotential_formula", "setEquipTagFormula", "str",
         equipotential_formula),
    )
    steps: list[dict] = []
    for field, setter, kind, v in setters:
        if v is None:
            continue
        if kind == "lang":
            rc = _rc(getattr(w, setter)(LANG, str(v)))
        elif kind == "float":
            rc = _rc(getattr(w, setter)(float(v)))
        else:
            rc = _rc(getattr(w, setter)(str(v)))
        steps.append({"field": field, "rc": rc, "rc_name": _rc_name(rc)})
    if not steps:
        return {"ok": True, "changed": [], "wire_style": _wire_style_row(w),
                "note": "nothing to change"}
    rc = _rc(w.update())
    steps.append({"field": "update", "rc": rc, "rc_name": _rc_name(rc)})
    return {"ok": all(s["rc"] in (0, None) for s in steps),
            "changed": [s["field"] for s in steps if s["field"] != "update"],
            "wire_style": _wire_style_row(w), "steps": steps}


# --------------------------------------------------------------------------
# Title blocks and cable references, both environment-level


def list_title_blocks(app: Any, client: Any,
                      name_contains: str | None = None,
                      limit: int = 100) -> dict:
    """The title blocks available to draw pages with.

    ``regenerate_title_blocks`` refreshes what a page prints; this says what
    it could print with.
    """
    env = _u(app.getEwEnvironment())
    mgr = _u(env.getEwTitleBlockManager())
    if mgr is None:
        return {"ok": False, "error": "getEwTitleBlockManager returned NULL"}
    needle = (name_contains or "").lower()
    rows, scanned = [], 0
    for t in _each(client, _u(mgr.getEwTitleBlockArray())):
        scanned += 1
        row = {"id": _text(t, "getID"),
               "name": _text(t, "getName"),
               "description": _text(t, "getDescription", LANG),
               "library_code": _text(t, "getLibraryCode")}
        if needle and needle not in str(row["name"] or "").lower():
            continue
        rows.append(row)
        if len(rows) >= limit:
            break
    rows.sort(key=lambda r: str(r["name"] or ""))
    return {"count": len(rows), "scanned": scanned, "title_blocks": rows}


def search_cable_references(app: Any, client: Any,
                            manufacturer: str | None = None,
                            reference: str | None = None,
                            description_contains: str | None = None,
                            limit: int = 50) -> dict:
    """The cable types in the library, which is what a cable is made from.

    ``list_cables`` reports the cables a project has; this reports what could
    be specified. Each row carries the core count, because a cable reference
    with the wrong number of cores is the usual reason a drawn cable will not
    take the conductors asked of it.
    """
    env = _u(app.getEwEnvironment())
    mgr = _u(env.getEwCableReferenceManager())
    if mgr is None:
        return {"ok": False,
                "error": "getEwCableReferenceManager returned NULL"}
    m_needle = (manufacturer or "").lower()
    r_needle = (reference or "").lower()
    d_needle = (description_contains or "").lower()
    rows, scanned = [], 0
    for c in _each(client, _u(mgr.getEwCableReferenceArray())):
        scanned += 1
        row = {"manufacturer": _text(c, "getManufacturer"),
               "reference": _text(c, "getReference"),
               "description": _text(c, "getDescription", LANG),
               "cores": _num(c, "getCableCoreCount"),
               "core_diameter": _num(c, "getCoreDiameter"),
               "library_code": _text(c, "getLibraryCode")}
        if m_needle and m_needle not in str(row["manufacturer"] or "").lower():
            continue
        if r_needle and r_needle not in str(row["reference"] or "").lower():
            continue
        if d_needle and d_needle not in str(row["description"] or "").lower():
            continue
        rows.append(row)
        if len(rows) >= limit:
            break
    rows.sort(key=lambda r: (str(r["manufacturer"] or ""),
                             str(r["reference"] or "")))
    return {"count": len(rows), "scanned": scanned,
            "cable_references": rows}


def list_harnesses(app: Any, client: Any) -> dict:
    """The project's harnesses, with the cables and wires filed under each.

    A harness groups conductors that are physically bundled, which is what a
    cut list and a 3D route are built from.
    """
    proj = _project(app)
    mgr = _u(proj.getEwProjectHarnessManager())
    if mgr is None:
        return {"ok": False,
                "error": "getEwProjectHarnessManager returned NULL"}
    rows = []
    for h in _each(client, _u(mgr.getEwProjectHarnessArray())):
        rows.append({"id": _num(h, "getID"),
                     "tag": _text(h, "getTag"),
                     "description": _text(h, "getDescription", LANG)})
    rows.sort(key=lambda r: str(r["tag"] or ""))
    return {"count": len(rows), "harnesses": rows}
