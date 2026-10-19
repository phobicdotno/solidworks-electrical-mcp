"""Raw database fields of project objects: read, probe, write.

Every project object (``IEwProjectFileX``, ``IEwProjectBookX``,
``IEwProjectComponentX`` ...) carries ``getProperty(name)`` and
``setProperty(name, value)``. The documentation does not say what ``name`` is.
It is the column name in the project database (``fil_fol_id``,
``bun_tagposition``, ``com_use_data2`` ...), and it reaches fields the typed
setters refuse or do not have at all: on an inserted page
``setEwProjectFolderID`` and ``setPosition`` return EW_BAD_INPUTS, while
``setProperty('fil_fol_id', ...)`` moves the page; books have no setter for
their order, ``bun_tagposition`` is it.

The column names are not listed anywhere in the API. The report templates
are the map: their SQL selects the columns by name. ``template_columns`` reads
them, ``probe_fields`` tries every name that could belong to an object (its
prefix with every column suffix seen anywhere) and keeps the ones the object
answers to - a known name returns rc 0, an unknown one EW_BAD_INPUTS (2).

Writes go through ``set_fields`` only: the name must be one the object
answers to, the old value is read first, and the new one is read back.
"""

from __future__ import annotations

import glob
import os
import re
from typing import Any

from .com import coerce_value
from .workflows import _need, _project, _rc, _rc_name, _u

# kind -> (column prefix, manager getter, finder)
KINDS: dict[str, tuple[str, str, str]] = {
    "file": ("fil", "getEwProjectFileManager", "findEwProjectFileByID"),
    "book": ("bun", "getEwProjectBookManager", "findEwProjectBookByID"),
    "folder": ("fol", "getEwProjectFolderManager", "findEwProjectFolderByID"),
    "component": ("com", "getEwProjectComponentManager",
                  "findEwProjectComponentByID"),
    "cable": ("cab", "getEwProjectCableManager", "findEwProjectCableByID"),
    "wire": ("wir", "getEwProjectWireManager", "findEwProjectWireByID"),
    "location": ("loc", "getEwProjectLocationManager",
                 "findEwProjectLocationByID"),
    "function": ("fun", "getEwProjectFunctionManager",
                 "findEwProjectFunctionByID"),
    "harness": ("har", "getEwProjectHarnessManager",
                "findEwProjectHarnessByID"),
    "line": ("lin", "getEwProjectLineManager", "findEwProjectLineByID"),
    "manufacturer_part": ("bom", "getEwProjectManufacturerPartManager",
                          "findEwProjectManufacturerPartByID"),
    "io": ("aio", "getEwProjectInputOutputManager",
           "findEwProjectInputOutputByID"),
}

_COL = re.compile(r"\b([a-z]{3})_([a-z0-9_]+)\b")


def template_dirs() -> list[str]:
    base = os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
                        "SOLIDWORKS Electrical")
    return [os.path.join(base, "BOMTemplate"),
            *glob.glob(os.path.join(base, "Projects", "*", "XmlConfig",
                                    "BOMTemplate"))]


def template_columns(dirs: list[str] | None = None) -> dict[str, list[str]]:
    """Column names the report templates select, grouped by prefix."""
    cols: dict[str, set[str]] = {}
    for d in dirs if dirs is not None else template_dirs():
        for path in glob.glob(os.path.join(d, "*.xml")):
            try:
                text = open(path, "rb").read().decode("utf-8", "ignore")
            except OSError:
                continue
            for q in re.findall(r"<SQLQuery>(.*?)</SQLQuery>", text, re.S):
                for pre, rest in _COL.findall(q):
                    cols.setdefault(pre, set()).add(f"{pre}_{rest}")
            for f in re.findall(r"<Field>([a-z]{3}_[a-z0-9_]+)</Field>", text):
                cols.setdefault(f[:3], set()).add(f)
    return {k: sorted(v) for k, v in sorted(cols.items())}


def candidates(prefix: str, columns: dict[str, list[str]]) -> list[str]:
    """Every name that could be a column of an object with this prefix: its
    own template columns plus its prefix with every suffix seen on any
    prefix (``fil_bun_id`` is in no template, ``loc_bun_id`` is)."""
    names = set(columns.get(prefix, []))
    for cols in columns.values():
        for c in cols:
            names.add(f"{prefix}_{c[4:]}")
    names.update(f"{prefix}_{p}_id" for p in columns)
    return sorted(names)


