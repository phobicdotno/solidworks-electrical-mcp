"""Project-wide operations: numbering, arrows, reports, DWG export.

These are the commands a drawing office runs across a whole project rather
than on one page - number the wires, renumber the component marks, place the
origin/destination arrows, generate the terminal-strip drawings, push the
reports out to Excel, hand the lot over as DWG.

Every one of them is a bulk edit with no undo, so they share a shape:

* a ``selection`` that is "all", a book, a folder, or an explicit list of
  folios, so a run can be tried on one page before it is turned loose on 181;
* an explicit ``action`` rather than a boolean, because "number the new
  wires" and "renumber every wire" are one enum apart and only one of them is
  safe on a project somebody has already wired;
* ``renumber_manual`` off by default. A mark somebody typed by hand is a
  decision, and a numbering pass should not quietly overwrite it.
"""

from __future__ import annotations

import os
import time
from typing import Any

from .workflows import (_each, _need, _rc, _rc_name, _text, _u,
                        find_folio, _folio_row, _project, list_folios)

# EwNumberWireAction.
WIRE_ACTIONS = {"new": 0, "new_and_recalculate": 1, "renumber": 2,
                "remove": 3}
# EwNumberMarkAction.
MARK_ACTIONS = {"update": 0, "renumber": 1}
# EwObjectType, the things a mark numbering pass can act on. It is a bit
# field: kObjectComponent is 4, kObjectCable 32, and so on.
MARK_OBJECTS = {"location": 1, "function": 2, "component": 4,
                "terminal_strip": 8, "terminal": 16, "cable": 32,
                "harness": 64}
# EwSelectionType.
SELECTION_TYPES = {"all": 0, "book": 1, "folder": 2, "folios": 3}
# EwAutoArrowActionType.
ARROW_ACTIONS = {"auto_connect": 0, "reconnect": 1, "remove": 2}
# EwTSDrawingOption.
TS_DRAWING_OPTIONS = {"delete_existing": 0, "keep_existing": 1}
# EwDwgSaveType / EwDwgVersion / EwDwgFileExport.
DWG_SAVE_TYPES = {"dwg": 0, "dxf": 1, "dxb": 2}
DWG_VERSIONS = {"2000": 23, "2004": 25, "2007": 27, "2010": 29,
                "2013": 31, "2018": 33}
DWG_EXPORT_MULTIPLE, DWG_EXPORT_SINGLE = 0, 1
# A file-per-page export is rejected outright without a naming formula, and
# the formula takes a bare variable name: FILE_TAG works, %FILE_TAG% does
# not. Measured, not documented.
DEFAULT_DWG_NAME_FORMULA = "FILE_TAG"
# EwFileExtension, for report export.
REPORT_FORMATS = {"txt": 0, "csv": 1, "xls": 2, "xlsx": 3, "xml": 4}


def _steps() -> tuple[list, Any]:
    """A step recorder: every COM call's return code, named, in order."""
    steps: list[dict] = []

    def step(label: str, value: Any) -> int | None:
        rc = _rc(value)
        steps.append({"step": label, "rc": rc, "rc_name": _rc_name(rc)})
        return rc

    return steps, step


def _tree(folder: str) -> dict:
    r"""Every file under a folder: relative path -> (size, mtime).

    Both exporters write into subfolders of the directory they are given - a
    DWG export lands in ``<project>\<book>\<page>.dwg`` - so a flat
    listdir sees an empty folder and reports a failure that did not happen.

    The size and mtime are here because the name alone is not enough either.
    A project is normally exported to the same folder every time, and on the
    second run the files are overwritten rather than added, so comparing
    names finds nothing new and calls a successful export a silent failure.
    """
    out: dict = {}
    for base, _, files in os.walk(folder):
        for f in files:
            full = os.path.join(base, f)
            try:
                st = os.stat(full)
            except OSError:
                continue
            out[os.path.relpath(full, folder)] = (st.st_size, st.st_mtime_ns)
    return out


def _written(before: dict, after: dict) -> list[str]:
    """The files that appeared or changed, which is what "it wrote" means."""
    return sorted(k for k, v in after.items() if before.get(k) != v)


def _export_outcome(rc: int | None, before: dict, after: dict,
                    what: str, output_dir: str) -> dict:
    """Judge an export by the folder, and tell the three cases apart.

    Measured: SOLIDWORKS does not rewrite a DWG that is already there. A
    second export to the same folder leaves the file untouched, mtime and
    all, so "nothing changed" means one of two quite different things and
    reporting either as a plain failure is wrong.

    * files appeared or changed  -> it worked;
    * nothing changed but the folder already holds files -> they were
      already there and were left alone, which is not a failure;
    * nothing changed and the folder is empty -> it really did nothing,
      whatever the return code said.
    """
    written = _written(before, after)
    out = {"ok": rc in (0, None) and bool(written or after),
           "files_written": written,
           "files_present": sorted(after),
           "output_dir": output_dir}
    if rc in (0, None) and not written and after:
        out["note"] = (f"{what} rewrote nothing: the files were already in "
                       f"{output_dir} and SOLIDWORKS leaves an existing "
                       f"export in place. Delete them first to force a "
                       f"fresh one.")
    elif rc in (0, None) and not after:
        out["error"] = (f"{what} reported success and wrote nothing under "
                        f"{output_dir}")
    return out


