"""Thin pywin32/COM wrapper around SOLIDWORKS Electrical.

The documented entry point on a SOLIDWORKS Electrical install is the COM
factory ``EwAPI.EwInteropFactoryX`` (interface ``IEwInteropFactoryX``,
inheriting ``IInteropFactoryX``). The factory hands out two top-level objects:

* ``getEwApplication(licenseKey, errorCode)`` → ``IEwApplicationX``
* ``getEwAPI(errorCode)`` → ``IEwAPIX``

``getEwApplication`` is gated behind a licence *code* that is separate from
the SOLIDWORKS program/seat licence. SOLIDWORKS ships a single shared key
embedded in its own add-in binaries — it is identical on every install, not a
per-customer secret — so a known-good default is bundled in
``DEFAULT_LICENCE_KEY`` and the server works out of the box. An explicit
``license_key`` argument or the ``SWELE_LICENCE_KEY`` environment variable
override the default.

Methods/properties are accessed dynamically through pywin32 late binding so
the wrapper does not need a generated typelib (the user's installed version
may differ from the 2026 docs the catalogue is built from).
"""

from __future__ import annotations

import os
import queue
import re
import threading
from typing import Any

# COM ProgID for the SW Electrical factory. The unversioned form picks the
# latest install; ``EwAPI.EwInteropFactoryX.<year>.<sp>`` pins a specific
# release (e.g. ``EwAPI.EwInteropFactoryX.2025.5``).
FACTORY_PROGID = "EwAPI.EwInteropFactoryX"
VERSIONED_PROGID_RE = re.compile(
    r"^EwAPI\.EwInteropFactoryX\.(\d{4})\.(\d+)$"
)

# Environment variable read by ``connect_application`` if no explicit key is
# passed. Lets the user wire a licence key in once via the MCP client's env
# config rather than passing it on every call.
LICENCE_ENV_VAR = "SWELE_LICENCE_KEY"

# SOLIDWORKS Electrical's COM API gates ``getEwApplication`` behind a licence
# *code* distinct from the program/seat licence. SOLIDWORKS ships one shared
# key that is embedded, identically, in its own add-in binaries (e.g.
# ``ewexceladdin.dll``, ``ewenvironmentarchiver.exe``) — it is the same on
# every install rather than a per-customer secret, so it is safe to bundle as a
# default. Verified against SW Electrical 2025 SP5:
# ``getEwApplication(DEFAULT_LICENCE_KEY)`` returns ``EW_NO_ERROR`` (0) and a
# live ``IEwApplicationX``. Override via ``license_key=`` or ``$SWELE_LICENCE_KEY``.
DEFAULT_LICENCE_KEY = (
    "4926A172437B649E1CC215D6820D10E279827EA6A735549D479F05A88D565E37"
    "DF1D32E499299E08E3D51521759776885C356801275ADFE764D1E0A360314B70"
    "EF1BD67685"
)

# EwErrorCode values relevant to licence diagnostics (from the API help's
# EnumDefinition.idl). Used to give a named error instead of a bare integer.
_EW_ERROR_NAMES = {
    -1: "EW_NOT_IMPLEMENTED",
    0: "EW_NO_ERROR",
    1: "EW_UNDEFINED_ERROR",
    39: "EW_INVALID_LICENSE",
    40: "EW_LICENSE_WITHOUT_API_OPTION",
    41: "EW_ERROR_WINDCHILL_LICENSE",
}


class SolidworksElectricalNotInstalledError(RuntimeError):
    """Raised when the SW Electrical COM factory cannot be created."""


class SolidworksElectricalLicenceError(RuntimeError):
    """Raised when no licence key is available for getEwApplication."""


def _require_pywin32():
    try:
        import win32com.client  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "pywin32 is required for SOLIDWORKS Electrical COM access. "
            "Install it on Windows: pip install pywin32"
        ) from e
    return win32com.client


