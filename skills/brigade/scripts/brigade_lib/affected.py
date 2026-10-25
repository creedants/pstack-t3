"""For AFFECTED_KEY, DEFAULT_SUITE, Layout, layout, PIN_CHARS, _diff_names, and _found_files, this module re-exports them from _affected. Patching one of those names here need not reroute bare-name calls inside _affected."""
import ast
import posixpath
from pathlib import Path, PurePosixPath
from brigade_lib import generated
from brigade_lib._affected.model import AFFECTED_KEY, DEFAULT_SUITE, Layout, _Closed, _Full, _Modules, _Open, layout
from brigade_lib._affected.report import _fallback, _paragraph, _settings
from brigade_lib._affected.scan import PIN_CHARS, _canonicals, _diff_names, _expand_one, _expandable, _found_files, _import_targets, _imports_of, _is_test_name, _loads_of, _looks_repo, _norm, _pin_literals, _private_of, _singles, _tracked, _tree_files, _tree_name
from brigade_lib.shared import BrigadeError
from brigade_lib.store import HELD_LOCKS


def _private_edges(loader, private, root, roots):
    prefix = private + "/"
    found = set()
    for edge in _imports_of(loader, root):
        try:
            resolved = _resolve_import(edge, loader, root, roots, 1)
        except TypeError:
            resolved = _resolve_import(edge, loader, root, roots)
        for got in resolved:
            if got.startswith(prefix):
                found.add(got)
    return found


def _init_private(norm, loader_path, root, roots):
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


def _resolves(edge, cands, loader_path, root, roots, depth):
    files = _found_files(root, cands)
    if depth or not _expandable(edge):
        return files
    out = list(files)
    for got in files:
        out.extend(_facade_private(got, root, roots))
        out.extend(_init_private(got, loader_path, root, roots))
    return tuple(out)


def _resolve_import(edge, loader_path, root, roots, depth=0, ancestors=True):
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
    if ancestors:
        cur = parent
        while cur and cur not in (".", "/"):
            cur = posixpath.dirname(cur)
            if not cur or cur in (".", "/"):
                break
            for tail in rels:
                cands.append(cur + "/" + tail)
    for tail in rels:
        for prefix in roots:
            cands.append(prefix + "/" + tail)
    return _resolves(edge, cands, loader_path, root, roots, depth)


def _resolve_proven(edge, loader_path, root, roots, depth=0):
    try:
        return _resolve_import(edge, loader_path, root, roots, depth, ancestors=False)
    except TypeError:
        try:
            return _resolve_import(edge, loader_path, root, roots, depth)
        except TypeError:
            return _resolve_import(edge, loader_path, root, roots)

def _one_hop(norm, base, gen_map, roots):
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
        for got in _resolve_import(target, norm, base, roots):
            for canon in _canonicals(_norm(got), gen_map):
                found.add(canon)
    for got in _facade_private(norm, base, roots):
        for canon in _canonicals(_norm(got), gen_map):
            found.add(canon)
    return tuple(sorted(found))


def _helper_entries(base, layout):
    entry_set = frozenset(_norm(one) for one in layout.entry_points)
    entries = {}
    for helper_rel in layout.helpers:
        norm = _norm(helper_rel)
        if not norm:
            entries[norm] = None
            continue
        try:
            text = (base / norm).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            entries[norm] = None
            continue
        try:
            mod = ast.parse(text, norm)
        except SyntaxError:
            entries[norm] = None
            continue
        singles = _singles(mod)
        entries[norm] = frozenset(_norm(ref) for ref in _loads_of(mod, singles) if _norm(ref) in entry_set)
    return entries