def _id_array(ids: list[int]) -> Any:
    import pythoncom  # type: ignore
    from win32com.client import VARIANT  # type: ignore
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_I4,
                   [int(i) for i in ids])


def _resolve_folios(app: Any, client: Any, pages: list | None,
                    file_ids: list[int] | None) -> tuple[list[int], list]:
    ids: list[int] = []
    rows: list[dict] = []
    for fid in file_ids or []:
        f = find_folio(app, client, file_id=fid)
        ids.append(_u(f.getID()))
        rows.append(_folio_row(f))
    for pg in pages or []:
        f = find_folio(app, client, page=pg)
        ids.append(_u(f.getID()))
        rows.append(_folio_row(f))
    return ids, rows


def _resolve_selection(selection: str | None, book_id: int | None,
                       folder_id: int | None, folio_ids: list[int]) -> str:
    """Work out the scope from what the caller actually asked for.

    Passing ``pages=["102"]`` without also passing ``selection="folios"``
    used to leave the scope at "all": the operation ran over the whole
    project, and the result still listed page 102 under "folios", so a
    renumber of 181 folios came back reading like a renumber of one page.

    So the scope is derived, not defaulted. Naming folios, a book or a
    folder means that. Naming nothing means the whole project, which stays
    possible but has to be the only thing asked for.
    """
    if selection is None:
        if folio_ids:
            return "folios"
        if book_id is not None:
            return "book"
        if folder_id is not None:
            return "folder"
        return "all"
    if selection not in SELECTION_TYPES:
        raise ValueError(f"unknown selection {selection!r}; "
                         f"have {sorted(SELECTION_TYPES)}")
    # An explicit scope that contradicts the scope arguments is a mistake
    # worth refusing: one of the two readings runs over the whole project.
    if selection != "folios" and folio_ids:
        raise ValueError(
            f"selection={selection!r} with pages/file_ids given is "
            f"ambiguous; pass selection='folios' to act on those pages, or "
            f"drop them to act on "
            + ('the whole project' if selection == 'all' else f'that {selection}'))
    if selection != "book" and book_id is not None:
        raise ValueError(f"selection={selection!r} with book_id given is "
                         f"ambiguous")
    if selection != "folder" and folder_id is not None:
        raise ValueError(f"selection={selection!r} with folder_id given is "
                         f"ambiguous")
    return selection


def _apply_selection(obj: Any, step: Any, selection: str,
                     book_id: int | None, folder_id: int | None,
                     folio_ids: list[int]) -> None:
    """Point a bulk operation at all of the project, a book, a folder or
    a list of folios. The scope is the difference between a rehearsal and a
    project-wide rewrite, so it is set explicitly every time."""
    if selection not in SELECTION_TYPES:
        raise ValueError(f"unknown selection {selection!r}; "
                         f"have {sorted(SELECTION_TYPES)}")
    step("setSelectionType", obj.setSelectionType(SELECTION_TYPES[selection]))
    if selection == "book":
        if book_id is None:
            raise ValueError("selection='book' needs book_id")
        step("setSelection", obj.setSelection(_id_array([book_id])))
    elif selection == "folder":
        if folder_id is None:
            raise ValueError("selection='folder' needs folder_id")
        step("setSelection", obj.setSelection(_id_array([folder_id])))
    elif selection == "folios":
        if not folio_ids:
            raise ValueError("selection='folios' needs pages or file_ids")
        step("setSelection", obj.setSelection(_id_array(folio_ids)))


# --------------------------------------------------------------------------
# Numbering


def number_wires(app: Any, client: Any, action: str = "new",
                 selection: str | None = None,
                 book_id: int | None = None,
                 folder_id: int | None = None,
                 pages: list | None = None,
                 file_ids: list[int] | None = None,
                 renumber_manual: bool = False,
                 reset_position: bool = False) -> dict:
    """Run the wire numbering pass.

    ``action``:

    * ``new`` - number only wires that have no number yet. The safe one.
    * ``new_and_recalculate`` - that, plus recompute the existing marks.
    * ``renumber`` - throw every wire number away and start again. On a
      project somebody has already built to, this changes labels that are
      printed on real wires in a real cabinet.
    * ``remove`` - strip the numbers entirely.

    ``renumber_manual`` decides whether a number somebody typed by hand is
    fair game; it is off, because a hand-typed mark is a decision.
    """
    if action not in WIRE_ACTIONS:
        raise ValueError(f"unknown action {action!r}; "
                         f"have {sorted(WIRE_ACTIONS)}")
    proj = _project(app)
    ids, rows = _resolve_folios(app, client, pages, file_ids)
    selection = _resolve_selection(selection, book_id, folder_id, ids)
    op = _u(proj.newEwProjectNumberWires())
    if op is None:
        return {"ok": False, "error": "newEwProjectNumberWires returned NULL"}
    steps, step = _steps()
    step("setActionOnManualNumber",
         op.setActionOnManualNumber(bool(renumber_manual)))
    step("setResetPosition", op.setResetPosition(bool(reset_position)))
    _apply_selection(op, step, selection, book_id, folder_id, ids)
    rc = step("process", op.process(WIRE_ACTIONS[action]))
    return {"ok": rc in (0, None), "action": action, "selection": selection,
            "folios": rows, "steps": steps}


