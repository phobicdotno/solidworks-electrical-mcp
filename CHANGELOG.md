# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The project had no release tags and the package version stayed at `0.1.0`
until 0.5.1. Versions 0.1.0 to 0.5.0 below are reconstructed from the git
history and grouped by when the work landed.

## [Unreleased]

## [0.5.1] - 2026-09-23

### Added

- Added CHANGELOG.md.

## [0.5.0] - 2026-09-22

### Added

- Task-level tools built on validated COM recipes: `project_info`,
  `list_folios`, `find_folio`, `list_locations`, `list_books_and_folders`,
  `list_components`, `find_component`, `list_cables`, `folio_symbols`,
  `export_folio_pdf`, `regenerate_title_blocks`, `rename_project`,
  `close_and_reopen_folio` and `reconnect`.
- `clone_component` and `delete_component`. Clone copies a component's
  tag-independent data and symbols to the next free slot on a rail. It runs
  as a dry run by default.
- `add_component` builds a new unit from a manufacturer part, taking the
  symbol from the part and the scale and rotation from existing symbols on
  the page. `shift_following` inserts a device into a rail. `allow_new_root`
  starts a deliberate new tag series.
- `delete_component` gains `close_gap` to pull a rail back after a shifted
  insert.
- `rename_component` reports linked references (which follow the new mark)
  separately from literal text references (which do not).
- `renumber_components` retags a run of components in one pass. It parks
  overlapping marks on temporary values first.
- `audit_tag_roots` finds and repairs drift between a component's mark and
  its TagRoot.
- Document tree and page tools: `add_folder`, `rename_folder`,
  `delete_folder`, `add_folio` and `delete_folio`.
- `add_location`, `delete_location` and `attach_manufacturer_part`. The last
  one attaches parts that are not in the library catalogue.
- `move_symbol` and `move_symbols` (batch). Both drag attached wire ends with
  the symbol and refuse moves that leave the drawable box.
- `check_drawing_rules` for the 10 mm connection grid and label spacing.
- `place_symbol` and `remove_symbol` to draw and undraw an existing
  component.
- `add_text`, `list_texts` and `remove_text` for free text on a page.
- `tests/test_workflow_tools.py` and the `tests/test_drawing_tools.py`
  round-trip test, which checks that the project returns to its baseline
  counts.

### Changed

- Line and text lookups ask SOLIDWORKS for a single folio's objects instead
  of scanning the whole project. A full scan remains as the fallback.
- Setting a mark now writes the mark, root and number together, so the
  TagRoot stays with the mark.
- A bare numeric tag splits to an empty root.
- `list_folios` sorts book-major, so books no longer interleave.
- `list_cables` reports `total_in_project` and `truncated`.
- Manufacturer-part ownership is looked up once per requested component
  instead of once per part.

### Fixed

- A stale COM factory is dropped and the application attach is retried
  once. Before this, starting the server before SOLIDWORKS caused misleading
  licence errors. A dead application pointer is also detected and replaced.
- Symbols are no longer written to a folio that is open in the GUI, because
  the editor could overwrite them. Clone, delete, place and remove close the
  folio first and reopen it afterwards.
- Manufacturer-part and symbol ownership is checked through the owner
  accessor, not the object id alone. Object ids can collide across tables.
- `place_symbol` validates a placed symbol's real connection points, not just
  its origin.
- Several tools reported success without checking the result: `move_symbols`
  wire ends and duplicate entries, `add_location` uniqueness, the page that
  `add_folio` settled on, `renumber_components` temp marks, orphaned wire
  ends after `remove_symbol`, `delete_folio` on text-only pages,
  `attach_manufacturer_part` deduplication, and coincident points in
  `check_drawing_rules`.
- `clone_component` raised after creating the component, which left orphans
  (a regression from the gap-close work).
- `find_folio` prefers an exact page mark and refuses an ambiguous one.
- Lowercase tag roots are normalised, and ambiguous tags are no longer
  treated as absent.