def _find(app: Any, kind: str, obj_id: int) -> Any:
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; one of {sorted(KINDS)}")
    _, mgr_getter, finder = KINDS[kind]
    mgr = _need(_u(getattr(_project(app), mgr_getter)()), mgr_getter)
    obj = _u(getattr(mgr, finder)(int(obj_id)))
    if obj is None:
        raise LookupError(f"no {kind} with id {obj_id}")
    return obj


def _get(obj: Any, name: str) -> tuple[Any, int | None]:
    try:
        r = obj.getProperty(name)
    except Exception:  # noqa: BLE001
        return None, 2
    return coerce_value(_u(r)), _rc(r)


def probe_fields(app: Any, client: Any, kind: str, obj_id: int,
                 dirs: list[str] | None = None) -> dict:
    """Which column names this object answers to, with their values."""
    obj = _find(app, kind, obj_id)
    prefix = KINDS[kind][0]
    known = {}
    for name in candidates(prefix, template_columns(dirs)):
        val, rc = _get(obj, name)
        if rc == 0:
            known[name] = val
    return {"ok": True, "kind": kind, "id": obj_id, "prefix": prefix,
            "count": len(known), "fields": known}


def get_fields(app: Any, client: Any, kind: str, obj_id: int,
               names: list[str]) -> dict:
    obj = _find(app, kind, obj_id)
    out, unknown = {}, []
    for n in names:
        val, rc = _get(obj, n)
        if rc == 0:
            out[n] = val
        else:
            unknown.append(n)
    return {"ok": not unknown, "kind": kind, "id": obj_id, "fields": out,
            "unknown": unknown}


def set_fields(app: Any, client: Any, kind: str, obj_id: int,
               values: dict[str, Any], dry_run: bool = True) -> dict:
    """Write column values with setProperty, then update() and read back.

    A name the object does not answer to is refused before anything is
    written, so a typo cannot land in some other field."""
    obj = _find(app, kind, obj_id)
    before, unknown = {}, []
    for n in values:
        val, rc = _get(obj, n)
        if rc == 0:
            before[n] = val
        else:
            unknown.append(n)
    if unknown:
        return {"ok": False, "error": f"{kind} {obj_id} has no field(s) "
                f"{unknown}; run probe_fields to see the ones it has",
                "unknown": unknown}
    plan = {n: {"from": before[n], "to": v} for n, v in values.items()}
    if dry_run:
        return {"ok": True, "dry_run": True, "kind": kind, "id": obj_id,
                "plan": plan}
    steps = {}
    for n, v in values.items():
        rc = _rc(obj.setProperty(n, v))
        steps[f"setProperty({n})"] = _rc_name(rc)
    steps["update"] = _rc_name(_rc(obj.update()))
    obj = _find(app, kind, obj_id)
    after = {n: _get(obj, n)[0] for n in values}
    mismatch = {n: after[n] for n in values if str(after[n]) != str(values[n])}
    return {"ok": not mismatch and all(s == "EW_NO_ERROR"
                                        for s in steps.values()),
            "dry_run": False, "kind": kind, "id": obj_id, "plan": plan,
            "steps": steps, "after": after, "not_applied": mismatch}


def move_folio(app: Any, client: Any, file_id: int,
               book_id: int | None = None, folder_id: int | None = None,
               position: int | None = None, dry_run: bool = True) -> dict:
    """Move a page to another book and/or folder, or to another position.

    ``folder_id=-1`` puts it at the top level of its book. A folder must
    belong to the target book. The typed setters refuse all of this on an
    inserted page; the database fields do not."""
    if book_id is None and folder_id is None and position is None:
        raise ValueError("give book_id, folder_id and/or position")
    values: dict[str, int] = {}
    target_book = book_id
    if folder_id is not None and folder_id != -1:
        folder = _find(app, "folder", folder_id)
        fbook = _get(folder, "fol_bun_id")[0]
        if fbook is None:
            fbook = _u(folder.getEwProjectBookID())
        if target_book is None:
            target_book = fbook
        elif int(fbook) != int(target_book):
            return {"ok": False, "error": f"folder {folder_id} is in book "
                    f"{fbook}, not book {target_book}"}
    if target_book is not None:
        _find(app, "book", target_book)
        values["fil_bun_id"] = int(target_book)
        if folder_id is None:
            page = _find(app, "file", file_id)
            cur_folder = _get(page, "fil_fol_id")[0]
            if cur_folder not in (None, -1):
                cur_book = _get(_find(app, "folder", cur_folder),
                                "fol_bun_id")[0]
                if cur_book is not None and int(cur_book) != int(target_book):
                    values["fil_fol_id"] = -1
    if folder_id is not None:
        values["fil_fol_id"] = int(folder_id)
    if position is not None:
        values["fil_tagposition"] = int(position)
    return set_fields(app, client, "file", file_id, values, dry_run=dry_run)