def number_marks(app: Any, client: Any, action: str = "update",
                 object_types: list[str] | None = None,
                 start_number: int | None = None,
                 step_increment: int | None = None,
                 renumber_manual: bool = False) -> dict:
    """Run the component-mark numbering pass.

    ``action`` is ``update`` (fill in what has no mark) or ``renumber``
    (assign every mark again from the start number). A renumber rewrites the
    tags the whole project cross-references, so it is the one to rehearse.

    ``object_types`` selects what is numbered: component, cable, terminal,
    terminal_strip, location, function, harness. It defaults to component.
    """
    if action not in MARK_ACTIONS:
        raise ValueError(f"unknown action {action!r}; "
                         f"have {sorted(MARK_ACTIONS)}")
    kinds = object_types or ["component"]
    unknown = [k for k in kinds if k not in MARK_OBJECTS]
    if unknown:
        raise ValueError(f"unknown object types {unknown}; "
                         f"have {sorted(MARK_OBJECTS)}")
    proj = _project(app)
    op = _u(proj.newEwProjectNumberMarks())
    if op is None:
        return {"ok": False, "error": "newEwProjectNumberMarks returned NULL"}
    steps, step = _steps()
    step("setActionOnManualNumber",
         op.setActionOnManualNumber(bool(renumber_manual)))
    for k in kinds:
        step(f"addNumberObjectType {k}",
             op.addNumberObjectType(MARK_OBJECTS[k]))
        if start_number is not None:
            step(f"setStartNumber {k}",
                 op.setStartNumber(MARK_OBJECTS[k], int(start_number)))
        if step_increment is not None:
            step(f"setStepIncrement {k}",
                 op.setStepIncrement(MARK_OBJECTS[k], int(step_increment)))
    rc = step("process", op.process(MARK_ACTIONS[action]))
    return {"ok": rc in (0, None), "action": action, "object_types": kinds,
            "steps": steps}


def generate_arrows(app: Any, client: Any, action: str = "auto_connect",
                    selection: str | None = None,
                    book_id: int | None = None,
                    folder_id: int | None = None, pages: list | None = None,
                    file_ids: list[int] | None = None,
                    origin_symbol: str | None = None,
                    destination_symbol: str | None = None,
                    replace_manual: bool = False) -> dict:
    """Place, refresh or strip the origin/destination arrows.

    Arrows are how a wire that leaves one sheet is picked up on another, so
    ``auto_connect`` is what turns a set of drawn pages into a wired project.
    ``remove`` strips them, which breaks those links.
    """
    if action not in ARROW_ACTIONS:
        raise ValueError(f"unknown action {action!r}; "
                         f"have {sorted(ARROW_ACTIONS)}")
    proj = _project(app)
    ids, rows = _resolve_folios(app, client, pages, file_ids)
    selection = _resolve_selection(selection, book_id, folder_id, ids)
    op = _u(proj.newEwProjectAutomaticArrows())
    if op is None:
        return {"ok": False,
                "error": "newEwProjectAutomaticArrows returned NULL"}
    steps, step = _steps()
    step("setActionOnManual", op.setActionOnManual(bool(replace_manual)))
    if origin_symbol:
        step("setOriginSymbol", op.setOriginSymbol(str(origin_symbol)))
    if destination_symbol:
        step("setDestinationSymbol",
             op.setDestinationSymbol(str(destination_symbol)))
    _apply_selection(op, step, selection, book_id, folder_id, ids)
    rc = step("process", op.process(ARROW_ACTIONS[action]))
    return {"ok": rc in (0, None), "action": action, "selection": selection,
            "folios": rows, "steps": steps}


def optimize_wire_order(app: Any, client: Any, selection: str | None = None,
                        book_id: int | None = None,
                        folder_id: int | None = None,
                        pages: list | None = None,
                        file_ids: list[int] | None = None,
                        remove_wire_cable_cores: bool = False,
                        remove_bridges: bool = False,
                        replace_manual: bool = False) -> dict:
    """Recompute the connection order within each equipotential.

    This decides which terminal a wire physically lands on when several share
    a potential, so it is what makes a from-to wiring list buildable rather
    than merely correct.
    """
    proj = _project(app)
    ids, rows = _resolve_folios(app, client, pages, file_ids)
    selection = _resolve_selection(selection, book_id, folder_id, ids)
    op = _u(proj.newEwProjectOptimizeWireOrder())
    if op is None:
        return {"ok": False,
                "error": "newEwProjectOptimizeWireOrder returned NULL"}
    steps, step = _steps()
    step("setActionOnManual", op.setActionOnManual(bool(replace_manual)))
    step("setActionRemoveWireCableCore",
         op.setActionRemoveWireCableCore(bool(remove_wire_cable_cores)))
    step("setActionRemoveBridges",
         op.setActionRemoveBridges(bool(remove_bridges)))
    _apply_selection(op, step, selection, book_id, folder_id, ids)
    rc = step("process", op.process())
    return {"ok": rc in (0, None), "selection": selection, "folios": rows,
            "steps": steps}


