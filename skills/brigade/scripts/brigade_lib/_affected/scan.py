import ast
import posixpath
import re
import subprocess
from pathlib import Path
from brigade_lib import generated
from brigade_lib.shared import BrigadeError

def _norm(raw):
    text = str(raw).strip().replace("\\", "/")
    if not text:
        return ""
    made = posixpath.normpath(text).lstrip("/")
    if made == "":
        return ""
    return made


def _roots(paths):
    """The parent directories of these repository paths in order with empties dropped and repeats removed."""
    seen = []
    for raw in paths:
        parent = posixpath.dirname(_norm(raw))
        if not parent or parent in (".", "/"):
            continue
        if parent not in seen:
            seen.append(parent)
    return tuple(seen)


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


def _expandable(edge):
    """Whether the edge's resolution may add a private package's files, judged by the imported name, or for a `*` or plain `import` edge by the last segment of its module."""
    level, module, symbol = edge
    last = symbol if symbol and symbol != "*" else (module.split(".")[-1] if module else "")
    return not last.startswith("test_")


def _private_of(norm):
    """The sibling private package directory of a facade path <pkg>/<name>.py, empty when the path cannot have one."""
    if not norm.endswith(".py"):
        return ""
    pkg, name = posixpath.split(norm)
    if not pkg or pkg in (".", "/"):
        return ""
    stem = posixpath.splitext(name)[0]
    if stem in ("__init__", "_") or stem.startswith("test_"):
        return ""
    return posixpath.join(pkg, "_" + stem)


def _imports_of(rel, root):
    """The import edges of the readable Python file at rel, empty when it cannot be read as UTF-8 or raises SyntaxError."""
    full = Path(str(root)) / rel
    try:
        text = full.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ()
    try:
        return tuple(sorted(_import_targets(ast.parse(text, rel))))
    except SyntaxError:
        return ()


def _resolves(edge, cands, loader_path, root, roots, depth):
    """The candidate files that exist, plus when depth is zero and the edge may expand, the files under each resolved facade's private package which the facade or that package's __init__.py imports, and the files a resolved private __init__.py imports under its own directory when the loader is that init's facade."""
    files = _found_files(root, cands)
    if depth or not _expandable(edge):
        return files
    out = list(files)
    for got in files:
        out.extend(_facade_private(got, root, roots))
        out.extend(_init_private(got, loader_path, root, roots))
    return tuple(out)


def _init_private(norm, loader_path, root, roots):
    """The files under a private package directory which its __init__.py imports, when norm is that __init__.py, the package's facade file exists, and the loader is that facade."""
    if not norm.endswith("/__init__.py"):
        return ()
    private = posixpath.dirname(norm)
    name = posixpath.basename(private)
    if len(name) < 2 or not name.startswith("_"):
        return ()
    facade = posixpath.join(posixpath.dirname(private), name[1:] + ".py")
    if _norm(loader_path) != facade or not _found_files(root, [facade]):
        return ()
    return _private_edges(norm, private, root, roots)


def _facade_private(norm, root, roots):
    """The files under the private package directory of facade norm that norm imports directly or that package's __init__.py imports, in sorted order, empty when norm has no such package."""
    private = _private_of(norm)
    if not private:
        return ()
    try:
        inside = (Path(str(root)) / private).is_dir()
    except OSError:
        inside = False
    if not inside:
        return ()
    found = _private_edges(norm, private, root, roots)
    for init in _found_files(root, [private + "/__init__.py"]):
        found |= _private_edges(init, private, root, roots)
    return tuple(sorted(found))


def _private_edges(loader, private, root, roots):
    """The resolutions of loader's imports that stay under the private directory."""
    prefix = private + "/"
    found = set()
    for edge in _imports_of(loader, root):
        for got in _resolve_import(edge, loader, root, roots, 1):
            if got.startswith(prefix):
                found.add(got)
    return found


def _resolve_import(edge, loader_path, root, roots, depth=0):
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
        return _resolves(edge, cands, loader_path, root, roots, depth)
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
        for prefix in roots:
            cands.append(prefix + "/" + tail)
    return _resolves(edge, cands, loader_path, root, roots, depth)


def _tree_files(root):
    base = Path(str(root))
    found = []
    for path in sorted(base.glob("tests/**/*.py")):
        try:
            if path.is_file():
                found.append(path)
        except OSError:
            continue
    return found


def _tree_name(path, root):
    try:
        rel = Path(str(path)).resolve().relative_to(Path(str(root)).resolve()).as_posix()
    except ValueError:
        return None
    if not rel.startswith("tests/") or not rel.endswith(".py"):
        return None
    return rel[:-3].replace("/", ".")


def _is_test_name(name):
    if not isinstance(name, str) or not name.startswith("tests."):
        return False
    stem = name.replace(".", "/")
    if "/test_" in "/" + stem:
        return True
    return stem.split("/")[-1].startswith("test_")


def _module_name(path, root):
    name = _tree_name(path, root)
    if name is None or not _is_test_name(name):
        return None
    return name


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


def _canonicals(norm, gen_map):
    try:
        twins = generated.twins(gen_map, norm)
    except Exception:
        return [norm]
    sources = sorted(t for t in twins if t.startswith("t3/"))
    others = sorted(t for t in twins if not t.startswith("t3/"))
    return sources + others


PIN_CHARS = 40


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


def _read_prefix(node, singles):
    """The directory a read's path expression starts with when the whole path cannot resolve."""
    operands = []
    cur = node
    while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div):
        operands.append(cur.right)
        cur = cur.left
    operands.append(cur)
    spine = ""
    resolved = False
    for operand in reversed(operands):
        got = _resolve_expr(operand, singles)
        if got is None:
            if not resolved:
                return ""
            break
        resolved = True
        if got:
            spine = f"{spine}/{got}" if spine else got
    else:
        return ""
    if not spine or spine.startswith("/") or ".." in spine.split("/") or "/" not in spine:
        return ""
    return _norm(spine)


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
            else:
                prefix = _read_prefix(recv, singles)
                if prefix:
                    found.add(prefix + "/*")
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


def _loads_of(mod, singles):
    found = set(_load_refs(mod, singles))
    for value in singles.values():
        if value and value.endswith(".py") and _looks_repo(value):
            found.add(value)
    return found
