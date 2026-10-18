import ast
import inspect
from pathlib import Path

from .pyast import _module_function, _target_binds

SHARED = (
    ("IGNORED", "assign"),
    ("config_home", "def"),
    ("new_file", "def"),
)

def _module_assignment(module, name):
    found = None
    for statement in module.body:
        if isinstance(statement, ast.Assign) and any(_target_binds(target, name) for target in statement.targets):
            found = statement
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None and _target_binds(statement.target, name):
            found = statement
    return found


def _shared_node(module, name, kind):
    if kind == "def":
        return _module_function(module, name)
    return _module_assignment(module, name)


def _load_shared(path):
    if not path.is_file():
        return None
    source = path.read_text()
    return source, ast.parse(source, str(path))


def _function_source(source, node):
    lines = source.splitlines(keepends=True)
    start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
    return "".join(inspect.getblock(lines[start - 1:]))


def _binds_shared(statement, name):
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return statement.name == name
    if isinstance(statement, ast.Assign):
        return any(_target_binds(target, name) for target in statement.targets)
    if isinstance(statement, ast.AnnAssign):
        return statement.value is not None and _target_binds(statement.target, name)
    if isinstance(statement, ast.AugAssign):
        return _target_binds(statement.target, name)
    return False


def _unresolved_bindings(module, name):
    found = []

    def walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if _binds_shared(child, name):
                    found.append(child)
                continue
            if _binds_shared(child, name):
                found.append(child)
            walk(child)

    for statement in module.body:
        if isinstance(statement, (ast.If, ast.Try, ast.With, ast.For, ast.While, ast.Match)):
            walk(statement)
        elif name == "IGNORED" and isinstance(statement, ast.AugAssign) and _target_binds(statement.target, name):
            found.append(statement)
    return found


def shared_source_findings(roles_path, install_path, roles_label="t3/scripts/roles.py", install_label="scripts/install.py"):
    """Report source text of IGNORED, config_home, or new_file that differs between the two files.

    Parse both files. Do not import them. IGNORED is the last assignment to
    that name directly in the module body. config_home and new_file are the
    last def of that name directly in the module body. A function's compared
    text is the lines from its first decorator, or from the def line when it
    has no decorator, through the last line inspect.getblock keeps. A
    decorator line, the def, the body, an inline comment on the last
    statement, and an indented comment after the last statement are kept.
    A blank line inside the function is kept. A blank line after the last
    kept line is dropped. A comment at column 0 after the function is
    dropped. An assignment is compared with ast.get_source_segment. A def,
    an async def, an assignment, an annotated assignment with a value, or an
    augmented assignment to one of these three names inside if, try, with,
    for, while, or match at module level is a binding the static comparison
    cannot resolve. An augmented assignment to IGNORED directly in the module
    body is the same kind of binding. A binding inside a function or class
    body is not a module binding. The last direct definition still wins among
    direct ones. A missing file, a missing name, or an unreadable file is a
    finding. None on both sides is not a match. An empty list means all three
    names exist as direct definitions in both files, none of them has a
    binding the static comparison cannot resolve, and their source text is
    the same.
    """
    roles_path = Path(roles_path)
    install_path = Path(install_path)
    loaded = {}
    findings = []
    for label, path in ((roles_label, roles_path), (install_label, install_path)):
        try:
            loaded[label] = _load_shared(path)
        except (OSError, ValueError, SyntaxError, UnicodeDecodeError) as error:
            loaded[label] = error
        if loaded[label] is None:
            findings.append(f"{label}: is missing, so IGNORED, config_home, and new_file were not compared")
        elif isinstance(loaded[label], Exception):
            findings.append(
                f"{label}: could not be read or parsed, so shared source was not compared: {type(loaded[label]).__name__}"
            )
    if any(not isinstance(loaded[label], tuple) for label in (roles_label, install_label)):
        return findings
    roles_source, roles_module = loaded[roles_label]
    install_source, install_module = loaded[install_label]
    for name, kind in SHARED:
        for label, module in ((roles_label, roles_module), (install_label, install_module)):
            for node in _unresolved_bindings(module, name):
                findings.append(f"{label}:{node.lineno}: {name} is bound where the static comparison cannot resolve it")
        roles_node = _shared_node(roles_module, name, kind)
        install_node = _shared_node(install_module, name, kind)
        if roles_node is None:
            findings.append(f"{roles_label}: has no {name}. Define it with the same source text as {install_label}")
        if install_node is None:
            findings.append(f"{install_label}: has no {name}. Define it with the same source text as {roles_label}")
        if roles_node is None or install_node is None:
            continue
        roles_text = _function_source(roles_source, roles_node) if kind == "def" else ast.get_source_segment(roles_source, roles_node)
        install_text = _function_source(install_source, install_node) if kind == "def" else ast.get_source_segment(install_source, install_node)
        if roles_text is None or install_text is None or roles_text != install_text:
            findings.append(
                f"{roles_label}: {name} differs from the one in {install_label}. Make the source text the same"
            )
    return findings