def generate_terminal_strip_drawings(app: Any, client: Any,
                                     component_ids: list[int] | None = None,
                                     book_id: int | None = None,
                                     keep_existing: bool = True) -> dict:
    """Draw the terminal-strip sheets for the strips in the project.

    A strip in this project is a part-less parent component carrying numbered
    children, one per terminal, so the strips to pass here are the parents -
    CC_T1, CC_T2 - not their children.

    ``keep_existing`` leaves drawings that are already there alone; turning it
    off deletes and regenerates them, which loses any hand editing.
    """
    proj = _project(app)
    op = _u(proj.newEwProjectGenerateTSDrawing())
    if op is None:
        return {"ok": False,
                "error": "newEwProjectGenerateTSDrawing returned NULL"}
    steps, step = _steps()
    option = "keep_existing" if keep_existing else "delete_existing"
    step("setDrawingOption",
         op.setDrawingOption(TS_DRAWING_OPTIONS[option]))
    for cid in component_ids or []:
        if book_id is None:
            raise ValueError("component_ids needs book_id: a generated "
                             "drawing has to land in a book")
        step(f"setDestinationBookOrFolderID {cid}",
             op.setDestinationBookOrFolderID(int(cid), int(book_id)))
    rc = step("generate", op.generate())
    return {"ok": rc in (0, None), "option": option,
            "component_ids": component_ids or [], "steps": steps}


# --------------------------------------------------------------------------
# Export


def export_dwg(app: Any, client: Any, output_dir: str,
               pages: list | None = None, file_ids: list[int] | None = None,
               all_pages: bool = False, save_type: str = "dwg",
               dwg_version: str = "2018", single_file: bool = False,
               file_name_formula: str | None = None,
               generate_automated_drawings: bool = False) -> dict:
    """Export folios as DWG or DXF files, which is how a project leaves here.

    ``save_type`` is dwg, dxf or dxb; ``dwg_version`` one of 2000, 2004,
    2007, 2010, 2013, 2018. ``single_file`` packs every folio into one
    drawing instead of a file per page.

    A file-per-page export needs a naming formula and will not run without
    one: ``exportDwg(kExportToMultipleFile)`` returns EW_BAD_INPUTS with no
    formula set. The formula takes a bare variable name - ``FILE_TAG`` names
    each file after its page mark - and NOT the percent-delimited form the
    title-block editor uses; ``%FILE_TAG%`` is rejected the same way as no
    formula at all. So one is supplied by default.

    Files land in a subfolder tree under ``output_dir``, named for the
    project and the book, and ``files_written`` reports them relative to it.
    """
    if save_type not in DWG_SAVE_TYPES:
        raise ValueError(f"unknown save_type {save_type!r}; "
                         f"have {sorted(DWG_SAVE_TYPES)}")
    if str(dwg_version) not in DWG_VERSIONS:
        raise ValueError(f"unknown dwg_version {dwg_version!r}; "
                         f"have {sorted(DWG_VERSIONS)}")
    proj = _project(app)
    output_dir = os.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    ids, rows = _resolve_folios(app, client, pages, file_ids)
    if not all_pages and not ids:
        raise ValueError("give pages, file_ids, or all_pages=True")

    op = _u(proj.newEwProjectExportDWGFiles())
    if op is None:
        return {"ok": False,
                "error": "newEwProjectExportDWGFiles returned NULL"}
    steps, step = _steps()
    step("setExportDirectory", op.setExportDirectory(output_dir))
    step("setDwgSaveType", op.setDwgSaveType(DWG_SAVE_TYPES[save_type]))
    step("setDwgVersion", op.setDwgVersion(DWG_VERSIONS[str(dwg_version)]))
    step("setGenerateAutomatedDrawings",
         op.setGenerateAutomatedDrawings(bool(generate_automated_drawings)))
    formula = file_name_formula
    if not single_file and not formula:
        formula = DEFAULT_DWG_NAME_FORMULA
    if formula:
        step("setStrFileNameFormula", op.setStrFileNameFormula(str(formula)))
    if not all_pages:
        step("setExportDwgSelectionFiles",
             op.setExportDwgSelectionFiles(_id_array(ids)))
    before = _tree(output_dir)
    rc = step("exportDwg", op.exportDwg(
        DWG_EXPORT_SINGLE if single_file else DWG_EXPORT_MULTIPLE))
    out = _export_outcome(rc, before, _tree(output_dir), "exportDwg",
                          output_dir)
    out.update({"save_type": save_type, "dwg_version": str(dwg_version),
                "file_name_formula": formula, "folios": rows,
                "steps": steps})
    return out


def list_reports(app: Any, client: Any) -> dict:
    """The report configurations attached to the project.

    These are the saved queries - bill of materials, wire list, terminal
    list, cable list - that ``export_reports`` runs. A project carries its
    own set, which is why this is a project read and not a library one.
    """
    proj = _project(app)
    mgr = _need(_u(proj.getEwProjectReportManager()),
                "getEwProjectReportManager")
    n = int(_u(mgr.getCount()) or 0)
    rows = []
    for i in range(n):
        r = _u(mgr.at(i))
        if r is None:
            continue
        rows.append({"id": _u(r.getID()),
                     "file_name": _text(r, "getReportFileName"),
                     "filter": _text(r, "getFilter"),
                     "order": _u(r.getOrderNo()),
                     "export_type": _u(r.getEwProjectDataExportType())})
    rows.sort(key=lambda x: (x["order"] if isinstance(x["order"], int) else 0,
                             x["id"]))
    return {"count": len(rows), "reports": rows}


