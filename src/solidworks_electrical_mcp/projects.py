"""Project lifecycle: list, open, close, create, delete, archive.

Everything in ``workflows.py`` starts from ``getEwProjectCurrent``, so it can
only act on whatever a human already opened in the GUI. That made the server
useless for the first and last step of any real job - pick the project, and
put it back. These functions work one level up, on the environment's project
manager, so a session can open the project it needs and hand it back when it
is done.

Which project is "current" is application state, not per-caller state: opening
one here changes what every other tool sees, exactly as clicking it in the
project manager would.
"""

from __future__ import annotations

import os
from typing import Any

from .workflows import (LANG, _each, _rc, _rc_name, _text, _u)

# EwProjectType (get_enum('EwProjectType')).
PROJECT_TYPE_NAMES = {-1: "unknown", 0: "project", 1: "macro"}

# EwEnvironmentFolderPathValue.kFolderPathProjectTemplate.
K_ENV_FOLDER_PROJECT_TEMPLATE = 16

# Project templates ship as <name>.proj.tewzip; insertFromTemplate wants the
# bare name.
TEMPLATE_SUFFIX = ".proj.tewzip"


def _manager(app: Any) -> Any:
    env = _u(app.getEwEnvironment())
    if env is None:
        raise RuntimeError("getEwEnvironment returned NULL")
    mgr = _u(env.getEwProjectManager())
    if mgr is None:
        raise RuntimeError("getEwProjectManager returned NULL")
    return mgr


def _row(p: Any) -> dict:
    ptype = _u(p.getProjectType())
    return {
        "id": _u(p.getID()),
        "name": _u(p.getName()),
        "description": _text(p, "getDescription", LANG),
        "type": PROJECT_TYPE_NAMES.get(ptype, str(ptype)),
        "type_code": ptype,
        "customer": _text(p, "getCustomerName"),
        "contract_number": _text(p, "getContractNumber"),
        "created_by": _text(p, "getCreatedBy"),
        "modified_by": _text(p, "getModifiedBy"),
        "modification_date": _text(p, "getModificationDate"),
        "open_by_me": bool(_u(p.isOpenByMe())),
        "open_by_another": bool(_u(p.isOpenByAnother())),
    }


def _find(app: Any, client: Any, project_id: int | None = None,
          name: str | None = None) -> Any:
    """Resolve a project by id, or by exact then substring name match."""
    mgr = _manager(app)
    if project_id is not None:
        p = _u(mgr.findEwProjectByID(int(project_id)))
        if p is None:
            raise LookupError(f"no project with id {project_id}")
        return p
    if not name:
        raise ValueError("give either project_id or name")
    p = _u(mgr.findEwProjectByName(str(name)))
    if p is not None:
        return p
    # findEwProjectByName is exact; fall back to a unique substring so a
    # caller can say "65021" rather than the full MR3ELE mark.
    needle = str(name).lower()
    hits = [q for q in _each(client, _u(mgr.getEwProjectArray()))
            if needle in str(_u(q.getName()) or "").lower()]
    if not hits:
        raise LookupError(f"no project named {name!r}")
    if len(hits) > 1:
        names = sorted(str(_u(q.getName())) for q in hits)
        raise LookupError(f"{name!r} matches {len(hits)} projects: {names}")
    return hits[0]


def _current_id(app: Any) -> int | None:
    proj = _u(app.getEwProjectCurrent())
    return None if proj is None else _u(proj.getID())


# --------------------------------------------------------------------------
# Read-only


def list_projects(app: Any, client: Any, name_contains: str | None = None,
                  project_type: str | None = "project") -> dict:
    """Every project in the environment, whether or not one is open.

    ``project_type`` defaults to "project" because the same manager also
    carries macros; pass None for both.
    """
    mgr = _manager(app)
    current = _current_id(app)
    needle = (name_contains or "").lower()
    rows = []
    for p in _each(client, _u(mgr.getEwProjectArray())):
        row = _row(p)
        if project_type and row["type"] != project_type:
            continue
        if needle and needle not in str(row["name"]).lower() \
                and needle not in str(row["description"] or "").lower():
            continue
        row["is_current"] = row["id"] == current
        rows.append(row)
    rows.sort(key=lambda r: str(r["modification_date"] or ""), reverse=True)
    return {"count": len(rows), "current_project_id": current,
            "projects": rows}


