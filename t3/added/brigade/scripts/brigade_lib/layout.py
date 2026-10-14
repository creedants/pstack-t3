import ast
import fnmatch
import posixpath
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class _Prefix:
    src: str
    dst: str


@dataclass(frozen=True)
class _Exact:
    src: str
    dst: str


@dataclass(frozen=True)
class _Glob:
    src: str
    pat: str
    dst: str


@dataclass(frozen=True)
class _Layout:
    copies: tuple
    pages: tuple


def _const(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _name(node):
    if isinstance(node, ast.Name):
        return node.id
    return None


def _div(node):
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return (node.left, node.right)
    return None


def _has(node, want):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id == want:
            return True
    return False


def _call_list(root, want):
    found = []
    for sub in ast.walk(root):
        if isinstance(sub, ast.Call):
            f = sub.func
            hit = False
            if isinstance(f, ast.Name) and f.id == want:
                hit = True
            if hit:
                found.append(sub)
    return found


def _top_assign(mod, want):
    for sub in mod.body:
        if isinstance(sub, ast.Assign) and len(sub.targets) == 1:
            got = _name(sub.targets[0])
            if got == want:
                return sub.value
    return None


def _root_name(mod, want, base):
    got = _top_assign(mod, want)
    if got is None:
        return None
    made = _div(got)
    if made is None:
        return None
    left, right = made
    if _name(left) != base:
        return None
    return _const(right)


def _out_name(mod):
    for sub in ast.walk(mod):
        if not isinstance(sub, ast.Call):
            continue
        f = sub.func
        if not (isinstance(f, ast.Attribute) and f.attr == "add_argument"):
            continue
        if not sub.args:
            continue
        if _const(sub.args[0]) != "--out":
            continue
        for key in sub.keywords:
            if key.arg != "default":
                continue
            made = _div(key.value)
            if made is None:
                continue
            left, right = made
            if _name(left) != "ROOT":
                continue
            got = _const(right)
            if got is None:
                continue
            return got
    return None


def _render_fn(mod):
    for sub in mod.body:
        if isinstance(sub, ast.FunctionDef) and sub.name == "render":
            return sub
    return None


def _cli_tools(mod):
    got = _top_assign(mod, "TOOLS")
    if got is None or not isinstance(got, (ast.Tuple, ast.List)):
        return None
    tools = []
    for one in got.elts:
        text = _const(one)
        if text is None:
            return None
        tools.append(text)
    return tuple(tools)


def _cli_out(mod):
    got = _top_assign(mod, "OUT")
    if got is None:
        return None
    parts = []
    cur = got
    while True:
        made = _div(cur)
        if made is None:
            break
        left, right = made
        tail = _const(right)
        if tail is None:
            return None
        parts.append(tail)
        cur = left
    if not parts or _name(cur) != "ROOT":
        return None
    parts.reverse()
    return posixpath.join(*parts)


def _page_name_ok(mod):
    for sub in mod.body:
        if not (isinstance(sub, ast.FunctionDef) and sub.name == "page_name"):
            continue
        args = sub.args
        if args.vararg or args.kwarg or args.kwonlyargs or args.posonlyargs:
            return None
        if len(args.args) != 1:
            return None
        arg = args.args[0].arg
        if len(sub.body) != 1 or not isinstance(sub.body[0], ast.Return):
            return None
        made = sub.body[0].value
        if not (isinstance(made, ast.BinOp) and isinstance(made.op, ast.Add)):
            return None
        if not (isinstance(made.right, ast.Constant) and made.right.value == ".md"):
            return None
        head = made.left
        if not (isinstance(head, ast.Attribute) and head.attr == "stem"):
            return None
        call = head.value
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "Path"):
            return None
        if len(call.args) != 1 or call.keywords:
            return None
        if _name(call.args[0]) != arg:
            return None
        return arg
    return None