def coerce_value(v: Any) -> Any:
    """Convert a COM return value into a JSON-safe Python value.

    MUST run on the COM apartment thread: a live COM object is bound to the
    apartment that created it, so even ``repr()`` or iteration from another
    thread raises ``RPC_E_WRONG_THREAD``. Scalars pass through; tuples/lists
    (pywin32 returns out-params as tuples, SAFEARRAYs as sequences) are
    coerced elementwise; anything else (a COM sub-object) degrades to a tagged
    repr so no thread-bound pointer ever escapes the worker.
    """
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (list, tuple)):
        return [coerce_value(x) for x in v]
    try:
        return [coerce_value(x) for x in list(v)]
    except Exception:
        return repr(v)


class _ComWorker:
    """Single dedicated thread that owns every COM call.

    SOLIDWORKS Electrical hands out STA (single-threaded-apartment) objects:
    each is usable only from the thread that created it. The MCP server runs
    tool functions on an anyio thread pool, so consecutive calls land on
    different threads — using a cached COM object from the "wrong" one raises
    ``RPC_E_WRONG_THREAD``. Funnelling all COM work onto one persistent STA
    thread keeps every factory/application/api object on its home apartment.
    """

    def __init__(self) -> None:
        self._tasks: queue.Queue = queue.Queue()
        self._thread = threading.Thread(
            target=self._run, name="swele-com", daemon=True)
        self._start_lock = threading.Lock()
        self._started = False

    def _ensure_started(self) -> None:
        with self._start_lock:
            if not self._started:
                self._thread.start()
                self._started = True

    def submit(self, fn):
        """Run ``fn`` on the COM thread; block for and return its result.

        Exceptions raised by ``fn`` are re-raised on the calling thread with
        their original type preserved, so callers keep catching
        ``SolidworksElectricalLicenceError`` etc. as before.
        """
        self._ensure_started()
        box: dict[str, Any] = {}
        done = threading.Event()
        self._tasks.put((fn, box, done))
        done.wait()
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def _run(self) -> None:
        import pythoncom  # type: ignore

        pythoncom.CoInitialize()  # STA apartment for all SW Electrical objects
        try:
            while True:
                fn, box, done = self._tasks.get()
                try:
                    box["value"] = fn()
                except BaseException as e:  # propagate everything to the caller
                    box["error"] = e
                finally:
                    done.set()
        finally:  # pragma: no cover - daemon thread runs for process lifetime
            pythoncom.CoUninitialize()


