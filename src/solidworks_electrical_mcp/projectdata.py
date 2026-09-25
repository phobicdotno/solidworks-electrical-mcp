"""The project's own data: snapshots, PLC I/O, functions, cables.

Four managers hanging off IEwProjectX that nothing reached before.

Snapshots come first because they are the safety net the rest of this server
needs. ``number_wires(action="renumber")`` rewrites every wire label in a
project and has no undo; a snapshot taken beforehand does. They are the same
project versions the GUI's version manager shows.

The I/O manager is the PLC side: one row per channel, tying a mnemonic and a
channel address to the component circuit it lives on. On a Maritime Robotics
vessel that is the join between the SOLIDWORKS drawing and the CODESYS
program, so it is worth being able to read and write from here.
"""

from __future__ import annotations

from typing import Any

from .workflows import (LANG, _each, _need, _rc, _rc_name, _text, _u,
                        _project, _set_mark)


# EwErrorCode.EW_PROJECT_OPENED.
EW_PROJECT_OPENED = 45


class _Closed:
    """Close the project for the duration of a block, then reopen it.

    The snapshot API is squeezed between two rules that contradict each
    other, and both were found by running the calls:

    * ``IEwProjectSnapshotX.create`` returns EW_PROJECT_OPENED and takes no
      snapshot while the project is open;
    * ``getEwProjectSnapshotManager`` returns NULL while it is closed, so the
      manager cannot be fetched once the project has been shut.

    The way through is to take the manager and build the snapshot object
    while the project is open, and only call ``create`` or ``restore``
    inside this block. Those pointers keep working across the close.

    Reopening is not optional tidying: every other tool in this server acts
    on "the open project", so leaving it closed would strand the session.
    That is why the reopen is in __exit__ and runs even when the block
    raises.
    """

    def __init__(self, app: Any, steps: list):
        self.app, self.steps = app, steps
        self.project_id = None

    def _step(self, label: str, value: Any) -> None:
        rc = _rc(value)
        self.steps.append({"step": label, "rc": rc, "rc_name": _rc_name(rc)})

    def __enter__(self) -> "_Closed":
        proj = _u(self.app.getEwProjectCurrent())
        if proj is None:
            raise RuntimeError("no project is open")
        self.project_id = _u(proj.getID())
        self._step("closeEwProjectID",
                   self.app.closeEwProjectID(int(self.project_id)))
        return self

    def __exit__(self, *exc) -> bool:
        if self.project_id is not None:
            self._step("reopen",
                       self.app.openEwProjectID(int(self.project_id)))
        return False


def _snapshot_row(s: Any) -> dict:
    return {"id": _u(s.getID()),
            "name": _text(s, "getName"),
            "description": _text(s, "getDescription", LANG),
            "created": _text(s, "getCreationDate"),
            "event_type": _u(s.getEventType()),
            "event_details": _text(s, "getEventDetails"),
            "file_size": _u(s.getFileSize())}


def list_snapshots(app: Any, client: Any) -> dict:
    """The project's versions, newest first.

    These are real restore points, not a log: ``restore_snapshot`` puts the
    project back to one. Taking one before a project-wide pass is the only
    undo this server has.
    """
    proj = _project(app)
    mgr = _need(_u(proj.getEwProjectSnapshotManager()),
                "getEwProjectSnapshotManager")
    n = int(_u(mgr.getCount()) or 0)
    rows = []
    for i in range(n):
        s = _u(mgr.at(i))
        if s is not None:
            rows.append(_snapshot_row(s))
    rows.sort(key=lambda r: str(r["created"] or ""), reverse=True)
    return {"count": len(rows), "snapshots": rows}


