"""Full content dump of folio 03 (Line diagram, file id 2238)."""
import pythoncom
import win32com.client as w

KEY = ("4926A172437B649E1CC215D6820D10E279827EA6A735549D479F05A88D565E37"
       "DF1D32E499299E08E3D51521759776885C356801275ADFE764D1E0A360314B70"
       "EF1BD67685")


def u(v):
    return v[0] if isinstance(v, tuple) else v


def g(obj, meth, *a):
    try:
        m = getattr(obj, meth)
        return u(m(*a))
    except Exception as e:
        return f"<{type(e).__name__}>"


pythoncom.CoInitialize()
f = w.Dispatch("EwAPI.EwInteropFactoryX")
app = u(f.getEwApplication(KEY))
proj = u(app.getEwProjectCurrent())
fm = u(proj.getEwProjectFileManager())
cm = u(proj.getEwProjectComponentManager())
sm = u(proj.getEwProjectSymbolManager())

f03 = None
for raw in u(fm.getEwProjectFileArray()):
    el = w.Dispatch(raw)
    if u(el.getTag()) == "03":
        f03 = el
        break
fid = f03.getID()
print("FOLIO: tag", g(f03, "getTag"), "| desc", g(f03, "getDescription", "en"),
      "| type", f03.getFileType(), "| id", fid,
      "| bookID", g(f03, "getEwProjectBookID"), "| pos", g(f03, "getPosition"))
print("path:", g(f03, "getFilePath"))

print("\nSYMBOLS ON FOLIO 03:")
syms = u(sm.getProjectSymbolsFromFileID(fid))
print("count:", len(syms))
for raw in syms:
    s = w.Dispatch(raw)
    oid = g(s, "getObjectID")
    mark = "?"
    try:
        comp = u(cm.findEwProjectComponentByID(oid))
        if comp:
            mark = u(comp.getTagPath())
    except Exception:
        pass
    x, y = g(s, "getXPosition"), g(s, "getYPosition")
    xs = f"{x:.1f}" if isinstance(x, float) else x
    ys = f"{y:.1f}" if isinstance(y, float) else y
    print(f"  name={g(s,'getEwSymbolName')} symType={g(s,'getEwSymbolType')} "
          f"comp={mark} rowcol={g(s,'getRowMark')}{g(s,'getColumnMark')} "
          f"X={xs} Y={ys} rot={g(s,'getRotationAngle')} "
          f"mfgPartID={g(s,'getManufacturerPartID')} "
          f"pts={g(s,'getEwProjectSymbolPointCount')} "
          f"cir={g(s,'getEwProjectSymbolCircuitCount')}")

print("\nWIRES ON FOLIO 03:")
wm = u(proj.getEwProjectWireManager())
won = []
for raw in u(wm.getEwProjectWireArray()):
    wire = w.Dispatch(raw)
    if g(wire, "getFileID") == fid:
        won.append(wire)
print("count:", len(won))
for wire in won[:20]:
    print("  wireID", g(wire, "getID"), "tag", g(wire, "getTag"))