def export_reports(app: Any, client: Any, output_dir: str,
                   report_ids: list[int] | None = None,
                   all_reports: bool = False, file_format: str = "xlsx",
                   include_column_header: bool = True,
                   one_sheet_per_break: bool = False,
                   add_to_project: bool = False) -> dict:
    """Export the project's reports to Excel, text, CSV or XML.

    ``file_format`` picks the writer: xlsx/xls go through the Excel export,
    txt/csv through the text one, xml through the XML one. Pass report ids
    from ``list_reports``, or ``all_reports``.

    Known not to deliver on SOLIDWORKS Electrical 2025 SP5: every writer
    returns EW_NO_ERROR and writes no file, in every format, with the target
    folder, the report ids, the extension and the all-reports flag all set
    and reading back correctly. Turning ``add_to_project`` on turns the
    silence into EW_OBJECT_NOT_FOUND instead. The result is therefore judged
    by what is on disk rather than by the return code, so a run that
    produces nothing says so instead of reporting success.
    """
    if file_format not in REPORT_FORMATS:
        raise ValueError(f"unknown file_format {file_format!r}; "
                         f"have {sorted(REPORT_FORMATS)}")
    if not all_reports and not report_ids:
        raise ValueError("give report_ids or all_reports=True")
    proj = _project(app)
    output_dir = os.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    op = _u(proj.newEwProjectExportReport())
    if op is None:
        return {"ok": False,
                "error": "newEwProjectExportReport returned NULL"}
    steps, step = _steps()
    step("setTargetFolder", op.setTargetFolder(output_dir))
    step("setOpenFile", op.setOpenFile(False))
    step("setAddCreatedFileToProject",
         op.setAddCreatedFileToProject(bool(add_to_project)))
    step("setExportAllReports", op.setExportAllReports(bool(all_reports)))
    step("setIncludeColumnHeader",
         op.setIncludeColumnHeader(bool(include_column_header)))
    step("setOneSheetByBreak", op.setOneSheetByBreak(bool(one_sheet_per_break)))
    step("setEwFileExtension",
         op.setEwFileExtension(REPORT_FORMATS[file_format]))
    if not all_reports:
        step("setEwProjectReportIDArray",
             op.setEwProjectReportIDArray(_id_array(report_ids or [])))

    before = _tree(output_dir)
    if file_format in ("xlsx", "xls"):
        rc = step("doExcelExport", op.doExcelExport())
    elif file_format == "xml":
        rc = step("doXMLExport", op.doXMLExport())
    else:
        rc = step("doTxtExport", op.doTxtExport())
    out = _export_outcome(rc, before, _tree(output_dir),
                          "the report writer", output_dir)
    out.update({"file_format": file_format, "steps": steps})
    if "error" in out:
        out["error"] += (". This is what SOLIDWORKS Electrical 2025 SP5 does "
                         "for every format from COM; use "
                         "generate_report_drawings to put the report into "
                         "the project as a folio, or run the export from the "
                         "GUI.")
    return out


def _folio_slots(app: Any, client: Any) -> dict[int, dict]:
    return {r["id"]: r for r in list_folios(app, client)["folios"]}


# Patched by the tests so a settle wait takes no wall-clock time.
_SLEEP = time.sleep
_CLOCK = time.monotonic

SETTLE_INTERVAL = 3.0
SETTLE_TIMEOUT = 120.0


def _slot_signature(slots: dict[int, dict]) -> dict:
    return {i: (r["book_id"], r["folder_id"], r["position"], r["page"],
                r["description"]) for i, r in slots.items()}


def _wait_for_settle(app: Any, client: Any, interval: float = SETTLE_INTERVAL,
                     timeout: float = SETTLE_TIMEOUT) -> tuple[dict, dict]:
    """Poll the folio list until two consecutive listings agree.

    ``doGenerateDrawings`` returns while SOLIDWORKS is still at work on the
    tree. Measured 2026-10-20 (2025 SP5, a 3-book project): right after the
    call the folio list carried 48 blank folios named after the report with
    no filter suffix (an empty page, 3.7 KB when exported) at the end of
    book 1's top level, and the report's own folios still had their ids
    and folder; about a minute later all but the last three blanks were
    gone and the report's folios had been moved to the book's top level
    (folder -1), after the cable list. Anything read or moved inside that
    window is read or moved on a tree SOLIDWORKS is still rewriting, so
    this waits for it to stop. Returns the settled listing and a report
    (``settled``, ``polls``, ``seconds``, ``transient_ids``: folios seen in
    an earlier poll but not in the last one).
    """
    timeout = float(timeout)
    if not timeout == timeout or timeout < 0:        # NaN or negative
        raise ValueError(f"settle_timeout must be a number of seconds >= 0, "
                         f"got {timeout!r}")
    start = _CLOCK()
    slots = _folio_slots(app, client)
    seen: set[int] = set(slots)
    polls, settled = 1, False
    while True:
        if _CLOCK() - start >= timeout:
            break
        _SLEEP(interval)
        nxt = _folio_slots(app, client)
        polls += 1
        seen |= set(nxt)
        same = _slot_signature(nxt) == _slot_signature(slots)
        slots = nxt
        if same:
            settled = True
            break
    info = {"settled": settled, "polls": polls,
            "seconds": round(_CLOCK() - start, 1),
            "interval": interval, "timeout": timeout,
            "transient_ids": sorted(seen - set(slots))}
    if not settled:
        info["error"] = (f"the folio list was still changing after "
                         f"{timeout:g} s; SOLIDWORKS may still be drawing "
                         f"the report, run list_folios again before "
                         f"touching the tree")
    return slots, info