def _cli_pages(path):
    try:
        mod = ast.parse(path.read_text(encoding="utf-8"), str(path))
    except (OSError, SyntaxError, UnicodeError):
        return ()
    tools = _cli_tools(mod)
    out = _cli_out(mod)
    if tools is None or not out or _page_name_ok(mod) is None:
        return ()
    return tuple((tool, out + "/" + PurePosixPath(tool).stem + ".md") for tool in tools)


def _resolve(node, arg, pub, run_map):
    made = _div(node)
    if made is None:
        got = _name(node)
        if got == arg:
            return pub
        if got in run_map:
            return pub + "/" + run_map[got]
        return None
    left, right = made
    tail = _const(right)
    if tail is None:
        return None
    head = _resolve(left, arg, pub, run_map)
    if head is None:
        return None
    return head + "/" + tail


def _layout(project_root):
    try:
        base = Path(str(project_root))
        b_path = base / "scripts" / "build.py"
        c_path = base / "scripts" / "cli_reference.py"
        b_mod = ast.parse(b_path.read_text(encoding="utf-8"), str(b_path))
        src_name = _root_name(b_mod, "T3", "ROOT")
        pub = _out_name(b_mod)
        if not src_name or not pub:
            return None
        fn = _render_fn(b_mod)
        if fn is None or not fn.args.args:
            return None
        arg = fn.args.args[0].arg
        pages = _cli_pages(c_path)
        vendor_vars = set()
        for sub in fn.body:
            if isinstance(sub, ast.Assign) and len(sub.targets) == 1:
                got = _name(sub.targets[0])
                if got and _has(sub.value, "VENDOR"):
                    vendor_vars.add(got)
        run_map = {}
        for sub in fn.body:
            if isinstance(sub, ast.Assign) and len(sub.targets) == 1:
                got = _name(sub.targets[0])
                made = _div(sub.value)
                if got and made is not None:
                    left, right = made
                    if _name(left) == arg:
                        tail = _const(right)
                        if tail is not None:
                            run_map[got] = tail
        prefix_spots = []
        for sub in fn.body:
            if not isinstance(sub, ast.For):
                continue
            outer = _name(sub.target)
            seq = sub.iter
            if isinstance(seq, (ast.Tuple, ast.List)):
                layers = [_const(one) for one in seq.elts]
                if any(one is None for one in layers):
                    continue
                base_var = None
                inner_var = None
                for step in ast.walk(sub):
                    if isinstance(step, ast.Assign) and len(step.targets) == 1:
                        made = _div(step.value)
                        if made is not None:
                            left, right = made
                            if _name(left) == "T3" and _name(right) == outer:
                                base_var = _name(step.targets[0])
                for call in _call_list(sub, "copy_file"):
                    if len(call.args) != 2:
                        continue
                    s_made = _div(call.args[0])
                    d_made = _div(call.args[1])
                    if s_made is None or d_made is None:
                        continue
                    s_l, s_r = s_made
                    d_l, d_r = d_made
                    s_n = _name(s_l)
                    d_n = _name(d_l)
                    s_v = _name(s_r)
                    d_v = _name(d_r)
                    if s_n == base_var and d_n == arg and s_v and s_v == d_v:
                        inner_var = s_v
                        break
                if base_var and inner_var:
                    prefix_spots.append((sub, outer, tuple(layers)))
        glob_spots = []
        for sub in fn.body:
            if not isinstance(sub, ast.For):
                continue
            outer = _name(sub.target)
            seq = sub.iter
            if not (isinstance(seq, ast.Call) and isinstance(seq.func, ast.Name) and seq.func.id == "sorted"):
                continue
            if len(seq.args) != 1:
                continue
            inner = seq.args[0]
            if not (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute) and inner.func.attr == "glob"):
                continue
            host = inner.func.value
            host_made = _div(host)
            if host_made is None:
                continue
            h_l, h_r = host_made
            if _name(h_l) != "T3":
                continue
            folder = _const(h_r)
            if folder is None:
                continue
            if len(inner.args) != 1:
                continue
            pat = _const(inner.args[0])
            if pat is None:
                continue
            run_var = None
            hold = None
            for call in _call_list(sub, "copy_file"):
                if len(call.args) != 2:
                    continue
                if _name(call.args[0]) != outer:
                    continue
                dest = call.args[1]
                dest_made = _div(dest)
                if dest_made is None:
                    continue
                d_l, d_r = dest_made
                if not (isinstance(d_r, ast.Attribute) and d_r.attr == "name"):
                    continue
                if _name(d_r.value) != outer:
                    continue
                base_made = _div(d_l)
                if base_made is None:
                    continue
                b_l, b_r = base_made
                hold = _const(b_r)
                cand = _name(b_l)
                if hold is None or cand is None:
                    continue
                if cand not in run_map:
                    continue
                run_var = cand
                break
            if run_var and hold is not None:
                glob_spots.append((sub, outer, folder, pat, run_var, hold))
        seen_calls = set()
        for spot, _o, _l in prefix_spots:
            for call in _call_list(spot, "copy_file"):
                seen_calls.add(id(call))
        for spot, _o, _f, _p, _r, _h in glob_spots:
            for call in _call_list(spot, "copy_file"):
                seen_calls.add(id(call))
        copies = []
        for _spot, _o, layers in prefix_spots:
            for one in layers:
                copies.append(_Prefix(src_name + "/" + one, pub))
        for _spot, _o, folder, pat, run_var, hold in glob_spots:
            dest = pub + "/" + run_map[run_var] + "/" + hold
            copies.append(_Glob(src_name + "/" + folder, pat, dest))
        all_calls = _call_list(fn, "copy_file")
        for call in all_calls:
            if id(call) in seen_calls:
                continue
            if len(call.args) != 2:
                return None
            if _has(call.args[0], "VENDOR"):
                continue
            hit_vendor = False
            for sub in ast.walk(call.args[0]):
                if isinstance(sub, ast.Name) and sub.id in vendor_vars:
                    hit_vendor = True
                    break
            if hit_vendor:
                continue
            s_made = _div(call.args[0])
            if s_made is None:
                return None
            s_l, s_r = s_made
            if _name(s_l) != "T3":
                return None
            head = _const(s_r)
            if head is None:
                return None
            tail = _resolve(call.args[1], arg, pub, run_map)
            if tail is None:
                return None
            copies.append(_Exact(src_name + "/" + head, tail))
        return _Layout(tuple(copies), tuple(pages))
    except KeyboardInterrupt:
        raise
    except Exception:
        return None


def _one(norm, lay):
    found = []
    for item in lay.copies:
        if isinstance(item, _Exact):
            if norm == item.src:
                if item.dst not in found:
                    found.append(item.dst)
            elif posixpath.dirname(item.src) == norm:
                if item.dst not in found:
                    found.append(item.dst)
        elif isinstance(item, _Glob):
            if norm == item.src:
                if item.dst not in found:
                    found.append(item.dst)
            else:
                head = item.src + "/"
                if norm.startswith(head):
                    rest = norm[len(head):]
                    if rest and "/" not in rest and fnmatch.fnmatch(rest, item.pat):
                        dest = item.dst + "/" + rest
                        if dest not in found:
                            found.append(dest)
        elif isinstance(item, _Prefix):
            if norm == item.src:
                if item.dst not in found:
                    found.append(item.dst)
            else:
                head = item.src + "/"
                if norm.startswith(head):
                    rest = norm[len(head):]
                    if rest:
                        dest = item.dst + "/" + rest
                        if dest not in found:
                            found.append(dest)
    for tool, head in lay.pages:
        if norm == tool:
            if head not in found:
                found.append(head)
        elif tool.startswith(norm + "/"):
            if head not in found:
                found.append(head)
    return tuple(found)