def _test_edges(targets, rel, base, roots):
    edges = set()
    importer_test = False
    if rel.endswith(".py"):
        stem = rel[:-3]
        if "/test_" in "/" + stem or stem.split("/")[-1].startswith("test_"):
            importer_test = True
    for target in targets:
        level, module, symbol = target
        if symbol and symbol != "*":
            last = symbol
        elif module:
            last = module.split(".")[-1]
        else:
            last = ""
        shaped = last.startswith("test_")
        resolved = _resolve_import(target, rel, base, roots)
        if shaped:
            proven = _resolve_proven(target, rel, base, roots)
            if len(proven) == 0 and len(resolved) > 0:
                if module and symbol and symbol != "*":
                    label = module + "." + symbol
                elif module:
                    label = module
                else:
                    label = symbol
                return frozenset(), "unproven import " + label + " in " + rel
        if shaped and len(resolved) != 1 and importer_test:
            if module and symbol and symbol != "*":
                label = module + "." + symbol
            elif module:
                label = module
            else:
                label = symbol
            return frozenset(), "unresolved import " + label + " in " + rel
        if shaped and len(resolved) != 1:
            if module and symbol and symbol != "*":
                label = module + "." + symbol
            elif module:
                label = module
            else:
                label = symbol
            found = set()
            for got in resolved:
                mod_name = _tree_name(Path(str(base)) / got, base)
                if mod_name is not None:
                    found.add(mod_name)
            if not found:
                return frozenset(), "unresolved import " + label + " in " + rel
            edges.update(found)
            continue
        for got in resolved:
            mod_name = _tree_name(Path(str(base)) / got, base)
            if mod_name is not None:
                edges.add(mod_name)
    return frozenset(edges), None


def _close(table, edges):
    pullers = {}
    for importer, imported in edges.items():
        for name in imported:
            pullers.setdefault(name, []).append(importer)
    closed = {}
    for key, value in table.items():
        members = set(value)
        work = list(members)
        while work:
            name = work.pop()
            for importer in pullers.get(name, ()):
                if importer in members:
                    continue
                members.add(importer)
                work.append(importer)
        closed[key] = members
    return closed


def _index(root, layout, gen_map=None):
    base = Path(str(root))
    if gen_map is None:
        try:
            gen_map = generated.read(base)
        except KeyboardInterrupt:
            raise
        except BrigadeError:
            raise
        except Exception:
            gen_map = generated.EMPTY
    files = _tree_files(base)
    helper_set = frozenset(_norm(one) for one in layout.helpers)
    entry_set = frozenset(_norm(one) for one in layout.entry_points)
    helper_entries = _helper_entries(base, layout)
    loads = {}
    pins = {}
    direct = {}
    edges = {}
    broken_helpers = set()
    for path in files:
        name = _tree_name(path, base)
        if not name:
            continue
        try:
            rel = Path(str(path)).resolve().relative_to(base.resolve()).as_posix()
        except ValueError:
            continue
        helper = _norm(rel) in helper_set
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            if helper:
                broken_helpers.add(_norm(rel))
            continue
        try:
            mod = ast.parse(text, str(path))
        except SyntaxError:
            if helper:
                broken_helpers.add(_norm(rel))
            continue
        singles = _singles(mod)
        refs = _loads_of(mod, singles)
        if helper:
            refs = frozenset(_norm(ref) for ref in refs if _norm(ref) in entry_set)
        if refs:
            loads[name] = refs
        lits = _pin_literals(mod)
        if lits:
            pins[name] = lits
        targets = _import_targets(mod)
        resolved = set()
        for target in targets:
            for got in _resolve_import(target, rel, base, layout.import_roots):
                resolved.add(got)
        if resolved:
            direct[name] = resolved
        edge_set, open_reason = _test_edges(targets, rel, base, layout.import_roots)
        if open_reason is not None:
            return _Open(open_reason)
        edges[name] = edge_set
    for refs in direct.values():
        for ref in refs:
            if _norm(ref) in broken_helpers:
                return _Open("unresolved helper " + _norm(ref))
    for name, refs in direct.items():
        for ref in refs:
            norm_ref = _norm(ref)
            if norm_ref in helper_entries:
                entry = helper_entries[norm_ref]
                if entry is None:
                    return _Open("unresolved helper " + norm_ref)
                if entry:
                    prior = loads.get(name, frozenset())
                    loads[name] = frozenset(set(prior) | set(entry))
    table = {}
    hops = {}
    for name, refs in loads.items():
        for ref in refs:
            norm = _norm(ref)
            if norm and _looks_repo(norm):
                for canon in _canonicals(norm, gen_map):
                    table.setdefault(canon, set()).add(name)
            if not norm or not norm.endswith(".py"):
                continue
            if norm not in hops:
                hops[norm] = _one_hop(norm, base, gen_map, layout.import_roots)
            for canon in hops[norm]:
                table.setdefault(canon, set()).add(name)
    for name, refs in direct.items():
        for ref in refs:
            for canon in _canonicals(_norm(ref), gen_map):
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
                twins = generated.twins(gen_map, _norm(rel))
                t3s = tuple(sorted(t for t in twins if t.startswith("t3/")))
                key = t3s if t3s else (rel,)
                classes.add(key)
            if len(classes) != 1:
                continue
            key = next(iter(classes))
            if set(key) & frozenset(layout.entry_points):
                continue
            if key[0].startswith("t3/"):
                for canon in key:
                    table.setdefault(canon, set()).update(tests)
            else:
                table.setdefault(key[0], set()).update(tests)
    unions = {}
    changed = True
    while changed:
        changed = False
        for key in sorted(table):
            if key not in unions:
                unions[key] = _facade_private(key, base, layout.import_roots)
        for facade in sorted(unions):
            members = unions[facade]
            if not members:
                continue
            own = set()
            for canon in _canonicals(facade, gen_map):
                own.update(table.get(canon, ()))
            if not own:
                continue
            for member in members:
                for canon in _canonicals(member, gen_map):
                    have = table.setdefault(canon, set())
                    if not own.issubset(have):
                        have.update(own)
                        changed = True
    closed = _close(table, edges)
    filtered = {}
    for key, value in closed.items():
        kept = tuple(sorted(one for one in value if _is_test_name(one)))
        if kept:
            filtered[key] = kept
    return _Closed(filtered)