def list_project_templates(app: Any, client: Any) -> dict:
    """The template names ``create_project`` accepts.

    Templates are files in the environment's ProjectTemplate folder, not rows
    in a database, so this reads the folder the application reports.
    """
    env = _u(app.getEwEnvironment())
    folder = _u(env.getFolderPath(K_ENV_FOLDER_PROJECT_TEMPLATE))
    names: list[str] = []
    if folder and os.path.isdir(folder):
        for entry in sorted(os.listdir(folder)):
            if entry.lower().endswith(TEMPLATE_SUFFIX):
                names.append(entry[:-len(TEMPLATE_SUFFIX)])
    return {"folder": folder, "count": len(names), "templates": names}


# --------------------------------------------------------------------------
# Lifecycle


def open_project(app: Any, client: Any, project_id: int | None = None,
                 name: str | None = None) -> dict:
    """Open a project and make it the current one for every other tool."""
    p = _find(app, client, project_id, name)
    row = _row(p)
    pid = row["id"]
    if row["open_by_another"]:
        return {"ok": False, "project": row,
                "error": f"{row['name']!r} is open by another user"}
    already = _current_id(app) == pid
    rc = _rc(app.openEwProjectID(int(pid)))
    now = _current_id(app)
    return {"ok": rc in (0, None) and now == pid, "project": row,
            "was_already_current": already,
            "rc": rc, "rc_name": _rc_name(rc), "current_project_id": now}


def close_project(app: Any, client: Any, project_id: int | None = None,
                  name: str | None = None) -> dict:
    """Close a project; with no argument, close whichever one is current.

    Closing is how SOLIDWORKS commits a project's pending state, so this is
    the right last step of a session rather than an optional tidy-up.
    """
    if project_id is None and not name:
        project_id = _current_id(app)
        if project_id is None:
            return {"ok": True, "closed": None,
                    "note": "no project was open"}
    p = _find(app, client, project_id, name)
    row = _row(p)
    rc = _rc(app.closeEwProjectID(int(row["id"])))
    return {"ok": rc in (0, None), "closed": row, "rc": rc,
            "rc_name": _rc_name(rc), "current_project_id": _current_id(app)}


def create_project(app: Any, client: Any, name: str,
                   template: str | None = None,
                   description: str | None = None,
                   customer: str | None = None,
                   contract_number: str | None = None,
                   open_after: bool = False) -> dict:
    """Create a project, optionally from one of the shipped templates.

    A template carries the drawing standard (symbol set, wire styles, title
    blocks, page format), so a project made without one starts empty and has
    to be configured by hand. Pass a name from ``list_project_templates``.

    The description and customer are written in a second pass with the new
    project open: ``IEwProjectX.update`` commits nothing on a closed project,
    it just returns EW_PROJECT_NOTOPENED, and the edits are lost without a
    word. The project is closed again afterwards unless ``open_after``.
    """
    name = str(name).strip()
    if not name:
        raise ValueError("a project needs a name")
    mgr = _manager(app)
    if _u(mgr.findEwProjectByName(name)) is not None:
        return {"ok": False, "error": f"a project named {name!r} already "
                                      f"exists"}
    if template:
        avail = list_project_templates(app, client)["templates"]
        if avail and template not in avail:
            return {"ok": False,
                    "error": f"unknown template {template!r}; have {avail}"}

    # Note which project to put back BEFORE creating anything. Reading it
    # after the insert would assume the insert leaves the current project
    # alone, and if a release ever changes that, the project the caller was
    # working in is the thing that gets lost.
    restore = _current_id(app)

    p = _u(mgr.newEwProject())
    if p is None:
        return {"ok": False, "error": "newEwProject returned NULL"}

    steps: list[dict] = []

    def step(label: str, value: Any) -> int | None:
        rc = _rc(value)
        steps.append({"step": label, "rc": rc, "rc_name": _rc_name(rc)})
        return rc

    step("setName", p.setName(name))
    if template:
        rc_ins = step("insertFromTemplate", p.insertFromTemplate(template))
    else:
        rc_ins = step("insert", p.insert())
    if rc_ins not in (0, None):
        return {"ok": False, "steps": steps,
                "error": f"creating {name!r} failed: {_rc_name(rc_ins)}"}

    pid = _u(p.getID())
    fields = {"description": description, "customer": customer,
              "contract_number": contract_number}
    wants_fields = any(v is not None for v in fields.values())

    if wants_fields or open_after:
        step("openEwProjectID", app.openEwProjectID(int(pid)))
    if wants_fields:
        # Re-fetch through the manager: the pointer that created the project
        # predates the open, and the fields are committed against the open one.
        live = _u(mgr.findEwProjectByID(int(pid))) or p
        step("setName", live.setName(name))
        if description is not None:
            step("setDescription", live.setDescription(LANG, description))
        if customer is not None:
            step("setCustomerName", live.setCustomerName(customer))
        if contract_number is not None:
            step("setContractNumber", live.setContractNumber(contract_number))
        step("update", live.update())
    if wants_fields and not open_after:
        step("closeEwProjectID", app.closeEwProjectID(int(pid)))
        if restore is not None and restore != pid:
            step("reopen previous", app.openEwProjectID(int(restore)))

    row = _row(_u(mgr.findEwProjectByID(int(pid))) or p)
    return {"ok": all(st["rc"] in (0, None) for st in steps),
            "project": row, "template": template,
            "current_project_id": _current_id(app), "steps": steps}