def _restore_positions(app: Any, client: Any, before: dict[int, dict],
                       after: dict[int, dict]) -> dict:
    """Put a report's folios back where they sat before the regeneration.

    Two things have been measured on 2025 SP5 (2026-10-20):

    * a regeneration deletes the report's folios and draws new ones (new
      ids) at the END of their folder, while the page marks stay what they
      were, so the tree and the PDF run cable list before bill of materials
      although the pages read 72-86 and 87-90;
    * a regeneration keeps the ids and MOVES the folios out of their folder
      to the book's top level (folder -1), after the cable list, once the
      tree has settled.

    The old slots are the fix in both cases. Every folio that survived goes
    back to its old (book, folder, position); the new folios of a replaced
    group take the old ones' folder and positions in order, matched by page
    mark first (the regenerated pages keep the old marks, the blanks carry
    new ones), then by description (a page the report grew by), and only
    when neither tells anything by the folder they landed in or as the only
    group. When the report grew, the pages that followed move up by the
    difference. A new folio that matches nothing is left alone and listed
    in ``unmatched_new``: the blank folios SOLIDWORKS leaves behind look
    like that. Residual ambiguity: a report with no filter suffix names its
    blanks exactly like its pages; a name-only match is taken for a grown
    page only when it sits after the mark-matched pages (the blanks sit
    before them), so check ``unmatched_new`` and the tree after such a run.
    """
    from . import rawfields as rf
    gone = [r for i, r in before.items() if i not in after]
    new = [r for i, r in after.items() if i not in before]
    key = lambda r: (r["book_id"], r["folder_id"])  # noqa: E731
    pos = lambda r: r["position"] if isinstance(r["position"], int) else 0  # noqa: E731
    slot = lambda r: (r["book_id"], r["folder_id"], pos(r))  # noqa: E731

    # Target slot of every folio that is on the tree now.
    target: dict[int, tuple] = {i: slot(before[i]) for i in after if i in before}

    # Replacement groups: the gone folios by their old (book, folder).
    groups = []
    for grp in sorted({key(r) for r in gone}, key=str):
        olds = sorted((r for r in gone if key(r) == grp), key=pos)
        groups.append({"key": grp, "olds": olds, "news": [], "by_desc": [],
                       "descs": {str(r["description"]) for r in olds},
                       "tags": {str(r["page"]) for r in olds}})
    all_descs = set().union(*(g["descs"] for g in groups)) if groups else set()
    all_tags = set().union(*(g["tags"] for g in groups)) if groups else set()
    by_tag = any(str(r["page"]) in all_tags for r in new)
    by_desc = any(str(r["description"]) in all_descs for r in new)
    unmatched: list[int] = []
    for r in sorted(new, key=pos):
        cands, bucket = [], "news"
        if by_tag:
            cands = [g for g in groups if str(r["page"]) in g["tags"]]
        if not cands and by_desc:
            cands = [g for g in groups if str(r["description"]) in g["descs"]]
            bucket = "by_desc"
        if len(cands) > 1:
            cands = [g for g in cands if g["key"] == key(r)] or cands[:1]
        if not cands and not by_tag and not by_desc:
            cands = [g for g in groups if g["key"] == key(r)]
            if not cands and len(groups) == 1:
                cands = groups
        if not cands:
            unmatched.append(r["id"])
            continue
        cands[0][bucket].append(r)
    for g in groups:
        # Pages that kept their mark first, then the ones matched by name
        # only (a page the report grew by). A report's pages are drawn in
        # sequence, so a name-only page counts as grown only when it sits
        # after the mark-matched ones; the blanks are made before the
        # report's pages are moved and so sit before them.
        grown = g["by_desc"]
        if g["news"]:
            tail = max(pos(r) for r in g["news"])
            grown = [r for r in g["by_desc"] if pos(r) > tail]
            unmatched += [r["id"] for r in g["by_desc"] if pos(r) <= tail]
        g["news"] = g["news"] + grown

    for g in groups:
        olds, news = g["olds"], g["news"]
        if not news:
            continue
        book, folder = g["key"]
        first, last = pos(olds[0]), pos(olds[-1])
        for i, r in enumerate(news):
            target[r["id"]] = (book, folder, first + i)
        delta = len(news) - len(olds)
        if delta > 0:
            for i, old in before.items():
                if i in target and i in after and old["book_id"] == book \
                        and pos(old) > last:
                    b, f, p = target[i]
                    target[i] = (b, f, p + delta)

    moves: list[dict] = []
    for i, (book, folder, p) in target.items():
        cur = after[i]
        values: dict[str, int] = {}
        if cur["folder_id"] != folder:
            values["fil_fol_id"] = int(folder)
        if cur["book_id"] != book:
            values["fil_bun_id"] = int(book)
        if pos(cur) != p:
            values["fil_tagposition"] = int(p)
        if not values:
            continue
        moves.append({"file_id": i, "page": cur["page"],
                      "from": pos(cur), "to": p,
                      "folder_from": cur["folder_id"], "folder_to": folder,
                      "values": values})
    results = []
    for m in moves:
        res = rf.set_fields(app, client, "file", m["file_id"],
                            m.pop("values"), dry_run=False)
        results.append({**m, "ok": bool(res.get("ok")),
                        "error": res.get("error")})
    out = {"folios_replaced": [r["id"] for r in gone],
           "folios_new": [r["id"] for r in new],
           "repositioned": results, "unmatched_new": unmatched,
           "ok": all(r["ok"] for r in results)}
    if unmatched:
        out["note"] = ("new folios that match no replaced one were left "
                       "where they are; SOLIDWORKS leaves blank folios "
                       "named after the report behind a regeneration "
                       "(check_page_ink, then delete_folio)")
    return out


