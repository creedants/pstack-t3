import ast
import fnmatch
import posixpath
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from brigade_lib import seam
from brigade_lib.layout import _layout, _twins
from brigade_lib.shared import BrigadeError
from brigade_lib.store import HELD_LOCKS


ALWAYS = "tests.test_pstack_t3"
WORKFLOW_MODULE = "tests.test_run_tests"
PIN_CHARS = 40
_ENTRYPOINTS = ("t3/added/brigade/scripts/brigade.py", "t3/added/landing/scripts/land.py")
_HELPERS = ("tests/brigade/support.py", "tests/landing/support.py")
_PY310 = "/home/marcus/.local/share/mise/installs/python/3.10.22/bin/python3.10"


@dataclass(frozen=True)
class _Modules:
    names: tuple


@dataclass(frozen=True)
class _Full:
    unmapped: tuple


@dataclass(frozen=True)
class _Index:
    table: dict


def _norm(raw):
    text = str(raw).strip().replace("\\", "/")
    if not text:
        return ""
    made = posixpath.normpath(text).lstrip("/")
    if made == "":
        return ""
    return made


def _tracked(root):
    try:
        result = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True,
                                encoding="utf-8", errors="surrogateescape")
    except OSError as error:
        raise BrigadeError(f"git ls-files failed: {error}") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise BrigadeError(f"git ls-files failed: {detail or result.returncode}")
    return frozenset(name for name in result.stdout.split("\0") if name)


def _diff_names(root, spec):
    try:
        result = subprocess.run(["git", "-C", str(root), "diff", "--name-only", spec], capture_output=True,
                                encoding="utf-8", errors="surrogateescape")
    except OSError as error:
        raise BrigadeError(f"git diff failed: {error}") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise BrigadeError(f"git diff failed: {detail or result.returncode}")
    return [line for line in result.stdout.splitlines() if line.strip()]


def _expand_one(root, norm, tracked):
    if norm in ("", ".", "/"):
        return (), (norm or ".",)
    if norm == ".." or norm.startswith("../"):
        return (), (norm,)
    path = Path(str(root)) / norm
    try:
        is_dir = path.is_dir()
        is_file = path.is_file()
    except OSError:
        return (), (norm,)
    if not is_dir and not is_file:
        return (), (norm,)
    if is_file:
        if norm not in tracked:
            return (), (norm,)
        return (norm,), ()
    prefix = norm + "/"
    found = sorted(name for name in tracked if name.startswith(prefix))
    if not found:
        return (), (norm,)
    return tuple(found), ()


def _resolve_expr(node, singles):
    if isinstance(node, ast.Name):
        if node.id == "ROOT":
            return ""
        return singles.get(node.id)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        text = node.value.strip().replace("\\", "/")
        if not text or text.startswith("/") or ".." in text.split("/"):
            return None
        if re.search(r"\s", text):
            return None
        made = posixpath.normpath(text).lstrip("/")
        if made in ("", ".", "..") or made.startswith("../"):
            return None
        return made
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _resolve_expr(node.left, singles)
        if left is None:
            return None
        if not isinstance(node.right, ast.Constant) or not isinstance(node.right.value, str):
            return None
        tail = node.right.value.strip().replace("\\", "/")
        if not tail or tail.startswith("/") or ".." in tail.split("/"):
            return None
        if re.search(r"\s", tail):
            return None
        tail = posixpath.normpath(tail).lstrip("/")
        if tail in ("", ".", "..") or tail.startswith("../"):
            return None
        if left == "":
            return tail
        return left + "/" + tail
    if isinstance(node, ast.Call):
        func = node.func
        name = ""
        if isinstance(func, ast.Name):
            name = func.id
        if name in ("str", "Path"):
            if len(node.args) == 1 and not node.keywords:
                return _resolve_expr(node.args[0], singles)
        return None
    return None


