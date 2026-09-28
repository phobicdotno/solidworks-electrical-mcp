# solidworks-electrical-mcp

MCP server for **SOLIDWORKS Electrical**, mastered against the
SOLIDWORKS Electrical API help: **2025** and **2026** ship side-by-side and
the server picks the right one at runtime.

The server attaches to a local SOLIDWORKS Electrical install via COM
(`EwAPI.EwInteropFactoryX`, late-bound through `pywin32`) and exposes the
full API surface (141 interfaces in 2025, 146 in 2026) through **101
task-level tools** plus a generic bridge for anything they do not cover.

A session can pick its own project, author the parts and symbols a job needs,
draw and annotate pages, run the project-wide numbering and export passes,
and take a restore point before doing so. None of that needed a human in the
GUI first.

## Status

Alpha. Tested on Windows 11 against SOLIDWORKS Electrical 2025 SP5; the
catalogue ships for both 2025 and 2026 and is selected automatically based
on the installed factory. Requires SOLIDWORKS Electrical to be installed
locally.

Every documented API behaviour here was measured against the running
application rather than read out of the published help, which is wrong often
enough that the distinction matters: two getters it lists do not exist, two
filter objects it documents do not filter, and one exporter reports success
while writing nothing. [CHANGELOG.md](CHANGELOG.md) records each of those
with what was seen.

## Task-level tools (start here)

These do the things a user actually asks for, with no COM knowledge needed.
Each one is a validated recipe over the generic bridge below. SOLIDWORKS
Electrical must be running; `list_projects` and `open_project` work with
nothing open, and everything else acts on whichever project is open.

### Project lifecycle

Start here: these pick which project the rest of the tools act on.

| Tool | Purpose |
|---|---|
| `list_projects(name_contains?, project_type?)` | Every project in the environment, open or not, newest change first. Works with nothing open. |
| `open_project(project_id? / name?)` | Open one and make it current. A name resolves exactly first, then by unique substring, so `"65021"` is enough. Refuses a project another user holds open. |
| `close_project(project_id? / name?)` | Close it; with no argument, whichever is current. Closing is how SOLIDWORKS commits pending state. |
| `create_project(name, template?, description?, customer?, contract_number?, open_after?)` | New project, optionally from a shipped template. |
| `list_project_templates()` | The template names `create_project` accepts. |
| `delete_project(project_id, confirm_name)` | Permanent. The name has to be typed back. |
| `archive_project(output_path, ...)` / `unarchive_project(archive_path, ...)` | `.tewzip` in and out, with or without the library content it references. |
| `project_properties(...)` | Read or set the title-block fields (customer, drawing office, contract number, addresses). |

`IEwProjectX.update` commits nothing on a **closed** project: it returns
`EW_PROJECT_NOTOPENED` and drops the edit without raising. `create_project`
therefore writes the description and customer in a second pass with the new
project open, and `project_properties` refuses a write to a project that is
not open rather than reporting one that did not happen.

### The environment library

| Tool | Purpose |
|---|---|
| `search_manufacturer_parts(manufacturer?, reference?, description_contains?, library_code?, part_type?, limit?, refresh?)` | The part catalogue. Every filter a substring; `part_type` exact. |
| `get_manufacturer_part(manufacturer, reference)` | One part in full, including its circuits and their terminals. |
| `create_manufacturer_part(...)` / `update_manufacturer_part(...)` / `delete_manufacturer_part(manufacturer, reference, confirm_reference)` | Author a part with its circuits, edit its fields, retire it. |
| `search_symbols(name?, symbol_type?, manufacturer?, reference?, library_code?, limit?, refresh?)` | The symbol library. |
| `get_symbol(name)` / `import_symbol(name, drawing_path, symbol_type, ...)` / `delete_symbol(name, confirm_name)` | Read a symbol, create one from a DWG or DXF, remove one. |
| `list_libraries()` | The library codes parts and symbols are filed under. |

