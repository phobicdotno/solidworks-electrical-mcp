# solidworks-electrical-mcp

MCP server for **SOLIDWORKS Electrical**, mastered against the
[SOLIDWORKS 2026 Electrical API help](https://help.solidworks.com/2026/english/api/help_list.htm?id=3).

The server attaches to a local SOLIDWORKS Electrical install via COM
(`EwAPI.EwInteropFactoryX`, late-bound through `pywin32`) and exposes the
entire 146-interface API surface to a Claude/MCP client through a small set
of discovery and execution tools.

## Status

Alpha. Tested on Windows 11 against SOLIDWORKS Electrical 2025 SP5; the
catalogue is built from the SOLIDWORKS 2026 docs (the API is stable across
adjacent releases). Requires SOLIDWORKS Electrical to be installed locally.

## How it works

The Doxygen-generated SW Electrical help is the **master** for what is
callable. A scraper walks `sldworkselecapihelp/annotated.html` and every
`interface_*.html` page and writes a JSON catalog (146 interfaces, ~2,300
members at 2026.0.0) into the package. At runtime the MCP server loads that
catalog and exposes:

| Tool | Purpose |
|---|---|
| `list_interfaces()` | All interface names (e.g. `IEwApplicationX`). |
| `search_api(query, limit)` | Ranked search across interfaces + members. |
| `get_api(interface, member?)` | Pull one interface or one member's signature/summary/URL. |
| `connect(license_key?)` | Dispatch the SW Electrical COM factory and attach an application. |
| `call(path, args?, root?)` | Late-bound dotted attribute access on `application` / `api` / `factory`. |

The catalog ships in the repo at
`src/solidworks_electrical_mcp/data/api_catalog.json`. Rebuild it after a SW
upgrade with `python -m solidworks_electrical_mcp.scrape`.

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
# Optional: rebuild the catalog from the live SW 2026 docs
# python -m solidworks_electrical_mcp.scrape
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