def create_snapshot(app: Any, client: Any, name: str,
                    description: str | None = None,
                    generate_automated_drawings: bool = False) -> dict:
    """Take a restore point of the project as it stands.

    Worth doing before anything in the automation module: those passes edit
    the whole project at once and nothing else here can put it back.

    The snapshot is built while the project is open and taken while it is
    closed, because the API insists on both: ``create`` refuses on an open
    project and the manager is unreachable on a closed one. The project is
    open again when this returns.

    On a large project this does not finish in any useful time, and the
    snapshot is not what costs. Measured on the 181-folio SeaLeopard
    project: ``create`` produced its 12 KB snapshot about 12 minutes in, and
    the reopen that follows had still not returned an hour later. A 4-folio
    template project goes through the whole cycle in 1.6 seconds.

    So treat this as usable on small projects and as a background job on
    large ones, and expect a call that looks hung to be sitting in the
    reopen with the snapshot already taken. ``list_snapshots`` will show it.
    ``delete_snapshot`` needs no close at all and is instant either way.
    """
    steps: list[dict] = []

    def step(label, value):
        rc = _rc(value)
        steps.append({"step": label, "rc": rc, "rc_name": _rc_name(rc)})
        return rc

    # Everything reachable only while the project is open, first.
    proj = _project(app)
    mgr = _u(proj.getEwProjectSnapshotManager())
    if mgr is None:
        return {"ok": False,
                "error": "getEwProjectSnapshotManager returned NULL"}
    snap = _u(mgr.newEwProjectSnapshot())
    if snap is None:
        return {"ok": False, "error": "newEwProjectSnapshot returned NULL"}
    step("setName", snap.setName(str(name)))
    if description is not None:
        step("setDescription", snap.setDescription(LANG, str(description)))
    step("setGenerateAutomatedDrawings",
         snap.setGenerateAutomatedDrawings(bool(generate_automated_drawings)))

    with _Closed(app, steps):
        rc = step("create", snap.create())
    return {"ok": rc in (0, None), "snapshot": _snapshot_row(snap),
            "steps": steps}


def restore_snapshot(app: Any, client: Any, snapshot_id: int,
                     confirm_name: str) -> dict:
    """Put the project back to a snapshot. ``confirm_name`` must match.

    Everything done since that snapshot is discarded - drawings, components,
    numbering, the lot. The name has to be typed back because an id read from
    a stale listing would otherwise roll a project back by weeks.

    Like ``create_snapshot``, the snapshot is resolved while the project is
    open and restored while it is closed, and the project is open again when
    this returns.
    """
    # Resolve and check the name BEFORE closing anything: a refusal should
    # cost nothing, and closing a project only to decline is just a way to
    # lose the session's place.
    proj = _project(app)
    mgr = _u(proj.getEwProjectSnapshotManager())
    if mgr is None:
        return {"ok": False,
                "error": "getEwProjectSnapshotManager returned NULL"}
    snap = _u(mgr.findProjectSnapshotByID(int(snapshot_id)))
    if snap is None:
        return {"ok": False, "error": f"no snapshot with id {snapshot_id}"}
    row = _snapshot_row(snap)
    if str(confirm_name) != str(row["name"]):
        return {"ok": False, "snapshot": row,
                "error": f"confirm_name {confirm_name!r} does not match "
                         f"{row['name']!r}; nothing was restored"}

    steps: list[dict] = []
    with _Closed(app, steps):
        rc = _rc(snap.restore())
        steps.append({"step": "restore", "rc": rc, "rc_name": _rc_name(rc)})
    return {"ok": rc in (0, None), "restored": row, "rc": rc,
            "rc_name": _rc_name(rc), "steps": steps}


def delete_snapshot(app: Any, client: Any, snapshot_id: int,
                    confirm_name: str) -> dict:
    """Remove a snapshot. ``confirm_name`` must match its name.

    Removing a restore point does not touch the project, so this is tried on
    the open one first and pays for a close and reopen only if SOLIDWORKS
    insists with EW_PROJECT_OPENED.
    """
    proj = _project(app)
    mgr = _u(proj.getEwProjectSnapshotManager())
    if mgr is None:
        return {"ok": False,
                "error": "getEwProjectSnapshotManager returned NULL"}
    snap = _u(mgr.findProjectSnapshotByID(int(snapshot_id)))
    if snap is None:
        return {"ok": False, "error": f"no snapshot with id {snapshot_id}"}
    row = _snapshot_row(snap)
    if str(confirm_name) != str(row["name"]):
        return {"ok": False, "snapshot": row,
                "error": f"confirm_name {confirm_name!r} does not match "
                         f"{row['name']!r}; nothing was deleted"}
    steps: list[dict] = []
    rc = _rc(snap.remove())
    steps.append({"step": "remove", "rc": rc, "rc_name": _rc_name(rc)})
    if rc == EW_PROJECT_OPENED:
        with _Closed(app, steps):
            rc = _rc(snap.remove())
            steps.append({"step": "remove (closed)", "rc": rc,
                          "rc_name": _rc_name(rc)})
    return {"ok": rc in (0, None), "deleted": row, "rc": rc,
            "rc_name": _rc_name(rc), "steps": steps}