def delete_project(app: Any, client: Any, project_id: int,
                   confirm_name: str) -> dict:
    """Permanently remove a project. ``confirm_name`` must match its name.

    There is no undo and no recycle bin: the drawings, the components and the
    whole database row go. The name has to be typed back so an id that drifted
    since it was looked up cannot delete the wrong project.
    """
    p = _find(app, client, project_id=project_id)
    row = _row(p)
    if str(confirm_name) != str(row["name"]):
        return {"ok": False, "project": row,
                "error": f"confirm_name {confirm_name!r} does not match "
                         f"{row['name']!r}; nothing was deleted"}
    if row["open_by_another"]:
        return {"ok": False, "project": row,
                "error": "open by another user; nothing was deleted"}
    if _current_id(app) == row["id"]:
        _rc(app.closeEwProjectID(int(row["id"])))
    rc = _rc(p.remove())
    gone = True
    try:
        _find(app, client, project_id=row["id"])
        gone = False
    except LookupError:
        pass
    return {"ok": rc in (0, None) and gone, "deleted": row, "rc": rc,
            "rc_name": _rc_name(rc), "confirmed_gone": gone}


# --------------------------------------------------------------------------
# Archive


def archive_project(app: Any, client: Any, output_path: str,
                    project_id: int | None = None, name: str | None = None,
                    with_dependencies: bool = True) -> dict:
    """Write a project out as a .tewzip archive.

    With ``with_dependencies`` the archive also carries the library content
    the project references (symbols, parts, title blocks), which is what makes
    it restorable on a machine with a different environment.
    """
    import pythoncom  # type: ignore
    from win32com.client import VARIANT  # type: ignore

    if project_id is None and not name:
        project_id = _current_id(app)
        if project_id is None:
            raise RuntimeError("no project is open; give project_id or name")
    p = _find(app, client, project_id, name)
    row = _row(p)
    output_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    mgr = _manager(app)
    ids = VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_I4, [int(row["id"])])
    rc = _rc(mgr.archive(ids, output_path, bool(with_dependencies)))
    exists = os.path.isfile(output_path)
    return {"ok": rc in (0, None) and exists, "project": row,
            "output_path": output_path, "exists": exists,
            "size_bytes": os.path.getsize(output_path) if exists else 0,
            "with_dependencies": bool(with_dependencies),
            "rc": rc, "rc_name": _rc_name(rc)}


