# Changelog

All notable changes to `solidworks-electrical-mcp` are recorded here.
Format follows [Keep a Changelog](https://keepachangelog.com/).

Every entry that records an API behaviour was measured against a running
SOLIDWORKS Electrical 2025 SP5, not read out of the published help. The help
is wrong often enough that this distinction is the point.

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
- Closing the 181-folio SeaLeopard project takes 0.1 s. Reopening it is the
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