Both catalogues are swept and filtered in the server, because the API cannot
do it: `IEwManufacturerPartFiltersX` has no setters at all, and
`IEwSymbolFiltersX` has setters that do nothing - `setManufacturer("Wago")`
then `getEwSymbolArray` returns `(0, ())` while a plain sweep finds four Wago
symbols. A sweep costs about 28 seconds for 1721 symbols and 8 for 479 parts,
so the index is cached per process, invalidated when the manager's count
moves or a write goes through, and rebuildable with `refresh`.

`import_symbol` takes DXF as happily as DWG. Attributes live in the drawing
and cannot be added through the API afterwards, so a symbol that should print
its component mark needs a `#TAG` ATTDEF in the file before import. `#TAG` is
the attribute SOLIDWORKS resolves to the mark; `#MARK`, `#COMPONENT_TAG`,
`#TAGREF` and the other obvious guesses render as their own literal text.

### Project-wide operations

Each of these rewrites a whole project at once and has no undo, so each takes
an explicit action and an explicit selection (`all`, a book, a folder, or a
list of folios). Take a snapshot first.

| Tool | Purpose |
|---|---|
| `number_wires(action, selection, ...)` | `new` numbers only wires that have none; `new_and_recalculate`, `renumber` and `remove` do what they say. `renumber_manual` is off by default, because a mark somebody typed is a decision. |
| `number_marks(action, object_types?, start_number?, step_increment?)` | Component-mark numbering: `update` or `renumber`, over components, cables, terminals, strips, locations, functions or harnesses. |
| `generate_arrows(action, selection, ...)` | Origin/destination arrows: `auto_connect`, `reconnect`, `remove`. |
| `optimize_wire_order(selection, ...)` | Recompute the connection order inside each equipotential. |
| `generate_terminal_strip_drawings(component_ids?, book_id?, keep_existing?)` | Draw the terminal-strip sheets. |
| `export_dwg(output_dir, pages? / all_pages?, save_type?, dwg_version?, single_file?)` | DWG/DXF out. |
| `list_reports()` / `export_reports(...)` / `generate_report_drawings(...)` | The project's saved report queries, to a file or into the document tree. |
| `list_wires(...)` / `find_wire(id)` / `update_wire(id, ...)` | The wiring list: both ends of every wire, component and terminal. |

A file-per-page DWG export is rejected without a naming formula, and the
formula takes a **bare** variable name: `FILE_TAG` works, `%FILE_TAG%` and no
formula are both `EW_BAD_INPUTS`. One is supplied by default, and the files
land in a subfolder tree under the export directory.

The report file writers are known not to deliver on 2025 SP5: every format
returns `EW_NO_ERROR` and writes nothing, with the target folder, report ids
and extension all set and reading back correct. Both exporters are therefore
judged by what landed on disk, so a run that wrote nothing says so instead of
reporting success.

### The project's own data

| Tool | Purpose |
|---|---|
| `list_snapshots()` / `create_snapshot(name, ...)` / `restore_snapshot(id, confirm_name)` / `delete_snapshot(id, confirm_name)` | Project versions. Real restore points, and the only undo this server has. |
| `list_io(...)` / `update_io(id, ...)` | The PLC I/O channels: mnemonic, key code, channel address, and the component circuit each sits on. |
| `list_functions()` / `add_function(tag, description?)` / `delete_function(id, confirm_tag)` | Functional groups (the `=` part of a tag path). |
| `update_cable(id, ...)` / `delete_cable(id, confirm_tag)` | The cable write side; `list_cables` is the read. |

Snapshots are squeezed between two rules that contradict each other. `create`
and `restore` return `EW_PROJECT_OPENED` while the project is open, but
`getEwProjectSnapshotManager` returns NULL while it is closed - so the obvious
reading, close it and then do the work, dies on a NULL manager. The one order
that works is to take the manager and build the snapshot object while the
project is open, close, `create`, and reopen; those pointers survive the
close. The reopen lives in the context manager's exit so it runs even when
the block raises, because every other tool acts on "the open project" and
leaving it closed would strand the session rather than merely fail.

