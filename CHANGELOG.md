# Changelog

All notable changes to `solidworks-electrical-mcp` are recorded here.
Format follows [Keep a Changelog](https://keepachangelog.com/).

Every entry that records an API behaviour was measured against a running
SOLIDWORKS Electrical 2025 SP5, not read out of the published help. The help
is wrong often enough that this distinction is the point.

## Unreleased

### Added

- `rename_cable(cable_id, new_tag, confirm_tag)`: a cable mark is rebuilt
  from root and number on `update()`, so both are set (`W107` to `C.W1`
  sets root `C.W` and number 1) and the mark is read back. Measured on a
  project of 117 cables and 715 cored wires: every wire drawn as a core
  prints the new mark without being touched, and no wire end moves. A mark
  another cable carries is refused, so a renumbering is done in an order
  that frees each target first.
- `list_wires(cable_id=...)`: the cores of one cable. `mark_contains` only
  ever searched the wire's own mark, never the cable core label next to it.
- `delete_cable(..., allow_wired=False)`: a cable with wires on its cores is
  refused and the wires are listed; the usual reason to delete a cable is a
  leftover nobody draws. The result now carries `wires_cut`.
- `archive_project(..., overwrite=False)`: measured, `archive()` answers
  `EW_BAD_INPUTS` and writes nothing when the target file exists. An
  existing file is now refused with that explanation, and with `overwrite`
  the archive is written under a temporary name and moved over the old one
  only once it exists.

- `list_lines(page)` and `edit_lines(ops, page)`: read, move, draw and
  remove wire segments on one page in one pass (`set` / `add` / `remove`
  ops, drawable-box check, dry run first, folio closed once). Moving a line
  end onto another connection point re-routes the conductor to that
  terminal. Measured: re-routing re-forms the wire objects, and the new
  wires lose their cable core association; no COM call can restore it
  (wire, line, cable and cable manager all lack a setter, and the generic
  `setProperty` names tried answer `EW_BAD_INPUTS`).
- Last-resort GUI tools `gui_screenshot`, `gui_click`, `gui_scroll`,
  `gui_keys`, `gui_type` (module `gui.py`) for the operations the API cannot
  do at all: associating cable cores, editing plain drawing texts and
  multiline texts, creating revisions. Every input first brings SOLIDWORKS
  Electrical or one of its own dialogs to the front and refuses when another
  application keeps the focus. Learned while building it: an ALT tap to win
  the foreground leaves the ribbon in keytip mode and swallows the next
  wheel or click (a SHIFT tap is used instead); pulling the main frame in
  front of one of its open dialogs moves the keyboard focus off the dialog
  and typed text is silently lost; `SendInput` with `KEYEVENTF_UNICODE`
  reached nothing in SOLIDWORKS dialogs, virtual-key presses do; on a
  drawing a negative wheel step zooms in. `gui_keys` also knows F1-F12,
  pageup, pagedown and the apps key.

### Measured: revisions

- No COM call creates or validates a revision. `IEwProjectFileX` only
  writes a revision's text (`setRevisionTranslatableTextAt(rev, 0, lang,
  text)`, measured: lands in the title block CHANGES field). Do not revise
  pages one at a time in the GUI: on the **book**, Revisions..., select the
  new revision, Validate, then Verify. SOLIDWORKS then asks whether to set
  all book documents to that revision; Yes moves every page (113 in one
  step), copying the book revision's description.
- `rebind_symbol(symbol_id, component_id)`: point a drawn symbol at another
  component. A page pasted in from another project draws its own copies of
  the devices on it, so the parts list counts them twice; rebinding the
  pasted symbol to the real component lets the copy be deleted. Closes the
  folio for the write when it is open in the editor, reads the binding back.

### Fixed

- `generate_report_drawings` returned while SOLIDWORKS was still rewriting
  the tree. It now waits for the folio list to settle (two consecutive
  listings alike, polled every 3 s, `settle_timeout` 120 s, reported under
  `settle`; `wait_for_settle=False` skips it with a warning) before it reads
  the tree, and `keep_position` restores the folder as well as the position
  of the report's folios, whether SOLIDWORKS moved them with their ids or
  replaced them with new ids (matched by page mark first, then by name).
  New folios that match no replaced one (the blanks, below) are left alone
  and listed under `unmatched_new`; a run whose tree did not settle is not
  repositioned at all.
- `edit_lines` on a line-diagram folio: `like_line_id` copied the other
  line's style into `setWireStyleID` on a schematic line, which made
  `newEwProjectLine` return NULL. The style id a line-diagram line reports
  is its synoptic style, so it now goes to `synoptic_style_id`; a bare
  `wire_style_id` on a line diagram is refused with a message that names
  `synoptic_style_id`.
- `edit_lines` reported a `set` as ok on the return codes alone. Every
  `set` is now compared with the read-back; an end that did not move is
  `applied: false` with a `not_applied` row (requested and read-back
  coordinates), and the result's `ok` is false. A line whose id is gone
  afterwards (merged, or re-formed under a new id, normal on a schematic)
  is `applied: "unknown"`, neither a success nor a failure.
- `move_symbol` / `move_symbols` dragged no wires when the page was closed
  in the editor: `getEwProjectLineArrayFromFileID` answers NULL rather than
  an error for a closed folio, and that was read as "no lines". It now falls
  back to the full line scan.
- `rename_component`, `renumber_components`, `clone_component` and
  `add_component` tested a new mark against every component in the
  project. A mark only has to be unique among siblings: SOLIDWORKS builds
  the tag path from function, location, parent and mark, and every PLC
  rack's first module is `N1`. Renaming a rack coupler to `N1` was refused
  as "already exists more than once", and a renumber could not give a
  module a mark a module of another rack holds. The checks now compare
  (parent, mark); top-level components share one group, so they stay as
  strict as before. `test_sibling_marks.py` cases A to D; against the old
  code A and C fail.
- `export_folio_pdf` reported `file_exists: false` and `ok: false` for every
  successful export. SOLIDWORKS does not write the name it is given: it
  puts the book number in front (`out.pdf` becomes `1-out.pdf`), one file
  per book, and returns `EW_NO_ERROR`. The export now finds that file and,
  with a single book, moves it to the requested path (`renamed_from` in the
  result); further book files are listed in `also_written`, in book order.
  A target held open by a viewer leaves the book file in place with a
  `warning` instead of failing an export that worked.
  `test_sibling_marks.py` case E.

- `number_wires`, `generate_arrows` and `optimize_wire_order` could not be
  scoped to a page from the tool side. The workflows derive the scope from
  what is passed and refuse a `selection` that contradicts it, but the tools
  defaulted `selection` to `"all"`, so every call with `pages` or `file_ids`
  came back "selection='all' with pages/file_ids given is ambiguous". The
  tools now
  default to `None` like the workflows. `test_tool_surface.py` gained check
  E: a tool may not default a parameter to a literal where its workflow
  derives that parameter from `None`. Against the old code it names all
  three tools.
- `move_symbols` reported ok whatever happened: every per-symbol return code
  was discarded and no position was read back. It now re-fetches each symbol
  after the folio closes, reads the position back and fails loudly with a
  per-symbol result list.

### Measured (SOLIDWORKS Electrical 2025 SP5)

- `doGenerateDrawings` returns before the work is done. Right after the
  call the folio list carried 48 blank folios named after the report (no
  filter suffix, an empty page, 3.7 KB when exported) at the end of the
  book's top level, while the report's 15 folios still had their ids and
  folder; about a minute later all but the last three blanks were gone and
  the report's folios had been moved to the book's top level (folder -1,
  after the cable list). Deleting the blanks inside that window worked,
  which is exactly the wrong thing to do (2026-10-20, 3-book project).
- A line end on a line-diagram folio (file type 1) does not move through
  the API: the four position setters and `update` answer `EW_NO_ERROR` and
  the line keeps its coordinates on read-back and in a later `list_lines`
  (line 117.5/290 - 95/290 asked to become 192.5/260 - 95/290). Remove and
  add instead. On a schematic folio the same setters work.
- A line-diagram line's `getWireStyleID` answers its synoptic style id
  (838); the same id as `synoptic_style_id` draws a line, as
  `wire_style_id` on a new schematic line it makes `newEwProjectLine`
  return NULL.
- A symbol displacement below about 0.2 mm is accepted and discarded: the
  setters and `update` return `EW_NO_ERROR` and the value reads back
  unchanged. Bisected on a layout footprint: 0.10, 0.15 and 0.20 dropped,
  0.24 and 0.25 applied, in both directions and with nothing adjacent, so it
  is a minimum-delta filter, not collision or snap. The filter applies to the
  net displacement and is enforced when the folio reopens, so
  `move_symbols` does a sub-threshold move as two hops that each clear it,
  and closing a hairline gap one symbol at a time cannot work.

## 0.2.0 - 2026-09-25

The server could only ever act on a project a human had already opened in the
GUI, and had no way to author a part, run a project-wide pass, or undo one.
This release closes all four gaps: 61 tools to 101, across five workflow
modules.

### Added

- **Project lifecycle** (`projects.py`): `list_projects`, `open_project`,
  `close_project`, `create_project`, `delete_project`,
  `list_project_templates`, `archive_project`, `unarchive_project`,
  `project_properties`. `list_projects` and `open_project` work with nothing
  open, so a session can pick its own project.
- **Environment library** (`library.py`): `search_manufacturer_parts`,
  `get_manufacturer_part`, `create_manufacturer_part`,
  `update_manufacturer_part`, `delete_manufacturer_part`, `search_symbols`,
  `get_symbol`, `import_symbol`, `delete_symbol`, `list_libraries`. Authoring
  a device used to mean a hand-written COM script per part.
- **Project-wide operations** (`automation.py`): `number_wires`,
  `number_marks`, `generate_arrows`, `optimize_wire_order`,
  `generate_terminal_strip_drawings`, `export_dwg`, `list_reports`,
  `export_reports`, `generate_report_drawings`, and the wiring list
  (`list_wires`, `find_wire`, `update_wire`) which carries both ends of every
  wire, component and terminal.
- **Project data** (`projectdata.py`): project snapshots
  (`list_snapshots`, `create_snapshot`, `restore_snapshot`,
  `delete_snapshot`) - the first undo this server has had - plus PLC I/O
  (`list_io`, `update_io`), functions and the cable write side.
- **Settings** (`settings.py`): `project_config` (147 values, read from the
  live type library rather than a hard-coded list), `list_wire_styles`,
  `update_wire_style`, `list_title_blocks`, `search_cable_references`,
  `list_harnesses`.
- Five offline test files driving fakes, because the interesting behaviour is
  all in the guards and a live test cannot check "delete_project refuses the
  wrong name" or "renumber passes the right enum" without doing the damage.

### Fixed

- `number_wires(pages=["102"], action="renumber")` left the scope at "all".
  It set selection type 0, never called `setSelection`, renumbered every wire
  in the project, and returned a result listing page 102 - a project-wide
  rewrite that read back as one page. `generate_arrows` and
  `optimize_wire_order` had it too. The scope is now derived from what was
  asked for, and a scope that contradicts the arguments given is refused.
- Both library delete guards compared `confirm_*` against the caller's own
  other argument, which can only catch typing the same string differently
  twice. They compare against what the lookup resolved, and name it.
- `create_manufacturer_part(replace=True)` and `import_symbol(replace=True)`
  removed the existing entry and, on a failed insert, left nothing behind
  while the cached index went on listing it. They now stop if the remove
  fails, drop the cache on every exit, and say when the old one is gone.
- Ten functions dereferenced a manager that reads NULL whenever its project
  is not open, handing back `'NoneType' object has no attribute 'getCount'`.
- `test_tool_surface.py` was silently vacuous for `find_folio`: it stopped at
  the first module attribute in the body and resolved a private helper. It
  resolves the real call target in both wrapper shapes; three injected drifts
  confirm it catches them.

### Measured (SOLIDWORKS Electrical 2025 SP5)

- `IEwProjectX.update` commits nothing on a **closed** project. It returns
  `EW_PROJECT_NOTOPENED` and drops the edit without raising, so a project made
  from a template keeps the template's description unless the fields are
  written again with it open.
- Snapshots contradict themselves: `create`/`restore` return
  `EW_PROJECT_OPENED` while the project is open, but
  `getEwProjectSnapshotManager` returns NULL while it is closed. The one
  workable order is to take the manager and build the snapshot while open,
  close, `create`, reopen. Those pointers survive the close.
- Neither library filter object filters. `IEwManufacturerPartFiltersX` has no
  setters at all; `IEwSymbolFiltersX` has setters that do nothing
  (`setManufacturer("Wago")` then `getEwSymbolArray` gives `(0, ())` while a
  sweep finds four). Both catalogues are swept here and cached: about 28 s
  for 1721 symbols, 8 s for 479 parts.
- A file-per-page DWG export needs a naming formula written **without**
  percent signs. `FILE_TAG` works; `%FILE_TAG%` and no formula are both
  `EW_BAD_INPUTS`. Output lands in `<dir>/<project>/<book>/<page>.dwg`.
- SOLIDWORKS does not rewrite a DWG that is already there. A second export to
  the same folder leaves the file untouched, so "no new files" is ambiguous
  between success and failure; both exporters now tell the three outcomes
  apart.
- The report file writers return `EW_NO_ERROR` and write no file, in every
  format, with every setting reading back correct;
  `setAddCreatedFileToProject(True)` turns the silence into
  `EW_OBJECT_NOT_FOUND`. Use `generate_report_drawings` or the GUI.
- Closing the 181-folio production project takes 0.1 s. Reopening it is the
  expensive half: `create_snapshot` wrote its 12 KB snapshot about 12 minutes
  in and the reopen had still not returned an hour later. The same cycle on a
  4-folio template project takes 1.6 s.
- The published help is not the method list. A wire style's colour is
  `getWireColorName`, not `getColorCode`; a cable reference's core count is
  `getCableCoreCount`, not `getCoreCount`. Both wrong names returned cleanly
  and gave nothing. Ask `typelib_members`, or `dir()` on the COM thread.

## 0.1.0

Initial server: API catalogue discovery (`search_api`, `get_api`,
`compare_versions`), the generic COM bridge (`call`, `call_ops`,
`array_ops`), and the first task-level tools over folios, components,
symbols, texts and the drawing rules.