def _select(root, paths, layout, change_dir=""):
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
        return _Modules((layout.always,))
    tracked = _tracked(base)
    try:
        gen_map = generated.read(base)
    except KeyboardInterrupt:
        raise
    except BrigadeError:
        raise
    except Exception:
        gen_map = generated.EMPTY
    index = _index(base, layout, gen_map)
    if isinstance(index, _Open):
        return _Full((index.reason,))
    expanded = []
    unmapped = []
    for norm in norms:
        files, bad = _expand_one(base, norm, tracked)
        unmapped.extend(bad)
        expanded.extend(files)
    if not expanded and unmapped:
        return _Full(tuple(sorted(set(unmapped))))
    if not expanded:
        return _Modules((layout.always,))
    modules = set()
    missing = set()
    for norm in expanded:
        if norm.startswith(".github/workflows/"):
            if layout.workflow_module:
                modules.add(layout.workflow_module)
            else:
                missing.add(norm)
            continue
        if change_dir and (norm == change_dir or norm.startswith(change_dir + "/")):
            continue
        if norm.startswith("tests/") and norm.endswith(".py"):
            stem = norm[:-3]
            if "/test_" in "/" + stem or PurePosixPath(norm).name.startswith("test_"):
                modules.add(stem.replace("/", "."))
                for canon in _canonicals(norm, gen_map):
                    hit = index.table.get(canon)
                    if hit:
                        modules.update(hit)
                continue
        found = set()
        for canon in _canonicals(norm, gen_map):
            found.update(index.table.get(canon, ()))
        if found:
            modules.update(found)
        else:
            missing.add(norm)
    if missing or unmapped:
        return _Full(tuple(sorted(set(list(missing) + unmapped))))
    modules.add(layout.always)
    return _Modules(tuple(sorted(modules)))


def verify_text(root, paths, land, meta=None, store=None):
    if HELD_LOCKS:
        raise BrigadeError(f"affected may run git; release {HELD_LOCKS[0]}'s store lock before scanning")
    picked, build, check, change_dir = _settings(root, meta, store)
    if picked is None:
        return _fallback(land, check)
    if isinstance(paths, str):
        items = [part for part in paths.split(",") if part.strip() != ""]
    else:
        items = []
        for one in paths:
            for part in str(one).split(","):
                if part.strip() != "":
                    items.append(part)
    result = _select(root, items, picked, change_dir)
    return _paragraph(result, land, picked, build, check)
