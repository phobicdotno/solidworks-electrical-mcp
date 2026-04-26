"""Scrape the SOLIDWORKS 2026 Electrical API help into the local JSON catalog.

The master is https://help.solidworks.com/2026/english/api/sldworkselecapihelp/
which is a Doxygen-generated site:

* ``annotated.html`` — flat list of all interfaces / classes / structs
* ``interface_<name>.html`` — per-interface pages with method/property tables

Run this once after install (and again after upgrading) to refresh the catalog
that powers ``search_api`` / ``get_api`` in the MCP server.

Usage
-----
    python -m solidworks_electrical_mcp.scrape

Optional flags:
    --base URL     override the help base (default: SW 2026 English)
    --out PATH     override the output JSON path
    --limit N      only scrape the first N interfaces (debugging)
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import httpx
from bs4 import BeautifulSoup, Tag

from . import catalog as catalog_mod
from .catalog import Catalog, Interface, Member

DEFAULT_BASE = catalog_mod.DOC_BASE
ANNOTATED = "annotated.html"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _http() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=30.0,
        follow_redirects=True,
    )


def _fetch(client: httpx.Client, base: str, page: str) -> str:
    r = client.get(base + page)
    r.raise_for_status()
    return r.text


def _interface_links(annotated_html: str) -> list[tuple[str, str, str]]:
    """Return (interface_name, page, summary) tuples from annotated.html."""
    soup = BeautifulSoup(annotated_html, "lxml")
    out: list[tuple[str, str, str]] = []
    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        if not re.match(r"^(interface|class|struct)[A-Za-z0-9_]+\.html$", href):
            continue
        name = (a.get_text() or "").strip()
        if not name:
            continue
        summary = ""
        td = a.find_parent("td")
        if td:
            sib = td.find_next_sibling("td")
            if sib:
                summary = " ".join(sib.get_text(" ", strip=True).split())
        out.append((name, href, summary))
    seen: set[str] = set()
    deduped = []
    for n, p, s in out:
        if p in seen:
            continue
        seen.add(p)
        deduped.append((n, p, s))
    return deduped


def _parse_members(page_html: str) -> list[Member]:
    soup = BeautifulSoup(page_html, "lxml")
    members: list[Member] = []

    for tr in soup.select("tr.memitem"):
        type_td = tr.find("td", class_="memItemLeft")
        name_td = tr.find("td", class_="memItemRight")
        if not isinstance(name_td, Tag):
            continue
        anchor_a = name_td.find("a", href=True)
        anchor = ""
        if anchor_a and "#" in anchor_a["href"]:
            anchor = anchor_a["href"].split("#", 1)[1]
        sig = " ".join(name_td.get_text(" ", strip=True).split())
        rtype = " ".join(type_td.get_text(" ", strip=True).split()) if isinstance(type_td, Tag) else ""
        full_sig = f"{rtype} {sig}".strip()
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", sig)
        name = m.group(1) if m else sig
        kind = "property" if rtype.lower().startswith("property") or "get_" in name or "set_" in name else "method"

        summary = ""
        next_tr = tr.find_next_sibling("tr")
        if next_tr and "memdesc" in (next_tr.get("class") or []):
            mdoc = next_tr.find("td", class_="mdescRight")
            if isinstance(mdoc, Tag):
                summary = " ".join(mdoc.get_text(" ", strip=True).split())

        members.append(Member(
            name=name, kind=kind, signature=full_sig, summary=summary, anchor=anchor,
        ))

    if not members:
        for h2 in soup.select("h2.memtitle, h2.groupheader"):
            txt = " ".join(h2.get_text(" ", strip=True).split())
            m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", txt)
            if not m:
                continue
            name = m.group(1)
            anchor_a = h2.find("a", attrs={"id": True})
            anchor = anchor_a["id"] if anchor_a else ""
            members.append(Member(
                name=name, kind="method", signature=txt, summary="", anchor=anchor,
            ))

    return members


def _interface_summary(page_html: str) -> str:
    soup = BeautifulSoup(page_html, "lxml")
    brief = soup.find("div", class_="textblock")
    if brief:
        first_p = brief.find("p")
        if first_p:
            return " ".join(first_p.get_text(" ", strip=True).split())
    return ""


def scrape(base: str = DEFAULT_BASE, limit: int | None = None,
           sleep: float = 0.1, on_progress=None) -> Catalog:
    with _http() as client:
        annotated = _fetch(client, base, ANNOTATED)
        links = _interface_links(annotated)
        if limit:
            links = links[:limit]
        ifaces: list[Interface] = []
        for idx, (name, page, fallback_summary) in enumerate(links, 1):
            try:
                html = _fetch(client, base, page)
            except httpx.HTTPError as e:
                if on_progress:
                    on_progress(idx, len(links), name, f"FAIL {e}")
                continue
            summary = _interface_summary(html) or fallback_summary
            members = _parse_members(html)
            ifaces.append(Interface(name=name, page=page, summary=summary, members=members))
            if on_progress:
                on_progress(idx, len(links), name, f"{len(members)} members")
            if sleep:
                time.sleep(sleep)
        return Catalog(ifaces)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="solidworks-electrical-scrape")
    p.add_argument("--base", default=DEFAULT_BASE)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--sleep", type=float, default=0.1)
    args = p.parse_args(argv)

    def progress(i: int, n: int, name: str, status: str) -> None:
        print(f"[{i:3d}/{n}] {name:50s} {status}", file=sys.stderr)

    cat = scrape(base=args.base, limit=args.limit, sleep=args.sleep,
                 on_progress=progress)
    out = catalog_mod.save(cat, args.out)
    total_members = sum(len(i.members) for i in cat.interfaces)
    print(f"Wrote {out} ({len(cat.interfaces)} interfaces, {total_members} members)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
