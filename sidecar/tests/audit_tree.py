"""A static sweep of the sidecar tree, standard-library ast only.

Four things that compiled, imported, passed pyflakes and still cost him:

  ORPHANED METHODS   On 2026-10-13 `_memory_hogs` was pasted at column 0
                     inside `class Proactive`: every method after it became a
                     nested function, "'Proactive' object has no attribute
                     'run_checks'" 19,047 times, no disk/memory/break checks
                     for two weeks. A def whose first parameter is self/cls
                     and whose nearest scope is a FUNCTION is that paste.
  UNANCHORED TASKS   asyncio holds a task weakly; a bare create_task() can be
                     collected mid-await and take a lock with it (release 64,
                     TTS wedged for an evening). Every fire-and-forget goes
                     through events.spawn().
  DEAD DEFINITIONS   module-level names nothing in the tree refers to.
                     Reported, never fatal: registries reach things by string.
  CONFIG DRIFT       config.get(...) paths with no default anywhere, and
                     defaults nothing reads. The first is fatal.

The gate checks itself first (a planted module with both fatal shapes), then
the real tree. Offline, no imports of the code under audit.
Run: python tests/audit_tree.py
"""
from __future__ import annotations

import ast
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP = {".venv", "build", "dist", "tests", "__pycache__"}
CONFIG_NAMES = {"config", "cfg", "_cfg", "CONFIG"}


def source_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if not any(x in p.parts for x in SKIP))


def parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return None


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


# ---- (1) orphaned methods ---------------------------------------------------
def orphaned_methods(tree: ast.Module, where: str) -> list[str]:
    out = []

    def visit(node, scope):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = child.args.posonlyargs + child.args.args
                if args and args[0].arg in ("self", "cls") and isinstance(
                        scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append(f"{where}:{child.lineno}  '{child.name}(self...)' is nested in "
                               f"'{scope.name}()' - a method that fell out of its class")
                visit(child, child)
            elif isinstance(child, ast.ClassDef):
                visit(child, child)
            else:
                visit(child, scope)
    visit(tree, tree)
    return out


# ---- (2) unanchored tasks ---------------------------------------------------
def _is_task_call(node) -> bool:
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
    return name in ("create_task", "ensure_future")


def unanchored_tasks(tree: ast.Module, where: str) -> list[str]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and _is_task_call(node.value):
            out.append(f"{where}:{node.lineno}  task result discarded (collectable mid-await); "
                       f"use events.spawn()")
        elif isinstance(node, ast.Lambda):
            for inner in ast.walk(node.body):
                if _is_task_call(inner):
                    out.append(f"{where}:{inner.lineno}  create_task inside a lambda; the "
                               f"caller discards it")
    return out


# ---- (3) dead module-level definitions --------------------------------------
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def dead_definitions(trees: dict[str, ast.Module]) -> list[str]:
    referenced: set[str] = set()
    exported: set[str] = set()
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                referenced.add(node.id)
            elif isinstance(node, ast.Attribute):
                referenced.add(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                # registry / handler / skill tables name things by string:
                # every identifier-shaped word in any string counts as a use
                referenced.update(_WORD.findall(node.value))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    referenced.add((a.asname or a.name).split(".")[0])
                    if isinstance(node, ast.ImportFrom):
                        referenced.add(a.name)
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == "__all__"):
                for elt in getattr(node.value, "elts", []):
                    if isinstance(elt, ast.Constant):
                        exported.add(str(elt.value))
    out = []
    for where, tree in sorted(trees.items()):
        for node in tree.body:
            names: list[tuple[str, int]] = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.decorator_list:
                    continue        # @app.post, @tool, @property: reached by the framework
                names.append((node.name, node.lineno))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name):
                        names.append((t.id, node.lineno))
            for name, line in names:
                if name.startswith("__") or name in exported or name.startswith("test_"):
                    continue
                if name not in referenced:
                    out.append(f"{where}:{line}  '{name}' is defined and never referred to")
    return out


# ---- (4) config drift ---------------------------------------------------------
def defaults_tree(config_tree: ast.Module) -> dict:
    """The DEFAULTS dict in config.py as nested dicts of keys, without
    evaluating anything: a value that is not a dict literal is a leaf."""
    def walk(node):
        if not isinstance(node, ast.Dict):
            return None
        out = {}
        for k, v in zip(node.keys, node.values):
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                out[k.value] = walk(v)
        return out
    for node in config_tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == "DEFAULTS" for t in targets):
                return walk(node.value) or {}
    return {}