Closing is cheap and reopening is not. `closeEwProjectID` on the 181-folio
SeaLeopard project returns in 0.1 s. Opening it again after that is the
expensive half: `create_snapshot` on that project produced its 12 KB
snapshot about 12 minutes in and the reopen had still not returned an hour
later, while the whole cycle on a 4-folio template project takes 1.6 s. So a
snapshot is usable on a small project and a background job on a large one,
and a call that looks hung is sitting in the reopen with the snapshot
already taken - `list_snapshots` will show it. `delete_snapshot` needs no
close and is instant either way.

### Pages and devices

| Tool | Purpose |
|---|---|
| `project_info()` | Name, id, customer, folder path, object counts of the open project. |
| `list_folios(book_id?, folder_id?, file_type?, description_contains?)` | Pages in tree order: id, page mark, description, type, book/folder/location. |
| `find_folio(page? / file_id?)` | One page by printed mark (`"61"`) or file id. |
| `list_locations()` | Locations with tag, tag path, description. |
| `list_books_and_folders()` | The document tree containers. |
| `list_components(tag_contains?, location_id?, parent_id?, with_parts?, limit?)` | Devices with tag path, description, parent, location and manufacturer parts. |
| `find_component(tag)` | Exact match on tag (`"A1"`) or full tag path (`"=F1+L1+L4+L2-A1"`). |
| `list_cables(limit?)` | Cables with reference, manufacturer, cores, length, end locations. |
| `folio_symbols(page? / file_id?)` | What is drawn on a page: symbol name, type, linked component, position. |
| `export_folio_pdf(output_path, pages? / file_ids? / all_pages?)` | Export pages to one PDF (folder auto-created). Read the PDF to see the drawing. |
| `regenerate_title_blocks()` | Refresh title blocks from project data (fails 45 while drawings are open). |
| `rename_project(new_name)` | Rename the project (drives the cover title). |
| `close_and_reopen_folio(page? / file_id?)` | Force a page to redraw. |
| `clone_component(source_tag, new_tag, page?, offset_x?, offset_y?, dry_run=True)` | "On sheet 10, clone K32 into K33": new component with the same description, location, class and manufacturer parts, plus the source's symbols on that page copied into the next free slot. Dry run by default. |
| `add_component(tag, manufacturer, reference, page?, after_tag?, shift_following?, x?, y?, dry_run=True)` | Build a unit with no source to copy. The symbol comes from the manufacturer part itself; scale and rotation from a neighbour, because a cabinet footprint is drawn scaled to real millimetres. `shift_following` inserts into a rail, pushing everything right of `after_tag` along by one device pitch. |
| `rename_component(tag, new_tag, scan_text?, dry_run=True)` | Retag a device. Symbols, cross-references and the BOM are linked by id and follow it; the mark spelled out as literal text does not, so every such place is listed for a human to judge. |
| `delete_component(component_id? / tag?, pages?, close_gap?)` | Remove a component and its symbols (the undo for a clone or an add). `close_gap` pulls the rail back over the hole. |
| `rename_component(tag, new_tag, scan_text?, dry_run=True)` | Retag a device; reports what follows the rename and what does not. |
| `renumber_components(renames, dry_run=True)` | Retag a whole run in one pass, collision-safe, with temp marks when the old and new sets overlap. |
| `audit_tag_roots(tag_contains?, fix?)` | Find and repair components whose stored tag root/number disagree with their mark. |
| `add_folder` / `rename_folder` / `delete_folder` | The document tree. |
| `add_folio(description, file_type, folder_id?, insert_before_page?, dry_run=True)` / `delete_folio` | Pages; `insert_before_page` cascades the following page numbers. |
| `add_location(tag, description, dry_run=True)` | A location. |
| `attach_manufacturer_part(tag, manufacturer, reference, ...)` | Give a component a part the library does not carry. |
| `place_symbol(tag, symbol_name, x, y, page, ...)` / `remove_symbol(symbol_id)` | Draw an existing component on a page, or undraw it. |
| `move_symbol` / `move_symbols(moves)` | Move a symbol WITH the wire ends drawn to it. Prefer the batch: moving one at a time walks every line in the project per symbol. |
| `add_text` / `list_texts` / `remove_text` | Free text on a page. Content is set before insert, unlike every other object. |
| `check_drawing_rules(page, min_spacing?, box?)` | Crowded or coincident connection points, and anything outside the drawable box. |
| `check_page_ink(page, ...)` | What a sheet actually DRAWS, measured off an export, which catches a footprint hanging outside the box that `check_drawing_rules` cannot see. |
| `place_symbols` / `remove_symbols` / `add_texts` / `remove_texts` | Batch forms: one folio close for the whole page. Strongly preferred over the singular ones. |
| `reconnect()` | Drop cached COM state and attach again (after SOLIDWORKS restarted). |