## [0.4.0] - 2026-06-19

### Added

- `shift_folio_numbers`, a collision-safe cascade of folio page numbers for
  inserting or closing gaps. It supports `dry_run`.
- Path segments accept inline args, such as `findEwProjectSymbolByID(42)`,
  for fetching by id or index while navigating.
- Typed VARIANT args through the `{"$variant": ..., "value": ...}` marker,
  for methods that need a specifically typed SAFEARRAY.
- `call_ops` and `array_ops` accept chained (dotted) member paths.
- `docs/live-verification-2026-06.md` records the June 2026 live
  verification against SW Electrical 2025.5.

### Changed

- `call_ops` and `array_ops` isolate a failing op in an `{"error": ...}`
  cell instead of aborting the whole sequence.

### Fixed

- `call` auto-invokes a zero-arg getter leaf instead of returning the bound
  method.

## [0.3.0] - 2026-06-15

### Added

- `typelib_members` for introspecting interfaces from the type library.
- `array_ops` supports `array_args` for parameterized array sources, such as
  folio symbols.
- Server instructions now cover the revision API limits, overlap checks for
  symbol moves, recipes for creating objects (location, folio, component,
  cable), schematic-drawing limits and the project-flip hazard, the
  `FromSymbolType` + `setObjectID` drawing recipe, a caveat against
  bulk-placing symbols at arbitrary coordinates, and symbol scaling with
  self-verification through PDF export.

### Removed

- The stray `page03.py` probe script.

## [0.2.0] - 2026-06-12

### Added

- The shared SW Electrical API licence key is bundled as the default.
  `license_key=` and `$SWELE_LICENCE_KEY` still override it.
- `call_ops` runs stateful sequences on a single retained COM object.
- `get_enum` reads type library enums, and `array_ops` gives access to
  collections.

### Changed

- All COM access runs on a dedicated STA thread, so application-root calls
  work.
- Client references in docs and comments are vendor-agnostic.
- `EwErrorCode` is decoded in licence-failure messages.

### Fixed

- Native stdout output from the COM DLLs no longer corrupts the stdio MCP
  transport. fd 1 is redirected away from the JSON-RPC stream.

## [0.1.0] - 2026-04-26

### Added

- First version of the stdio MCP server (FastMCP + pywin32) for SOLIDWORKS
  Electrical, with the tools `list_interfaces`, `search_api`, `get_api`,
  `connect` and `call`.
- A Doxygen scraper that mirrors the SOLIDWORKS Electrical API help into a
  JSON catalog.
- The 2025 and 2026 API catalogs ship in the repo. The server picks the
  highest installed version by default.
- `list_versions` and `compare_versions`, plus
  `docs/version-diff-2025-2026.md`.
- ROADMAP.md, which defers the interactive widget work.

### Changed

- The FastMCP startup banner is suppressed, and `compare_versions` returns
  hints when an interface or member is missing.

### Fixed

- The COM entry point is `EwAPI.EwInteropFactoryX`, not `EApp.Application`.
- The scraper handles Doxygen help pages wrapped in Next.js.

[Unreleased]: https://github.com/phobicdotno/solidworks-electrical-mcp/commits/main
[0.5.1]: https://github.com/phobicdotno/solidworks-electrical-mcp/compare/8431391...main
[0.5.0]: https://github.com/phobicdotno/solidworks-electrical-mcp/compare/bf976b9...8431391
[0.4.0]: https://github.com/phobicdotno/solidworks-electrical-mcp/compare/22e1dee...bf976b9
[0.3.0]: https://github.com/phobicdotno/solidworks-electrical-mcp/compare/bcf522d...22e1dee
[0.2.0]: https://github.com/phobicdotno/solidworks-electrical-mcp/compare/3bb0cf5...bcf522d
[0.1.0]: https://github.com/phobicdotno/solidworks-electrical-mcp/commits/3bb0cf5