def _singles(mod):
    counts = {}
    for sub in mod.body:
        if isinstance(sub, ast.Assign) and len(sub.targets) == 1 and isinstance(sub.targets[0], ast.Name):
            counts[sub.targets[0].id] = counts.get(sub.targets[0].id, 0) + 1
    found = {}
    for sub in mod.body:
        if isinstance(sub, ast.Assign) and len(sub.targets) == 1 and isinstance(sub.targets[0], ast.Name):
            name = sub.targets[0].id
            if counts.get(name) != 1:
                continue
            if name == "ROOT":
                found[name] = ""
                continue
            got = _resolve_expr(sub.value, found)
            if got is not None:
                found[name] = got
    return found


def _looks_repo(value):
    if not value or value.startswith("/") or ".." in value.split("/"):
        return False
    if re.search(r"\s", value):
        return False
    if value in ("README.md", "CHANGELOG.md"):
        return True
    return "/" in value


def _load_refs(mod, singles):
    found = set()
    for sub in ast.walk(mod):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        is_run_path = (isinstance(func, ast.Attribute) and func.attr == "run_path") or (
            isinstance(func, ast.Name) and func.id == "run_path")
        is_reader = isinstance(func, ast.Attribute) and func.attr in ("read_text", "read_bytes")
        is_proc = (isinstance(func, ast.Attribute) and func.attr in ("run", "Popen", "call", "check_call", "check_output")
                   and isinstance(func.value, ast.Name) and func.value.id == "subprocess") or (
            isinstance(func, ast.Name) and func.id == "Popen")
        if is_run_path:
            if not sub.args:
                continue
            got = _resolve_expr(sub.args[0], singles)
            if got and _looks_repo(got):
                found.add(got)
        elif is_reader:
            recv = func.value
            got = _resolve_expr(recv, singles)
            if got is None and isinstance(recv, ast.Call):
                inner = recv.args[0] if len(recv.args) == 1 and not recv.keywords else None
                got = _resolve_expr(inner, singles) if inner is not None else None
            if got and _looks_repo(got):
                found.add(got)
        elif is_proc:
            if not sub.args:
                continue
            first = sub.args[0]
            if isinstance(first, (ast.List, ast.Tuple)):
                for one in first.elts:
                    got = _resolve_expr(one, singles)
                    if got and _looks_repo(got):
                        found.add(got)
    return found


def _pin_literals(mod):
    found = set()
    for sub in ast.walk(mod):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and len(sub.value) >= PIN_CHARS:
            if "\n" not in sub.value:
                found.add(sub.value)
    return found


def _import_targets(mod):
    found = set()
    for sub in ast.walk(mod):
        if isinstance(sub, ast.Import):
            for one in sub.names:
                found.add((0, one.name, ""))
        elif isinstance(sub, ast.ImportFrom):
            base = sub.module or ""
            level = sub.level or 0
            for one in sub.names:
                if one.name == "*":
                    found.add((level, base, "*"))
                else:
                    found.add((level, base, one.name))
    return found


def _found_files(root, cands):
    seen = []
    for cand in cands:
        if not cand or cand.startswith("/") or ".." in cand.split("/"):
            continue
        full = Path(str(root)) / cand
        try:
            if full.is_file():
                if cand not in seen:
                    seen.append(cand)
        except OSError:
            continue
    return tuple(seen)


