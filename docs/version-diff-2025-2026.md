# SOLIDWORKS Electrical API — diff 2025 → 2026

Generated from
[help.solidworks.com/2025/.../sldworkselecapihelp/](https://help.solidworks.com/2025/english/api/sldworkselecapihelp/annotated.html)
vs
[help.solidworks.com/2026/.../sldworkselecapihelp/](https://help.solidworks.com/2026/english/api/sldworkselecapihelp/annotated.html)
by `solidworks_electrical_mcp.catalog.diff`. The structured form is
`src/solidworks_electrical_mcp/data/version_diff_2025_2026.json`.

| | 2025 | 2026 | Δ |
|---|---:|---:|---:|
| Interfaces | 141 | 146 | **+5** |
| Members    | 2,234 | 2,304 | **+70** |

Both releases use the same `EwAPI.EwInteropFactoryX` ProgID and
`IEwInteropFactoryX → IEwApplicationX / IEwAPIX` factory shape. The MCP
ships catalogs for both and selects the right one at runtime based on the
installed factory; the choice is overridable per call via the `version`
argument on `search_api`, `get_api`, and `compare_versions`.

## Added interfaces (5, none removed)

| Interface | Purpose |
|---|---|
| `IEwCircuitTypeManagerX` | Manage `IEwCircuitTypeX` objects. |
| `IEwCircuitTypeX` | Manage a circuit type (new typed-circuit model in 2026). |
| `IEwProgressX` | Generic progress reporter, used by long-running operations. |
| `IEwProjectTranslateX` | Translate project metadata; reachable from `IEwProjectX.newEwProjectTranslate`. |
| `IEwSwCabinetX` | SOLIDWORKS Design (3D) cabinet binding. |

## Removed members (2)

* `IEwProjectTagObjectX.getDescription`
* `IEwProjectTagObjectX.setDescription`

Both gone in 2026. If you read or write tag-object descriptions in a 2025
codepath, that path won't compile against 2026.

## Signature changes (2)

Pure parameter-name casing — `Id` → `ID`. COM late binding is positional, so
existing pywin32 / VBA code keeps working; typed-wrapper consumers (e.g.
generated `EnsureDispatch` stubs) need a regen.

| Member | 2025 | 2026 |
|---|---|---|
| `IEwClassificationX.findEwClassByID` | `findEwClassByID(LONG lClassId, …)` | `findEwClassByID(LONG lClassID, …)` |
| `IEwClassificationX.moveClass` | `moveClass(long lClassId, long lClassIdParent)` | `moveClass(long lClassID, long lClassIDParent)` |

## Added members (29 across 10 interfaces)

| Interface | Added |
|---|---|
| `IEwApplicationX` | `setEwProjectCurrent` |
| `IEwCableReferenceX` | `getLength`, `setLength` |
| `IEwClassX` | `getClassificationType`, `setClassificationType` |
| `IEwDialogProgressX` | `getFirstProgress`, `getSecondProgress` |
| `IEwEXCELImportLibraryX` | `getUpdateExistingData`, `setUpdateExistingData` |
| `IEwManagerDialogX` | `getColumnOrderArray`, `getColumnVisible`, `setColumnOrderArray`, `setColumnVisible` |
| `IEwProjectComponentX` | `getEwProjectComponentCircuitArray`, `getEwProjectManufacturerPartArray`, `getEwProjectManufacturerPartIDArray` |
| `IEwProjectExportPDFX` | `getEwPDFFileStructure`, `setEwPDFFileStructure`, `getFileNameFormula`, `setFileNameFormula`, `getFolderNameFormula`, `setFolderNameFormula`, `getTargetFolder`, `setTargetFolder` |
| `IEwProjectUpdateReplaceDataX` | `setForceCompatibilityType`, `setStrReplaceConfigName` |
| `IEwProjectX` | `isOpenByAnother`, `isOpenByMe`, `newEwProjectTranslate` |

The biggest functional gain is on `IEwProjectExportPDFX` — 2026 lets you
control the PDF folder/file naming formula and target folder programmatically
instead of going through the dialog.

## Notable summary / behaviour changes

* **`IEwClassX.setClassID` is deprecated since 2025 SP3** in favour of
  `IEwClassX.setClassificationID()`. Both methods are still present in 2026,
  but the deprecation note is new in the 2026 docs.
* Branding: "SOLIDWORKS" → "SOLIDWORKS Design" in `IEwEnvironmentX.getSOLIDWORKSFolderPath`,
  `IEwProjectEntity3DX.getSWID`, and a handful of others — descriptions only.
* `IEwClassificationX.newEwClass` summary tightened: "Create a new node in
  electrical classification" → "Create a new class in classification".
* `IEwProjectExportPDFX.{get,set}ExportOnePDFFileByBook` lost their summary
  text in 2026 (the methods themselves still exist).
* Wire-style interface summaries split: `IEwProjectWireStyleSynopticX` is now
  documented as wiring-line-diagram-specific (was previously generic with
  the schematic versions).

## Other casing-only summary tweaks (no behaviour change)

`Id`/`3d` rendered consistently as `ID`/`3D` in 2026 docs across
`IEwClassificationManagerX.findEwClassByID`,
`IEwClassificationX.{get,set}3DPartName`, several `IEwProjectWireX.get*`
methods that reference `EwWireExtremityType`, and a handful of
`IEwProjectComponentManagerX.*` summaries that dropped a stray "ID" word.
Listed in the JSON diff for completeness, no consumer change required.

## How the MCP picks a version

1. If the caller passes `version=` to `search_api` / `get_api` /
   `compare_versions`, that wins.
2. Otherwise the server probes the COM registry for installed
   `EwAPI.EwInteropFactoryX.<year>.<sp>` keys and uses the highest matching
   shipped catalog. (Verified at startup, no licence required.)
3. Otherwise it falls back to `DEFAULT_VERSION` (`2026`).

`list_interfaces()` and `connect()` are version-agnostic — they reflect what
the live install exposes, not what the doc catalog claims.
