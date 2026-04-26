"""API catalog: the in-memory index of SW Electrical interfaces and members.

Catalogues are versioned per SW Electrical major release (e.g. 2025, 2026).
The MCP ships one JSON catalogue per supported version; the server picks the
right one at runtime based on the installed COM factory version (or the
caller's explicit ``version`` argument).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

DEFAULT_VERSION = "2026"
SUPPORTED_VERSIONS = ("2025", "2026")
DOC_BASE_TEMPLATE = (
    "https://help.solidworks.com/{version}/english/api/sldworkselecapihelp/"
)


def doc_base(version: str) -> str:
    return DOC_BASE_TEMPLATE.format(version=version)


def catalog_filename(version: str) -> str:
    return f"api_catalog_{version}.json"


@dataclass(frozen=True)
class Member:
    name: str
    kind: str
    signature: str
    summary: str
    anchor: str

    def url(self, interface_page: str, version: str) -> str:
        anchor = f"#{self.anchor}" if self.anchor else ""
        return f"{doc_base(version)}{interface_page}{anchor}"


@dataclass
class Interface:
    name: str
    page: str
    summary: str = ""
    members: list[Member] = field(default_factory=list)

    def url(self, version: str) -> str:
        return f"{doc_base(version)}{self.page}"


class Catalog:
    def __init__(self, version: str,
                 interfaces: Iterable[Interface] | None = None) -> None:
        self.version = version
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
                    "version": self.version,
                    "interface": iface.name,
                    "summary": iface.summary,
                    "url": iface.url(self.version),
                }))
            for m in iface.members:
                score = _score(m.name.lower(), m.summary.lower(), terms)
                if score:
                    hits.append((score, {
                        "kind": m.kind,
                        "version": self.version,
                        "interface": iface.name,
                        "member": m.name,
                        "signature": m.signature,
                        "summary": m.summary,
                        "url": m.url(iface.page, self.version),
                    }))
        hits.sort(key=lambda x: -x[0])
        return [h[1] for h in hits[:limit]]

    def to_json(self) -> dict:
        return {
            "schema": 2,
            "version": self.version,
            "doc_base": doc_base(self.version),
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
        version = data.get("version") or DEFAULT_VERSION
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
        return cls(version, ifaces)


def _score(name: str, summary: str, terms: list[str]) -> int:
    score = 0
    for t in terms:
        if t in name:
            score += 10 if name == t else 5
        if t in summary:
            score += 1
    return score


def data_dir() -> Path:
    return Path(__file__).parent / "data"


def default_path(version: str) -> Path:
    return data_dir() / catalog_filename(version)


def load(version: str = DEFAULT_VERSION,
         path: Path | None = None) -> Catalog:
    p = path or default_path(version)
    if not p.exists():
        return Catalog(version)
    with p.open(encoding="utf-8") as f:
        return Catalog.from_json(json.load(f))


def save(catalog: Catalog, path: Path | None = None) -> Path:
    p = path or default_path(catalog.version)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(catalog.to_json(), f, indent=2)
    return p


def available_versions() -> list[str]:
    """Versions for which a catalog JSON file is shipped on disk."""
    out = []
    for v in SUPPORTED_VERSIONS:
        if default_path(v).exists():
            out.append(v)
    return out


def diff(old: Catalog, new: Catalog) -> dict:
    """Compute a structured interface- and member-level diff between two
    catalogues. Old vs new is by chronological order of versions, so callers
    typically do ``diff(load("2025"), load("2026"))``.
    """
    by_name_old = {i.name: i for i in old.interfaces}
    by_name_new = {i.name: i for i in new.interfaces}

    added_ifaces = sorted(set(by_name_new) - set(by_name_old))
    removed_ifaces = sorted(set(by_name_old) - set(by_name_new))
    common = sorted(set(by_name_old) & set(by_name_new))

    iface_changes: list[dict] = []
    for name in common:
        a, b = by_name_old[name], by_name_new[name]
        am = {m.name: m for m in a.members}
        bm = {m.name: m for m in b.members}
        added = sorted(set(bm) - set(am))
        removed = sorted(set(am) - set(bm))
        sig_changed: list[dict] = []
        summary_changed: list[dict] = []
        for mname in sorted(set(am) & set(bm)):
            if am[mname].signature != bm[mname].signature:
                sig_changed.append({
                    "member": mname,
                    "old_signature": am[mname].signature,
                    "new_signature": bm[mname].signature,
                })
            elif am[mname].summary != bm[mname].summary:
                summary_changed.append({
                    "member": mname,
                    "old_summary": am[mname].summary,
                    "new_summary": bm[mname].summary,
                })
        iface_summary_changed = a.summary != b.summary
        if (added or removed or sig_changed or summary_changed
                or iface_summary_changed):
            iface_changes.append({
                "interface": name,
                "summary_changed": iface_summary_changed,
                "old_summary": a.summary if iface_summary_changed else None,
                "new_summary": b.summary if iface_summary_changed else None,
                "added_members": added,
                "removed_members": removed,
                "signature_changes": sig_changed,
                "summary_changes": summary_changed,
            })

    return {
        "old_version": old.version,
        "new_version": new.version,
        "added_interfaces": added_ifaces,
        "removed_interfaces": removed_ifaces,
        "interface_changes": iface_changes,
        "totals": {
            "interfaces_added": len(added_ifaces),
            "interfaces_removed": len(removed_ifaces),
            "interfaces_changed": len(iface_changes),
        },
    }
