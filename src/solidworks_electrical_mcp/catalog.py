"""API catalog: the in-memory index of SW Electrical interfaces and members.

The catalog is the master source of truth for what `search_api` and `get_api`
can return. It is built from the SOLIDWORKS 2026 API help (Doxygen) by
`scrape.py` and persisted as JSON. The MCP server loads it lazily at startup
and survives an empty/missing file (search just returns nothing).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

CATALOG_FILENAME = "api_catalog.json"
DOC_BASE = "https://help.solidworks.com/2026/english/api/sldworkselecapihelp/"


@dataclass(frozen=True)
class Member:
    name: str
    kind: str
    signature: str
    summary: str
    anchor: str

    def url(self, interface_page: str) -> str:
        anchor = f"#{self.anchor}" if self.anchor else ""
        return f"{DOC_BASE}{interface_page}{anchor}"


@dataclass
class Interface:
    name: str
    page: str
    summary: str = ""
    members: list[Member] = field(default_factory=list)

    @property
    def url(self) -> str:
        return f"{DOC_BASE}{self.page}"


class Catalog:
    def __init__(self, interfaces: Iterable[Interface] | None = None) -> None:
        self._interfaces: dict[str, Interface] = {}
        for iface in interfaces or []:
            self._interfaces[iface.name.lower()] = iface

    @property
    def interfaces(self) -> list[Interface]:
        return list(self._interfaces.values())

    def get_interface(self, name: str) -> Interface | None:
        return self._interfaces.get(name.lower())

    def search(self, query: str, limit: int = 25) -> list[dict]:
        q = query.strip().lower()
        if not q:
            return []
        terms = [t for t in q.split() if t]
        hits: list[tuple[int, dict]] = []
        for iface in self._interfaces.values():
            score = _score(iface.name.lower(), iface.summary.lower(), terms)
            if score:
                hits.append((score + 5, {
                    "kind": "interface",
                    "interface": iface.name,
                    "summary": iface.summary,
                    "url": iface.url,
                }))
            for m in iface.members:
                score = _score(m.name.lower(), m.summary.lower(), terms)
                if score:
                    hits.append((score, {
                        "kind": m.kind,
                        "interface": iface.name,
                        "member": m.name,
                        "signature": m.signature,
                        "summary": m.summary,
                        "url": m.url(iface.page),
                    }))
        hits.sort(key=lambda x: -x[0])
        return [h[1] for h in hits[:limit]]

    def to_json(self) -> dict:
        return {
            "schema": 1,
            "doc_base": DOC_BASE,
            "interfaces": [
                {
                    "name": i.name,
                    "page": i.page,
                    "summary": i.summary,
                    "members": [
                        {
                            "name": m.name,
                            "kind": m.kind,
                            "signature": m.signature,
                            "summary": m.summary,
                            "anchor": m.anchor,
                        }
                        for m in i.members
                    ],
                }
                for i in self._interfaces.values()
            ],
        }

    @classmethod
    def from_json(cls, data: dict) -> "Catalog":
        ifaces = [
            Interface(
                name=i["name"],
                page=i["page"],
                summary=i.get("summary", ""),
                members=[
                    Member(
                        name=m["name"],
                        kind=m.get("kind", "method"),
                        signature=m.get("signature", ""),
                        summary=m.get("summary", ""),
                        anchor=m.get("anchor", ""),
                    )
                    for m in i.get("members", [])
                ],
            )
            for i in data.get("interfaces", [])
        ]
        return cls(ifaces)


def _score(name: str, summary: str, terms: list[str]) -> int:
    score = 0
    for t in terms:
        if t in name:
            score += 10 if name == t else 5
        if t in summary:
            score += 1
    return score


def default_path() -> Path:
    return Path(__file__).parent / "data" / CATALOG_FILENAME


def load(path: Path | None = None) -> Catalog:
    p = path or default_path()
    if not p.exists():
        return Catalog()
    with p.open(encoding="utf-8") as f:
        return Catalog.from_json(json.load(f))


def save(catalog: Catalog, path: Path | None = None) -> Path:
    p = path or default_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(catalog.to_json(), f, indent=2)
    return p