Two failure modes are handled automatically: a COM factory dispatched while
SOLIDWORKS Electrical was not yet running answers NULL forever (it used to
surface as a bogus `EW_INVALID_LICENSE`), and an application pointer cached
before the program was closed is dead. Both are detected and re-attached.

## How it works

The Doxygen-generated SW Electrical help is the **master** for what is
callable. A scraper walks `sldworkselecapihelp/annotated.html` and every
`interface_*.html` page and writes a JSON catalog per major release into
the package. At runtime the MCP server loads every shipped catalog and
exposes:

| Tool | Purpose |
|---|---|
| `list_versions()` | Shipped catalogs, installed versions, and the active default. |
| `list_interfaces(version?)` | All interface names in the chosen catalog. |
| `search_api(query, limit, version?)` | Ranked search across interfaces + members. |
| `get_api(interface, member?, version?)` | Pull one interface or one member. |
| `compare_versions(interface, member?)` | Cross-version diff for one interface/member. |
| `connect(license_key?)` | Dispatch the SW Electrical COM factory and attach an application. |
| `call(path, args?, root?)` | Late-bound dotted attribute access on `application` / `api` / `factory`. |

Catalogs ship in the repo as
`src/solidworks_electrical_mcp/data/api_catalog_<version>.json`. Rebuild
one with `python -m solidworks_electrical_mcp.scrape --version 2026`.

### Version selection

* If a tool gets an explicit `version=`, that wins.
* Otherwise the server probes `HKLM\SOFTWARE\Classes` for
  `EwAPI.EwInteropFactoryX.<year>.<sp>` keys and picks the highest installed
  major that also has a shipped catalog. (Probe runs once at startup, no
  licence required.)
* If neither produces a match, the newest shipped catalog is used.

A complete 2025→2026 changelog is in
[`docs/version-diff-2025-2026.md`](docs/version-diff-2025-2026.md): 5 new
interfaces, 29 new members, 2 removed members, 2 signature tweaks, plus a
`setClassID` deprecation note.

## COM entry point and licence

The Win32 COM ProgID is **`EwAPI.EwInteropFactoryX`** (interface
`IEwInteropFactoryX`). The factory hands out:

* `getEwApplication(licenseKey, errorCode)` → `IEwApplicationX`, gated behind
  a licence *code* that is separate from the SOLIDWORKS program/seat licence.
  SOLIDWORKS ships a single shared key embedded identically in its own add-in
  binaries (e.g. `ewexceladdin.dll`, `ewenvironmentarchiver.exe`), it is the
  same on every install, not a per-customer secret, so a known-good default
  is **bundled** (`DEFAULT_LICENCE_KEY` in `com.py`) and the application root
  works out of the box. Override with `SWELE_LICENCE_KEY` in the env (read by
  `connect`) or `license_key=` on the `connect` tool if a future release
  rotates the key.
* `getEwAPI(errorCode)` → `IEwAPIX`, no licence required, gives access to
  application-discovery and version helpers.

All three roots are reachable from the `call` tool via `root="application"`
(default) / `root="api"` / `root="factory"`. The application root additionally
needs SOLIDWORKS Electrical to be **running**.

## Install

