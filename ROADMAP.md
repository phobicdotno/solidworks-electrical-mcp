# Roadmap

## Deferred — interactive widgets (MCP Apps surface)

Not in v0.1. The current server returns plain JSON from every tool, which is the
right default for a discovery + COM-execute server. Re-evaluate when one real
workflow is up and the friction shows up in actual conversations.

### (b) Manufacturer-part picker widget

**Why it would help:** SW Electrical projects pull from large catalogs
(manufacturer parts, cables, terminals, symbols) that cannot be
realistically ranked from a plain text dump. A searchable picker widget
renders the filtered list inline; the user clicks one row; the chosen part ID
is sent back into the conversation as a user message.

**Shape:**

- **Tool:** `pick_manufacturer_part(filter?: string, family?: string)` — returns
  a JSON list of candidates and declares
  `_meta.ui.resourceUri = "ui://widgets/part-picker.html"`.
- **Widget:** scrollable list, search box, thumbnail (if available), part
  number / description / family. Click → `app.sendMessage({...})` with the
  selected part ID.
- **Backed by:** the SW Electrical manufacturer-part API (look up via
  `search_api("manufacturer part")` once the catalog is scraped).

### (c) Symbol picker

Same shape as (b) but over the SW Electrical symbol library. Probably worth
building only if (b) lands and proves useful.

### (d) Schematic preview / wire-route diff

Visual; widget territory if/when we expose drawing-export tools. Out of scope
until the read-only tool surface is fleshed out.

### (e) Confirm-before-execute for destructive `call()`

Probably **elicitation**, not a widget. The MCP elicitation primitive renders a
native confirm dialog with no UI build. Wire this into `call()` whenever the
resolved member name matches a destructive pattern (`Delete*`, `Remove*`,
`Clear*`, etc.).

## Other deferred work

- **MCPB packaging.** Ship as a single `.mcpb` bundle so users don't need
  Python on PATH. Defer until v0.1 has stabilised.
- **Auto-refresh catalog on SW upgrade.** Detect installed SW version at
  startup and rebuild the catalog automatically if the cached catalog is from
  a different release.
- **Type-aware `call()`.** Use the catalog's parsed parameter types to coerce
  Python args into the right COM variant (e.g. enums, BSTR vs LPWSTR).
- **Read-only tool split.** Separate `query_*` tools (annotated read-only) from
  `call()` so hosts that whitelist read-only tools can use the safe surface
  without prompting.
