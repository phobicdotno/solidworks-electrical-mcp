# Staging ledger

New behaviour learned while using the server (an API rule, a fix, a new tool)
lands on the `staging` branch first, never on `main`. The machine that drives
SOLIDWORKS Electrical runs the server from a checkout of `staging`, so ordinary
work is the test.

## Rules

- **Stage:** commit the change to `staging` with its tests, add a row below
  (status `staged`, 0 confirmations), push `staging`.
- **Confirm:** a confirmation is a later session that used the changed tool or
  recipe on a real project and saw it behave as described. Add the date and
  what was done. The session that wrote the change does not count.
- **Promote:** at 2 confirmations, merge the change to `main` (cherry-pick its
  commits, or fast-forward if `staging` holds nothing else unconfirmed), run
  `tests/run_all.py`, push `main`, set the row to `promoted` with the commit.
- **Fail:** if it misbehaves, fix it on `staging`, note what failed, and start
  the count again at 0.
- Keep `staging` rebased on `main` after each promotion.

## Ledger

| Change | Commit(s) | Staged | Confirmations | Status |
|---|---|---|---|---|
| `rename_cable`, `list_wires(cable_id)`, `delete_cable(allow_wired)`, `archive_project(overwrite)` | c740fdc | 2026-10-18 | 2026-10-18: live check on a 117-cable project (rename away and back, refusal of a taken mark, 4 cores listed by cable, wired cable refused, archive replaced) | on `main` before this ledger existed; confirmations still recorded |
| `edit_lines` add op with `synoptic_style_id` (draws a line-diagram line, `newEwProjectLine(1)` + `setWireStyleSynopticID`) | this commit | 2026-10-18 | | staged, 0 confirmations. Measured: a schematic line cannot be converted (EW_BAD_INPUTS); "Add a cable" opened on one API-drawn line-diagram line, not on two others. Later the same day: each added segment becomes its own mesh, so a cable put on a route of several segments covers only the clicked one; draw a bent route with the GUI "Draw cable" command (one line, one mesh) |
| `gui_click` `modifiers` ("ctrl", "shift", "ctrl+shift" held around the click) | this commit | 2026-10-18 | | staged, 0 confirmations. Measured: ctrl+click selected three line segments together; "Add a cable" still does not open on a multi-line selection |
| `generate_report_drawings`: one generate run per report id | this commit | 2026-10-18 | | staged, 0 confirmations. Measured: two ids in one run drew only the first report and still returned 0 |
| `shift_folio_numbers` / `add_folio(insert_before_page)`: cascade stays in one book (`book_id`, else the book of `place_file_id`); refused in a multi-book project without either | this commit | 2026-10-18 | | staged, 0 confirmations. Measured: with a second book, page numbers repeat between books and the old plan collided |
