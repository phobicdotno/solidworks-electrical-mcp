"""Live test of the task-level tools against the open project.

Drives the real server over stdio (fresh process, so it runs the on-disk
code) and exercises every task-level tool. Read-only tools must return real,
internally consistent data; export_folio_pdf must produce a file on disk;
rename_project is round-tripped to the SAME name so nothing changes.

Requires SOLIDWORKS Electrical running with a project open; otherwise SKIP.

Run directly:
    .venv/Scripts/python.exe tests/test_workflow_tools.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = REPO / ".venv" / "Scripts" / "python.exe"

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

    def readline(timeout=180):
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

    try:
        rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "t", "version": "0"}})
        proc.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        proc.stdin.flush()
        time.sleep(0.3)

        conn = tool("connect")
        if not conn.get("application"):
            print(f"SKIP: application did not attach "
                  f"({conn.get('licence_error', conn)})")
            return 0

        info = tool("project_info")
        print("project_info:", info.get("name"), info.get("counts"))
        check(info.get("ok") and isinstance(info.get("name"), str)
              and info["name"], "project_info: no project name")
        counts = info.get("counts", {})
        check(counts.get("folios", 0) > 0, "project_info: zero folios")

        folios = tool("list_folios")
        rows = folios.get("folios", [])
        print(f"list_folios: {folios.get('count')} rows; first={rows[:1]}")
        check(folios.get("ok") and folios.get("count") == counts.get("folios"),
              "list_folios: count disagrees with project_info")
        check(all(isinstance(r["id"], int) and r["page"] for r in rows),
              "list_folios: rows missing id/page")
        books_seen = [r["book_id"] for r in rows]
        check(books_seen == sorted(books_seen, key=books_seen.index),
              "list_folios: books interleave; expected book-major tree order")
        check(all(not isinstance(r["description"], dict) for r in rows),
              "list_folios: a description came back as an error dict")
        covers = tool("list_folios", {"file_type": "cover_page"})
        print("cover pages:", [(r["page"], r["description"]) for r in
                               covers.get("folios", [])])
        check(covers.get("count", 0) >= 1, "list_folios: no cover page found")

        pick = next((r for r in rows if r["file_type"] in
                     ("mixed_scheme", "line_diagram", "folio")), rows[0])
        ff = tool("find_folio", {"page": pick["page"]})
        check(ff.get("ok") and ff.get("id") == pick["id"],
              f"find_folio by page {pick['page']!r} did not return id {pick['id']}")
        ff2 = tool("find_folio", {"file_id": pick["id"]})
        check(ff2.get("ok") and ff2.get("page") == pick["page"],
              "find_folio by id did not return the same page")
        bad = tool("find_folio", {"page": "ZZZ999"})
        check(bad.get("ok") is False and "no folio" in str(bad.get("error")),
              "find_folio: missing page should be a clean error")

        locs = tool("list_locations")
        print(f"list_locations: {locs.get('count')} ->",
              [l["tag"] for l in locs.get("locations", [])][:8])
        check(locs.get("ok") and locs.get("count") == counts.get("locations"),
              "list_locations: count disagrees with project_info")

        bf = tool("list_books_and_folders")
        print(f"books={len(bf.get('books', []))} folders={len(bf.get('folders', []))}")
        check(bf.get("ok") and len(bf.get("books", [])) == counts.get("books"),
              "list_books_and_folders: book count disagrees")

        comps = tool("list_components", {"limit": 5})
        print("list_components(5):", [(c["tag_path"], [p["reference"] for p in
                                       c.get("parts", [])])
                                      for c in comps.get("components", [])])
        check(comps.get("ok") and comps.get("count") == 5
              and comps.get("total_in_project") == counts.get("components"),
              "list_components: limit/total inconsistent with project_info")
        first = comps["components"][0]
        fc = tool("find_component", {"tag": first["tag_path"]})
        check(fc.get("ok") and any(c["id"] == first["id"]
                                   for c in fc.get("components", [])),
              f"find_component by tag path {first['tag_path']!r} missed id {first['id']}")
        fc2 = tool("find_component", {"tag": first["tag"]})
        check(fc2.get("ok") and any(c["id"] == first["id"]
                                    for c in fc2.get("components", [])),
              f"find_component by bare tag {first['tag']!r} missed id {first['id']}")
        print(f"find_component({first['tag']!r}): {fc2.get('count')} match(es)")

        cables = tool("list_cables", {"limit": 3})
        print("list_cables(3):", [(c["tag"], c["reference"]) for c in
                                  cables.get("cables", [])])
        check(cables.get("ok") and (cables.get("count") == min(3, counts.get("cables", 0)))
              and cables.get("total_in_project") == counts.get("cables")
              and cables.get("truncated") == (counts.get("cables", 0) > 3),
              "list_cables: count/total/truncated inconsistent with project_info")

        # Walk schematic pages until one carries symbols (some are empty).
        drawn = None
        for cand in [r for r in rows if r["file_type"] in
                     ("mixed_scheme", "line_diagram", "folio")][:15]:
            syms = tool("folio_symbols", {"page": cand["page"]})
            check(syms.get("ok") and syms.get("folio", {}).get("id") == cand["id"],
                  f"folio_symbols: wrong folio resolved for page {cand['page']}")
            if syms.get("count", 0) > 0:
                drawn = (cand, syms)
                break
        check(drawn is not None, "folio_symbols: no symbols on the first 15 "
                                 "schematic pages")
        if drawn:
            cand, syms = drawn
            print(f"folio_symbols(page {cand['page']} {cand['description']!r}): "
                  f"{syms['count']} symbols; first={syms['symbols'][:1]}")
            check(all("symbol_name" in s and "x" in s for s in syms["symbols"]),
                  "folio_symbols: rows missing fields")
            check(any(s.get("component_tag_path") for s in syms["symbols"]),
                  "folio_symbols: no symbol resolved to a component tag path")

        out_dir = Path(tempfile.gettempdir()) / "swele_mcp_test" / "nested"
        out_pdf = out_dir / f"page_{pick['page']}.pdf"
        if out_pdf.exists():
            out_pdf.unlink()
        pdf = tool("export_folio_pdf", {"output_path": str(out_pdf),
                                        "pages": [pick["page"]]})
        print("export_folio_pdf:", pdf.get("ok"), pdf.get("size_bytes"),
              [(s["step"], s.get("rc")) for s in pdf.get("steps", [])])
        check(pdf.get("ok") and out_pdf.is_file() and out_pdf.stat().st_size > 1000,
              f"export_folio_pdf did not produce a PDF: {pdf}")
        try:
            out_pdf.unlink()
        except OSError:
            pass

        # clone_component / delete_component round trip on the drawn page:
        # clone the first symbol's component into a throwaway tag, verify the
        # component, its parts and the copied symbol, then delete it again.
        if drawn:
            cand, syms = drawn
            src_sym = next((s for s in syms["symbols"]
                            if s.get("component_tag_path")), None)
            src_tag = src_sym["component_tag_path"] if src_sym else None
            src_bare = src_tag.split("-")[-1] if src_tag else ""
            m = re.match(r"([A-Za-z]+)", src_bare)   # leading letters only
            src_root = m.group(1) if m else "Z"
            tmp_tag = src_root + "9901"
            tool("delete_component", {"tag": tmp_tag,
                                      "pages": [cand["page"]]})  # crash leftover
            if src_tag:
                wrong = tool("clone_component", {"source_tag": src_tag,
                                                 "new_tag": "ZZ9901",
                                                 "page": cand["page"]})
                check(wrong.get("ok") is False and "tag root" in str(wrong.get("error")),
                      "clone_component must refuse a different tag root")
                plan = tool("clone_component", {"source_tag": src_tag,
                                                "new_tag": tmp_tag,
                                                "page": cand["page"]})
                check(plan.get("ok") and plan.get("dry_run") is True
                      and plan.get("plan", {}).get("symbols_to_copy"),
                      f"clone_component dry run failed: {plan}")
                before = tool("find_component", {"tag": tmp_tag})
                check(before.get("count") == 0,
                      "clone_component dry run must not create anything")
                lower = tool("clone_component",
                             {"source_tag": src_tag,
                              "new_tag": tmp_tag.lower(),
                              "page": cand["page"]})
                check(lower.get("ok")
                      and lower.get("plan", {}).get("new_tag") == tmp_tag,
                      "clone_component must normalise a lowercase tag root "
                      f"to the project casing (got {lower.get('plan', {}).get('new_tag')!r})")
                done = tool("clone_component", {"source_tag": src_tag,
                                                "new_tag": tmp_tag,
                                                "page": cand["page"],
                                                "dry_run": False})
                print("clone_component:", done.get("ok"),
                      done.get("new_component", {}).get("tag_path"),
                      "parts:", len(done.get("new_component", {}).get("parts", [])),
                      "symbols:", done.get("symbols_placed"))
                check(done.get("ok") and not done.get("errors"),
                      f"clone_component failed: {done.get('errors')}")
                new_id = done.get("new_component", {}).get("id")
                src_parts = plan["plan"]["source"]["parts"]
                check(len(done.get("new_component", {}).get("parts", []))
                      == len(src_parts),
                      "clone_component: manufacturer parts not copied")
                after = tool("folio_symbols", {"page": cand["page"]})
                check(any(s["component_id"] == new_id for s in after.get("symbols", [])),
                      "clone_component: no symbol for the clone on the page")
                dele = tool("delete_component", {"component_id": new_id,
                                                 "pages": [cand["page"]]})
                check(dele.get("symbols_removed"),
                      "delete_component: expected to remove the bound symbol")
                check(dele.get("ok"), f"delete_component failed: {dele}")
                gone = tool("find_component", {"tag": tmp_tag})
                check(gone.get("count") == 0, "delete_component: clone still exists")
                after2 = tool("folio_symbols", {"page": cand["page"]})
                check(after2.get("count") == syms["count"],
                      "delete_component: symbol count did not return to baseline")

        name = info["name"]
        rn = tool("rename_project", {"new_name": name})
        print("rename_project (round-trip):", rn)
        check(rn.get("ok") and rn.get("new_name") == name,
              "rename_project round-trip failed")

        rg = tool("regenerate_title_blocks")
        print("regenerate_title_blocks:", rg.get("rc_name"), "open:",
              rg.get("open_folios"))
        check(rg.get("rc") in (0, 45),
              f"regenerate_title_blocks: unexpected rc {rg.get('rc')}")

        rc = tool("reconnect")
        check(rc.get("application") is True, f"reconnect failed: {rc}")
        info2 = tool("project_info")
        check(info2.get("name") == name, "project_info after reconnect differs")

    finally:
        if proc.poll() is None:
            proc.terminate()

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: every task-level tool returned real, consistent data from the "
          "open project; PDF export produced a file; reconnect re-attached.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