def _resolve_import(edge, loader_path, root):
    if isinstance(edge, str):
        parts = edge.split(".")
        cands = []
        if loader_path:
            parent = posixpath.dirname(_norm(loader_path))
            for depth in range(len(parts)):
                tail = "/".join(parts[depth:]) + ".py"
                if parent:
                    cands.append(parent + "/" + tail)
                cands.append("/".join(parts[depth:]) + ".py")
            base_parts = parts[:-1]
            if base_parts:
                if parent:
                    cands.append(parent + "/" + "/".join(base_parts) + ".py")
                cands.append("/".join(base_parts) + ".py")
        else:
            cands.append("/".join(parts) + ".py")
        cands.append("/".join(parts) + "/__init__.py")
        for tail in ("/".join(parts) + ".py", "/".join(parts) + "/__init__.py"):
            for prefix in ("scripts", "t3/scripts", "t3/added/brigade/scripts/brigade_lib"):
                cands.append(prefix + "/" + tail)
        return _found_files(root, cands)
    level, module, symbol = edge
    if symbol == "*":
        symbol = ""
    module_parts = module.split(".") if module else []
    if level and level > 0:
        parent = posixpath.dirname(_norm(loader_path) if loader_path else "")
        parts = parent.split("/") if parent else []
        if level - 1 > len(parts):
            return ()
        base_dir = "/".join(parts[:len(parts) - (level - 1)]) if level - 1 else parent
        cands = []
        if module_parts:
            mod_rel = "/".join(module_parts)
            if base_dir:
                cands.append(base_dir + "/" + mod_rel + ".py")
                cands.append(base_dir + "/" + mod_rel + "/__init__.py")
            else:
                cands.append(mod_rel + ".py")
                cands.append(mod_rel + "/__init__.py")
        if symbol:
            sym_rel = "/".join(module_parts + [symbol]) if module_parts else symbol
            if base_dir:
                cands.append(base_dir + "/" + sym_rel + ".py")
                cands.append(base_dir + "/" + sym_rel + "/__init__.py")
            else:
                cands.append(sym_rel + ".py")
                cands.append(sym_rel + "/__init__.py")
        return _found_files(root, cands)
    parent = posixpath.dirname(_norm(loader_path) if loader_path else "")
    cands = []
    rels = []
    if module_parts:
        mod_rel = "/".join(module_parts)
        rels.append(mod_rel + ".py")
        rels.append(mod_rel + "/__init__.py")
    if symbol:
        sym_rel = "/".join(module_parts + [symbol]) if module_parts else symbol
        rels.append(sym_rel + ".py")
        rels.append(sym_rel + "/__init__.py")
    if not rels and module_parts:
        mod_rel = "/".join(module_parts)
        rels = [mod_rel + ".py", mod_rel + "/__init__.py"]
    for tail in rels:
        if parent:
            cands.append(parent + "/" + tail)
        cands.append(tail)
    for tail in rels:
        for prefix in ("scripts", "t3/scripts", "t3/added/brigade/scripts/brigade_lib"):
            cands.append(prefix + "/" + tail)
    return _found_files(root, cands)


def _one_hop(norm, base, lay):
    if not norm or not norm.endswith(".py"):
        return ()
    full = base / norm
    try:
        if not full.is_file():
            return ()
        text = full.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ()
    try:
        lmod = ast.parse(text, str(full))
    except SyntaxError:
        return ()
    found = set()
    for target in _import_targets(lmod):
        for got in _resolve_import(target, norm, base):
            for canon in _canonicals(_norm(got), lay):
                found.add(canon)
    return tuple(sorted(found))


def _loads_of(mod, singles):
    found = set(_load_refs(mod, singles))
    for value in singles.values():
        if value and value.endswith(".py") and _looks_repo(value):
            found.add(value)
    return found


def _canonicals(norm, lay):
    try:
        twins = _twins(norm, lay)
    except Exception:
        return [norm]
    sources = sorted(t for t in twins if t.startswith("t3/"))
    if sources:
        return sources
    return [norm]


def _module_name(path, root):
    try:
        rel = Path(str(path)).resolve().relative_to(Path(str(root)).resolve()).as_posix()
    except ValueError:
        return None
    if not rel.startswith("tests/") or not rel.endswith(".py"):
        return None
    stem = rel[:-3]
    if "/test_" not in "/" + stem and not stem.split("/")[-1].startswith("test_"):
        return None
    return stem.replace("/", ".")