def generate_report_drawings(app: Any, client: Any,
                             report_ids: list[int] | None = None,
                             all_reports: bool = False,
                             book_or_folder_id: int | None = None,
                             keep_position: bool = False,
                             wait_for_settle: bool = True,
                             settle_timeout: float = SETTLE_TIMEOUT) -> dict:
    """Render the reports as folios inside the project instead of to a file.

    This is what puts the bill of materials or the wire list into the
    document tree as printable pages, so the drawing set carries its own
    tables rather than pointing at a spreadsheet somebody has to find.

    ``doGenerateDrawings`` returns before SOLIDWORKS is done with the tree:
    for about a minute the folio list carries dozens of blank folios named
    after the report, and the report's own folios are moved (or replaced,
    see ``_restore_positions``) only at the end of that. ``wait_for_settle``
    polls the folio list every few seconds until two consecutive listings
    agree (``settle_timeout`` seconds at most, the wait is reported under
    ``settle``), and only then reads the tree for ``keep_position`` and
    returns. ``keep_position`` records where the report's folios sat and
    puts the regenerated ones back there, folder and position; without it,
    ``move_folio`` puts them back by hand.
    """
    if not all_reports and not report_ids:
        raise ValueError("give report_ids or all_reports=True")
    proj = _project(app)
    # One run per report: handed several ids at once, doGenerateDrawings
    # returns success but only draws the first (SOLIDWORKS Electrical 2025
    # SP5, measured 2026-10-18).
    batches = [None] if all_reports else [[int(r)] for r in report_ids]
    steps, step = _steps()
    rcs = []
    restored: list[dict] = []
    settles: list[dict] = []
    for ids in batches:
        op = _u(proj.newEwProjectExportReport())
        if op is None:
            return {"ok": False,
                    "error": "newEwProjectExportReport returned NULL",
                    "steps": steps}
        step("setExportAllReports", op.setExportAllReports(bool(all_reports)))
        if ids is not None:
            step("setEwProjectReportIDArray",
                 op.setEwProjectReportIDArray(_id_array(ids)))
        if book_or_folder_id is not None:
            step("setBookOrFolderId",
                 op.setBookOrFolderId(int(book_or_folder_id)))
        before = _folio_slots(app, client) if keep_position else {}
        rc = step("doGenerateDrawings", op.doGenerateDrawings())
        rcs.append(rc)
        if rc not in (0, None):
            continue
        if wait_for_settle:
            after, info = _wait_for_settle(app, client,
                                           timeout=float(settle_timeout))
            settles.append(info)
            if not info["settled"]:
                # The tree is still moving: a restore now would write
                # positions SOLIDWORKS is about to rewrite.
                continue
        else:
            after = _folio_slots(app, client) if keep_position else {}
        if keep_position:
            restored.append(_restore_positions(app, client, before, after))
    ok = (all(rc in (0, None) for rc in rcs)
          and all(r["ok"] for r in restored)
          and all(s["settled"] for s in settles))
    out = {"ok": ok, "report_ids": report_ids or [],
           "all_reports": bool(all_reports), "steps": steps}
    if wait_for_settle:
        out["settle"] = settles
        if not all(s["settled"] for s in settles):
            out["error"] = next(s["error"] for s in settles
                                if not s["settled"])
            if keep_position:
                out["error"] += ("; keep_position was not applied to that "
                                 "run, use move_folio once the tree is still")
    else:
        out["warning"] = ("returned without waiting: SOLIDWORKS keeps "
                          "rewriting the tree for about a minute after "
                          "doGenerateDrawings (blank folios named after "
                          "the report appear and go); list_folios again "
                          "before moving or deleting anything")
    if keep_position:
        out["positions"] = restored
    else:
        out["note"] = ("regenerated report folios land at the end of their "
                       "folder with new ids, or are moved to the book's top "
                       "level; pass keep_position=True, or "
                       "move_folio(folder_id=..., position=...), to restore "
                       "the tree order before a PDF export")
    return out


# --------------------------------------------------------------------------
# Wires and equipotentials, read side


