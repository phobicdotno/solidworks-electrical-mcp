"""Structural test: every MCP tool must actually reach its workflow.

The tools in server.py are thin wrappers that forward to a workflow function.
Nothing checks that the two agree, and the two ways they can disagree both
fail silently - the tool works, it just quietly does the wrong thing:

  * a tool declares a parameter and then does not forward it. The client sees
    the option, sets it, and it is ignored.
  * a workflow grows a parameter that no tool parameter can reach, so the
    capability exists but nothing can ask for it. check_page_ink shipped like
    this: max_frame_mm and frame_thickness_mm decide whether a large device
    is mistaken for sheet frame, and the frame heuristic had already been
    wrong once (a 260 mm enclosure was being discarded), yet neither could be
    adjusted from the tool.

Neither is visible from the outside, so this checks the shapes directly.

Cases:
  A. every workflow function is reachable through some tool
  B. no tool declares a parameter it never forwards
  C. no workflow parameter is unreachable from its tool
  D. every tool has a docstring (it is the client-facing description)

Run directly:
    .venv/Scripts/python.exe tests/test_tool_surface.py
"""
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "solidworks_electrical_mcp"

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)
        print("FAIL:", msg)


def ok(msg: str, mark: int) -> None:
    if len(failures) == mark:
        print(msg)


# The tool surface is fed by more than one workflow module, each imported
# under its own alias in server.py. Auditing only workflows.py would let a
# whole module of tools drift unchecked.
MODULES = {"wf": "workflows.py", "pj": "projects.py",
           "lib": "library.py"}

sv_src = (SRC / "server.py").read_text(encoding="utf-8")
sv_tree = ast.parse(sv_src)

trees = {alias: ast.parse((SRC / fn).read_text(encoding="utf-8"))
         for alias, fn in MODULES.items()}
# alias -> {name: FunctionDef}, plus a flat view for the wrapper checks.
mod_funcs = {alias: {n.name: n for n in tree.body
                     if isinstance(n, ast.FunctionDef)}
             for alias, tree in trees.items()}


def arg_names(fn, skip=0):
    a = fn.args
    return [x.arg for x in (a.posonlyargs + a.args)][skip:] + \
           [x.arg for x in a.kwonlyargs]


def is_tool(fn):
    return any(getattr(d, "attr", getattr(d, "id", "")) == "tool"
               for d in fn.decorator_list)


tools = [n for n in sv_tree.body if isinstance(n, ast.FunctionDef)
         and is_tool(n)]
check(len(tools) > 30, f"expected a substantial tool surface, got {len(tools)}")

mark = len(failures)
# --- A: nothing in a workflow module is stranded --------------------------
public, stranded = [], []
for alias, funcs in mod_funcs.items():
    names = [n for n in funcs if not n.startswith("_")]
    public += names
    stranded += [f"{alias}.{n}" for n in names if f"{alias}.{n}" not in sv_src]
check(not stranded,
      f"A: these workflows are not reachable through any tool: {stranded}")
ok(f"A ok: all {len(public)} workflow functions are exposed", mark)

mark = len(failures)
# --- B and C: the wrapper and the workflow must agree ---------------------
# dry_run is exempt: some tools deliberately fix it rather than expose it.
EXEMPT = {"dry_run"}
dropped_any, unreachable_any = [], []
for tool in tools:
    target = target_alias = None
    for sub in ast.walk(tool):
        if (isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name)
                and sub.value.id in mod_funcs
                and sub.attr in mod_funcs[sub.value.id]):
            target_alias, target = sub.value.id, sub.attr
            break
    if target is None:
        continue                      # not a workflow wrapper
    declared = set(arg_names(tool))
    forwarded = {kw.arg for sub in ast.walk(tool)
                 if isinstance(sub, ast.Call)
                 for kw in sub.keywords if kw.arg}
    # skip app and client, which the COM layer supplies
    accepted = set(arg_names(mod_funcs[target_alias][target], skip=2))

    dropped = sorted(declared - forwarded - EXEMPT)
    if dropped:
        dropped_any.append(f"{tool.name} declares {dropped} and never "
                           f"forwards them")
    unreachable = sorted(accepted - declared - EXEMPT)
    if unreachable:
        unreachable_any.append(f"{tool.name} cannot reach "
                               f"{target_alias}.{target}{tuple(unreachable)}")

check(not dropped_any,
      "B: parameters declared but silently ignored:\n    "
      + "\n    ".join(dropped_any))
ok("B ok: every declared tool parameter is forwarded", mark)

mark = len(failures)
check(not unreachable_any,
      "C: workflow parameters no tool can reach:\n    "
      + "\n    ".join(unreachable_any))
ok("C ok: every workflow parameter is reachable from its tool", mark)

mark = len(failures)
# --- D: the docstring is the client-facing description --------------------
undocumented = [t.name for t in tools if not ast.get_docstring(t)]
check(not undocumented,
      f"D: these tools have no description for the client: {undocumented}")
ok(f"D ok: all {len(tools)} tools carry a description", mark)

print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("all tool-surface checks passed")