def _test_files(root):
    base = Path(str(root))
    found = []
    for path in sorted(base.glob("tests/**/test_*.py")):
        try:
            if path.is_file():
                found.append(path)
        except OSError:
            continue
    return found


def _index(root):
    base = Path(str(root))
    try:
        lay = _layout(base)
    except Exception:
        lay = None
    files = _test_files(base)
    helper_entry = set()
    for helper_rel in _HELPERS:
        try:
            helper_text = (base / helper_rel).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        try:
            helper_mod = ast.parse(helper_text, helper_rel)
        except SyntaxError:
            continue
        helper_singles = _singles(helper_mod)
        for ref in _loads_of(helper_mod, helper_singles):
            norm = _norm(ref)
            if norm in _ENTRYPOINTS:
                helper_entry.add(norm)
    loads = {}
    pins = {}
    direct = {}
    mods = {}
    for path in files:
        name = _module_name(path, base)
        if not name:
            continue
        mods[str(path)] = name
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        try:
            mod = ast.parse(text, str(path))
        except SyntaxError:
            continue
        singles = _singles(mod)
        refs = _loads_of(mod, singles)
        if refs:
            loads[name] = refs
        lits = _pin_literals(mod)
        if lits:
            pins[name] = lits
        rel = Path(str(path)).resolve().relative_to(base.resolve()).as_posix()
        targets = _import_targets(mod)
        resolved = set()
        for target in targets:
            for got in _resolve_import(target, rel, base):
                resolved.add(got)
        if resolved:
            direct[name] = resolved
    combined = {}
    for name in set(list(loads) + list(direct)):
        own = set(loads.get(name, ()))
        helpers_hit = False
        for ref in direct.get(name, ()):
            if _norm(ref) in _HELPERS:
                helpers_hit = True
                break
        if helpers_hit:
            own = own | helper_entry
        if own:
            combined[name] = own
    table = {}
    hops = {}
    for name, refs in combined.items():
        for ref in refs:
            norm = _norm(ref)
            if norm and _looks_repo(norm):
                for canon in _canonicals(norm, lay):
                    table.setdefault(canon, set()).add(name)
            if not norm or not norm.endswith(".py"):
                continue
            if norm not in hops:
                hops[norm] = _one_hop(norm, base, lay)
            for canon in hops[norm]:
                table.setdefault(canon, set()).add(name)
    for name, refs in direct.items():
        for ref in refs:
            for canon in _canonicals(_norm(ref), lay):
                table.setdefault(canon, set()).add(name)
    if pins:
        outside = []
        for path in sorted(base.rglob("*")):
            try:
                rel = path.resolve().relative_to(base.resolve()).as_posix()
            except ValueError:
                continue
            if rel.startswith("tests/") or rel.startswith("vendor/") or ".git/" in rel or "__pycache__" in rel:
                continue
            try:
                if not path.is_file():
                    continue
            except OSError:
                continue
            outside.append((rel, path))
        contents = {}
        for rel, path in outside:
            try:
                if path.suffix not in (".py", ".md", ".txt", ".json", ".yml", ".yaml", ".toml", ".cfg"):
                    continue
                text = path.read_text(encoding="utf-8", errors="strict")
            except (OSError, UnicodeError, ValueError):
                continue
            contents[rel] = text
        lits_to_tests = {}
        for name, lits in pins.items():
            for lit in lits:
                lits_to_tests.setdefault(lit, set()).add(name)
        for lit, tests in lits_to_tests.items():
            holders = [rel for rel, text in contents.items() if lit in text]
            if not holders:
                continue
            classes = set()
            for rel in holders:
                twins = _twins(_norm(rel), lay)
                t3s = tuple(sorted(t for t in twins if t.startswith("t3/")))
                key = t3s if t3s else (rel,)
                classes.add(key)
            if len(classes) != 1:
                continue
            key = next(iter(classes))
            if set(key) & frozenset(_ENTRYPOINTS):
                continue
            if key[0].startswith("t3/"):
                for canon in key:
                    table.setdefault(canon, set()).update(tests)
            else:
                table.setdefault(key[0], set()).update(tests)
    ordered = {key: tuple(sorted(value)) for key, value in table.items()}
    return _Index(ordered)