def _wire_row(w: Any) -> dict:
    return {
        "id": _u(w.getID()),
        "mark": _text(w, "getTag"),
        "mark_root": _text(w, "getTagRoot"),
        "mark_number": _u(w.getTagNumber()),
        "equipotential": _text(w, "getEquipotential"),
        "signal": _text(w, "getSignal"),
        "colour": _text(w, "getColorCode"),
        "section_or_gauge": _text(w, "getSectionOrGauge"),
        "diameter": _u(w.getDiameter()),
        "length": _u(w.getLength()),
        "cable_id": _u(w.getCableID()),
        "cable_core": _text(w, "getCableCoreDescription"),
        "from": _text(w, "getOrigin"),
        "from_component_id": _u(w.getOriginComponentID()),
        "from_terminal": _text(w, "getOriginComponentTerminalNumber"),
        "to": _text(w, "getDestination"),
        "to_component_id": _u(w.getDestinationComponentID()),
        "to_terminal": _text(w, "getDestinationComponentTerminalNumber"),
    }


def list_wires(app: Any, client: Any, mark_contains: str | None = None,
               equipotential_contains: str | None = None,
               component_id: int | None = None,
               cable_id: int | None = None,
               limit: int = 500) -> dict:
    """Every wire in the project as a from-to row.

    A wire here is the logical conductor the project reasons about rather
    than a line drawn on a page, and each row carries both ends - component
    and terminal - which is what makes this a wiring list somebody can
    actually build from rather than a list of labels.

    ``component_id`` narrows it to the wires landing on one device, which is
    the usual question: what is connected to this thing. ``cable_id`` narrows
    it to the cores of one cable; ``mark_contains`` only searches the wire's
    own mark, never the cable core label printed next to it.
    """
    proj = _project(app)
    mgr = _need(_u(proj.getEwProjectWireManager()),
                "getEwProjectWireManager")
    needle = (mark_contains or "").lower()
    eq_needle = (equipotential_contains or "").lower()
    rows, scanned = [], 0
    for w in _each(client, _u(mgr.getEwProjectWireArray())):
        scanned += 1
        # Test the filters on the one or two getters they need before
        # building the row. A row is eighteen COM round trips, and a
        # filtered call used to pay all of them for every wire in the
        # project to keep a handful: 763 wires, one device, fourteen
        # thousand calls. The filters are the cheap part; do them first.
        if component_id is not None and component_id not in (
                _u(w.getOriginComponentID()),
                _u(w.getDestinationComponentID())):
            continue
        if cable_id is not None and _u(w.getCableID()) != int(cable_id):
            continue
        if needle and needle not in str(_text(w, "getTag") or "").lower():
            continue
        if eq_needle and eq_needle not in str(
                _text(w, "getEquipotential") or "").lower():
            continue
        rows.append(_wire_row(w))
        if len(rows) >= limit:
            break
    return {"count": len(rows), "scanned": scanned, "limit": limit,
            "wires": rows}


def find_wire(app: Any, client: Any, wire_id: int) -> dict:
    """One wire in full, by id."""
    proj = _project(app)
    mgr = _need(_u(proj.getEwProjectWireManager()),
                "getEwProjectWireManager")
    w = _u(mgr.findEwProjectWireByID(int(wire_id)))
    if w is None:
        return {"ok": False, "error": f"no wire with id {wire_id}"}
    return {"ok": True, "wire": _wire_row(w)}


_WIRE_SETTERS = (
    ("mark", "setTag", "str"),
    ("signal", "setSignal", "str"),
    ("colour", "setColorCode", "str"),
    ("section_or_gauge", "setSectionOrGauge", "str"),
    ("diameter", "setDiameter", "float"),
    ("length", "setLength", "float"),
    ("fixed_length", "setIsFixedLength", "bool"),
)


def update_wire(app: Any, client: Any, wire_id: int,
                mark: str | None = None, signal: str | None = None,
                colour: str | None = None,
                section_or_gauge: str | None = None,
                diameter: float | None = None,
                length: float | None = None,
                fixed_length: bool | None = None) -> dict:
    """Change one wire's own properties; anything left null is untouched.

    Setting ``mark`` by hand makes it a manual number, which a later
    ``number_wires`` pass leaves alone unless it is told otherwise - that is
    the point of typing one.
    """
    proj = _project(app)
    mgr = _need(_u(proj.getEwProjectWireManager()),
                "getEwProjectWireManager")
    w = _u(mgr.findEwProjectWireByID(int(wire_id)))
    if w is None:
        return {"ok": False, "error": f"no wire with id {wire_id}"}
    values = {"mark": mark, "signal": signal, "colour": colour,
              "section_or_gauge": section_or_gauge, "diameter": diameter,
              "length": length, "fixed_length": fixed_length}
    steps, step = _steps()
    for field, setter, kind in _WIRE_SETTERS:
        v = values.get(field)
        if v is None:
            continue
        arg = float(v) if kind == "float" else (
            bool(v) if kind == "bool" else str(v))
        step(field, getattr(w, setter)(arg))
    if not steps:
        return {"ok": True, "changed": [], "wire": _wire_row(w),
                "note": "nothing to change"}
    step("update", w.update())
    return {"ok": all(st["rc"] in (0, None) for st in steps),
            "changed": [st["step"] for st in steps if st["step"] != "update"],
            "wire": _wire_row(w), "steps": steps}
