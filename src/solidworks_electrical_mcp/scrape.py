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
import json
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
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(\{.*?\})</script>', re.S,
)


def _http() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=30.0,
        follow_redirects=True,
    )


def _fetch(client: httpx.Client, base: str, page: str) -> str:
    """Fetch a help page and return only the inner Doxygen HTML.

    The help site is a Next.js shell; the real Doxygen markup lives in
    ``__NEXT_DATA__.props.pageProps.helpContentData.helpText``. Pulling that
    out up-front means the rest of the parser only has to deal with vanilla
    Doxygen tables.
    """
    r = client.get(base + page)
    r.raise_for_status()
    return _extract_help_text(r.text)


def _extract_help_text(html: str) -> str:
    m = NEXT_DATA_RE.search(html)
    if not m:
        return html
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return html
    text = (data.get("props", {})
                .get("pageProps", {})
                .get("helpContentData", {})
                .get("helpText"))
    return text if isinstance(text, str) and text else html


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

    for tr in soup.select("tr[class^='memitem:'], tr[class*=' memitem:']"):
        type_td = tr.find("td", class_="memItemLeft")
        name_td = tr.find("td", class_="memItemRight")
        if not isinstance(name_td, Tag):
            continue
        anchor_a = name_td.find("a", href=True)
        anchor = ""
        if anchor_a and "#" in anchor_a["href"]:
            anchor = anchor_a["href"].split("#", 1)[1]
        sig = " ".join(name_td.get_text(" ", strip=True).split())
        rtype = ""
        if isinstance(type_td, Tag):
            rtype = " ".join(type_td.get_text(" ", strip=True).split())
        full_sig = f"{rtype} {sig}".strip()
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", sig)
        name = m.group(1) if m else sig
        is_property = (rtype.lower().startswith("property")
                       or name.startswith(("get_", "set_", "put_")))
        is_inherited = "inherit" in (tr.get("class") or [])
        kind = "property" if is_property else "method"
        if is_inherited:
            kind = f"inherited-{kind}"

        summary = ""
        for sib in tr.find_next_siblings("tr", limit=2):
            cls = sib.get("class") or []
            if any(c.startswith("memdesc:") for c in cls):
                mdoc = sib.find("td", class_="mdescRight")
                if isinstance(mdoc, Tag):
                    summary = " ".join(mdoc.get_text(" ", strip=True).split())
                break
            if any(c.startswith("memitem:") for c in cls):
                break

        members.append(Member(
            name=name, kind=kind, signature=full_sig, summary=summary, anchor=anchor,
        ))

    return members


def _interface_summary(page_html: str) -> str:
    soup = BeautifulSoup(page_html, "lxml")
    for blk in soup.find_all("div", class_="textblock"):
        p = blk.find("p")
        if p:
            text = " ".join(p.get_text(" ", strip=True).split())
            if text:
                return text
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
