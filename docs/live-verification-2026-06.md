# Live verification — June 2026

Validated the MCP end-to-end against a running **SOLIDWORKS Electrical 2025.5**
instance (catalog `2025`), driving the server over stdio. This records what was
exercised, the one bug found and fixed, and the safe-write protocol used.

## Read control

`connect` attaches via `EwAPI.EwInteropFactoryX` to the live application. Read
paths verified against the open project: `getName`, `getID`, `getDescription`,
counts (`getEwProjectLocationManager.getCount`, component/manufacturer-part
counts), and array enumeration via `array_ops` over the location, component,
cable, file (folio), and book arrays. `typelib_members` and `get_enum` read the
live type library directly (ground truth where the scraped catalog is
incomplete).

## Bug found and fixed — `call` leaf not auto-invoked

The server instructions document `call('getEwProjectCurrent.getName')` (no
`args`) as the way to read a value, but the leaf segment was only invoked when
`args` was supplied. With `args` omitted, `call` returned the bound method
itself (`"<bound method ...>"`) instead of the value, even though intermediate
path segments were already auto-called by `_navigate`.

Fixed so a callable leaf with no `args` is auto-called (pass an explicit `args`
list when a member needs parameters). Regression test:
`tests/test_call_zero_arg_leaf.py` (integration; SKIPs without the app).
Verified live: `getEwProjectCurrent.getName` and `getApplicationVersion` now
return values with and without `args=[]`.

## Write control (CRUD) — verified on a disposable project

All writes were done on a throwaway, template-based project, never on
production data. Before each write the protocol asserts focus:
`openEwProjectID(id)` then confirm `getEwProjectCurrent.getID()` is the intended
project (guards against the documented mid-session GUI focus flip).

Verified, each op returning `EW_NO_ERROR (0)`:

| Capability | Path |
| --- | --- |
| Create location | `call_ops` on `…LocationManager.newEwProjectLocation` → `insert` → `setTag`/`setDescription` → `update` |
| Create component | `…ComponentManager.newEwProjectComponent` → `insert` → `setTag`/`setDescription` → `setLocationID` → `update` |
| Create cable | `…CableManager.newEwProjectCable` → `insert` → `setTag`/`setDescription`/`setArticleNumber`/`setSupplierName`/`setLength` → `update` |
| Field persistence | read-back confirmed tag, description, article number, supplier, length all stored |
| Relational link | component `getLocationID` returned the created location's id |
| Update / edit | `array_ops` filtered by `getID` → `setDescription` → `update`, read-back confirmed |
| Delete | `array_ops` filtered by `getID` → `remove`; counts returned to baseline |

Confirms the documented create pattern (`newX → insert → set fields → update`;
fields set before `insert` are silently dropped) and that `remove` cleanly
deletes. Cable reference/supplier are free text (no catalog part required).

Test objects were deleted after each pass; project counts returned to their
pre-test baseline.