def _uses_config(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "config":
            if any(a.name in CONFIG_NAMES for a in node.names):
                return True
        if isinstance(node, ast.Import) and any(a.name == "config" for a in node.names):
            return True
    return False


def config_reads(tree: ast.Module, where: str) -> list[tuple[tuple[str, ...], bool, str]]:
    """(path, has_call_site_default, location) for every config.get("a", "b", ...)."""
    if not _uses_config(tree):
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        fn = node.func
        recv = fn.value
        recv_name = recv.id if isinstance(recv, ast.Name) else (
            recv.attr if isinstance(recv, ast.Attribute) else "")
        if fn.attr != "get" or recv_name not in CONFIG_NAMES or not node.args:
            continue
        path = []
        for a in node.args:
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                path.append(a.value)
            else:
                break           # a computed key: only the prefix can be judged
        if not path:
            continue
        has_default = any(k.arg == "default" for k in node.keywords)
        out.append((tuple(path), has_default, f"{where}:{node.lineno}"))
    return out


def _lookup(defaults: dict, path: tuple[str, ...]) -> bool:
    cur = defaults
    for key in path:
        if cur is None:
            return True         # walked into a leaf: its shape is runtime data
        if key not in cur:
            return False
        cur = cur[key]
    return True


def _leaves(defaults: dict, prefix=()) -> list[tuple[str, ...]]:
    out = []
    for k, v in defaults.items():
        if isinstance(v, dict) and v:
            out.extend(_leaves(v, prefix + (k,)))
        else:
            out.append(prefix + (k,))
    return out


def config_drift(trees: dict[str, ast.Module], config_rel: str) -> tuple[list[str], list[str]]:
    """(fatal, report): reads with no default anywhere; defaults never read."""
    cfg = trees.get(config_rel)
    if cfg is None:
        return [f"{config_rel} not found"], []
    defaults = defaults_tree(cfg)
    reads = []
    for where, tree in trees.items():
        if where == config_rel:
            continue
        reads.extend(config_reads(tree, where))
    fatal, read_paths = [], set()
    for path, has_default, loc in reads:
        read_paths.add(path)
        if not _lookup(defaults, path) and not has_default:
            fatal.append(f"{loc}  config.get{path!r} has no default in DEFAULTS and none at "
                         f"the call: it reads None on a fresh install")
    report = []
    for leaf in _leaves(defaults):
        covered = any(leaf[:len(p)] == p for p in read_paths)
        if not covered:
            report.append(f"DEFAULTS{''.join(f'[{k!r}]' for k in leaf)} is set and never read")
    return fatal, sorted(report)


# ---- the sweep --------------------------------------------------------------
def audit(root: Path, config_rel: str = "config.py") -> dict[str, list[str]]:
    trees: dict[str, ast.Module] = {}
    syntax = []
    for path in source_files(root):
        tree = parse(path)
        if tree is None:
            syntax.append(f"{_rel(path, root)}: does not parse")
            continue
        trees[_rel(path, root)] = tree
    orphans, tasks = [], []
    for where, tree in trees.items():
        orphans.extend(orphaned_methods(tree, where))
        if Path(where).name != "events.py":      # spawn() is the one allowed create_task
            tasks.extend(unanchored_tasks(tree, where))
    drift_fatal, drift_report = config_drift(trees, config_rel)
    return {
        "SYNTAX": syntax,
        "ORPHANED METHODS": orphans,
        "UNANCHORED TASKS": tasks,
        "CONFIG READ WITHOUT DEFAULT": drift_fatal,
        "DEAD DEFINITIONS (report)": dead_definitions(trees),
        "DEFAULTS NEVER READ (report)": drift_report,
    }


FATAL = ("SYNTAX", "ORPHANED METHODS", "UNANCHORED TASKS", "CONFIG READ WITHOUT DEFAULT")

PLANTED = '''
import asyncio
from config import config

class Proactive:
    def rules(self):
        return []

def _memory_hogs():
    return []

    def run_checks(self):          # pasted at column 0: now nested in _memory_hogs
        return self.rules()

async def loop():
    asyncio.create_task(loop())    # bare: nobody holds it
    cb = lambda: asyncio.ensure_future(loop())
    held = asyncio.create_task(loop())
    await held

def ok():
    return config.get("present", "key"), config.get("absent", "key", default=1)

def bad():
    return config.get("absent", "key")
'''

PLANTED_CONFIG = '''
DEFAULTS = {"present": {"key": 1, "unread": 2}}
'''


def self_test() -> list[str]:
    """The gate on a module written to trip it. Returns what is wrong with the gate."""
    problems = []
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "planted.py").write_text(PLANTED.lstrip("\n"), encoding="utf-8")
        (root / "config.py").write_text(PLANTED_CONFIG.lstrip("\n"), encoding="utf-8")
        found = audit(root)
        orph = found["ORPHANED METHODS"]
        if not (len(orph) == 1 and "run_checks" in orph[0] and "_memory_hogs" in orph[0]):
            problems.append(f"orphaned method not found as expected: {orph}")
        tasks = found["UNANCHORED TASKS"]
        if len(tasks) != 2 or not any(":15 " in t for t in tasks) or not any("lambda" in t for t in tasks):
            problems.append(f"bare and lambda create_task not both found, held one ignored: {tasks}")
        drift = found["CONFIG READ WITHOUT DEFAULT"]
        if len(drift) != 1 or "('absent', 'key')" not in drift[0] or ":24 " not in drift[0]:
            problems.append(f"config read without default: {drift}")
        unread = found["DEFAULTS NEVER READ (report)"]
        if len(unread) != 1 or "'unread'" not in unread[0]:
            problems.append(f"never-read default: {unread}")
        dead = found["DEAD DEFINITIONS (report)"]
        if not any("'bad'" in x for x in dead) or any("'loop'" in x or "'rules'" in x for x in dead):
            problems.append(f"dead definitions: {dead}")
        if found["SYNTAX"]:
            problems.append(f"syntax: {found['SYNTAX']}")
    return problems


def main() -> int:
    print("-- the gate on a planted module --")
    problems = self_test()
    for p in problems:
        print(f"  FAIL  {p}")
    if not problems:
        print("  PASS  orphaned method, bare and lambda create_task, config read without default,"
              " unread default, dead def: all found; held task and read defaults: not")

    found = audit(ROOT)
    fatal = 0
    for title, findings in found.items():
        print(f"\n-- {title} ({len(findings)}) --")
        for f in findings:
            print(f"  {'FAIL' if title in FATAL else 'note'}  {f}")
        if not findings:
            print("  clean")
        if title in FATAL:
            fatal += len(findings)
    n = len(source_files(ROOT))
    print(f"\n{n} files; {fatal} fatal finding(s)")
    print("ALL PASS" if not fatal and not problems else f"{fatal + len(problems)} FAILURES")
    return 0 if not fatal and not problems else 1


if __name__ == "__main__":
    sys.exit(main())