class ElectricalApp:
    """Lazy singleton wrapper for the SW Electrical COM surface.

    Public methods marshal their work onto a single STA thread (`_ComWorker`);
    the ``*_locked`` helpers hold the actual COM logic and only ever run there,
    so cached COM objects never cross an apartment boundary.
    """

    def __init__(self) -> None:
        self._factory: Any | None = None
        self._app: Any | None = None
        self._api: Any | None = None
        self._enums: dict[str, dict[str, int]] | None = None
        self._worker = _ComWorker()

    # --- COM logic; these run ONLY on the worker thread -------------------

    def _factory_locked(self) -> Any:
        if self._factory is not None:
            return self._factory
        client = _require_pywin32()
        try:
            self._factory = client.Dispatch(FACTORY_PROGID)
        except Exception as e:
            raise SolidworksElectricalNotInstalledError(
                f"Could not create COM dispatch for {FACTORY_PROGID!r}. "
                "Is SOLIDWORKS Electrical installed and registered?"
            ) from e
        return self._factory

    def _connect_application_locked(self, license_key: str | None) -> Any:
        if self._app is not None:
            return self._app
        key = (license_key or os.environ.get(LICENCE_ENV_VAR)
               or DEFAULT_LICENCE_KEY)
        factory = self._factory_locked()
        # IInteropFactoryX::getEwApplication(BSTR licenceKey, EwErrorCode* err).
        # Late-bound, the out-param is omitted by the caller and returned as the
        # second tuple element alongside the IEwApplicationX return value.
        result = factory.getEwApplication(key)
        if isinstance(result, tuple):
            self._app, err = result[0], result[-1]
        else:
            self._app, err = result, None
        if self._app is None:
            name = _EW_ERROR_NAMES.get(err, f"EwErrorCode {err}")
            raise SolidworksElectricalLicenceError(
                f"getEwApplication returned NULL ({name}). The licence code "
                "was rejected. Pass a valid license_key= or set "
                f"${LICENCE_ENV_VAR}; the bundled default may be outdated, or "
                "SOLIDWORKS Electrical may not be running."
            )
        return self._app

    def _get_api_locked(self) -> Any:
        if self._api is not None:
            return self._api
        factory = self._factory_locked()
        result = factory.getEwAPI()
        if isinstance(result, tuple):
            self._api = result[0]
        else:
            self._api = result
        return self._api

    def _root_target(self, root: str) -> Any:
        if root == "application":
            return self._connect_application_locked(None)
        if root == "api":
            return self._get_api_locked()
        if root == "factory":
            return self._factory_locked()
        raise ValueError(f"unknown COM root {root!r}")

    def _navigate(self, target: Any, parts: list[str]) -> Any:
        """Walk dotted path segments, auto-calling callable getters and
        unwrapping ``(object, errorCode)`` tuples (see ``_call_locked``)."""
        for p in parts:
            attr = getattr(target, p)
            if callable(attr):
                res = attr()
                target = res[0] if isinstance(res, tuple) else res
            else:
                target = attr
            if target is None:
                raise AttributeError(
                    f"path stopped at {p!r}: it returned NULL (no such object, "
                    "or nothing is currently active)")
        return target

    def _eval_member(self, obj: Any, member_path: str,
                     args: list | None) -> Any:
        """Evaluate a (possibly dotted) member path on ``obj``.

        Intermediate segments are auto-called with no args and their
        ``(object, errorCode)`` tuples unwrapped (via ``_navigate``), so a
        single op can chain object->object — e.g. ``getEwProjectComponent``
        (returns the component) ``.getParentID`` (reads its parent). The
        final segment is called with ``args`` (or returned uncalled when
        ``args`` is None, preserving the single-member convention). For a
        plain single-segment member this is identical to the old behaviour.
        """
        parts = member_path.split(".")
        target = obj if len(parts) == 1 else self._navigate(obj, parts[:-1])
        leaf = getattr(target, parts[-1])
        return leaf if args is None else leaf(*args)

    def _call_ops_locked(self, target_path: str | None,
                         ops: list[dict], root: str) -> list[Any]:
        target = self._root_target(root)
        if target_path:
            target = self._navigate(target, target_path.split("."))
        results: list[Any] = []
        for op in ops:
            result = self._eval_member(target, op["member"], op.get("args"))
            results.append(coerce_value(result))
        return results

    def _array_ops_locked(self, array_path: str | None, select: dict | None,
                          ops: list[dict], root: str, limit: int | None,
                          array_args: list | None) -> list[dict]:
        client = _require_pywin32()
        target = self._root_target(root)
        if not array_path:
            arr = target
        elif array_args:
            # The array comes from a method that takes arguments (e.g.
            # getProjectSymbolsFromFileID(fileID)) — navigate to its owner,
            # then call the final segment with array_args and unwrap.
            parts = array_path.split(".")
            owner = self._navigate(target, parts[:-1])
            res = getattr(owner, parts[-1])(*array_args)
            arr = res[0] if isinstance(res, tuple) else res
        else:
            arr = self._navigate(target, array_path.split("."))
        rows: list[dict] = []
        for idx, raw in enumerate(arr):
            # VARIANT arrays come back as raw PyIDispatch; wrap each so late
            # binding (getattr/method calls) works.
            try:
                el = client.Dispatch(raw)
            except Exception:
                el = raw
            if select is not None:
                sv = self._eval_member(el, select["member"],
                                       select.get("args") or [])
                sv = sv[0] if isinstance(sv, tuple) else sv
                if sv != select.get("equals"):
                    continue
            results = []
            for op in (ops or []):
                # Isolate per-op failures so one element missing a chained
                # object (e.g. a part with no component -> NULL) does not abort
                # the whole enumeration; that cell reports its error instead.
                try:
                    r = self._eval_member(el, op["member"], op.get("args"))
                    results.append(coerce_value(r))
                except Exception as exc:  # noqa: BLE001
                    results.append({"error": f"{type(exc).__name__}: {exc}"})
            rows.append({"index": idx, "results": results})
            if limit and len(rows) >= limit:
                break
        return rows

    def _list_enums_locked(self, name: str | None) -> dict[str, dict[str, int]]:
        if self._enums is None:
            import pythoncom  # type: ignore
            factory = self._factory_locked()
            tlib, _idx = factory._oleobj_.GetTypeInfo().GetContainingTypeLib()
            enums: dict[str, dict[str, int]] = {}
            for i in range(tlib.GetTypeInfoCount()):
                nm = tlib.GetDocumentation(i)[0]
                info = tlib.GetTypeInfo(i)
                if info.GetTypeAttr().typekind != pythoncom.TKIND_ENUM:
                    continue
                members: dict[str, int] = {}
                ta = info.GetTypeAttr()
                for v in range(ta.cVars):
                    vd = info.GetVarDesc(v)
                    members[info.GetNames(vd.memid)[0]] = vd.value
                enums[nm] = members
            self._enums = enums
        if name is None:
            return self._enums
        return {name: self._enums.get(name, {})}

    # COM VARTYPE -> short readable name, for typelib member signatures.
    _VT = {2: "i2", 3: "i4", 4: "r4", 5: "r8", 7: "DATE", 8: "BSTR",
           9: "IDispatch", 11: "BOOL", 12: "VARIANT", 13: "IUnknown",
           16: "i1", 17: "ui1", 18: "ui2", 19: "ui4", 20: "i8", 21: "ui8",
           22: "INT_PTR", 23: "UINT_PTR", 24: "void", 26: "UINT_PTR",
           27: "INT_PTR"}

    def _typelib_members_locked(self, interface: str) -> dict:
        import pythoncom  # type: ignore
        factory = self._factory_locked()
        tlib, _idx = factory._oleobj_.GetTypeInfo().GetContainingTypeLib()
        for i in range(tlib.GetTypeInfoCount()):
            if tlib.GetDocumentation(i)[0] != interface:
                continue
            info = tlib.GetTypeInfo(i)
            ta = info.GetTypeAttr()
            skip = {"QueryInterface", "AddRef", "Release", "GetTypeInfoCount",
                    "GetTypeInfo", "GetIDsOfNames", "Invoke"}
            members = []
            for fnum in range(ta.cFuncs):
                fd = info.GetFuncDesc(fnum)
                names = info.GetNames(fd.memid)
                fname = names[0]
                if fname in skip:
                    continue
                pnames = names[1:]
                params = []
                try:
                    for ai, elem in enumerate(fd.args):
                        td = elem[0]
                        vt = td[0] if isinstance(td, tuple) else td
                        pn = pnames[ai] if ai < len(pnames) else f"p{ai}"
                        params.append(f"{self._VT.get(vt, f'vt{vt}')} {pn}")
                except Exception:
                    pass
                members.append({"name": fname,
                                "signature": f"{fname}({', '.join(params)})"})
            return {"interface": interface, "members": members}
        return {"interface": interface, "error": "not found in type library"}

    def _call_locked(self, path: str, args: list[Any] | None,
                     root: str) -> Any:
        target = self._root_target(root)
        # Navigate intermediate path segments (auto-calling getters, unwrapping
        # tuples) so a single path can step through objects, e.g.
        # ``getEwProjectCurrent.getName``; the last segment is the leaf to call.
        parts = path.split(".")
        target = self._navigate(target, parts[:-1])
        leaf = getattr(target, parts[-1])
        if args is not None:
            result = leaf(*args)
        elif callable(leaf):
            # Auto-call a zero-arg getter leaf so the documented recipe
            # ``call('getEwProjectCurrent.getName')`` returns the value instead
            # of a bound-method repr. This mirrors how _navigate auto-calls
            # callable getters on intermediate path segments. Pass an explicit
            # ``args`` list when a member needs parameters.
            result = leaf()
        else:
            result = leaf
        # Coerce here, on the apartment thread, so no COM object escapes.
        return coerce_value(result)

    # --- public API; marshalled onto the worker thread -------------------

    def factory(self) -> Any:
        return self._worker.submit(self._factory_locked)

    def connect_application(self, license_key: str | None = None) -> Any:
        return self._worker.submit(
            lambda: self._connect_application_locked(license_key))

    def get_api(self) -> Any:
        return self._worker.submit(self._get_api_locked)

    def disconnect(self) -> None:
        def _reset() -> None:
            self._factory = self._app = self._api = None
        self._worker.submit(_reset)

    def call(self, path: str, args: list[Any] | None = None,
             root: str = "application") -> Any:
        """Resolve a dotted attribute path against one of the COM roots and
        return a JSON-safe value.

        ``root`` selects which top-level object to walk from:

        * ``"application"`` — ``IEwApplicationX`` (default; needs licence key)
        * ``"api"`` — ``IEwAPIX``
        * ``"factory"`` — ``IEwInteropFactoryX`` (no licence required)

        Examples
        --------
        ``call("getApplicationVersion", [])`` reads the app version string.
        ``call("getEwAPI", [], root="factory")`` calls a factory method.
        """
        return self._worker.submit(lambda: self._call_locked(path, args, root))

    def call_ops(self, target_path: str | None, ops: list[dict],
                 root: str = "application") -> list[Any]:
        """Navigate to one COM object and run several members on that SAME
        retained object, in order, returning a JSON-safe result per op.

        Required for stateful sequences where the object must persist across
        calls — e.g. ``getEwProjectCurrent`` then ``setName`` + ``update`` +
        ``getName``. Doing those as separate ``call`` invocations fails because
        each re-navigates and releases a fresh wrapper, discarding the edit.

        ``target_path`` is a dotted path to the object (every segment is
        auto-called/unwrapped); pass ``None``/empty to operate on the root.
        ``ops`` is a list of ``{"member": str, "args": list | None}``.
        """
        return self._worker.submit(
            lambda: self._call_ops_locked(target_path, ops, root))

    def array_ops(self, array_path: str | None, ops: list[dict],
                  select: dict | None = None, root: str = "application",
                  limit: int | None = None,
                  array_args: list | None = None) -> list[dict]:
        """Enumerate a COM array and run members on each element.

        COM arrays (``VARIANT`` of ``IDispatch``) come back from a plain
        ``call`` as opaque handles that can't be indexed or invoked. This
        navigates to such an array, wraps each element, optionally filters by
        ``select`` (``{"member","args","equals"}``), and runs ``ops`` on each
        matching element — returning ``{"index", "results"}`` per element.

        ``array_args`` supplies arguments to the final array-producing call
        when it takes parameters (e.g. ``getProjectSymbolsFromFileID(fileID)``).
        """
        return self._worker.submit(
            lambda: self._array_ops_locked(array_path, select, ops, root, limit,
                                           array_args))

    def list_enums(self, name: str | None = None) -> dict[str, dict[str, int]]:
        """Read COM enum members from the installed type library.

        Enum values (e.g. ``EwProjectDataObjectType.kProjectDataTitleBlock``)
        are not in the scraped doc catalog; this reads them from the live
        typelib so ``call``/``call_ops`` integer arguments are knowable.
        """
        return self._worker.submit(lambda: self._list_enums_locked(name))

    def typelib_members(self, interface: str) -> dict:
        """List every member of an interface from the live type library.

        The scraped doc catalog can be incomplete (e.g. it omits
        ``IEwProjectFileX.setRevisionTranslatableTextAt``). The typelib is
        ground truth for what is actually callable, so this surfaces
        undocumented members with their parameter names/types.
        """
        return self._worker.submit(
            lambda: self._typelib_members_locked(interface))


_singleton = ElectricalApp()


def app() -> ElectricalApp:
    return _singleton


def installed_versions() -> list[tuple[int, int]]:
    """Probe HKLM\\SOFTWARE\\Classes for ``EwAPI.EwInteropFactoryX.<y>.<sp>``
    keys and return the parsed (year, service-pack) pairs sorted newest-first.

    Returns an empty list if the registry key is missing (SW Electrical not
    installed) or winreg is unavailable (non-Windows).
    """
    try:
        import winreg  # type: ignore
    except ImportError:
        return []
    out: list[tuple[int, int]] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\Classes") as root:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(root, i)
                except OSError:
                    break
                m = VERSIONED_PROGID_RE.match(name)
                if m:
                    out.append((int(m.group(1)), int(m.group(2))))
                i += 1
    except OSError:
        return []
    return sorted(set(out), reverse=True)


def detect_installed_version() -> str | None:
    """Return the major-year of the newest installed SW Electrical, as a
    string (e.g. ``"2025"``), or ``None`` if nothing is installed."""
    vs = installed_versions()
    return str(vs[0][0]) if vs else None
