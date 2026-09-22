"""Live round trip over the tree, page and drawing tools.

Builds a scratch folder, page, location, component and symbols, exercises
every tool added for drawing work, then takes it all back out and checks the
project returns to the counts it started with. Anything left behind is a
failure: these tools write to a real drawing, so an undo that does not undo
is the defect worth catching.

Requires SOLIDWORKS Electrical running with a project open; otherwise SKIP.

Run directly:
    .venv/Scripts/python.exe tests/test_drawing_tools.py
"""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = REPO / ".venv" / "Scripts" / "python.exe"

TAG = "ZZTEST"          # scratch marks, cleaned up at the end
failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)
        print("FAIL:", msg)


def main() -> int:
    proc = subprocess.Popen(
        [str(PY), "-m", "solidworks_electrical_mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, cwd=str(REPO), text=True, bufsize=1,
    )
    _id = [0]

    def readline(timeout=300):
        box = {}

        def _r():
            box["v"] = proc.stdout.readline()
        t = threading.Thread(target=_r, daemon=True)
        t.start()
        t.join(timeout)
        return box.get("v")

    def rpc(method, params):
        _id[0] += 1
        proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "id": _id[0], "method": method,
             "params": params}) + "\n")
        proc.stdin.flush()
        line = readline()
        return json.loads(line) if line else {"_": "no response"}

    def tool(name, args=None):
        r = rpc("tools/call", {"name": name, "arguments": args or {}})
        return r.get("result", {}).get("structuredContent", r)

    folder_id = folio_id = comp_made = loc_id = None
    try:
        rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "t", "version": "0"}})
        proc.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        proc.stdin.flush()
        time.sleep(0.3)

        if not tool("connect").get("application"):
            print("SKIP: application did not attach")
            return 0

        base = tool("project_info")["counts"]
        print("baseline:", base)

        # --- the document tree -------------------------------------------
        book = tool("list_books_and_folders")["books"][0]["id"]
        fol = tool("add_folder", {"tag": "ZZ9", "description": "scratch",
                                  "book_id": book, "dry_run": False})
        check(fol.get("ok"), f"add_folder failed: {fol}")
        folder_id = fol.get("folder", {}).get("id")
        dup = tool("add_folder", {"tag": "ZZ9", "description": "dup",
                                  "book_id": book, "dry_run": False})
        check(dup.get("ok") is False and "already" in str(dup.get("error")),
              "add_folder must refuse a duplicate tag in the same book")

        pg = tool("add_folio", {"description": "scratch page",
                                "file_type": "mixed_scheme",
                                "folder_id": folder_id, "dry_run": False})
        check(pg.get("ok"), f"add_folio failed: {pg}")
        folio_id = pg.get("folio", {}).get("id")
        page_mark = pg.get("folio", {}).get("page")
        print("scratch folder", folder_id, "page", page_mark, folio_id)

        # --- a location and a component with a part the library lacks -----
        loc = tool("add_location", {"tag": "ZZ8", "description": "scratch loc",
                                    "dry_run": False})
        check(loc.get("ok"), f"add_location failed: {loc}")
        loc_id = loc.get("location", {}).get("id")
        dupl = tool("add_location", {"tag": "ZZ8", "description": "dup",
                                     "dry_run": False})
        check(dupl.get("ok") is False, "add_location must refuse a duplicate tag")

        # Pick a K-ROOTED component: a substring match can land on CC_K1,
        # whose root is CC, and the clone would rightly refuse the K target.
        pool = tool("list_components", {"tag_contains": "K", "limit": 0,
                                        "with_parts": False})
        krooted = [c for c in pool["components"]
                   if str(c["tag"]).startswith("K")
                   and str(c["tag"])[1:2].isdigit()]
        check(bool(krooted), "no K-rooted component to clone from")
        src_tag = krooted[0]["tag_path"]
        made = tool("clone_component", {"source_tag": src_tag,
                                        "new_tag": "K9911", "dry_run": False})
        check(made.get("ok"), f"clone_component failed: {made.get('errors')}")
        comp_made = made.get("new_component", {}).get("id")

        part = tool("attach_manufacturer_part",
                    {"tag": "K9911", "manufacturer": "ZZTestCo",
                     "reference": "ZZ-1", "description": "scratch part",
                     "dry_run": False})
        check(part.get("ok"), f"attach_manufacturer_part failed: {part}")
        again = tool("attach_manufacturer_part",
                     {"tag": "K9911", "manufacturer": "ZZTestCo",
                      "reference": "ZZ-1", "dry_run": False})
        check(again.get("note") == "already attached",
              "attaching the same part twice should be a no-op")

        # --- drawing -------------------------------------------------------
        sym = tool("place_symbol", {"tag": "K9911",
                                    "symbol_name": "Relay switch Sideways",
                                    "x": 100.0, "y": 200.0, "page": page_mark,
                                    "symbol_type": 20, "dry_run": False})
        check(sym.get("ok"), f"place_symbol failed: {sym}")
        sym_id = sym.get("symbol_id")
        check(bool(sym.get("points")), "place_symbol should report real points")

        far = tool("place_symbol", {"tag": "K9911",
                                    "symbol_name": "Relay switch Sideways",
                                    "x": 100.0, "y": 999.0, "page": page_mark,
                                    "symbol_type": 20, "dry_run": False})
        check(far.get("ok") is False, "place_symbol must refuse outside the box")

        mv = tool("move_symbols", {"moves": [{"symbol_id": sym_id,
                                              "dx": 20.0, "dy": 0.0}],
                                   "dry_run": False})
        check(mv.get("ok"), f"move_symbols failed: {mv}")
        twice = tool("move_symbols", {"moves": [{"symbol_id": sym_id, "dx": 1.0},
                                                {"symbol_id": sym_id, "dx": 2.0}],
                                      "dry_run": True})
        check(twice.get("ok") is False and "twice" in str(twice.get("error")),
              "move_symbols must refuse the same symbol twice in one batch")
        off = tool("move_symbols", {"moves": [{"symbol_id": sym_id,
                                               "dx": 9999.0}],
                                    "dry_run": True})
        check(off.get("ok") is False, "move_symbols must refuse leaving the box")

        txt = tool("add_text", {"text": "scratch note", "x": 100.0, "y": 210.0,
                                "page": page_mark, "dry_run": False})
        check(txt.get("ok") and txt.get("text_id", 0) > 0,
              f"add_text failed: {txt}")
        lst = tool("list_texts", {"page": page_mark})
        check(any(t["text"] == "scratch note" for t in lst.get("texts", [])),
              "list_texts did not return the text just added")

        rules = tool("check_drawing_rules", {"page": page_mark})
        check(rules.get("ok"), f"scratch page breaks the rules: {rules}")
        check(rules.get("min_spacing") == 10.0,
              f"default spacing should be the 10 mm grid step, got "
              f"{rules.get('min_spacing')}")

        blocked = tool("delete_folio", {"file_id": folio_id})
        check(blocked.get("ok") is False,
              "delete_folio must refuse while the page still has content")

        # --- tear down -----------------------------------------------------
        for t in tool("list_texts", {"page": page_mark}).get("texts", []):
            tool("remove_text", {"text_id": t["id"]})
        rs = tool("remove_symbol", {"symbol_id": sym_id})
        check(rs.get("ok"), f"remove_symbol failed: {rs}")
        dc = tool("delete_component", {"component_id": comp_made,
                                       "pages": [page_mark]})
        check(dc.get("ok"), f"delete_component failed: {dc}")
        comp_made = None
        df = tool("delete_folio", {"file_id": folio_id})
        check(df.get("ok"), f"delete_folio failed: {df}")
        folio_id = None
        dfo = tool("delete_folder", {"folder_id": folder_id})
        check(dfo.get("ok"), f"delete_folder failed: {dfo}")
        folder_id = None
        dl = tool("delete_location", {"location_id": loc["location"]["id"]})
        check(dl.get("ok"), f"delete_location failed: {dl}")
        loc_id = None

        after = tool("project_info")["counts"]
        print("after   :", after)
        for k in ("folios", "components", "locations"):
            check(after[k] == base[k],
                  f"{k} did not return to baseline: {base[k]} -> {after[k]}")

    finally:
        # never leave scratch objects behind, even on an early failure
        if comp_made:
            tool("delete_component", {"component_id": comp_made})
        if folio_id:
            tool("delete_folio", {"file_id": folio_id})
        if folder_id:
            tool("delete_folder", {"folder_id": folder_id})
        if loc_id:
            tool("delete_location", {"location_id": loc_id})
        if proc.poll() is None:
            proc.terminate()

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: tree, page, part and drawing tools round-tripped and the "
          "project returned to its baseline counts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
