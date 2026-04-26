# solidworks-electrical-mcp

MCP server for **SOLIDWORKS Electrical**, mastered against the
[SOLIDWORKS 2026 Electrical API help](https://help.solidworks.com/2026/english/api/help_list.htm?id=3).

The server attaches to a local SOLIDWORKS Electrical install via COM
(`EApp.Application`, late-bound through `pywin32`) and exposes the entire
146-interface API surface to a Claude/MCP client through a small set of
discovery and execution tools.

## Status

Alpha. Scaffold only. Tested target: SOLIDWORKS Electrical 2024-2026 on
Windows 10/11. Requires SOLIDWORKS Electrical to be installed locally.

## How it works

The Doxygen-generated SW Electrical help is the **master** for what is
callable. A scraper walks `sldworkselecapihelp/annotated.html` and every
`interface_*.html` page and writes a JSON catalog into the package. At
runtime the MCP server loads that catalog and exposes:

| Tool | Purpose |
|---|---|
| `list_interfaces()` | All interface names (e.g. `IEwApplication`). |
| `search_api(query, limit)` | Ranked search across interfaces + members. |
| `get_api(interface, member?)` | Pull one interface or one member's signature/summary/URL. |
| `connect()` | Attach to `EApp.Application` via COM. |
| `call(path, args?)` | Late-bound dotted attribute access on the live app. |

The catalog is built once with `python -m solidworks_electrical_mcp.scrape`
and refreshed when SW upgrades to a new yearly release.

## Install

```bash
git clone https://github.com/phobicdotno/solidworks-electrical-mcp.git
cd solidworks-electrical-mcp
python -m venv .venv && .venv\Scripts\activate
pip install -e .
python -m solidworks_electrical_mcp.scrape       # builds data/api_catalog.json
```

## Wire up to Claude Code

Add to `~/.claude/settings.json` under `mcpServers`:

```jsonc
{
  "mcpServers": {
    "solidworks-electrical": {
      "type": "stdio",
      "command": "C:\\path\\to\\repo\\.venv\\Scripts\\python.exe",
      "args": ["-m", "solidworks_electrical_mcp"]
    }
  }
}
```

## Why local stdio (not remote HTTP)

SOLIDWORKS Electrical is a Windows desktop application accessed through COM —
the MCP server has to run on the same machine as the SW process. Local
stdio is the right shape; an MCPB bundle is a future packaging option.

## License

MIT — see [LICENSE](LICENSE).
