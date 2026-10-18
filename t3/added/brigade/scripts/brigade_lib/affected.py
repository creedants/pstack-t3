import ast
from pathlib import Path, PurePosixPath
from brigade_lib import generated
from brigade_lib._affected.model import AFFECTED_KEY, DEFAULT_SUITE, Layout, _Closed, _Full, _Modules, _Open, layout
from brigade_lib._affected.report import _fallback, _paragraph, _settings
from brigade_lib._affected.scan import PIN_CHARS, _canonicals, _diff_names, _expand_one, _found_files, _import_targets, _loads_of, _looks_repo, _module_name, _norm, _pin_literals, _resolve_import, _singles, _test_files, _tracked
from brigade_lib.shared import BrigadeError
from brigade_lib.store import HELD_LOCKS

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
    return tuple(sorted(found))


def _helper_entries(base, layout):
    entries = {}
    for helper_rel in layout.helpers:
        try:
            helper_text = (Path(str(base)) / helper_rel).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            entries[helper_rel] = None
            continue
        try:
            helper_mod = ast.parse(helper_text, helper_rel)
        except SyntaxError:
            entries[helper_rel] = None
            continue
        helper_singles = _singles(helper_mod)
        found = frozenset(
            _norm(ref) for ref in _loads_of(helper_mod, helper_singles) if _norm(ref) in layout.entry_points
        )
        entries[helper_rel] = found
    return entries


def _test_edges(targets, rel, base, roots):
    edges = set()
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
        if shaped and len(resolved) != 1:
            if module and symbol and symbol != "*":
                label = module + "." + symbol
            elif module:
                label = module
            else:
                label = symbol
            return frozenset(), "unresolved import " + label + " in " + rel
        for got in resolved:
            mod_name = _module_name(Path(str(base)) / got, base)
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
    files = _test_files(base)
    helper_entries = _helper_entries(base, layout)
    loads = {}
    pins = {}
    direct = {}
    edges = {}
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
            for got in _resolve_import(target, rel, base, layout.import_roots):
                resolved.add(got)
        if resolved:
            direct[name] = resolved
        edge_set, open_reason = _test_edges(targets, rel, base, layout.import_roots)
        if open_reason is not None:
            return _Open(open_reason)
        edges[name] = edge_set
    combined = {}
    for name in set(list(loads) + list(direct)):
        own = set(loads.get(name, ()))
        for ref in direct.get(name, ()):
            norm_ref = _norm(ref)
            if norm_ref in helper_entries:
                entry = helper_entries[norm_ref]
                if entry is None:
                    return _Open("unresolved helper " + norm_ref)
                own = own | set(entry)
        if own:
            combined[name] = own
    table = {}
    hops = {}
    for name, refs in combined.items():
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
    closed = _close(table, edges)
    ordered = {key: tuple(sorted(value)) for key, value in closed.items()}
    return _Closed(ordered)


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
        found = None
        for canon in _canonicals(norm, gen_map):
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