def set_book_order(app: Any, client: Any, book_ids: list[int],
                   dry_run: bool = True) -> dict:
    """Order the books in the document tree and the PDF export: the first id
    gets position 0, the next 1, and so on (``bun_tagposition``; new books
    are created with -1, which sorts them before book 1)."""
    plans = [set_fields(app, client, "book", b, {"bun_tagposition": i},
                        dry_run=dry_run) for i, b in enumerate(book_ids)]
    return {"ok": all(p["ok"] for p in plans), "dry_run": dry_run,
            "books": plans}


def add_book(app: Any, client: Any, tag: str, description: str,
             dry_run: bool = True) -> dict:
    """Create a book and put it after the existing ones (a new book starts at
    position -1, before book 1)."""
    mgr = _need(_u(_project(app).getEwProjectBookManager()),
                "getEwProjectBookManager")
    books = []
    for raw in _u(mgr.getEwProjectBookArray()) or ():
        try:
            b = client.Dispatch(raw)
        except Exception:  # noqa: BLE001
            b = raw
        books.append((_u(b.getID()), coerce_value(_u(b.getTag())),
                      _get(b, "bun_tagposition")[0]))
    if any(str(t) == str(tag) for _, t, _ in books):
        return {"ok": False, "error": f"a book with tag {tag!r} exists",
                "books": books}
    position = max([p for _, _, p in books if p is not None] + [-1]) + 1
    if dry_run:
        return {"ok": True, "dry_run": True, "tag": tag,
                "description": description, "position": position,
                "books": books}
    b = _u(mgr.newEwProjectBook())
    steps = {"setTag": _rc_name(_rc(b.setTag(str(tag)))),
             "setDescription": _rc_name(_rc(b.setDescription("en",
                                                             description))),
             "insert": _rc_name(_rc(b.insert()))}
    bid = _u(b.getID())
    steps["setProperty(bun_tagposition)"] = _rc_name(
        _rc(b.setProperty("bun_tagposition", position)))
    steps["update"] = _rc_name(_rc(b.update()))
    return {"ok": all(v == "EW_NO_ERROR" for v in steps.values()),
            "dry_run": False, "book_id": bid, "tag": tag,
            "position": position, "steps": steps}


def _all(app: Any, client: Any, getter: str, array: str) -> list:
    mgr = _need(_u(getattr(_project(app), getter)()), getter)
    out = []
    for raw in _u(getattr(mgr, array)()) or ():
        try:
            out.append(client.Dispatch(raw))
        except Exception:  # noqa: BLE001
            out.append(raw)
    return out


def move_folder(app: Any, client: Any, folder_id: int, book_id: int,
                dry_run: bool = True) -> dict:
    """Move a folder with its sub-folders and every page in them to another
    book. Setting only the folder's book leaves its pages behind in the old
    book (seen 2026-10-18), so the pages are moved with it."""
    _find(app, "book", book_id)
    _find(app, "folder", folder_id)
    folders = {int(_get(f, "fol_id")[0]): _get(f, "fol_fol_id")[0]
               for f in _all(app, client, "getEwProjectFolderManager",
                             "getEwProjectFolderArray")}
    tree = {int(folder_id)}
    grew = True
    while grew:
        grew = False
        for fid, parent in folders.items():
            if fid not in tree and parent is not None and int(parent) in tree:
                tree.add(fid)
                grew = True
    pages = [int(_get(p, "fil_id")[0])
             for p in _all(app, client, "getEwProjectFileManager",
                           "getEwProjectFileArray")
             if _get(p, "fil_fol_id")[0] is not None
             and int(_get(p, "fil_fol_id")[0]) in tree]
    plan = {"folders": sorted(tree), "pages": pages, "book_id": book_id}
    if dry_run:
        return {"ok": True, "dry_run": True, **plan}
    results = [set_fields(app, client, "folder", f, {"fol_bun_id": book_id},
                          dry_run=False) for f in sorted(tree)]
    results += [set_fields(app, client, "file", p, {"fil_bun_id": book_id},
                           dry_run=False) for p in pages]
    bad = [r for r in results if not r.get("ok")]
    return {"ok": not bad, "dry_run": False, **plan, "failed": bad}