def unarchive_project(app: Any, client: Any, archive_path: str,
                      with_dependencies: bool = True) -> dict:
    """Restore a .tewzip archive, and report which project it became."""
    archive_path = os.path.abspath(archive_path)
    if not os.path.isfile(archive_path):
        raise FileNotFoundError(archive_path)
    mgr = _manager(app)
    before = {r["id"] for r in list_projects(app, client,
                                             project_type=None)["projects"]}
    res = mgr.unarchive(archive_path, bool(with_dependencies))
    rc = _rc(res)
    after = list_projects(app, client, project_type=None)["projects"]
    new = [r for r in after if r["id"] not in before]
    return {"ok": rc in (0, None) and bool(new), "archive_path": archive_path,
            "rc": rc, "rc_name": _rc_name(rc), "restored": new}


# --------------------------------------------------------------------------
# Properties


_PROPERTY_SETTERS = (
    ("description", "setDescription", True),
    ("customer", "setCustomerName", False),
    ("customer_address1", "setCustomerAddress1", False),
    ("customer_address2", "setCustomerAddress2", False),
    ("customer_address3", "setCustomerAddress3", False),
    ("drawing_office", "setDrawingOfficeName", False),
    ("drawing_office_address1", "setDrawingOfficeAddress1", False),
    ("drawing_office_address2", "setDrawingOfficeAddress2", False),
    ("drawing_office_address3", "setDrawingOfficeAddress3", False),
    ("contract_number", "setContractNumber", False),
    ("extern_id", "setExternID", False),
)


def project_properties(app: Any, client: Any,
                       project_id: int | None = None,
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

    ``project_id``/``name`` pick the project and default to the open one;
    every other argument left at None is read rather than written. These are
    the fields the title block prints, so a change is only visible on the
    sheets after ``regenerate_title_blocks``.

    The project's own name is deliberately not settable here - renaming is a
    bigger operation than editing a caption, and ``rename_project`` owns it.
    """
    if project_id is None and not name:
        project_id = _current_id(app)
        if project_id is None:
            raise RuntimeError("no project is open; give project_id or name")
    p = _find(app, client, project_id, name)
    pid = _u(p.getID())

    values = {
        "description": description, "customer": customer,
        "customer_address1": customer_address1,
        "customer_address2": customer_address2,
        "customer_address3": customer_address3,
        "drawing_office": drawing_office,
        "drawing_office_address1": drawing_office_address1,
        "drawing_office_address2": drawing_office_address2,
        "drawing_office_address3": drawing_office_address3,
        "contract_number": contract_number, "extern_id": extern_id,
    }

    wanted = [f for f, _, _ in _PROPERTY_SETTERS if values.get(f) is not None]
    if wanted and _current_id(app) != pid:
        # update() on a closed project returns EW_PROJECT_NOTOPENED and drops
        # the edit without raising. Say so rather than reporting a write that
        # did not happen.
        return {"ok": False, "project": _row(p), "changed": [], "steps": [],
                "error": f"{_u(p.getName())!r} is not the open project; "
                         f"SOLIDWORKS commits these fields only while it is "
                         f"open. Run open_project first."}

    steps: list[dict] = []
    for field, setter, needs_lang in _PROPERTY_SETTERS:
        if values.get(field) is None:
            continue
        value = str(values[field])
        args = (LANG, value) if needs_lang else (value,)
        rc = _rc(getattr(p, setter)(*args))
        steps.append({"field": field, "value": value, "rc": rc,
                      "rc_name": _rc_name(rc)})
    if steps:
        rc = _rc(p.update())
        steps.append({"step": "update", "rc": rc, "rc_name": _rc_name(rc)})

    row = _row(p)
    row["customer_address"] = [_text(p, f"getCustomerAddress{n}")
                               for n in (1, 2, 3)]
    row["drawing_office"] = _text(p, "getDrawingOfficeName")
    row["drawing_office_address"] = [_text(p, f"getDrawingOfficeAddress{n}")
                                     for n in (1, 2, 3)]
    row["extern_id"] = _text(p, "getExternID")
    return {"ok": all(s.get("rc") in (0, None) for s in steps),
            "project": row,
            "changed": [s["field"] for s in steps if "field" in s],
            "steps": steps}
