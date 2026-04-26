# solidworks-electrical-mcp

MCP server for **SOLIDWORKS Electrical**, mastered against the
SOLIDWORKS Electrical API help — **2025** and **2026** ship side-by-side and
the server picks the right one at runtime.

The server attaches to a local SOLIDWORKS Electrical install via COM
(`EwAPI.EwInteropFactoryX`, late-bound through `pywin32`) and exposes the
full API surface (141 interfaces in 2025, 146 in 2026) to a Claude/MCP
client through a small set of discovery, comparison, and execution tools.

## Status

Alpha. Tested on Windows 11 against SOLIDWORKS Electrical 2025 SP5; the
catalogue ships for both 2025 and 2026 and is selected automatically based
on the installed factory. Requires SOLIDWORKS Electrical to be installed
locally.

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

* `getEwApplication(licenseKey, errorCode)` → `IEwApplicationX` — needs a
  licence key. SW Electrical add-ins ship their own keys; for development
  set `SWELE_LICENCE_KEY` in the env (read by `connect`) or pass
  `license_key=` directly to the `connect` tool.
* `getEwAPI(errorCode)` → `IEwAPIX` — no licence required, gives access to
  application-discovery and version helpers.

Both roots are reachable from the `call` tool via `root="api"` /
`root="factory"`; the default `root="application"` requires the licence key.

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

## Wire up to Claude Code

Add to `~/.claude/settings.json` under `mcpServers`:

```jsonc
{
  "mcpServers": {
    "solidworks-electrical": {
      "type": "stdio",
      "command": "C:\\path\\to\\repo\\.venv\\Scripts\\python.exe",
      "args": ["-m", "solidworks_electrical_mcp"],
      "env": {
        "SWELE_LICENCE_KEY": "<your SW Electrical add-in licence key>"
      }
    }
  }
}
```

The `env` block is optional — without a key, `connect`, `search_api`,
`get_api`, `list_interfaces`, and `call(..., root="api"|"factory")` all
work; only `call(..., root="application")` requires the licence.

## Why local stdio (not remote HTTP)

SOLIDWORKS Electrical is a Windows desktop application accessed through COM —
the MCP server has to run on the same machine as the SW process. Local
stdio is the right shape; an MCPB bundle is a future packaging option (see
[ROADMAP.md](ROADMAP.md)).

## License

MIT — see [LICENSE](LICENSE).