# --------------------------------------------------------------------------
# PLC input/output channels


def _io_row(io: Any) -> dict:
    return {"id": _u(io.getID()),
            "mnemonic": _text(io, "getMnemonic"),
            "key_code": _text(io, "getKeyCode"),
            "channel_address": _text(io, "getChannelAddress"),
            "description": _text(io, "getDescription", LANG),
            "macro": _text(io, "getMacroName"),
            "function_id": _u(io.getFunctionID()),
            "component_circuit_id": _u(io.getProjectComponentCircuitID())}


def list_io(app: Any, client: Any, mnemonic_contains: str | None = None,
            description_contains: str | None = None,
            limit: int = 500) -> dict:
    """The project's PLC I/O channels.

    Each row ties a mnemonic and a channel address to the component circuit
    it sits on, which is the join between this drawing set and the PLC
    program that drives it.

    Both come back empty on a channel nobody has assigned yet - 98 of them
    on the SeaLeopard project, every one carrying a key code (POD, PID, PIA,
    POA) and a description but no mnemonic - so key_code and description are
    what identify an unassigned channel.
    """
    proj = _project(app)
    mgr = _need(_u(proj.getEwProjectInputOutputManager()),
                "getEwProjectInputOutputManager")
    m_needle = (mnemonic_contains or "").lower()
    d_needle = (description_contains or "").lower()
    rows, scanned = [], 0
    for io in _each(client, _u(mgr.getEwProjectInputOutputArray())):
        scanned += 1
        row = _io_row(io)
        if m_needle and m_needle not in str(row["mnemonic"] or "").lower():
            continue
        if d_needle and d_needle not in str(
                row["description"] or "").lower():
            continue
        rows.append(row)
        if len(rows) >= limit:
            break
    return {"count": len(rows), "scanned": scanned, "limit": limit,
            "io": rows}


def update_io(app: Any, client: Any, io_id: int,
              mnemonic: str | None = None, description: str | None = None,
              key_code: str | None = None,
              macro_name: str | None = None,
              function_id: int | None = None) -> dict:
    """Change one I/O channel; anything left null is untouched.

    The channel address is computed from the component and its circuit, so it
    is read-only here: move the channel by changing what it is attached to,
    not by typing a different address.
    """
    proj = _project(app)
    mgr = _need(_u(proj.getEwProjectInputOutputManager()),
                "getEwProjectInputOutputManager")
    io = _u(mgr.findEwProjectInputOutputByID(int(io_id)))
    if io is None:
        return {"ok": False, "error": f"no I/O channel with id {io_id}"}
    steps: list[dict] = []

    def step(label: str, value: Any) -> None:
        rc = _rc(value)
        steps.append({"field": label, "rc": rc, "rc_name": _rc_name(rc)})

    if mnemonic is not None:
        step("mnemonic", io.setMnemonic(str(mnemonic)))
    if description is not None:
        step("description", io.setDescription(LANG, str(description)))
    if key_code is not None:
        step("key_code", io.setKeyCode(str(key_code)))
    if macro_name is not None:
        step("macro_name", io.setMacroName(str(macro_name)))
    if function_id is not None:
        step("function_id", io.setFunctionID(int(function_id)))
    if not steps:
        return {"ok": True, "changed": [], "io": _io_row(io),
                "note": "nothing to change"}
    rc = _rc(io.update())
    steps.append({"field": "update", "rc": rc, "rc_name": _rc_name(rc)})
    return {"ok": all(s["rc"] in (0, None) for s in steps),
            "changed": [s["field"] for s in steps if s["field"] != "update"],
            "io": _io_row(io), "steps": steps}


