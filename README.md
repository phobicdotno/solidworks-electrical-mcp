# solidworks-electrical-mcp

MCP server for **SOLIDWORKS Electrical**, mastered against the
SOLIDWORKS Electrical API help — **2025** and **2026** ship side-by-side and
the server picks the right one at runtime.

The server attaches to a local SOLIDWORKS Electrical install via COM
(`EwAPI.EwInteropFactoryX`, late-bound through `pywin32`) and exposes the
full API surface (141 interfaces in 2025, 146 in 2026) to an MCP
client through a small set of discovery, comparison, and execution tools.

## Status

Alpha. Tested on Windows 11 against SOLIDWORKS Electrical 2025 SP5; the
catalogue ships for both 2025 and 2026 and is selected automatically based
on the installed factory. Requires SOLIDWORKS Electrical to be installed
locally.

## Task-level tools (start here)

These do the things a user actually asks for, with no COM knowledge needed.
Each one is a validated recipe over the generic bridge below, and each is
covered by `tests/test_workflow_tools.py` against a live project. SOLIDWORKS
Electrical must be running with a project open.

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

* `getEwApplication(licenseKey, errorCode)` → `IEwApplicationX` — gated behind
  a licence *code* that is separate from the SOLIDWORKS program/seat licence.
  SOLIDWORKS ships a single shared key embedded identically in its own add-in
  binaries (e.g. `ewexceladdin.dll`, `ewenvironmentarchiver.exe`) — it is the
  same on every install, not a per-customer secret — so a known-good default
  is **bundled** (`DEFAULT_LICENCE_KEY` in `com.py`) and the application root
  works out of the box. Override with `SWELE_LICENCE_KEY` in the env (read by
  `connect`) or `license_key=` on the `connect` tool if a future release
  rotates the key.
* `getEwAPI(errorCode)` → `IEwAPIX` — no licence required, gives access to
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

No `env` block is needed — the shared licence code is bundled, so
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

Seven tests genuinely run with no application: `test_tool_surface` (every
tool reaches its workflow and forwards what it declares),
`test_batch_symbol_ops` (the
folio close/open batching), `test_drawing_rules` (dot-to-dot spacing, the
label-width rule and the drawable box), `test_page_ink` (measuring what a
sheet really draws, against synthetic A3 sheets), `test_tag_marks` (tag root,
number and the namespace prefix), `test_stale_factory_retry` (cached COM
objects outliving the program) and `test_stdout_isolation`. Everything else
needs the program running with a project open, which the two `--live` tests
require outright.

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

SOLIDWORKS Electrical is a Windows desktop application accessed through COM —
the MCP server has to run on the same machine as the SW process. Local
stdio is the right shape; an MCPB bundle is a future packaging option (see
[ROADMAP.md](ROADMAP.md)).

## License

MIT — see [LICENSE](LICENSE).