def _select(root, paths):
    base = Path(str(root))
    if isinstance(paths, str):
        raws = [part for part in paths.split(",") if part.strip() != ""]
    else:
        raws = []
        for one in paths:
            for part in str(one).split(","):
                if part.strip() != "":
                    raws.append(part)
    norms = [_norm(raw) for raw in raws]
    norms = [n if n != "" else "." for n in norms]
    if not norms:
        return _Modules((ALWAYS,))
    tracked = _tracked(base)
    try:
        lay = _layout(base)
    except Exception:
        lay = None
    index = _index(base)
    expanded = []
    unmapped = []
    for norm in norms:
        files, bad = _expand_one(base, norm, tracked)
        unmapped.extend(bad)
        expanded.extend(files)
    if not expanded and unmapped:
        return _Full(tuple(sorted(set(unmapped))))
    if not expanded:
        return _Modules((ALWAYS,))
    modules = set()
    missing = set()
    for norm in expanded:
        if norm.startswith(".github/workflows/"):
            modules.add(WORKFLOW_MODULE)
            continue
        if norm.startswith("changes/"):
            continue
        if norm.startswith("tests/") and norm.endswith(".py"):
            stem = norm[:-3]
            if "/test_" in "/" + stem or PurePosixPath(norm).name.startswith("test_"):
                modules.add(stem.replace("/", "."))
                for canon in _canonicals(norm, lay):
                    hit = index.table.get(canon)
                    if hit:
                        modules.update(hit)
                continue
        found = None
        for canon in _canonicals(norm, lay):
            hit = index.table.get(canon)
            if hit:
                found = hit
                break
        if found:
            modules.update(found)
        else:
            missing.add(norm)
    if missing or unmapped:
        return _Full(tuple(sorted(set(list(missing) + unmapped))))
    modules.add(ALWAYS)
    return _Modules(tuple(sorted(modules)))


def _paragraph(result, land):
    land_text = str(land)
    if isinstance(result, _Full):
        names = ", ".join(f"`{name}`" for name in result.unmapped)
        return (f"No affected mapping for {names}. "
                f"Run the full suite in the foreground as `python3 {land_text} slot --slots 2 -- python3 scripts/run_tests.py -j 8`. "
                f"Run the same suite on Python 3.10 in the foreground as `python3 {land_text} slot --slots 2 -- {_PY310} scripts/run_tests.py -j 8`. "
                f"Run `python3 {land_text} slot -- python3 scripts/build.py` and leave no regeneration diff. "
                f"Run `python3 {land_text} slot -- python3 scripts/check.py`. "
                f"Keep each build and test in a foreground slot.")
    names = ", ".join(f"`{name}`" for name in result.names)
    joined = " ".join(result.names)
    return (f"Affected test modules are {names}. "
            f"Run them in the foreground as `python3 {land_text} slot -- python3 -m unittest {joined}`. "
            f"Run the same modules on Python 3.10 in the foreground as `python3 {land_text} slot -- {_PY310} -m unittest {joined}`. "
            f"Run `python3 {land_text} slot -- python3 scripts/build.py` and leave no regeneration diff. "
            f"Run `python3 {land_text} slot -- python3 scripts/check.py`. "
            f"Keep each build and test in a foreground slot.")


def verify_text(root, paths, land):
    if HELD_LOCKS:
        raise BrigadeError(f"affected may run git; release {HELD_LOCKS[0]}'s store lock before scanning")
    if isinstance(paths, str):
        items = [part for part in paths.split(",") if part.strip() != ""]
    else:
        items = []
        for one in paths:
            for part in str(one).split(","):
                if part.strip() != "":
                    items.append(part)
    result = _select(root, items)
    return _paragraph(result, land)