# --------------------------------------------------------------------------
# Functions


def _function_row(f: Any) -> dict:
    return {"id": _u(f.getID()),
            "tag": _text(f, "getTag"),
            "tag_path": _text(f, "getTagPath"),
            "tag_root": _text(f, "getTagRoot"),
            "tag_number": _u(f.getTagNumber()),
            "description": _text(f, "getDescription", LANG)}


def list_functions(app: Any, client: Any) -> dict:
    """The project's functional groups (the ``=`` part of a tag path).

    A location says where a device is; a function says what job it belongs
    to. Both prefix a component's full mark, so a project that uses functions
    and a tool that cannot see them disagree about what a device is called.
    """
    proj = _project(app)
    mgr = _need(_u(proj.getEwProjectFunctionManager()),
                "getEwProjectFunctionManager")
    rows = [_function_row(f)
            for f in _each(client, _u(mgr.getEwProjectFunctionArray()))]
    rows.sort(key=lambda r: str(r["tag_path"] or r["tag"] or ""))
    return {"count": len(rows), "functions": rows}


def add_function(app: Any, client: Any, tag: str,
                 description: str | None = None) -> dict:
    """Create a functional group."""
    proj = _project(app)
    mgr = _need(_u(proj.getEwProjectFunctionManager()),
                "getEwProjectFunctionManager")
    for f in _each(client, _u(mgr.getEwProjectFunctionArray())):
        if str(_u(f.getTag()) or "") == str(tag):
            return {"ok": False, "function": _function_row(f),
                    "error": f"a function tagged {tag!r} already exists"}
    f = _u(mgr.newEwProjectFunction())
    if f is None:
        return {"ok": False, "error": "newEwProjectFunction returned NULL"}
    steps: list[dict] = []

    def step(label: str, value: Any) -> None:
        rc = _rc(value)
        steps.append({"step": label, "rc": rc, "rc_name": _rc_name(rc)})

    step("insert", f.insert())
    for k, v in _set_mark(f, str(tag)).items():
        steps.append({"step": k, "rc": v, "rc_name": _rc_name(v)})
    if description is not None:
        step("setDescription", f.setDescription(LANG, str(description)))
    step("update", f.update())
    return {"ok": all(s["rc"] in (0, None) for s in steps),
            "function": _function_row(f), "steps": steps}


def delete_function(app: Any, client: Any, function_id: int,
                    confirm_tag: str) -> dict:
    """Remove a functional group. ``confirm_tag`` must match its tag.

    Components filed under it lose that part of their mark, so this changes
    how they are named.
    """
    proj = _project(app)
    mgr = _need(_u(proj.getEwProjectFunctionManager()),
                "getEwProjectFunctionManager")
    f = _u(mgr.findEwProjectFunctionByID(int(function_id)))
    if f is None:
        return {"ok": False, "error": f"no function with id {function_id}"}
    row = _function_row(f)
    if str(confirm_tag) != str(row["tag"]):
        return {"ok": False, "function": row,
                "error": f"confirm_tag {confirm_tag!r} does not match "
                         f"{row['tag']!r}; nothing was deleted"}
    rc = _rc(f.remove())
    gone = _u(mgr.findEwProjectFunctionByID(int(function_id))) is None
    return {"ok": rc in (0, None) and gone, "deleted": row, "rc": rc,
            "rc_name": _rc_name(rc), "confirmed_gone": gone}


# --------------------------------------------------------------------------
# Cables, write side. ``list_cables`` in workflows.py covers the read.