```bash
git clone https://github.com/phobicdotno/solidworks-electrical-mcp.git
cd solidworks-electrical-mcp
python -m venv .venv && .venv\Scripts\activate
pip install -e .
# Optional: rebuild a catalog from the live SW docs after a yearly release
# python -m solidworks_electrical_mcp.scrape --version 2026
# python -m solidworks_electrical_mcp.scrape --version 2027
```

## Wire up to an MCP client

Add the server to your MCP client's configuration (the `mcpServers` block in
its settings):

```jsonc
{
  "mcpServers": {
    "solidworks-electrical": {
      "type": "stdio",
      "command": "C:\\path\\to\\repo\\.venv\\Scripts\\python.exe",
      "args": ["-m", "solidworks_electrical_mcp"]
    }
  }
}
```

No `env` block is needed: the shared licence code is bundled, so
`call(..., root="application")` works once SOLIDWORKS Electrical is running.
Set `SWELE_LICENCE_KEY` only to override the bundled key (e.g. if a future
release rotates it).

## Running the tests

```
.venv/Scripts/python.exe tests/run_all.py          # everything that runs offline
.venv/Scripts/python.exe tests/run_all.py --live   # plus the two that drive the app
```

Do not use plain `pytest` here. The tests are standalone scripts with no test
functions, so pytest collects nothing and exits **green** with "no tests ran".

`run_all.py` also refuses a second false all-clear: most of these scripts skip
at runtime when SOLIDWORKS Electrical is not attached, and they exit 0 when
they do, so the exit code reports a pass for a test that checked nothing. The
runner reads the SKIP marker out of the output and reports it as a skip.

Most of the suite runs with no application at all. The interesting ones are
the ones that check a rule a live test could not check without doing the
damage it guards against:

* `test_project_lifecycle` - a project open by another user is refused, an
  ambiguous name is refused rather than guessed, `delete_project` needs its
  name typed back, and the description is committed with the project open.
* `test_library` - the catalogue cache is invalidated by a count change, a
  write, or `refresh`; a part or symbol is never silently overwritten; a
  failing terminal fails the part rather than shipping an unlabelled one.
* `test_automation` - every action reaches `process` with its own enum.
  `number_wires("new")` passes 0 and `renumber` passes 2, and the second
  rewrites every wire label in the project; a selection that is incomplete
  is refused rather than widened to everything; an exporter that reports
  success and writes nothing is not a success.
* `test_project_data` - `restore_snapshot` needs the snapshot's name back,
  a snapshot is taken with the project closed and the project is open again
  afterwards even when the attempt fails.
* `test_tool_surface` - every tool reaches its workflow, in every workflow
  module, and forwards every parameter it declares.
* `test_renumber`, `test_batch_symbol_ops`, `test_drawing_rules`,
  `test_page_ink`, `test_tag_marks`, `test_stale_factory_retry`,
  `test_stdout_isolation` - bulk retag collisions, the folio close/open
  batching, spacing and the drawable box, measuring what a sheet really
  draws, tag roots and namespaces, cached COM objects outliving the program,
  and keeping native logging off the JSON-RPC wire.

Only the two `--live` tests need the program running with a project open.

## Editing a page: batch, do not loop

`place_symbol`, `remove_symbol`, `add_text` and `remove_text` each close and
reopen the folio around every single call, because a symbol written into a
folio the GUI has open is discarded when the editor saves its copy back.
Calling them in a loop churns the editor once per unit and can take
SOLIDWORKS Electrical down - a twelve-device cabinet page did exactly that.

Use the batch forms, which close once and reopen once for the whole page:
`place_symbols`, `remove_symbols`, `add_texts`, `remove_texts`, `move_symbols`.
Open and close the folio per TASK, not per unit.

## Why local stdio (not remote HTTP)

SOLIDWORKS Electrical is a Windows desktop application accessed through COM,
the MCP server has to run on the same machine as the SW process. Local
stdio is the right shape; an MCPB bundle is a future packaging option (see
[ROADMAP.md](ROADMAP.md)).

## License

MIT, see [LICENSE](LICENSE).