_CABLE_SETTERS = (
    ("description", "setDescription", "lang"),
    ("length", "setLength", "float"),
    ("fixed_length", "setIsFixedLength", "bool"),
    ("diameter", "setDiameter", "float"),
    ("bend_radius", "setBendRadius", "float"),
    ("colour", "setColorCode", "str"),
    ("family", "setFamily", "str"),
    ("supplier", "setSupplierName", "str"),
    ("stock_number", "setStockNumber", "str"),
    ("article_number", "setArticleNumber", "str"),
    ("linear_mass", "setLinearMass", "str"),
    ("standard", "setStandard", "str"),
    ("applied_voltage", "setAppliedVoltage", "float"),
    ("full_load_current", "setFullLoadCurrent", "float"),
    ("voltage_drop", "setVoltageDrop", "float"),
    ("upstream_location_id", "setUpStreamLocationID", "int"),
    ("downstream_location_id", "setDownStreamLocationID", "int"),
    ("function_id", "setFunctionID", "int"),
)


def _cable_row(c: Any) -> dict:
    return {"id": _u(c.getID()),
            "tag": _text(c, "getTag"),
            "tag_root": _text(c, "getTagRoot"),
            "tag_number": _u(c.getTagNumber()),
            "description": _text(c, "getDescription", LANG),
            "manufacturer": _text(c, "getManufacturer"),
            "reference": _text(c, "getReference"),
            "cores": _u(c.getCoreCount()),
            "length": _u(c.getLength()),
            "fixed_length": bool(_u(c.getIsFixedLength())),
            "diameter": _u(c.getDiameter()),
            "colour": _text(c, "getColorCode"),
            "family": _text(c, "getFamily"),
            "supplier": _text(c, "getSupplierName"),
            "upstream_location_id": _u(c.getUpStreamLocationID()),
            "downstream_location_id": _u(c.getDownStreamLocationID()),
            "function_id": _u(c.getFunctionID())}


def update_cable(app: Any, client: Any, cable_id: int,
                 description: str | None = None,
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
    recalculation: a cut length somebody measured on the boat is fixed, a
    length the software worked out is not.
    """
    proj = _project(app)
    mgr = _need(_u(proj.getEwProjectCableManager()),
                "getEwProjectCableManager")
    c = _u(mgr.findEwProjectCableByID(int(cable_id)))
    if c is None:
        return {"ok": False, "error": f"no cable with id {cable_id}"}
    here = locals()
    steps: list[dict] = []
    for field, setter, kind in _CABLE_SETTERS:
        v = here.get(field)
        if v is None:
            continue
        if kind == "lang":
            rc = _rc(getattr(c, setter)(LANG, str(v)))
        elif kind == "float":
            rc = _rc(getattr(c, setter)(float(v)))
        elif kind == "int":
            rc = _rc(getattr(c, setter)(int(v)))
        elif kind == "bool":
            rc = _rc(getattr(c, setter)(bool(v)))
        else:
            rc = _rc(getattr(c, setter)(str(v)))
        steps.append({"field": field, "rc": rc, "rc_name": _rc_name(rc)})
    if not steps:
        return {"ok": True, "changed": [], "cable": _cable_row(c),
                "note": "nothing to change"}
    rc = _rc(c.update())
    steps.append({"field": "update", "rc": rc, "rc_name": _rc_name(rc)})
    return {"ok": all(s["rc"] in (0, None) for s in steps),
            "changed": [s["field"] for s in steps if s["field"] != "update"],
            "cable": _cable_row(c), "steps": steps}


def delete_cable(app: Any, client: Any, cable_id: int,
                 confirm_tag: str) -> dict:
    """Remove a cable. ``confirm_tag`` must match its tag.

    The cable's cores go with it, so anything drawn as one of those cores
    loses its conductor.
    """
    proj = _project(app)
    mgr = _need(_u(proj.getEwProjectCableManager()),
                "getEwProjectCableManager")
    c = _u(mgr.findEwProjectCableByID(int(cable_id)))
    if c is None:
        return {"ok": False, "error": f"no cable with id {cable_id}"}
    row = _cable_row(c)
    if str(confirm_tag) != str(row["tag"]):
        return {"ok": False, "cable": row,
                "error": f"confirm_tag {confirm_tag!r} does not match "
                         f"{row['tag']!r}; nothing was deleted"}
    rc = _rc(c.remove())
    gone = _u(mgr.findEwProjectCableByID(int(cable_id))) is None
    return {"ok": rc in (0, None) and gone, "deleted": row, "rc": rc,
            "rc_name": _rc_name(rc), "confirmed_gone": gone}
